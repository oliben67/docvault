from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ..config import VaultConfig
from ..core.vault import DocVault  # re-exported for create_mountable_router callers
from .shim import DocVaultShim

_DESCRIPTION = """\
**DocVault** is a git-backed document vault for structured and binary data.
Every write is a git commit; every document has a full, auditable revision
history; any document can be retrieved exactly as it was at any point in time.

## Core concepts

| Concept | Description |
|---------|-------------|
| **Document** | JSON or binary data stored with auto-generated metadata: `id`, `creator`, `created_at`, `updated_at`, `summary`, `keywords`. Every write produces a git commit. |
| **Store** | A named, versioned sub-vault that organises documents logically. Stores can be nested and can define optional slot schemas. Without a store, documents are free-floating in the root vault. |
| **Locked store** | A store created with `locked=true` only accepts bulk updates via `deploy`; individual document updates are rejected. |
| **Vault versioning** | The vault carries a semantic version (`major.minor.patch`). Bumping creates a git tag — a permanent snapshot of the entire collection. |
| **Summarization** | On-demand or automatic LLM inference (Claude) that fills `summary` and `keywords` from document content. |
| **Point-in-time retrieval** | Fetch any document at any git ref: commit SHA, tag, or branch name. |

## Deployment models

**Standalone server** — run `docvault serve` and interact over HTTP.

**Embedded shim** — mount DocVault inside your own FastAPI app:

```python
shim = DocVaultShim(VaultConfig(vault_path="./vault"))
app = FastAPI(lifespan=shim.wrap_lifespan())
app.include_router(shim.router)
```

## Authentication

| Mode | Description |
|------|-------------|
| `none` | Open access — for local development. |
| `api_key` | Static keys checked via `X-API-Key` header. |
| `passthrough` | Delegate to your host app's own auth dependency. |
"""

_TAGS_METADATA = [
    {"name": "Health", "description": "Liveness probe — no authentication required."},
    {
        "name": "Documents",
        "description": (
            "Full CRUD for root-level (free-floating) documents plus commit history, "
            "point-in-time retrieval, and on-demand LLM summarization."
        ),
    },
    {
        "name": "Stores",
        "description": (
            "Named sub-vaults that organise documents into structured, versioned namespaces. "
            "Each store can define optional slot schemas and may be locked to bulk-only updates."
        ),
    },
    {
        "name": "Vault",
        "description": (
            "Vault-level metadata and semantic versioning. "
            "Bump the version to create a git tag snapshot."
        ),
    },
    {
        "name": "Summarization",
        "description": "LLM-powered metadata inference. Requires `llm_api_key` in the vault config.",
    },
]


def create_app(
    config: VaultConfig,
    passthrough_dep: Callable[..., Any] | None = None,
    cors_origins: list[str] | None = None,
) -> FastAPI:
    """Build a standalone FastAPI application."""
    shim = DocVaultShim(config, passthrough_dep=passthrough_dep)

    app = FastAPI(
        title="DocVault",
        description=_DESCRIPTION,
        version="0.1.0",
        openapi_tags=_TAGS_METADATA,
        contact={"name": "DocVault", "url": "https://github.com/your-org/docvault"},
        license_info={"name": "MIT"},
        lifespan=shim.wrap_lifespan(),
    )

    if cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    app.include_router(shim.router)
    return app


def create_mountable_router(
    vault: DocVault,
    passthrough_dep: Callable[..., Any] | None = None,
    prefix: str = "/docvault",
):
    """Return an APIRouter for an already-initialised vault.

    Prefer :class:`~docvault.api.shim.DocVaultShim` for new integrations —
    it owns the vault lifecycle and composes cleanly with FastAPI's lifespan.
    """
    from .auth import build_auth_dep
    from .router import create_router

    return create_router(
        vault, build_auth_dep(vault.config, passthrough_dep), prefix=prefix
    )
