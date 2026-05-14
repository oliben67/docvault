# Future imports (must occur at the beginning of the file):
from __future__ import annotations

# Standard library imports:
import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

# Third party imports:
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

# Local imports:
from docvault.api.app import create_app
from docvault.config import VaultConfig
from docvault.core.vault import DocVault


@asynccontextmanager
async def asgi_lifespan_client(
    app: FastAPI,
    base_url: str = "http://test",
) -> AsyncGenerator[AsyncClient, None]:
    """Drive the ASGI lifespan protocol then yield an AsyncClient.

    ASGITransport does not send lifespan events automatically, so tests that
    rely on startup logic (e.g. store.init()) must use this helper instead of
    creating AsyncClient directly.
    """
    inbox: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    outbox: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    async def receive() -> dict[str, Any]:
        return await inbox.get()

    async def send(message: dict[str, Any]) -> None:
        await outbox.put(message)

    lifespan_task = asyncio.create_task(
        app({"type": "lifespan", "asgi": {"version": "3.0"}}, receive, send)
    )

    # startup
    await inbox.put({"type": "lifespan.startup"})
    startup_msg = await outbox.get()
    if startup_msg["type"] == "lifespan.startup.failed":
        lifespan_task.cancel()
        raise RuntimeError(f"ASGI startup failed: {startup_msg.get('message', '')}")

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url=base_url
        ) as client:
            yield client
    finally:
        await inbox.put({"type": "lifespan.shutdown"})
        try:
            await asyncio.wait_for(lifespan_task, timeout=5.0)
        except (TimeoutError, asyncio.CancelledError):
            lifespan_task.cancel()


@pytest_asyncio.fixture
async def store(tmp_path):
    cfg = VaultConfig(vault_path=tmp_path / "vault")
    s = DocVault(cfg)
    await s.init()
    return s


@pytest_asyncio.fixture
async def client(tmp_path):
    cfg = VaultConfig(vault_path=tmp_path / "vault")
    s = DocVault(cfg)
    await s.init()
    app = create_app(cfg)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c
