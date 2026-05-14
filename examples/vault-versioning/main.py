"""
Vault versioning example.

Shows how to:
- Inspect the vault's current semantic version
- Bump patch / minor / major independently
- Create named document versions alongside vault version bumps
- List vault version history
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from docvault.config import VaultConfig
from docvault.core.document import CreateDocInput, UpdateDocInput
from docvault.core.vault import DocVault


async def main() -> None:
    with tempfile.TemporaryDirectory() as d:
        cfg = VaultConfig(vault_path=Path(d) / "vault")
        store = DocVault(cfg)
        await store.init()

        # Inspect initial vault state
        vault = await store.get_vault()
        print(f"Initial vault version: {vault.version}  (name={vault.name})")

        # Create a document with a named version tag
        doc = await store.create_doc(
            CreateDocInput(
                content={"api_url": "https://api.example.com", "timeout": 30},
                creator="platform-team",
                named_version="v0.1.0-alpha",
                summary="Initial API gateway config",
            )
        )
        print(f"\nCreated doc {doc.meta.id[:12]}  named_version={doc.meta.named_version}")

        # Bump vault patch version
        vault = await store.bump_vault_version("patch")
        print(f"\nAfter patch bump: {vault.version}")

        # Update the document to reflect the new config
        doc = await store.update_doc(
            doc.meta.id,
            UpdateDocInput(
                content={"api_url": "https://api.example.com", "timeout": 60},
                named_version="v0.1.1",
            ),
        )
        print(f"Updated doc  named_version={doc.meta.named_version}")

        # Bump minor (resets patch to 0)
        vault = await store.bump_vault_version("minor")
        print(f"\nAfter minor bump: {vault.version}")

        # Bump major (resets minor and patch to 0)
        vault = await store.bump_vault_version("major")
        print(f"After major bump: {vault.version}")

        # Verify expected version
        assert str(vault.version) == "1.0.0", f"Expected 1.0.0, got {vault.version}"

        # Create a second document
        doc2 = await store.create_doc(
            CreateDocInput(
                content={"rate_limit": 1000, "burst": 200},
                creator="platform-team",
                named_version="v1.0.0",
                keywords=["rate-limiting", "production"],
            )
        )
        print(f"\nCreated doc2 {doc2.meta.id[:12]}  named_version={doc2.meta.named_version}")

        # List all documents to see both
        all_docs = await store.list_docs()
        print(f"\nVault contains {len(all_docs)} document(s):")
        for m in all_docs:
            print(f"  {m.id[:12]}  named_version={m.named_version or '—':<12}  keywords={m.keywords}")

        print(f"\nFinal vault: {vault.name} v{vault.version}")


if __name__ == "__main__":
    asyncio.run(main())
