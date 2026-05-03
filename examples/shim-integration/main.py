"""Hello-world FastAPI app that demonstrates DocVault shim integration.

This example shows all three lifecycle patterns for embedding DocVault
inside your own FastAPI application via `DocVaultShim`.

Run it:
    uv run uvicorn main:app --reload --port 54321

Then visit:
    http://localhost:54321           — your app's hello-world route
    http://localhost:54321/docs      — Swagger UI (DocVault endpoints + your routes)
    http://localhost:54321/api/v1/health — DocVault health check

Run demo.py (while the server is up) to deploy the template to template-deployment.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from docvault.api import DocVaultShim
from docvault.config import VaultConfig
from docvault.core.template import TemplateCreateInput
from docvault.exceptions import TemplateNotFoundError

# ── 1. Configure the vault ───────────────────────────────────────────────────
VAULT_DIR = Path(__file__).parent / ".demo-vault"
TEMPLATE_SOURCE = Path(__file__).parent / "template-source"
TEMPLATE_NAME = "project"

config = VaultConfig(
    vault_path=VAULT_DIR,
    vault_name="hello-world-vault",
    vault_description="Demo vault for the shim-integration example",
)

# ── 2. Create the shim ───────────────────────────────────────────────────────
shim = DocVaultShim(config, app_name="shim-integration-demo")


# ── 3. Lifespan: boot DocVault then load the template-source folder ──────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    async with shim.lifespan():
        try:
            existing = await shim.store._get_template_by_name(TEMPLATE_NAME)
            app.state.template_id = existing.id
        except TemplateNotFoundError:
            result = await shim.store.create_template(
                TemplateCreateInput(
                    name=TEMPLATE_NAME,
                    description="Loaded from template-source folder",
                    path=TEMPLATE_SOURCE,
                ),
                creator="demo",
            )
            app.state.template_id = result.id
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


