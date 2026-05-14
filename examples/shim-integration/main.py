"""Hello-world FastAPI app that demonstrates DocVault shim integration.

This example shows all three lifecycle patterns for embedding DocVault
inside your own FastAPI application via `DocVaultShim`.

Run it:
    uv run uvicorn main:app --reload --port 54321

Then visit:
    http://localhost:54321           — your app's hello-world route
    http://localhost:54321/docs      — Swagger UI (DocVault endpoints + your routes)
    http://localhost:54321/api/v1/health — DocVault health check

Run demo.py (while the server is up) to deploy the store to template-deployment.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from docvault.api import DocVaultShim
from docvault.config import VaultConfig
from docvault.core.store import StoreCreateInput
from docvault.exceptions import StoreNotFoundError

# ── 1. Configure the vault ───────────────────────────────────────────────────
VAULT_DIR = Path(__file__).parent / ".demo-vault"
STORE_SOURCE = Path(__file__).parent / "template-source"
STORE_NAME = "project"

config = VaultConfig(
    vault_path=VAULT_DIR,
    vault_name="hello-world-vault",
    vault_description="Demo vault for the shim-integration example",
)

# ── 2. Create the shim ───────────────────────────────────────────────────────
shim = DocVaultShim(config, app_name="shim-integration-demo")


# ── 3. Lifespan: boot DocVault then load the store-source folder ─────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    async with shim.lifespan():
        try:
            store_obj = await shim.store.get_store(STORE_NAME)
            meta = await store_obj.get_meta()
            app.state.store_id = meta.id
        except StoreNotFoundError:
            store_obj = await shim.store.create_store(
                StoreCreateInput(
                    name=STORE_NAME,
                    description="Loaded from template-source folder",
                    path=STORE_SOURCE,
                ),
                creator="demo",
            )
            meta = await store_obj.get_meta()
            app.state.store_id = meta.id
        yield


# ── 4. Build your app ────────────────────────────────────────────────────────
app = FastAPI(
    title="Shim Integration Example",
    lifespan=lifespan,
)

app.include_router(shim.router)


# ── 5. Your own routes ───────────────────────────────────────────────────────
@app.get("/")
async def hello_world():
    """Your app's own root endpoint — completely independent of DocVault."""
    return {
        "message": "Hello, world!",
        "docvault": {
            "prefix": "/api/v1",
            "health": "http://localhost:54321/api/v1/health",
            "docs": "http://localhost:54321/docs",
        },
    }


@app.get("/vault-info")
async def vault_info():
    """Reads vault metadata from the store."""
    vault = await shim.store.get_vault()
    return {
        "vault_name": vault.name,
        "version": str(vault.version) if vault.version else None,
        "description": vault.description,
        "vault_path": str(VAULT_DIR),
    }
