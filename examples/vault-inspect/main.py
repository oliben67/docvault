"""
Vault inspection example.

Shows how to list and inspect all content in a vault:
- Vault metadata and semantic version
- All stores with their slot structures and version history
- All documents with metadata, filtering, and content preview
- Store satisfaction status (which required slots are filled)
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from docvault.config import VaultConfig
from docvault.core.document import CreateDocInput
from docvault.core.vault import DocVault
from docvault.core.store import DocSlot, StoreCreateInput


async def populate(vault: DocVault) -> None:
    """Seed the vault with a couple of stores and documents."""
    # Store 1 — microservice config
    store1 = await vault.create_store(
        StoreCreateInput(
            name="microservice",
            description="Standard microservice document set",
            structure={
                "config/app": DocSlot(
                    required=True,
                    description="Application config",
                    json_schema={
                        "type": "object",
                        "required": ["host", "port"],
                        "properties": {
                            "host": {"type": "string"},
                            "port": {"type": "integer"},
                        },
                    },
                ),
                "config/database": DocSlot(required=True, description="DB settings"),
                "docs/readme": DocSlot(required=False, description="Service README"),
            },
        )
    )
    await store1.create_doc(
        CreateDocInput(
            content={"host": "api.example.com", "port": 8080},
            creator="platform-bot",
            path="config/app",
            keywords=["production", "api"],
        )
    )
    await store1.create_doc(
        CreateDocInput(
            content={"url": "postgres://db.internal/app", "pool": 10},
            creator="platform-bot",
            path="config/database",
        )
    )

    # Store 2 — feature flag (empty structure, just a named container)
    await vault.create_store(
        StoreCreateInput(
            name="feature-flags",
            description="Runtime feature toggles",
            structure={
                "flags/global": DocSlot(required=True, description="Global flag set"),
            },
        )
    )
    # Leave feature-flags unsatisfied — no documents added

    # A couple of freestanding documents (no store)
    await vault.create_doc(
        CreateDocInput(
            content={"note": "remember to rotate keys before the release"},
            creator="alice",
            keywords=["ops", "security"],
            summary="Pre-release ops note",
        )
    )
    await vault.create_doc(
        CreateDocInput(
            content={"ticket": "PROJ-42", "status": "resolved"},
            creator="bob",
            keywords=["bug"],
        )
    )


# ── Display helpers ────────────────────────────────────────────────────────────

def _truncate(text: str, width: int = 60) -> str:
    return (text[: width - 1] + "…") if len(text) > width else text


async def list_vault(vault: DocVault) -> None:

    # ── 1. Vault metadata ──────────────────────────────────────────────────────
    vault_meta = await vault.get_vault()
    print("=" * 64)
    print(f"  Vault: {vault_meta.name}")
    print(f"  Version: v{vault_meta.version}")
    if vault_meta.description:
        print(f"  {vault_meta.description}")
    print(f"  Updated: {vault_meta.updated_at.strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 64)

    # ── 2. Stores ──────────────────────────────────────────────────────────────
    stores = await vault.list_stores()
    print(f"\n── Stores ({len(stores)}) {'─' * 40}")

    for store_meta in sorted(stores, key=lambda s: s.name):
        validation = await vault.validate_store(store_meta.name)
        status = "✓ satisfied" if validation.valid else f"✗ missing: {', '.join(validation.missing)}"

        print(f"\n  {store_meta.name}  v{store_meta.version}")
        print(f"    id:     {store_meta.id}")
        if store_meta.description:
            print(f"    desc:   {store_meta.description}")
        print(f"    status: {status}")
        if store_meta.locked:
            print(f"    locked: yes")

        if store_meta.structure:
            print(f"    slots ({len(store_meta.structure)}):")
            for slot_path, slot in sorted(store_meta.structure.items()):
                req = "required" if slot.required else "optional"
                schema = " [schema]" if slot.json_schema else ""
                filled = "●" if slot_path in validation.satisfied else "○"
                print(f"      {filled} {slot_path}  ({req}){schema}")

        if len(store_meta.version_history) > 1:
            print(f"    version history: {len(store_meta.version_history)} snapshots")

        if validation.extra:
            print(f"    extra docs (not in structure): {', '.join(validation.extra)}")

        # List documents in this store
        store_obj = await vault.get_store(store_meta.name)
        store_docs = await store_obj.list_docs()
        if store_docs:
            print(f"    documents ({len(store_docs)}):")
            for meta in store_docs:
                path_tag = f"  path={meta.path}" if meta.path else ""
                print(f"      {meta.id[:12]}…{path_tag}")

    # ── 3. Root-level documents ────────────────────────────────────────────────
    all_docs = await vault.list_docs()
    print(f"\n── Root documents ({len(all_docs)}) {'─' * 40}")

    for meta in all_docs:
        print(f"\n  {meta.id[:12]}…")
        print(f"    creator:  {meta.creator}")
        print(f"    updated:  {meta.updated_at.strftime('%Y-%m-%d %H:%M UTC')}")
        if meta.summary:
            print(f"    summary:  {_truncate(meta.summary)}")
        if meta.keywords:
            print(f"    keywords: {', '.join(meta.keywords)}")
        if meta.named_version:
            print(f"    version:  {meta.named_version}")
        print(f"    size:     {meta.size_bytes} bytes")

    # ── 4. Filter examples ─────────────────────────────────────────────────────
    print(f"\n── Filtered views {'─' * 40}")

    tagged = await vault.list_docs(keywords=["ops"])
    print(f"\n  Root docs with keyword 'ops': {len(tagged)}")
    for m in tagged:
        print(f"    {m.id[:12]}…  creator={m.creator}  keywords={m.keywords}")

    by_creator = await vault.list_docs(creator="alice")
    print(f"\n  Root docs by 'alice': {len(by_creator)}")
    for m in by_creator:
        print(f"    {m.id[:12]}…  summary={_truncate(m.summary or '—', 50)}")

    # Store-level filter
    store_obj = await vault.get_store("microservice")
    store_prod_docs = await store_obj.list_docs(keywords=["production"])
    print(f"\n  microservice store docs with keyword 'production': {len(store_prod_docs)}")
    for m in store_prod_docs:
        print(f"    {m.id[:12]}…  path={m.path}  keywords={m.keywords}")

    print()


async def main() -> None:
    with tempfile.TemporaryDirectory() as d:
        cfg = VaultConfig(vault_path=Path(d) / "vault", vault_name="demo-vault")
        vault = DocVault(cfg)
        await vault.init()

        await populate(vault)
        await list_vault(vault)


if __name__ == "__main__":
    asyncio.run(main())
