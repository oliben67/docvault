from __future__ import annotations

import pytest

from docvault.api.auth import build_auth_dep
from docvault.config import AuthMode, VaultConfig


def _cfg(tmp_path, **kwargs):
    return VaultConfig(vault_path=tmp_path / "v", **kwargs)


class TestAuthModeNone:
    async def test_returns_callable(self, tmp_path):
        dep = build_auth_dep(_cfg(tmp_path))
        assert callable(dep)

    async def test_returns_default_app_name(self, tmp_path):
        dep = build_auth_dep(_cfg(tmp_path))
        result = await dep()
        assert result == {"user": "anonymous"}

    async def test_custom_app_name(self, tmp_path):
        dep = build_auth_dep(_cfg(tmp_path), app_name="my-service")
        result = await dep()
        assert result == {"user": "my-service"}

    async def test_app_name_empty_string(self, tmp_path):
        dep = build_auth_dep(_cfg(tmp_path), app_name="")
        result = await dep()
        assert result == {"user": ""}


class TestAuthModeApiKey:
    def _api_cfg(self, tmp_path, keys):
        return _cfg(tmp_path, auth_mode=AuthMode.API_KEY, api_keys=keys)

    async def test_valid_key_returns_user_dict(self, tmp_path):
        dep = build_auth_dep(self._api_cfg(tmp_path, ["sk-12345678abcdef"]))
        result = await dep(api_key="sk-12345678abcdef")
        assert "user" in result
        assert result["user"].endswith("***")

    async def test_valid_key_user_contains_prefix(self, tmp_path):
        dep = build_auth_dep(self._api_cfg(tmp_path, ["sk-12345678abcdef"]))
        result = await dep(api_key="sk-12345678abcdef")
        assert result["user"].startswith("sk-12345")

    async def test_invalid_key_raises_401(self, tmp_path):
        from fastapi import HTTPException

        dep = build_auth_dep(self._api_cfg(tmp_path, ["valid-key"]))
        with pytest.raises(HTTPException) as exc_info:
            await dep(api_key="wrong-key")
        assert exc_info.value.status_code == 401

    async def test_missing_key_raises_401(self, tmp_path):
        from fastapi import HTTPException

        dep = build_auth_dep(self._api_cfg(tmp_path, ["valid-key"]))
        with pytest.raises(HTTPException) as exc_info:
            await dep(api_key=None)
        assert exc_info.value.status_code == 401

    async def test_empty_string_key_raises_401(self, tmp_path):
        from fastapi import HTTPException

        dep = build_auth_dep(self._api_cfg(tmp_path, ["valid-key"]))
        with pytest.raises(HTTPException) as exc_info:
            await dep(api_key="")
        assert exc_info.value.status_code == 401

    async def test_multiple_valid_keys(self, tmp_path):
        keys = ["key-alpha", "key-beta", "key-gamma"]
        dep = build_auth_dep(self._api_cfg(tmp_path, keys))
        for k in keys:
            result = await dep(api_key=k)
            assert "user" in result

    async def test_error_detail_message(self, tmp_path):
        from fastapi import HTTPException

        dep = build_auth_dep(self._api_cfg(tmp_path, ["valid-key"]))
        with pytest.raises(HTTPException) as exc_info:
            await dep(api_key="bad")
        assert "API key" in exc_info.value.detail

    async def test_www_authenticate_header(self, tmp_path):
        from fastapi import HTTPException

        dep = build_auth_dep(self._api_cfg(tmp_path, ["k"]))
        with pytest.raises(HTTPException) as exc_info:
            await dep(api_key=None)
        assert "WWW-Authenticate" in exc_info.value.headers


class TestAuthModePassthrough:
    def _passthrough_cfg(self, tmp_path):
        return _cfg(tmp_path, auth_mode=AuthMode.PASSTHROUGH)

    def test_returns_passthrough_dep_directly(self, tmp_path):
        async def my_dep():
            return {"user": "from-passthrough"}

        dep = build_auth_dep(self._passthrough_cfg(tmp_path), passthrough_dep=my_dep)
        assert dep is my_dep

    def test_none_passthrough_dep_raises_value_error(self, tmp_path):
        with pytest.raises(ValueError, match="passthrough_dep"):
            build_auth_dep(self._passthrough_cfg(tmp_path), passthrough_dep=None)

    async def test_passthrough_dep_callable_works(self, tmp_path):
        async def my_dep():
            return {"user": "alice", "role": "admin"}

        dep = build_auth_dep(self._passthrough_cfg(tmp_path), passthrough_dep=my_dep)
        result = await dep()
        assert result == {"user": "alice", "role": "admin"}
