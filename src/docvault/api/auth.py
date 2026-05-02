from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader

from ..config import AuthMode, VaultConfig

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def build_auth_dep(
    config: VaultConfig,
    passthrough_dep: Callable | None = None,
    app_name: str = "anonymous",
) -> Callable:
    if config.auth_mode == AuthMode.NONE:

        async def no_auth() -> dict[str, Any]:
            return {"user": app_name}

        return no_auth

    if config.auth_mode == AuthMode.API_KEY:
        valid_keys = frozenset(config.api_keys)

        async def api_key_auth(
            api_key: str | None = Security(_api_key_header),
        ) -> dict[str, Any]:
            if not api_key or api_key not in valid_keys:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid or missing API key",
                    headers={"WWW-Authenticate": "ApiKey"},
                )
            return {"user": f"{api_key[:8]}***"}

        return api_key_auth

    if config.auth_mode == AuthMode.PASSTHROUGH:
        if passthrough_dep is None:
            raise ValueError(
                "A passthrough_dep callable is required when auth_mode is PASSTHROUGH. "
                "If the host app has no auth yet, set auth_mode=NONE."
            )
        return passthrough_dep

    raise ValueError(f"Unknown auth mode: {config.auth_mode}")
