from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ..config import VaultConfig
from ..core.store import DocVault  # re-exported for create_mountable_router callers
from .shim import DocVaultShim

_DESCRIPTION = """\
**DocVault** is a git-backed document store for structured data. Every write is
a git commit; every document has a full, auditable revision history; any
document can be retrieved exactly as it was at any point in time.

It ships as a standalone REST API, an embeddable FastAPI shim, and a CLI —
so it fits equally well as an independent service or as a library wired inside
your existing application.

## Core concepts

| Concept | Description |
|---------|-------------|
| **Document** | Arbitrary JSON (or any text file) stored with auto-generated metadata: `id`, `creator`, `created_at`, `updated_at`, `summary`, `keywords`. Every create, update, and delete produces a git commit. |
| **Template** | A named folder-structure blueprint made up of named *slots*. Each slot can carry a JSON Schema that validates documents on write. Templates can be bootstrapped from a local directory — any file type is supported. |
| **Template export / deploy** | A template and all its slot documents can be packaged into a zip archive and extracted to any local path, preserving original file names and extensions. |
| **Vault versioning** | The vault carries a semantic version (`major.minor.patch`). Bumping the version creates a git tag — a permanent, addressable snapshot of the entire document collection. |
| **Summarization** | On-demand or automatic LLM inference (Claude) that fills in `summary` and `keywords` from document content. Requires an Anthropic API key. |
| **Point-in-time retrieval** | Fetch any document at any git ref: commit SHA, tag, or branch name. |

## Deployment models

**Standalone server** — run `docvault serve` and interact over HTTP. All
endpoints are documented in this Swagger UI.

**Embedded shim** — mount DocVault inside your own FastAPI app with two lines:

```python
shim = DocVaultShim(VaultConfig(vault_path="./vault"))
app = FastAPI(lifespan=shim.wrap_lifespan())
app.include_router(shim.router)
```

All DocVault routes are namespaced under a configurable prefix (default
`/api/v1`) and never collide with your own routes.

## Authentication

| Mode | Description |
|------|-------------|
| `none` | Open access — for local development. |
| `api_key` | Static keys checked via `X-API-Key` header. |
| `passthrough` | Delegate to your host app's own auth dependency. |

## Interactive testing

Use the **Authorize** button above to set your API key when
`auth_mode = api_key`, then expand any endpoint and click **Try it out**.

## Generating the OpenAPI spec

```bash
task openapi   # writes docs/openapi.json without a running server
```
"""

_TAGS_METADATA = [
    {
        "name": "Health",
        "description": "Liveness probe — no authentication required.",
    },
    {
        "name": "Documents",
        "description": (
            "Full CRUD for JSON documents plus commit history, point-in-time retrieval, "
            "and on-demand LLM summarization."
        ),
    },
    {
        "name": "Templates",
        "description": "Named JSON Schema definitions used to validate and default-fill documents.",
    },
    {
        "name": "Vault",
        "description": (
            "Vault-level metadata and semantic versioning. "
            "Bump the version to create a git tag snapshot of the collection."
        ),
    },
    {
        "name": "Summarization",
        "description": (
            "LLM-powered metadata inference. Requires `llm_api_key` in the vault config."
        ),
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
    store: DocVault,
    passthrough_dep: Callable[..., Any] | None = None,
    prefix: str = "/docvault",
):
    """Return an APIRouter for an already-initialised store.

    .. note::
        Prefer :class:`~docvault.api.shim.DocVaultShim` for new integrations —
        it owns the store lifecycle and composes cleanly with FastAPI's lifespan.

    Usage::

        app = FastAPI()
        store = DocVault(config)
        await store.init()           # caller manages lifecycle
        app.include_router(
            create_mountable_router(store, passthrough_dep=my_auth_dep)
        )
    """
    from .auth import build_auth_dep
    from .router import create_router

    return create_router(
        store, build_auth_dep(store.config, passthrough_dep), prefix=prefix
    )
