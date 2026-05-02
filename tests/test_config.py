from __future__ import annotations

import json
from pathlib import Path


from docvault.config import AuthMode, VaultConfig, load_config


# ---------------------------------------------------------------------------
# AuthMode enum
# ---------------------------------------------------------------------------


class TestAuthMode:
    def test_values(self):
        assert AuthMode.NONE == "none"
        assert AuthMode.API_KEY == "api_key"
        assert AuthMode.PASSTHROUGH == "passthrough"

    def test_is_str_enum(self):
        assert isinstance(AuthMode.NONE, str)
        assert isinstance(AuthMode.API_KEY, str)
        assert isinstance(AuthMode.PASSTHROUGH, str)

    def test_membership(self):
        assert (
            "none" in AuthMode.__members__.values() or AuthMode("none") == AuthMode.NONE
        )
        assert AuthMode("api_key") == AuthMode.API_KEY
        assert AuthMode("passthrough") == AuthMode.PASSTHROUGH


# ---------------------------------------------------------------------------
# VaultConfig defaults and field behaviour
# ---------------------------------------------------------------------------


class TestVaultConfigDefaults:
    def test_defaults(self, tmp_path):
        cfg = VaultConfig(vault_path=tmp_path / "vault")
        assert cfg.vault_name == "default"
        assert cfg.vault_description == ""
        assert cfg.auth_mode == AuthMode.NONE
        assert cfg.api_keys == []
        assert cfg.default_creator == "system"
        assert cfg.git_author_name == "docvault"
        assert cfg.git_author_email == "docvault@localhost"
        assert cfg.llm_api_key is None
        assert cfg.llm_model == "claude-haiku-4-5-20251001"
        assert cfg.auto_summarize is False

    def test_vault_path_absolute_passthrough(self, tmp_path):
        cfg = VaultConfig(vault_path=tmp_path)
        assert cfg.vault_path == tmp_path.resolve()

    def test_vault_path_relative_expansion(self):
        cfg = VaultConfig(vault_path="./some/relative/path")
        # Should be resolved to an absolute path
        assert cfg.vault_path.is_absolute()
        assert cfg.vault_path == Path("./some/relative/path").expanduser().resolve()

    def test_vault_path_tilde_expansion(self):
        cfg = VaultConfig(vault_path="~/my-vault")
        assert cfg.vault_path.is_absolute()
        assert not str(cfg.vault_path).startswith("~")
        assert str(cfg.vault_path).endswith("my-vault")

    def test_vault_path_string_input(self, tmp_path):
        cfg = VaultConfig(vault_path=str(tmp_path))
        assert cfg.vault_path == tmp_path.resolve()

    def test_auth_mode_api_key_with_keys(self, tmp_path):
        cfg = VaultConfig(
            vault_path=tmp_path,
            auth_mode=AuthMode.API_KEY,
            api_keys=["key-one", "key-two"],
        )
        assert cfg.auth_mode == AuthMode.API_KEY
        assert cfg.api_keys == ["key-one", "key-two"]

    def test_auth_mode_string_coercion(self, tmp_path):
        cfg = VaultConfig(vault_path=tmp_path, auth_mode="api_key")  # type: ignore[arg-type]
        assert cfg.auth_mode == AuthMode.API_KEY


# ---------------------------------------------------------------------------
# generate_api_key
# ---------------------------------------------------------------------------


class TestGenerateApiKey:
    def test_returns_string(self, tmp_path):
        cfg = VaultConfig(vault_path=tmp_path)
        key = cfg.generate_api_key()
        assert isinstance(key, str)

    def test_length_is_43_chars(self, tmp_path):
        # secrets.token_urlsafe(32) always produces a 43-character string
        cfg = VaultConfig(vault_path=tmp_path)
        key = cfg.generate_api_key()
        assert len(key) == 43

    def test_urlsafe_characters(self, tmp_path):
        cfg = VaultConfig(vault_path=tmp_path)
        key = cfg.generate_api_key()
        # urlsafe base64 uses A-Z, a-z, 0-9, -, _
        import re

        assert re.fullmatch(r"[A-Za-z0-9_\-]+", key), f"Non-urlsafe chars in {key!r}"

    def test_uniqueness(self, tmp_path):
        cfg = VaultConfig(vault_path=tmp_path)
        keys = {cfg.generate_api_key() for _ in range(20)}
        assert len(keys) == 20, "Expected all generated keys to be unique"


