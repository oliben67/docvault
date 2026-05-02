# Shim Integration Example

Demonstrates embedding DocVault inside your own FastAPI application using
`DocVaultShim`. The shim owns the vault store and its lifecycle, and exposes
an `APIRouter` you mount alongside your own routes.

## Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (or `pip`)
- `docvault` installed in your environment

```bash
# From the repo root
pip install -e .
# or with uv
uv pip install -e .
```

## Running the example

**Terminal 1 — start the server:**

```bash
cd examples/shim-integration
uv run uvicorn main:app --reload --port 54321
```

The server starts on `http://localhost:54321`. A `.demo-vault/` directory is
created automatically on first startup — this is the git-backed store. The
`template-source/` folder is ingested as a template named `project` during
startup.

**Terminal 2 — run the demo script:**

```bash
cd examples/shim-integration
uv run python demo.py
```

`demo.py` connects to the vault, retrieves the `project` template ID, calls
the server's export endpoint to download a zip of all template documents, and
extracts them into `template-deployment/`. Output looks like:

```
Template: project/v1.0.0/<hash>
Deployed 4 file(s) to .../template-deployment:
  Project/SubDir1/file1.json
  Project/SubDir1/file2.json
  Project/SubDir2/data.json
  Project/SubDir2/notes.json
```

## Endpoints

| URL | Description |
|-----|-------------|
| `GET http://localhost:54321/` | Your app's own hello-world route |
| `GET http://localhost:54321/vault-info` | Reads vault metadata from the store |
| `GET http://localhost:54321/api/v1/health` | DocVault liveness probe |
| `GET http://localhost:54321/api/v1/documents` | List all documents |
| `GET http://localhost:54321/docs` | Swagger UI — all routes in one place |
| `GET http://localhost:54321/openapi.json` | OpenAPI spec |

## Lifecycle patterns

`main.py` shows three ways to wire DocVault's lifecycle into your app.
Switch between them by commenting/uncommenting the relevant block.

### Pattern 1 — `wrap_lifespan` (active by default)

Simplest option. Pass the result directly to `FastAPI(lifespan=...)`.
DocVault boots before the first request is served.

```python
app = FastAPI(lifespan=shim.wrap_lifespan())
app.include_router(shim.router)
```

To compose with an existing lifespan, pass it as an argument:

```python
app = FastAPI(lifespan=shim.wrap_lifespan(my_existing_lifespan))
```

### Pattern 2 — `lifespan` context manager (recommended for real apps)

Full control over startup/shutdown ordering. Use when you need to initialise
other resources (databases, caches, …) alongside DocVault.

```python
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    async with shim.lifespan():
        # DocVault is ready here. Run your own startup.
        yield
        # Your own shutdown runs here.

app = FastAPI(lifespan=lifespan)
app.include_router(shim.router)
```

### Pattern 3 — `startup` event (legacy)

For frameworks or DI containers that use `add_event_handler`.

```python
app = FastAPI()
app.add_event_handler("startup", shim.startup)
app.include_router(shim.router)
```

## Authentication

By default the example runs with `auth_mode = "none"` (no authentication).

To require an API key, update the config and pass a dependency:

```python
from docvault.config import AuthMode, VaultConfig
from docvault.api import DocVaultShim

config = VaultConfig(vault_path=VAULT_DIR, auth_mode=AuthMode.API_KEY)
shim = DocVaultShim(config)
```

Or inject your host app's existing auth dependency directly:

```python
shim = DocVaultShim(config, auth_dep=require_logged_in_user)
```

## Accessing the store directly

`shim.store` is the underlying `DocVault` instance. You can call it from your
own route handlers after the app has started:

```python
@app.get("/vault-info")
async def vault_info():
    vault = await shim.store.get_vault()
    return {"vault_name": vault.name, "version": str(vault.version)}
```

## Cleanup

The `.demo-vault/` directory is a plain git repository. Delete it to start
fresh:

```bash
rm -rf examples/shim-integration/.demo-vault
```
