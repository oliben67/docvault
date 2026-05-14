"""
Markdown vault example.

Shows how to:
- Create a named Store to organise related markdown files
- Store markdown files in the store, keyed by their original path
- Retrieve any file back by that exact path string
- Update a file and retrieve the new version
- List all stored paths
- Recover a previous version using git history

The original path (e.g. "docs/architecture.md") is preserved in the
document metadata so you can always find a file the same way you
would on disk.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Any

from docvault.config import VaultConfig
from docvault.core.document import CreateDocInput, UpdateDocInput
from docvault.core.vault import DocVault, Store
from docvault.core.store import StoreCreateInput
from docvault.exceptions import DocumentNotFoundError


# ── Source documents ───────────────────────────────────────────────────────────

DOCS: dict[str, str] = {
    "README.md": """\
# payment-api

REST service that handles payment processing.

## Quick start

```bash
docker run -p 8080:8080 payment-api:latest
```

## Configuration

See `docs/configuration.md` for all environment variables.
""",
    "docs/architecture.md": """\
# Architecture

## Overview

payment-api is a stateless service that proxies requests to the
upstream payment processor and records every transaction in the
audit log.

## Components

- **API layer** — FastAPI, handles auth and validation
- **Processor client** — thin wrapper around the provider SDK
- **Audit log** — append-only Postgres table
""",
    "docs/runbook.md": """\
# Runbook

## Restarting the service

```bash
kubectl rollout restart deployment/payment-api
```

## Checking logs

```bash
kubectl logs -l app=payment-api --tail=100
```

## Common alerts

| Alert | Cause | Action |
|-------|-------|--------|
| HighErrorRate | upstream timeouts | check provider status page |
| SlowResponses | DB contention | run EXPLAIN on slow queries |
""",
}


# ── Vault helpers ──────────────────────────────────────────────────────────────

STORE_NAME = "markdown-docs"


def _text_content(body: str) -> dict[str, Any]:
    """Wrap raw text in the vault's text-content envelope."""
    return {"content": body, "_source_ext": "md"}


def _unwrap(content: dict[str, Any]) -> str:
    """Extract the raw text from the vault envelope."""
    return content.get("content", "")


async def put(store: Store, path: str, body: str, creator: str = "writer") -> str:
    """Store or replace a markdown file by its original path. Returns the doc ID."""
    existing = await find(store, path)
    if existing is None:
        doc = await store.create_doc(
            CreateDocInput(
                content=_text_content(body),
                creator=creator,
                path=path,
                commit_message=f"Add {path}",
            )
        )
        return doc.meta.id
    else:
        doc = await store.update_doc(
            existing.meta.id,
            UpdateDocInput(
                content=_text_content(body),
                commit_message=f"Update {path}",
            ),
        )
        return doc.meta.id


async def get(store: Store, path: str) -> str:
    """Retrieve the current content of a markdown file by its original path."""
    doc = await find(store, path)
    if doc is None:
        raise DocumentNotFoundError(f"No document at path {path!r}")
    return _unwrap(doc.content)


async def find(store: Store, path: str):
    """Return the Document for *path*, or None if not found."""
    metas = await store.list_docs()
    for meta in metas:
        if meta.path == path:
            return await store.get_doc(meta.id)
    return None


async def list_paths(store: Store) -> list[str]:
    """Return every stored path, sorted."""
    metas = await store.list_docs()
    return sorted(m.path for m in metas if m.path)


# ── Main ───────────────────────────────────────────────────────────────────────

def _rule(title: str = "") -> None:
    if title:
        print(f"\n── {title} {'─' * (56 - len(title))}")
    else:
        print("─" * 60)


async def main() -> None:
    with tempfile.TemporaryDirectory() as d:
        cfg = VaultConfig(vault_path=Path(d) / "vault")
        vault = DocVault(cfg)
        await vault.init()

        # ── 0. Create the store ────────────────────────────────────────────────
        store = await vault.create_store(
            StoreCreateInput(name=STORE_NAME, description="Markdown documentation files")
        )

        # ── 1. Store all markdown files ────────────────────────────────────────
        _rule("Storing markdown files")
        for path, body in DOCS.items():
            doc_id = await put(store, path, body)
            print(f"  stored  {path:<30}  id={doc_id[:12]}…")

        # ── 2. List all stored paths ───────────────────────────────────────────
        _rule("Paths in the vault")
        for path in await list_paths(store):
            print(f"  {path}")

        # ── 3. Retrieve a file by its original path ────────────────────────────
        _rule("Retrieving docs/architecture.md")
        content = await get(store, "docs/architecture.md")
        print(content)

        # ── 4. Update a file and retrieve the new version ─────────────────────
        _rule("Updating docs/runbook.md")
        updated_runbook = DOCS["docs/runbook.md"] + """\
## Scaling

```bash
kubectl scale deployment/payment-api --replicas=6
```
"""
        await put(store, "docs/runbook.md", updated_runbook)
        print("  pushed update")

        _rule("Retrieving docs/runbook.md after update")
        content = await get(store, "docs/runbook.md")
        print(content)

        # ── 5. Recover a previous version ─────────────────────────────────────
        _rule("Recovering previous version of docs/runbook.md")
        doc = await find(store, "docs/runbook.md")
        history = await store.get_doc_history(doc.meta.id)
        # history is newest-first; the oldest commit is the original version
        original_sha = history[-1].sha
        original_doc = await store.get_doc_at_ref(doc.meta.id, original_sha)
        print(f"  commit  {original_sha[:10]}  ({history[-1].message})")
        print()
        print(_unwrap(original_doc.content))


if __name__ == "__main__":
    asyncio.run(main())
