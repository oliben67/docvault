"""
Auth modes example.

Shows how to embed DocVault inside a FastAPI app and configure three
different authentication strategies:

  1. NONE       – every request is anonymous (dev/internal use)
  2. API_KEY    – caller must supply X-API-Key header
  3. PASSTHROUGH – the host app supplies its own auth dependency

Run:
    uv run python examples/auth-modes/main.py
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import uvicorn
from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from docvault.api.app import create_app
from docvault.config import AuthMode, VaultConfig


# ── 1. Anonymous / NONE mode ──────────────────────────────────────────────────

def build_none_app(vault_path: Path) -> FastAPI:
    cfg = VaultConfig(vault_path=vault_path, auth_mode=AuthMode.NONE)
    return create_app(cfg)


# ── 2. API key mode ───────────────────────────────────────────────────────────

def build_api_key_app(vault_path: Path) -> FastAPI:
    cfg = VaultConfig(
        vault_path=vault_path,
        auth_mode=AuthMode.API_KEY,
        api_keys=["sk-dev-demo-key-1234", "sk-ci-bot-key-5678"],
    )
    return create_app(cfg)


# ── 3. Passthrough mode – host app owns auth ──────────────────────────────────

_bearer = HTTPBearer(auto_error=False)

KNOWN_TOKENS = {"token-alice": "alice", "token-bob": "bob"}


async def _jwt_auth(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> dict:
    """Minimal stand-in for a real JWT/OAuth2 validator."""
    if creds is None or creds.credentials not in KNOWN_TOKENS:
        raise HTTPException(status_code=401, detail="Invalid or missing Bearer token")
    return {"user": KNOWN_TOKENS[creds.credentials], "auth_scheme": "bearer"}


def build_passthrough_app(vault_path: Path) -> FastAPI:
    cfg = VaultConfig(vault_path=vault_path, auth_mode=AuthMode.PASSTHROUGH)
    return create_app(cfg, passthrough_dep=_jwt_auth)


# ── Demo: exercise each app with httpx ───────────────────────────────────────

async def demo_none(app: FastAPI) -> None:
    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/api/v1/health")
        print(f"[NONE]   health → {r.status_code}  {r.json()}")
        r = await c.post("/api/v1/docs", json={"content": {"x": 1}, "creator": "anon"})
        print(f"[NONE]   create → {r.status_code}  id={r.json()['meta']['id'][:12]}")


async def demo_api_key(app: FastAPI) -> None:
    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        # No key → 401
        r = await c.get("/api/v1/docs")
        print(f"[API_KEY] no key  → {r.status_code}")

        # Wrong key → 401
        r = await c.get("/api/v1/docs", headers={"X-API-Key": "wrong"})
        print(f"[API_KEY] bad key → {r.status_code}")

        # Valid key → 200
        r = await c.get("/api/v1/docs", headers={"X-API-Key": "sk-dev-demo-key-1234"})
        print(f"[API_KEY] ok key  → {r.status_code}  docs={r.json()}")


async def demo_passthrough(app: FastAPI) -> None:
    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        # No token → 401
        r = await c.get("/api/v1/docs")
        print(f"[PASS]   no token  → {r.status_code}")

        # Bad token → 401
        r = await c.get("/api/v1/docs", headers={"Authorization": "Bearer bad-token"})
        print(f"[PASS]   bad token → {r.status_code}")

        # Valid token → 200
        r = await c.get("/api/v1/docs", headers={"Authorization": "Bearer token-alice"})
        print(f"[PASS]   alice     → {r.status_code}  docs={r.json()}")


async def main() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)

        none_app = build_none_app(root / "vault-none")
        api_key_app = build_api_key_app(root / "vault-apikey")
        passthrough_app = build_passthrough_app(root / "vault-pass")

        # Use lifespan context to initialise each app
        from contextlib import AsyncExitStack
        from httpx import ASGITransport, AsyncClient

        async with AsyncExitStack() as stack:
            await stack.enter_async_context(none_app.router.lifespan_context(none_app))
            await stack.enter_async_context(api_key_app.router.lifespan_context(api_key_app))
            await stack.enter_async_context(passthrough_app.router.lifespan_context(passthrough_app))

            await demo_none(none_app)
            await demo_api_key(api_key_app)
            await demo_passthrough(passthrough_app)


if __name__ == "__main__":
    asyncio.run(main())
