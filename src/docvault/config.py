from __future__ import annotations

import json
import os
import secrets
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, field_validator


class AuthMode(StrEnum):
    NONE = "none"
    API_KEY = "api_key"
    PASSTHROUGH = "passthrough"


class VaultConfig(BaseModel):
    vault_path: Path
    vault_name: str = "default"
    vault_description: str = ""
    auth_mode: AuthMode = AuthMode.NONE
    api_keys: list[str] = []
    default_creator: str = "system"
    git_author_name: str = "docvault"
    git_author_email: str = "docvault@localhost"
    # LLM summarization
    llm_api_key: str | None = None
    llm_model: str = "claude-haiku-4-5-20251001"
    auto_summarize: bool = False

    @field_validator("vault_path", mode="before")
    @classmethod
    def expand_path(cls, v: object) -> Path:
        return Path(str(v)).expanduser().resolve()

    def generate_api_key(self) -> str:
        return secrets.token_urlsafe(32)


def load_config(config_file: Path | None = None) -> VaultConfig:
    data: dict = {}

    cfg_path = config_file or Path("docvault.json")
    if cfg_path.exists():
        data = json.loads(cfg_path.read_text("utf-8"))

    env_map = {
        "DOCVAULT_PATH": "vault_path",
        "DOCVAULT_VAULT_NAME": "vault_name",
        "DOCVAULT_AUTH_MODE": "auth_mode",
        "DOCVAULT_DEFAULT_CREATOR": "default_creator",
        "DOCVAULT_GIT_AUTHOR_NAME": "git_author_name",
        "DOCVAULT_GIT_AUTHOR_EMAIL": "git_author_email",
        "DOCVAULT_LLM_API_KEY": "llm_api_key",
        "DOCVAULT_LLM_MODEL": "llm_model",
    }
    for env_key, field in env_map.items():
        if val := os.getenv(env_key):
            data[field] = val

    if raw_keys := os.getenv("DOCVAULT_API_KEYS"):
        data["api_keys"] = [k.strip() for k in raw_keys.split(",") if k.strip()]

    if raw_auto := os.getenv("DOCVAULT_AUTO_SUMMARIZE"):
        data["auto_summarize"] = raw_auto.lower() in ("1", "true", "yes")

    data.setdefault("vault_path", "./vault")
    return VaultConfig(**data)
