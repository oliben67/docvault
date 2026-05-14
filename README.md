# DocVault

DocVault is a git-backed document store for structured data. Every write is a git commit; every document carries a full, auditable revision history; any document can be retrieved exactly as it was at any point in time.

It ships as a standalone REST API, an embeddable FastAPI shim, and a CLI — fitting equally as an independent microservice or as a library embedded inside your existing application.

Documents can be plain JSON, text, or binary files. **Stores** define named folder structures with optional per-slot JSON Schema validation. Stores are content-addressable: their ID encodes both the source path and a hash of the content, so any change to the folder is detected automatically and increments the store version. Revert a folder to a previous snapshot and the version number reverts with it. A store can be bootstrapped from a local directory, exported as a zip archive, and deployed to any target path. The vault carries a semantic version that can be bumped to create a permanent git-tag snapshot of the entire collection. Stores can be marked locked to prevent direct document modification (deploy bypasses the lock). An optional LLM integration (Claude) auto-generates summaries and keywords from document content.

---

## Table of contents

- [Features](#features)
- [Installation](#installation)
- [Quick start — standalone server](#quick-start--standalone-server)
- [Quick start — embedded in your FastAPI app](#quick-start--embedded-in-your-fastapi-app)
- [The Shim — embedding DocVault](#the-shim--embedding-docvault)
  - [Pattern 1: lifespan context manager](#pattern-1-lifespan-context-manager-recommended)
  - [Pattern 2: wrap_lifespan helper](#pattern-2-wrap_lifespan-helper)
  - [Pattern 3: direct startup call](#pattern-3-direct-startup-call)
  - [Authentication](#authentication-in-the-shim)
  - [Custom URL prefix](#custom-url-prefix)
  - [Testing your integration](#testing-your-shim-integration)
- [Stores & versioning](#stores--versioning)
  - [Creating a store](#creating-a-store)
  - [Content-addressable IDs](#content-addressable-ids)
  - [Integer versioning and upsert](#integer-versioning-and-upsert)
  - [Version revert](#version-revert)
  - [Locked stores](#locked-stores)
  - [Binary documents](#binary-documents)
  - [Recursive nesting](#recursive-nesting)
- [Configuration](#configuration)
- [CLI reference](#cli-reference)
- [LLM summarization](#llm-summarization)
- [API reference](#api-reference)
- [Development](#development)
- [Full manual](https://github.com/oliben67/docvault/blob/main/MANUAL.md)

---

## Features

- **Git-backed storage** — every document write (create, update, delete) produces a git commit. Full history with author, timestamp, and message.
- **Point-in-time retrieval** — fetch any document at any commit SHA, tag, or branch.
- **Stores with JSON Schema** — define named folder structures; each slot can carry its own JSON Schema draft-7 constraint validated on every write.
- **Content-addressable store IDs** — store IDs encode `{name}:{path_hash}:{content_hash}`. Change the source folder and the ID changes; revert the folder and the original ID comes back.
- **Integer store versioning** — stores start at version `1`. Each detected content change bumps the version. Reverting to a previously-seen content snapshot reverts the version number too.
- **Locked stores** — mark a store as locked to prevent direct document modification; the `deploy()` path bypasses the lock for authorized writes.
- **Binary documents** — store any binary file (images, PDFs, arbitrary bytes) via a base64 envelope; retrieve the original bytes transparently.
- **Recursive nesting** — stores can contain sub-stores, each with their own document trees and version history.
- **Vault versioning** — bump the vault's semantic version and create a git tag snapshot of the entire collection.
- **Batch deploy** — create or replace many documents in one atomic commit via `store.deploy()`.
- **LLM summarization** — auto-generate `summary` and `keywords` from document content using Claude.
- **Flexible authentication** — none (dev), static API keys, or passthrough to your own auth system.
- **Embeddable** — mount DocVault inside any existing FastAPI app via `DocVaultShim` without conflicts.
- **Interactive Swagger UI** — browse and test all endpoints at `/docs` when the server is running.

---

## Installation

```bash
# Core (REST API + CLI)
pip install py-docvault
# or
uv add py-docvault

# With LLM summarization
pip install "py-docvault[llm]"
uv add "py-docvault[llm]"
```

Requires Python 3.11+.

---

## Quick start — standalone server

```bash
# 1. Create a vault
docvault init ./my-vault

# 2. Start the server
docvault serve --host 0.0.0.0 --port 8000

# 3. Open the interactive docs
open http://localhost:8000/docs
```

The Swagger UI at `/docs` lets you explore and execute every endpoint directly from the browser. A ReDoc view is also available at `/redoc`.

---

## Quick start — embedded in your FastAPI app

```python
from fastapi import FastAPI
from docvault import DocVaultShim, VaultConfig

config = VaultConfig(vault_path="./vault")
shim = DocVaultShim(config)

app = FastAPI(lifespan=shim.wrap_lifespan())
app.include_router(shim.router)
```

That's it. All DocVault routes are now available at `/api/v1/…` inside your app. See [The Shim](#the-shim--embedding-docvault) for advanced patterns.

---

## The Shim — embedding DocVault

`DocVaultShim` is the primary integration point for host apps. It owns the `DocVault` store and its lifecycle, and provides three mounting patterns so it fits cleanly into whatever lifecycle management your app already uses.

### Constructor

```python
DocVaultShim(
    config: VaultConfig,
    *,
    auth_dep: Callable | None = None,        # see Authentication section below
    passthrough_dep: Callable | None = None, # alternative to auth_dep
    prefix: str = "/api/v1",                 # URL prefix for all routes
)
```

| Parameter | Description |
|-----------|-------------|
| `config` | `VaultConfig` instance (see [Configuration](#configuration)) |
| `auth_dep` | A FastAPI dependency callable injected into every route for auth side-effects. Takes priority over `passthrough_dep`. |
| `passthrough_dep` | Used when `config.auth_mode == AuthMode.PASSTHROUGH`. Equivalent to `auth_dep` but routed through `build_auth_dep`. |
| `prefix` | URL prefix for all DocVault routes. Default: `/api/v1`. |

### Pattern 1: lifespan context manager (recommended)

Use this when your app already has a lifespan function and you want full control over the order of startup/shutdown operations.

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from docvault import DocVaultShim, VaultConfig

config = VaultConfig(vault_path="./vault")
shim = DocVaultShim(config)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # DocVault boots first
    async with shim.lifespan():
        # your startup code here — vault is ready
        await connect_database()
        yield
        # your shutdown code here

app = FastAPI(lifespan=lifespan)
app.include_router(shim.router)
```

`shim.lifespan()` is a plain async context manager — no FastAPI-specific coupling. Enter it anywhere an `async with` block is valid.

### Pattern 2: wrap_lifespan helper

Use this when you want DocVault to compose with your existing lifespan function, or when you have no lifespan at all.

**Without an existing lifespan:**

```python
app = FastAPI(lifespan=shim.wrap_lifespan())
app.include_router(shim.router)
```

**With an existing lifespan:**

```python
@asynccontextmanager
async def my_lifespan(app: FastAPI):
    await connect_database()
    yield
    await disconnect_database()

# DocVault boots first, then your lifespan is entered
app = FastAPI(lifespan=shim.wrap_lifespan(my_lifespan))
app.include_router(shim.router)
```

`wrap_lifespan` always boots DocVault *before* delegating to the host lifespan, so DocVault routes are usable from the very first request.

### Pattern 3: direct startup call

Use this when your framework manages dependencies through a DI container, service locator, or explicit `on_startup` hook.

```python
shim = DocVaultShim(config)
app.include_router(shim.router)

# Anywhere before the first request is served:
await shim.startup()
```

`startup()` is idempotent — calling it multiple times is safe. It simply calls `await store.init()` which is a no-op if the vault already exists.

### Authentication in the shim

DocVault supports three auth strategies, configured via the `auth_dep` constructor parameter and `config.auth_mode`.

#### Option A — `auth_dep` (recommended for host apps)

Pass any FastAPI dependency callable. It is executed before every DocVault route. The return value is ignored by DocVault — raise `HTTPException(401)` or `403` to block requests.

```python
from fastapi import Depends, HTTPException, Request
from your_app.auth import verify_token

async def require_admin(request: Request):
    token = request.headers.get("Authorization", "").removeprefix("Bearer ")
    user = await verify_token(token)
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin required")
    return user

shim = DocVaultShim(config, auth_dep=require_admin)
```

The dependency can itself declare `Depends(...)` and will be resolved by FastAPI's DI engine:

```python
async def require_logged_in(current_user = Depends(get_current_user)):
    if not current_user:
        raise HTTPException(401)
    return current_user

shim = DocVaultShim(config, auth_dep=require_logged_in)
```

#### Option B — `passthrough_dep` with `AuthMode.PASSTHROUGH`

Equivalent to `auth_dep` but uses the `build_auth_dep` code path. Requires `auth_mode = "passthrough"` in your config:

```python
config = VaultConfig(vault_path="./vault", auth_mode="passthrough")
shim = DocVaultShim(config, passthrough_dep=your_auth_callable)
```

#### Option C — built-in API key auth

Set `auth_mode = "api_key"` and provide keys in your config. Every request must include an `X-API-Key` header.

```python
config = VaultConfig(
    vault_path="./vault",
    auth_mode="api_key",
    api_keys=["sk-your-key-here"],
)
shim = DocVaultShim(config)
```

#### Option D — no auth (development)

The default. All requests are allowed. Do not use in production.

```python
config = VaultConfig(vault_path="./vault")  # auth_mode defaults to "none"
shim = DocVaultShim(config)
```

### Custom URL prefix

Override the default `/api/v1` prefix to avoid collisions with your existing routes:

```python
shim = DocVaultShim(config, prefix="/v2/documents")
# Routes are now: /v2/documents/health, /v2/documents/docs, etc.
```

### Testing your shim integration

`ASGITransport` (used by httpx for in-process testing) does not trigger the ASGI lifespan protocol. If your test relies on `store.init()` running (e.g. document CRUD), use the `asgi_lifespan_client` helper from `tests/conftest.py`:

```python
from tests.conftest import asgi_lifespan_client

async def test_my_integration():
    shim = DocVaultShim(config)
    app = FastAPI(lifespan=shim.wrap_lifespan())
    app.include_router(shim.router)

    async with asgi_lifespan_client(app) as client:
        resp = await client.post("/api/v1/docs", json={...})
        assert resp.status_code == 201
```

`asgi_lifespan_client` manually drives the ASGI `lifespan.startup` / `lifespan.shutdown` event cycle before and after yielding the client.

Tests that only hit `/health` or test auth rejection (no store access needed) can use `AsyncClient(transport=ASGITransport(app=app))` directly.

---

## Stores & versioning

### Creating a store

Stores define a named set of document slots — logical paths such as `"config/app"` or `"docs/readme"` — with optional JSON Schema validation per slot. There are two ways to create one:

**From a structure dict** (define slots explicitly):

```python
from docvault.core.store import DocSlot, StoreCreateInput
from docvault.core.vault import DocVault

vault = DocVault(config)
await vault.init()

store = await vault.create_store(
    StoreCreateInput(
        name="microservice",
        structure={
            "config/app": DocSlot(
                required=True,
                json_schema={
                    "type": "object",
                    "required": ["host", "port"],
                    "properties": {
                        "host": {"type": "string"},
                        "port": {"type": "integer"},
                    },
                },
            ),
            "config/database": DocSlot(required=True),
            "docs/readme": DocSlot(required=False),
        },
    )
)
meta = await store.get_meta()
print(meta.name, meta.id, meta.version)
```

**From a path** (scan a folder or single file):

```python
store = await vault.create_store(
    StoreCreateInput(name="project-docs", path=Path("./docs-folder"))
)
```

The folder is scanned recursively. Each file becomes a slot (using its relative path without extension). The file contents are ingested as vault documents. A single file creates one flat slot named after the file stem.

`create_store` returns a `Store` object. Call `await store.get_meta()` to get a `StoreMeta` with `id`, `name`, `version`, `structure`, and `version_history`. Use the store name for all subsequent get/validate/export/delete operations.

### Content-addressable IDs

Store IDs are not random UUIDs — they encode identity and content:

```
{name}:{md5(path)}:{md5(content)}
```

- **name** — the store name (first segment, also the storage key)
- **md5(path)** — hash of the source path (or name for structure-based stores)
- **md5(content)** — hash of the file tree or structure; this is what changes when content changes

The same `name` + same `content_hash` → same ID. The same `name` + different `content_hash` → different ID.

### Integer versioning and upsert

Store version is an integer starting at `1`. `create_store` is an **upsert**:

| Scenario | Result |
|----------|--------|
| Store name does not exist | Created with `version=1` |
| Same name, same content hash | No-op — existing `Store` returned unchanged |
| Same name, new content hash (not seen before) | Version incremented by `max(history) + 1` |
| Same name, content hash matches a historical snapshot | Version reverted to the snapshot's number |

The `version_history` field on `StoreMeta` records every `{content_hash: version}` mapping the store has ever had.

```python
# Create
store = await vault.create_store(StoreCreateInput(name="svc", path=folder))
meta = await store.get_meta()
print(meta.version)          # 1
print(meta.version_history)  # {"<hash_a>": 1}

# Change the folder contents
store = await vault.create_store(StoreCreateInput(name="svc", path=folder))
meta = await store.get_meta()
print(meta.version)          # 2
print(meta.version_history)  # {"<hash_a>": 1, "<hash_b>": 2}

# Revert the folder to its original contents
store = await vault.create_store(StoreCreateInput(name="svc", path=folder))
meta = await store.get_meta()
print(meta.version)          # 1  ← reverted
```

### Version revert

When the source folder is restored to a previously-seen state, the version number goes back to the number recorded for that content hash. History is never erased — reverting to v1 and then making a new change will produce v3, not v2, because v2 remains in history.

### Locked stores

A store can be marked as locked during creation:

```python
store = await vault.create_store(
    StoreCreateInput(name="prod-config", locked=True)
)
```

Locked stores reject direct document modification:

```python
# These raise StoreValidationError on a locked store:
await store.update_doc(doc_id, UpdateDocInput(...))
await store.delete_doc(doc_id)

# deploy() is the authorized path — it bypasses the lock:
await store.deploy([DeployDocSpec(path="config/app", content={...}, creator="ci-bot")])
```

This lets you model "write-only-via-CI" patterns where human ad-hoc edits are blocked but automated deploys succeed.

### Binary documents

Any binary file can be stored using `binary_content` and `mime_type`:

```python
from docvault.core.document import CreateDocInput

pdf_bytes = Path("report.pdf").read_bytes()
doc = await store.create_doc(
    CreateDocInput(
        binary_content=pdf_bytes,
        mime_type="application/pdf",
        creator="uploader",
        path="reports/q1",
    )
)
```

Binary content is stored internally as a base64 envelope `{"_binary": true, "_mime": "...", "_data": "..."}`. The `DocumentMeta.is_binary` field is `True` and `mime_type` is populated. When you call `store.get_doc(id)`, the raw bytes are returned in `doc.binary_content`.

### Recursive nesting

Every `DocVault` and every `Store` is a `_VaultNode` — they share the same document CRUD interface. A store can contain sub-stores:

```python
parent_store = await vault.create_store(StoreCreateInput(name="platform"))

sub_store = await parent_store.create_store(
    StoreCreateInput(name="monitoring")
)
await sub_store.create_doc(CreateDocInput(content={"alerts": True}, creator="ops"))
```

Sub-stores live at `<vault_path>/stores/platform/stores/monitoring/` in the git repo and participate in the same git history as their parent.

---

## Configuration

Configuration is resolved in this order (later sources win):

1. `docvault.json` in the current directory (or `--config` path)
2. Environment variables

### docvault.json

```json
{
  "vault_path": "./vault",
  "vault_name": "my-vault",
  "vault_description": "Production document store",
  "auth_mode": "api_key",
  "api_keys": ["sk-aaaa", "sk-bbbb"],
  "default_creator": "system",
  "git_author_name": "docvault-bot",
  "git_author_email": "bot@example.com",
  "llm_api_key": "sk-ant-...",
  "llm_model": "claude-haiku-4-5-20251001",
  "auto_summarize": false
}
```

### Environment variables

| Variable | Config field | Notes |
|----------|-------------|-------|
| `DOCVAULT_PATH` | `vault_path` | |
| `DOCVAULT_VAULT_NAME` | `vault_name` | |
| `DOCVAULT_AUTH_MODE` | `auth_mode` | `none`, `api_key`, or `passthrough` |
| `DOCVAULT_API_KEYS` | `api_keys` | Comma-separated list |
| `DOCVAULT_DEFAULT_CREATOR` | `default_creator` | |
| `DOCVAULT_GIT_AUTHOR_NAME` | `git_author_name` | |
| `DOCVAULT_GIT_AUTHOR_EMAIL` | `git_author_email` | |
| `DOCVAULT_LLM_API_KEY` | `llm_api_key` | Anthropic API key |
| `DOCVAULT_LLM_MODEL` | `llm_model` | Default: `claude-haiku-4-5-20251001` |
| `DOCVAULT_AUTO_SUMMARIZE` | `auto_summarize` | `1`, `true`, or `yes` |

### Full field reference

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `vault_path` | path | `./vault` | Directory where git repo and documents are stored |
| `vault_name` | string | `"default"` | Logical name for this vault |
| `vault_description` | string | `""` | Human-readable description |
| `auth_mode` | enum | `"none"` | Auth strategy: `none`, `api_key`, `passthrough` |
| `api_keys` | list[str] | `[]` | Valid keys when `auth_mode = "api_key"` |
| `default_creator` | string | `"system"` | Fallback creator used by background jobs |
| `git_author_name` | string | `"docvault"` | Git author name for system commits |
| `git_author_email` | string | `"docvault@localhost"` | Git author email for system commits |
| `llm_api_key` | string | `null` | Anthropic API key — required for summarization |
| `llm_model` | string | `"claude-haiku-4-5-20251001"` | Claude model for summarization |
| `auto_summarize` | bool | `false` | Auto-run LLM on every create/update |

---

## CLI reference

```
docvault [OPTIONS] COMMAND [ARGS]...
```

### Global option

`-c, --config PATH` — Path to a `docvault.json` file. Defaults to `./docvault.json`.

---

### `docvault init [PATH]`

Create a new vault (or open an existing one — idempotent).

```bash
docvault init ./my-vault
docvault init                # uses vault_path from config
```

---

### `docvault serve`

Start the REST API server.

```bash
docvault serve
docvault serve --host 0.0.0.0 --port 9000
```

| Flag | Default | Description |
|------|---------|-------------|
| `--host` | `127.0.0.1` | Bind address |
| `-p, --port` | `8000` | Port number |

---

### `docvault docs`

```bash
docvault docs list [--creator NAME] [--keywords KW1,KW2]
docvault docs get <DOC_ID>
docvault docs create --creator alice --file content.json [--summary TEXT] [--keywords KW1,KW2]
docvault docs create --creator alice --file -           # read JSON from stdin
docvault docs update <DOC_ID> --file updated.json [--summary TEXT] [--keywords KW]
docvault docs delete <DOC_ID> [--force]
docvault docs history <DOC_ID> [--max 20]
docvault docs at <DOC_ID> <REF>                         # git SHA, tag, or branch
docvault docs summarize <DOC_ID> [--overwrite]
docvault docs summarize-all [--overwrite]
```

---

### `docvault stores`

```bash
docvault stores list
docvault stores get <STORE_NAME>
docvault stores create <NAME> [--file schema.json] [--path ./folder] [--description TEXT] [--locked]
docvault stores delete <STORE_NAME> [--force]
docvault stores validate <STORE_NAME>
docvault stores docs list <STORE_NAME> [--keywords KW1,KW2]
docvault stores docs deploy <STORE_NAME> --file specs.json
```

`--file schema.json` — JSON file mapping slot paths to `DocSlot` objects (explicit structure).

`--path` — folder or single file to ingest as slots. Every file in the folder becomes a document slot; a single file creates one flat slot named after the file stem. `create` is an upsert: if a store with the same name already exists, it is updated if the content changed, or left unchanged if it has not.

`<STORE_NAME>` for `get`, `delete`, `validate`, and `docs` subcommands is the store name (not the content-addressable ID). Use `stores list` to see all current stores.

`specs.json` for `docs deploy` is a JSON array of `DeployDocSpec` objects:

```json
[
  { "path": "config/app",      "content": { "host": "api.example.com", "port": 8080 }, "creator": "ci-bot" },
  { "path": "config/database", "content": { "host": "db.internal" },                   "creator": "ci-bot" }
]
```

---

### `docvault vault`

```bash
docvault vault info
docvault vault versions
docvault vault bump [major|minor|patch]     # default: patch
```

---

### `docvault config`

```bash
docvault config show               # print resolved config (keys masked)
docvault config generate-key       # print a random API key
docvault config generate-key -n 3  # print 3 keys
```

---

## LLM summarization

DocVault uses Claude to infer `summary` (a one-sentence description) and `keywords` (a list of tags) from document content.

**Setup:**

```bash
export DOCVAULT_LLM_API_KEY="sk-ant-..."
# or set llm_api_key in docvault.json
```

**Auto-summarize on every write:**

```json
{ "auto_summarize": true }
```

**On-demand via API:**

```
POST /api/v1/docs/{id}/summarize
POST /api/v1/docs/summarize/all
```

**On-demand via CLI:**

```bash
docvault docs summarize <DOC_ID>
docvault docs summarize-all
```

Summarization is skipped if the document already has a `summary` unless `--overwrite` / `?overwrite=true` is passed.

---

## API reference

Full endpoint reference: **[MANUAL.md](https://github.com/oliben67/docvault/blob/main/MANUAL.md)**

When the server is running, the interactive Swagger UI is at:

```
http://localhost:8000/docs
```

ReDoc is at `/redoc`. The raw OpenAPI spec is at `/openapi.json`.

### Key routes

| Method | Route | Description |
|--------|-------|-------------|
| `GET` | `/api/v1/health` | Health check |
| `GET` | `/api/v1/vault` | Vault metadata |
| `POST` | `/api/v1/docs` | Create a root document |
| `GET` | `/api/v1/docs` | List root documents |
| `GET` | `/api/v1/docs/{id}` | Get a document |
| `PUT` | `/api/v1/docs/{id}` | Update a document |
| `DELETE` | `/api/v1/docs/{id}` | Delete a document |
| `GET` | `/api/v1/docs/{id}/history` | Document history |
| `GET` | `/api/v1/docs/{id}/at/{ref}` | Document at git ref |
| `POST` | `/api/v1/stores` | Create a store |
| `GET` | `/api/v1/stores` | List stores |
| `GET` | `/api/v1/stores/{name}` | Get a store |
| `DELETE` | `/api/v1/stores/{name}` | Delete a store |
| `GET` | `/api/v1/stores/{name}/validate` | Validate store satisfaction |
| `GET` | `/api/v1/stores/{name}/export` | Export store as zip |
| `POST` | `/api/v1/stores/{name}/deploy` | Batch deploy documents to a store |
| `GET` | `/api/v1/stores/{name}/docs` | List store documents |
| `POST` | `/api/v1/stores/{name}/docs` | Create a store document |
| `GET` | `/api/v1/stores/{name}/docs/{id}` | Get a store document |

To export the OpenAPI spec without a running server:

```bash
task openapi           # writes docs/openapi.json
```

---

## Development

### Setup

```bash
git clone https://github.com/oliben67/docvault
cd docvault
uv sync --all-extras
```

### Taskfile tasks

| Task | Description |
|------|-------------|
| `task test` | Run the test suite |
| `task test:v` | Verbose test output |
| `task lint` | Ruff lint check |
| `task lint:fix` | Auto-fix safe violations |
| `task fmt` | Format with ruff |
| `task fmt:check` | Check formatting (CI) |
| `task check` | Full CI gate: fmt:check + lint + test |
| `task fix` | lint:fix + fmt |
| `task dev` | Start dev server with auto-reload |
| `task openapi` | Export OpenAPI spec to `docs/openapi.json` |
| `task build` | Build wheel |
| `task example:shim:clean` | Wipe shim-integration demo state |
| `task example:shim:server` | Start shim-integration server on `:54321` |
| `task example:shim:demo` | Run shim-integration demo script |

### Running tests

```bash
task test
# or directly:
uv run pytest tests/ -v
```

The test suite uses `pytest-asyncio` in `auto` mode. All async test functions run in their own event loop.

### Project layout

```
src/docvault/
├── __init__.py          # public API: DocVaultShim, VaultConfig, load_config
├── config.py            # VaultConfig, load_config, AuthMode
├── exceptions.py        # DocVaultError hierarchy
├── api/
│   ├── __init__.py      # exports DocVaultShim
│   ├── app.py           # create_app (standalone FastAPI factory)
│   ├── auth.py          # build_auth_dep
│   ├── router.py        # create_router (all HTTP endpoints)
│   └── shim.py          # DocVaultShim (host-app integration)
├── core/
│   ├── document.py      # Document, DocumentMeta, CreateDocInput, UpdateDocInput
│   ├── vault_meta.py    # VaultMeta, VaultVersion
│   ├── git_backend.py   # GitBackend (asyncio.to_thread wrapper)
│   ├── vault.py         # _VaultNode base, Store, DocVault
│   ├── store.py         # Store models: StoreCreateInput, StoreMeta, DocSlot,
│   │                    #   DeployDocSpec, DeployStoreInput
│   └── summarizer.py    # DocumentSummarizer (Anthropic API)
└── tools/
    └── deploy.py        # deploy_store (zip export → local filesystem)
```
