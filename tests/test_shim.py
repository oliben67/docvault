# Future imports (must occur at the beginning of the file):
from __future__ import annotations

# Standard library imports:
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

# Third party imports:
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

# Local imports:
from docvault.api import DocVaultShim
from docvault.config import VaultConfig
from tests.conftest import asgi_lifespan_client


@pytest.fixture
def config(tmp_path):
    return VaultConfig(vault_path=tmp_path / "vault")


# ── Pattern 1: lifespan context manager ───────────────────────────────────────


async def test_pattern_lifespan_ctx(config: VaultConfig):
    shim = DocVaultShim(config)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        async with shim.lifespan():
            yield

    app = FastAPI(lifespan=lifespan)
    app.include_router(shim.router)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/v1/health")
    assert resp.status_code == 200


# ── Pattern 2: wrap_lifespan ───────────────────────────────────────────────────


async def test_pattern_wrap_lifespan_no_existing(config: VaultConfig):
    shim = DocVaultShim(config)
    app = FastAPI(lifespan=shim.wrap_lifespan())
    app.include_router(shim.router)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/v1/health")
    assert resp.status_code == 200


async def test_pattern_wrap_lifespan_with_existing(config: VaultConfig):
    host_started: list[bool] = []

    @asynccontextmanager
    async def host_lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        host_started.append(True)
        yield

    shim = DocVaultShim(config)
    app = FastAPI(lifespan=shim.wrap_lifespan(host_lifespan))
    app.include_router(shim.router)

    async with asgi_lifespan_client(app) as client:
        resp = await client.get("/api/v1/health")
    assert resp.status_code == 200
    assert host_started == [True], "host lifespan must be entered"


# ── Pattern 3: startup() called directly ──────────────────────────────────────


async def test_pattern_startup_direct(config: VaultConfig):
    """startup() can be awaited directly to initialise the vault."""
    shim = DocVaultShim(config)
    app = FastAPI()
    app.include_router(shim.router)

    await shim.startup()  # caller-managed init (no lifespan wiring needed)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/v1/health")
    assert resp.status_code == 200


# ── Custom auth_dep injection ──────────────────────────────────────────────────


async def test_custom_auth_dep_blocks_request(config: VaultConfig):
    # Third party imports:
    from fastapi import HTTPException

    async def deny_all() -> dict[str, Any]:
        raise HTTPException(status_code=403, detail="forbidden")

    shim = DocVaultShim(config, auth_dep=deny_all)
    app = FastAPI(lifespan=shim.wrap_lifespan())
    app.include_router(shim.router)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/v1/docs")
    assert resp.status_code == 403


async def test_custom_auth_dep_allows_request(config: VaultConfig):
    async def allow_all() -> dict[str, Any]:
        return {"user": "test-host-user"}

    shim = DocVaultShim(config, auth_dep=allow_all)
    app = FastAPI(lifespan=shim.wrap_lifespan())
    app.include_router(shim.router)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/v1/docs")
    assert resp.status_code == 200


# ── Custom prefix ──────────────────────────────────────────────────────────────


async def test_custom_prefix(config: VaultConfig):
    shim = DocVaultShim(config, prefix="/v2/store")
    app = FastAPI(lifespan=shim.wrap_lifespan())
    app.include_router(shim.router)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (await client.get("/v2/store/health")).status_code == 200
        assert (await client.get("/api/v1/health")).status_code == 404


# ── app_name as default creator ────────────────────────────────────────────────


async def test_app_name_used_as_creator_when_none_provided(config: VaultConfig):
    shim = DocVaultShim(config, app_name="my-service")
    app = FastAPI(lifespan=shim.wrap_lifespan())
    app.include_router(shim.router)

    async with asgi_lifespan_client(app) as client:
        resp = await client.post(
            "/api/v1/docs",
            json={"content": {"key": "value"}},  # no creator field
        )
    assert resp.status_code == 201
    assert resp.json()["meta"]["creator"] == "my-service"


async def test_explicit_creator_overrides_app_name(config: VaultConfig):
    shim = DocVaultShim(config, app_name="my-service")
    app = FastAPI(lifespan=shim.wrap_lifespan())
    app.include_router(shim.router)

    async with asgi_lifespan_client(app) as client:
        resp = await client.post(
            "/api/v1/docs",
            json={"content": {"key": "value"}, "creator": "alice"},
        )
    assert resp.status_code == 201
    assert resp.json()["meta"]["creator"] == "alice"


# ── Routes work end-to-end ─────────────────────────────────────────────────────


async def test_docs_roundtrip_via_shim(config: VaultConfig):
    shim = DocVaultShim(config)
    app = FastAPI(lifespan=shim.wrap_lifespan())
    app.include_router(shim.router)

    async with asgi_lifespan_client(app) as client:
        resp = await client.post(
            "/api/v1/docs", json={"content": {"hello": "world"}, "creator": "bob"}
        )
        assert resp.status_code == 201
        doc_id = resp.json()["meta"]["id"]

        resp = await client.get(f"/api/v1/docs/{doc_id}")
        assert resp.status_code == 200
        assert resp.json()["content"]["hello"] == "world"
