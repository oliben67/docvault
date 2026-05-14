"""
Document history example.

Shows how to:
- Create and update a document multiple times
- List the full commit history for a document
- Fetch the document at a specific historical revision
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

        # Create initial document
        doc = await store.create_doc(
            CreateDocInput(
                content={"version": "1.0", "env": "development", "debug": True},
                creator="alice",
                summary="App configuration",
            )
        )
        print(f"Created:  {doc.meta.id[:12]}  v={doc.content['version']}")

        # Apply a series of updates
        for v, env in [("1.1", "staging"), ("2.0", "production"), ("2.1", "production")]:
            doc = await store.update_doc(
                doc.meta.id,
                UpdateDocInput(content={"version": v, "env": env, "debug": False}),
            )
            print(f"Updated:  {doc.meta.id[:12]}  v={doc.content['version']}  env={doc.content['env']}")

        # List full history (newest first)
        history = await store.get_doc_history(doc.meta.id)
        print(f"\nHistory ({len(history)} commits):")
        for entry in history:
            print(f"  {entry.sha[:10]}  {entry.message[:60]}")

        # Fetch at oldest commit to see the original content
        oldest_sha = history[-1].sha
        old_doc = await store.get_doc_at_ref(doc.meta.id, oldest_sha)
        print(f"\nAt oldest commit ({oldest_sha[:10]}):")
        print(f"  version={old_doc.content['version']}  env={old_doc.content['env']}")

        # Fetch at latest commit (same as current)
        latest_sha = history[0].sha
        latest_doc = await store.get_doc_at_ref(doc.meta.id, latest_sha)
        print(f"\nAt latest commit ({latest_sha[:10]}):")
        print(f"  version={latest_doc.content['version']}  env={latest_doc.content['env']}")

        # max_count limits how many commits are returned
        recent = await store.get_doc_history(doc.meta.id, max_count=2)
        print(f"\nLast 2 commits only: {[e.sha[:10] for e in recent]}")


if __name__ == "__main__":
    asyncio.run(main())
