# DocVault

DocVault is a git-backed document store for structured data. Every write is a git commit; every document carries a full, auditable revision history; any document can be retrieved exactly as it was at any point in time.

It ships as a standalone REST API, an embeddable FastAPI shim, and a CLI — fitting equally as an independent microservice or as a library embedded inside your existing application.

Documents can be plain JSON or any text-based file. Templates define named folder structures with optional JSON Schema validation per slot. A template can be bootstrapped from a local directory, exported as a zip archive, and deployed to any target path — preserving original file names and extensions. The vault carries a semantic version that can be bumped to create a permanent git-tag snapshot of the entire collection. An optional LLM integration (Claude) auto-generates summaries and keywords from document content.

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
- [Configuration](#configuration)
- [CLI reference](#cli-reference)
- [LLM summarization](#llm-summarization)
- [API reference](#api-reference)
- [Development](#development)

---

## Features

- **Git-backed storage** — every document write (create, update, delete) produces a git commit. Full history with author, timestamp, and message.
- **Point-in-time retrieval** — fetch any document at any commit SHA, tag, or branch.
- **JSON Schema templates** — register named schemas; documents are validated and default-filled on creation/update.
- **Vault versioning** — bump the vault's semantic version and create a git tag snapshot.
- **Batch deploy** — create many documents from a single template in one atomic commit.
- **LLM summarization** — auto-generate `summary` and `keywords` from document content using Claude.
- **Flexible authentication** — none (dev), static API keys, or passthrough to your own auth system.
- **Embeddable** — mount DocVault inside any existing FastAPI app via `DocVaultShim` without conflicts.
- **Interactive Swagger UI** — browse and test all endpoints at `/docs` when the server is running.

---

## Installation

```bash
# Core (REST API + CLI)
pip install docvault
# or
uv add docvault

# With LLM summarization
pip install "docvault[llm]"
uv add "docvault[llm]"
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

Use this when your framework manages dependencies through a DI container, service locator, or explicit `on_startup` hook (e.g. a background worker, Celery beat, or a plain asyncio script).

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
docvault docs list [--template NAME] [--creator NAME] [--keywords KW1,KW2]
docvault docs get <DOC_ID>
docvault docs create --creator alice --file content.json [--template NAME] [--summary TEXT] [--keywords KW1,KW2]
docvault docs create --creator alice --file -           # read JSON from stdin
docvault docs update <DOC_ID> --file updated.json [--summary TEXT] [--keywords KW]
docvault docs delete <DOC_ID> [--force]
docvault docs history <DOC_ID> [--max 20]
docvault docs at <DOC_ID> <REF>                         # git SHA, tag, or branch
docvault docs summarize <DOC_ID> [--overwrite]
docvault docs summarize-all [--overwrite]
```

---

### `docvault templates`

```bash
docvault templates list
docvault templates get <NAME>
docvault templates create <NAME> --file schema.json [--description TEXT]
docvault templates delete <NAME> [--force]
```

---

### `docvault vault`

```bash
docvault vault info
docvault vault versions
docvault vault bump [major|minor|patch]     # default: patch
docvault vault deploy --template NAME --file specs.json
```

`specs.json` is a JSON array of `DeployDocSpec` objects:

```json
[
  { "content": { "name": "Alice" }, "creator": "hr-bot", "keywords": ["engineering"] },
  { "content": { "name": "Bob" },  "creator": "hr-bot", "keywords": ["design"] }
]
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

Full endpoint reference: **[docs/api.md](docs/api.md)**

When the server is running, the interactive Swagger UI is at:

```
http://localhost:8000/docs
```

ReDoc is at `/redoc`. The raw OpenAPI spec is at `/openapi.json`.

To export the spec without a running server:

```bash
task openapi           # writes docs/openapi.json
```

---

## Development

### Setup

```bash
git clone https://github.com/your-org/docvault
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

The test suite uses `pytest-asyncio` in `auto` mode. All test functions that are `async def` run in their own event loop.

### Project layout

```
src/docvault/
├── __init__.py          # public API: DocVault, VaultConfig, load_config
├── config.py            # VaultConfig, load_config, AuthMode
├── exceptions.py        # DocVaultError hierarchy
├── api/
│   ├── __init__.py      # exports DocVaultShim
│   ├── app.py           # create_app (standalone FastAPI factory)
│   ├── auth.py          # build_auth_dep
│   ├── router.py        # create_router (all HTTP endpoints)
│   └── shim.py          # DocVaultShim (host-app integration)
└── core/
    ├── document.py      # Document, DocumentMeta, CreateDocInput, UpdateDocInput
    ├── vault_meta.py    # VaultMeta, VaultVersion
    ├── git_backend.py   # GitBackend (asyncio.to_thread wrapper)
    ├── store.py         # DocVault (main async store)
    ├── summarizer.py    # DocumentSummarizer (Anthropic API)
    ├── tools/
    │   └── deploy.py    # deploy_template (zip export → local filesystem)
    └── template.py      # Template, TemplateCreateInput, DeployVaultInput
```