# ---------------------------------------------------------------------------
# load_config – no file, defaults
# ---------------------------------------------------------------------------


class TestLoadConfigNoFile:
    def test_no_file_uses_defaults(self, tmp_path, monkeypatch):
        # Run from tmp_path so "docvault.json" does not exist
        monkeypatch.chdir(tmp_path)
        # Clear relevant env vars to avoid interference from the real environment
        for key in (
            "DOCVAULT_PATH",
            "DOCVAULT_VAULT_NAME",
            "DOCVAULT_AUTH_MODE",
            "DOCVAULT_API_KEYS",
            "DOCVAULT_AUTO_SUMMARIZE",
            "DOCVAULT_LLM_MODEL",
            "DOCVAULT_LLM_API_KEY",
            "DOCVAULT_DEFAULT_CREATOR",
            "DOCVAULT_GIT_AUTHOR_NAME",
            "DOCVAULT_GIT_AUTHOR_EMAIL",
        ):
            monkeypatch.delenv(key, raising=False)

        cfg = load_config()
        assert cfg.vault_name == "default"
        assert cfg.auth_mode == AuthMode.NONE
        assert cfg.auto_summarize is False
        assert cfg.vault_path.is_absolute()

    def test_no_file_default_vault_path_resolves(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        for key in (
            "DOCVAULT_PATH",
            "DOCVAULT_VAULT_NAME",
            "DOCVAULT_AUTH_MODE",
            "DOCVAULT_API_KEYS",
            "DOCVAULT_AUTO_SUMMARIZE",
            "DOCVAULT_LLM_MODEL",
        ):
            monkeypatch.delenv(key, raising=False)

        cfg = load_config()
        # Default is "./vault", which should resolve relative to cwd
        assert cfg.vault_path == (tmp_path / "vault").resolve()


# ---------------------------------------------------------------------------
# load_config – from JSON file
# ---------------------------------------------------------------------------


class TestLoadConfigFromFile:
    def test_reads_json_file(self, tmp_path, monkeypatch):
        for key in (
            "DOCVAULT_PATH",
            "DOCVAULT_VAULT_NAME",
            "DOCVAULT_AUTH_MODE",
            "DOCVAULT_API_KEYS",
            "DOCVAULT_AUTO_SUMMARIZE",
            "DOCVAULT_LLM_MODEL",
        ):
            monkeypatch.delenv(key, raising=False)

        config_data = {
            "vault_path": str(tmp_path / "my-vault"),
            "vault_name": "from-file",
            "auth_mode": "api_key",
            "api_keys": ["file-key-1", "file-key-2"],
            "auto_summarize": True,
            "llm_model": "claude-opus-4",
        }
        cfg_file = tmp_path / "docvault.json"
        cfg_file.write_text(json.dumps(config_data), encoding="utf-8")

        cfg = load_config(config_file=cfg_file)
        assert cfg.vault_name == "from-file"
        assert cfg.auth_mode == AuthMode.API_KEY
        assert cfg.api_keys == ["file-key-1", "file-key-2"]
        assert cfg.auto_summarize is True
        assert cfg.llm_model == "claude-opus-4"
        assert cfg.vault_path == (tmp_path / "my-vault").resolve()

    def test_custom_config_file_path(self, tmp_path, monkeypatch):
        for key in (
            "DOCVAULT_PATH",
            "DOCVAULT_VAULT_NAME",
            "DOCVAULT_AUTH_MODE",
            "DOCVAULT_API_KEYS",
            "DOCVAULT_AUTO_SUMMARIZE",
            "DOCVAULT_LLM_MODEL",
        ):
            monkeypatch.delenv(key, raising=False)

        config_data = {
            "vault_path": str(tmp_path / "custom"),
            "vault_name": "custom-name",
        }
        cfg_file = tmp_path / "custom-config.json"
        cfg_file.write_text(json.dumps(config_data), encoding="utf-8")

        cfg = load_config(config_file=cfg_file)
        assert cfg.vault_name == "custom-name"


# ---------------------------------------------------------------------------
# load_config – env var overrides
# ---------------------------------------------------------------------------


class TestLoadConfigEnvVars:
    def _clean_env(self, monkeypatch):
        for key in (
            "DOCVAULT_PATH",
            "DOCVAULT_VAULT_NAME",
            "DOCVAULT_AUTH_MODE",
            "DOCVAULT_API_KEYS",
            "DOCVAULT_AUTO_SUMMARIZE",
            "DOCVAULT_LLM_MODEL",
            "DOCVAULT_LLM_API_KEY",
            "DOCVAULT_DEFAULT_CREATOR",
            "DOCVAULT_GIT_AUTHOR_NAME",
            "DOCVAULT_GIT_AUTHOR_EMAIL",
        ):
            monkeypatch.delenv(key, raising=False)

    def test_docvault_path_env(self, tmp_path, monkeypatch):
        self._clean_env(monkeypatch)
        monkeypatch.setenv("DOCVAULT_PATH", str(tmp_path / "env-vault"))
        cfg = load_config()
        assert cfg.vault_path == (tmp_path / "env-vault").resolve()

    def test_docvault_vault_name_env(self, tmp_path, monkeypatch):
        self._clean_env(monkeypatch)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("DOCVAULT_VAULT_NAME", "env-vault-name")
        cfg = load_config()
        assert cfg.vault_name == "env-vault-name"

    def test_docvault_auth_mode_env(self, tmp_path, monkeypatch):
        self._clean_env(monkeypatch)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("DOCVAULT_AUTH_MODE", "api_key")
        cfg = load_config()
        assert cfg.auth_mode == AuthMode.API_KEY

    def test_docvault_api_keys_comma_separated(self, tmp_path, monkeypatch):
        self._clean_env(monkeypatch)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("DOCVAULT_API_KEYS", "key-a, key-b , key-c")
        cfg = load_config()
        assert cfg.api_keys == ["key-a", "key-b", "key-c"]

    def test_docvault_auto_summarize_true_values(self, tmp_path, monkeypatch):
        self._clean_env(monkeypatch)
        monkeypatch.chdir(tmp_path)
        for truthy in ("1", "true", "yes", "True", "YES"):
            monkeypatch.setenv("DOCVAULT_AUTO_SUMMARIZE", truthy)
            cfg = load_config()
            assert cfg.auto_summarize is True, f"Expected True for {truthy!r}"

    def test_docvault_auto_summarize_false_values(self, tmp_path, monkeypatch):
        self._clean_env(monkeypatch)
        monkeypatch.chdir(tmp_path)
        for falsy in ("0", "false", "no", "False", "NO"):
            monkeypatch.setenv("DOCVAULT_AUTO_SUMMARIZE", falsy)
            cfg = load_config()
            assert cfg.auto_summarize is False, f"Expected False for {falsy!r}"

    def test_docvault_llm_model_env(self, tmp_path, monkeypatch):
        self._clean_env(monkeypatch)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("DOCVAULT_LLM_MODEL", "claude-opus-4")
        cfg = load_config()
        assert cfg.llm_model == "claude-opus-4"

    def test_env_vars_win_over_file_values(self, tmp_path, monkeypatch):
        self._clean_env(monkeypatch)
        config_data = {
            "vault_path": str(tmp_path / "file-vault"),
            "vault_name": "from-file",
            "llm_model": "model-from-file",
        }
        cfg_file = tmp_path / "docvault.json"
        cfg_file.write_text(json.dumps(config_data), encoding="utf-8")

        monkeypatch.setenv("DOCVAULT_VAULT_NAME", "from-env")
        monkeypatch.setenv("DOCVAULT_LLM_MODEL", "model-from-env")

        cfg = load_config(config_file=cfg_file)
        assert cfg.vault_name == "from-env"
        assert cfg.llm_model == "model-from-env"

    def test_api_keys_env_wins_over_file(self, tmp_path, monkeypatch):
        self._clean_env(monkeypatch)
        config_data = {
            "vault_path": str(tmp_path / "v"),
            "api_keys": ["file-key"],
        }
        cfg_file = tmp_path / "docvault.json"
        cfg_file.write_text(json.dumps(config_data), encoding="utf-8")

        monkeypatch.setenv("DOCVAULT_API_KEYS", "env-key-1,env-key-2")
        cfg = load_config(config_file=cfg_file)
        assert cfg.api_keys == ["env-key-1", "env-key-2"]
