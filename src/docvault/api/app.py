from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ..config import VaultConfig
from ..core.store import DocVault  # re-exported for create_mountable_router callers
from .shim import DocVaultShim

_DESCRIPTION = """\
**DocVault** is a git-backed JSON document store with full revision history,
JSON Schema templates, vault versioning, and optional LLM metadata inference.

## Key concepts

| Concept | Description |
|---------|-------------|
| **Document** | Arbitrary JSON with auto-generated metadata. Every write is a git commit. |
| **Template** | Named JSON Schema that validates documents on create/update, with field defaults. |
| **Vault** | Vault-level metadata with a semantic version tag. Bump to snapshot the collection. |
| **Summarization** | LLM inference that fills in `summary` and `keywords` from document content. |

## Interactive testing

Use the **Authorize** button above to set your API key (if `auth_mode = api_key`),
then expand any endpoint and click **Try it out**.

## Generating the OpenAPI spec

```bash
# Requires a running server
curl http://localhost:8000/openapi.json > docs/openapi.json
# Or via the Taskfile
task openapi
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
