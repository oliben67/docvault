# Future imports (must occur at the beginning of the file):
from __future__ import annotations

# Standard library imports:
from collections.abc import AsyncGenerator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

# Third party imports:
from fastapi import APIRouter, FastAPI

from ..config import VaultConfig
from ..core.vault import DocVault
from .auth import build_auth_dep
from .router import create_router


class DocVaultShim:
    """Mount DocVault routes inside an existing FastAPI application.

    The shim owns the :class:`~docvault.core.store.DocVault` store and provides
    three patterns for integrating its lifecycle with the host app.

    **Pattern 1 — lifespan context manager (recommended)**::

        shim = DocVaultShim(config, auth_dep=host_auth_dep)

        @asynccontextmanager
        async def lifespan(app):
            async with shim.lifespan():
                # other host startup/teardown here
                yield

        app = FastAPI(lifespan=lifespan)
        app.include_router(shim.router)

    **Pattern 2 — lifespan composition helper**::

        # Use when you already have a lifespan and want to compose them.
        app = FastAPI(lifespan=shim.wrap_lifespan(existing_lifespan))
        app.include_router(shim.router)

        # Or without an existing lifespan:
        app = FastAPI(lifespan=shim.wrap_lifespan())
        app.include_router(shim.router)

    **Pattern 3 — startup event (legacy)**::

        app.add_event_handler("startup", shim.startup)
        app.include_router(shim.router)

    Authentication
    --------------
    *auth_dep* (recommended for host-app integration)
        Directly inject a FastAPI dependency callable. The returned value is
        passed as ``_p`` to every route handler but is otherwise ignored by
        DocVault — use it purely for side-effects (raising 401/403).

    *passthrough_dep* + ``auth_mode = "passthrough"`` in config
        Same as *auth_dep* but routed through :func:`~docvault.api.auth.build_auth_dep`;
        requires ``config.auth_mode == AuthMode.PASSTHROUGH``.

    When neither is supplied, ``build_auth_dep`` is used with the config's
    ``auth_mode`` (``"none"`` or ``"api_key"``).
    """

    def __init__(
        self,
        config: VaultConfig,
        *,
        auth_dep: Callable[..., Any] | None = None,
        passthrough_dep: Callable[..., Any] | None = None,
        app_name: str = "anonymous",
        prefix: str = "/api/v1",
    ) -> None:
        self.store = DocVault(config)
        _auth = auth_dep or build_auth_dep(config, passthrough_dep, app_name=app_name)
        self._router = create_router(self.store, _auth, prefix=prefix)

    # ── Public surface ─────────────────────────────────────────────────────

    @property
    def router(self) -> APIRouter:
        """The :class:`fastapi.APIRouter` to pass to ``app.include_router()``."""
        return self._router

    async def startup(self) -> None:
        """Initialize (or open) the vault.  Safe to call repeatedly.

        Use with ``app.add_event_handler("startup", shim.startup)`` or call
        directly inside an ``async with`` lifespan block.
        """
        await self.store.init()

    @asynccontextmanager
    async def lifespan(self) -> AsyncGenerator[None, None]:
        """Async context manager that boots the vault on entry.

        Intended for use inside the host app's own lifespan function::

            @asynccontextmanager
            async def lifespan(app):
                async with shim.lifespan():
                    yield
        """
        await self.store.init()
        yield

    def wrap_lifespan(
        self,
        existing_lifespan: Callable[..., Any] | None = None,
    ) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
        """Return a lifespan function that boots DocVault then delegates.

        Pass the result directly as ``lifespan=`` when constructing the host
        :class:`fastapi.FastAPI` instance::

            app = FastAPI(lifespan=shim.wrap_lifespan())

        To compose with the host app's own lifespan, pass it as the argument::

            app = FastAPI(lifespan=shim.wrap_lifespan(my_existing_lifespan))

        The host lifespan is entered *after* the vault is ready, so DocVault
        routes are usable from the very first request.
        """
        store = self.store

        @asynccontextmanager
        async def _combined(app: FastAPI) -> AsyncGenerator[None, None]:
            await store.init()
            if existing_lifespan is not None:
                async with existing_lifespan(app):
                    yield
            else:
                yield

        return _combined
