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

`demo.py` is an upsert — it calls `create_template` with the `template-source/`
folder. If the folder contents have not changed since the last run the existing
template is returned unchanged; if the folder was modified the version number
is incremented. The script then calls the server's export endpoint to download
a zip of all slot documents and extracts them into `template-deployment/`.
Output looks like:

```
Template: project:4e0bafd4a90aec2fd17daf34bb63ee48:a4c09516f9b683a2fbd288f3b157a51e
Deployed 4 file(s) to .../template-deployment:
  SubDir1/file1.json
  SubDir1/file2.json
  SubDir2/data.json
  SubDir2/notes.json
```

The template ID format is `{name}:{md5(path)}:{md5(content)}`. The last
segment changes whenever the folder contents change.

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

### Pattern 1 — `lifespan` context manager (active by default in this example)

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

### Pattern 2 — `wrap_lifespan` helper

Simplest option when you have no existing lifespan.

```python
app = FastAPI(lifespan=shim.wrap_lifespan())
app.include_router(shim.router)
```

Compose with an existing lifespan by passing it as an argument:

```python
app = FastAPI(lifespan=shim.wrap_lifespan(my_existing_lifespan))
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
