"""
Store versioning example.

Shows how store versions work when folder content changes:
- First creation → version 1
- Content changes → version increments (2, 3, ...)
- Content reverts to a known snapshot → version reverts to the original number
- Same content as current → no-op, same ID returned
- Two stores with same name + same content are treated as copies
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from docvault.config import VaultConfig
from docvault.core.vault import DocVault
from docvault.core.store import StoreCreateInput


async def main() -> None:
    with tempfile.TemporaryDirectory() as d:
        cfg = VaultConfig(vault_path=Path(d) / "vault")
        vault = DocVault(cfg)
        await vault.init()

        # Set up a folder that will act as the store source
        src = Path(d) / "config-store"
        src.mkdir()
        (src / "app.json").write_text('{"host": "localhost", "port": 8080}', "utf-8")
        (src / "database.json").write_text('{"url": "postgres://db/app"}', "utf-8")

        # ── Creation (version 1) ──────────────────────────────────────────────
        store_obj = await vault.create_store(
            StoreCreateInput(name="platform-config", path=src)
        )
        meta = await store_obj.get_meta()
        print(f"[create]  version={meta.version}  id={meta.id}")

        # ── No-op: same folder, same content ─────────────────────────────────
        store_obj2 = await vault.create_store(
            StoreCreateInput(name="platform-config", path=src)
        )
        meta2 = await store_obj2.get_meta()
        assert meta2.id == meta.id, "Same content must return same ID"
        print(f"[no-op]   version={meta2.version}  id={meta2.id}  (unchanged)")

        # ── Content change → version 2 ────────────────────────────────────────
        (src / "app.json").write_text(
            '{"host": "api.prod.example.com", "port": 443}', "utf-8"
        )
        store_v2 = await vault.create_store(
            StoreCreateInput(name="platform-config", path=src)
        )
        meta_v2 = await store_v2.get_meta()
        print(f"[update]  version={meta_v2.version}  id={meta_v2.id}")
        assert meta_v2.version == 2

        # ── Another change → version 3 ────────────────────────────────────────
        (src / "database.json").write_text(
            '{"url": "postgres://prod-db/app", "pool": 20}', "utf-8"
        )
        store_v3 = await vault.create_store(
            StoreCreateInput(name="platform-config", path=src)
        )
        meta_v3 = await store_v3.get_meta()
        print(f"[update]  version={meta_v3.version}  id={meta_v3.id}")
        assert meta_v3.version == 3

        # ── Revert to v1 content → version goes back to 1 ────────────────────
        (src / "app.json").write_text('{"host": "localhost", "port": 8080}', "utf-8")
        (src / "database.json").write_text('{"url": "postgres://db/app"}', "utf-8")
        store_reverted = await vault.create_store(
            StoreCreateInput(name="platform-config", path=src)
        )
        meta_reverted = await store_reverted.get_meta()
        print(f"[revert]  version={meta_reverted.version}  id={meta_reverted.id}  (back to v1)")
        assert meta_reverted.version == 1
        assert meta_reverted.id == meta.id

        # ── Copies: different store name, same structure ───────────────────────
        store_copy = await vault.create_store(
            StoreCreateInput(
                name="platform-config-staging",
                structure=dict(meta_reverted.structure),
            )
        )
        meta_copy = await store_copy.get_meta()
        print(
            f"\n[copy]    name={meta_copy.name}  version={meta_copy.version}"
        )
        assert meta_copy.name == "platform-config-staging"
        assert meta_copy.version == 1

        # Redeploy same copy structure → no-op
        store_copy2 = await vault.create_store(
            StoreCreateInput(
                name="platform-config-staging",
                structure=dict(meta_reverted.structure),
            )
        )
        meta_copy2 = await store_copy2.get_meta()
        assert meta_copy2.id == meta_copy.id
        print(f"[no-op]   name={meta_copy.name}  id={meta_copy2.id}  (unchanged)")

        # ── Summary ───────────────────────────────────────────────────────────
        stores = await vault.list_stores()
        print(f"\nVault contains {len(stores)} store(s):")
        for s in stores:
            print(
                f"  {s.name:<28} version={s.version}"
                f"  history_entries={len(s.version_history)}"
            )


if __name__ == "__main__":
    asyncio.run(main())
