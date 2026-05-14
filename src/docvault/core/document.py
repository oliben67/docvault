from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DocumentMeta(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    creator: str
    summary: str = ""
    keywords: list[str] = Field(default_factory=list)
    size_bytes: int = 0
    path: str | None = None
    named_version: str | None = None
    is_binary: bool = False
    mime_type: str | None = None


class Document(BaseModel):
    meta: DocumentMeta
    content: dict[str, Any]


class CommitInfo(BaseModel):
    sha: str
    message: str
    author: str
    timestamp: datetime


class CreateDocInput(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "content": {"host": "api.example.com", "port": 8080},
                "creator": "alice",
                "summary": "Application configuration for the API service",
                "keywords": ["config", "api"],
                "path": "config/app",
                "named_version": None,
                "commit_message": None,
            }
        }
    )

    content: dict[str, Any] = Field(default_factory=dict)
    binary_content: bytes | None = None
    mime_type: str | None = None
    creator: str | None = None
    summary: str = ""
    keywords: list[str] = Field(default_factory=list)
    path: str | None = None
    named_version: str | None = None
    commit_message: str | None = None

    @model_validator(mode="after")
    def _check_binary(self) -> "CreateDocInput":
        if self.binary_content is not None:
            if self.content:
                raise ValueError("'content' and 'binary_content' are mutually exclusive")
            if self.mime_type is None:
                raise ValueError("'mime_type' is required when 'binary_content' is set")
        return self


class UpdateDocInput(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "content": {"host": "api.example.com", "port": 9090},
                "summary": "Updated port to 9090",
                "keywords": ["config", "api", "updated"],
                "named_version": "v2",
                "commit_message": None,
            }
        }
    )

    content: dict[str, Any] | None = None
    binary_content: bytes | None = None
    mime_type: str | None = None
    summary: str | None = None
    keywords: list[str] | None = None
    named_version: str | None = None
    commit_message: str | None = None

    @model_validator(mode="after")
    def _check_binary(self) -> "UpdateDocInput":
        if self.binary_content is not None and self.content is not None:
            raise ValueError("'content' and 'binary_content' are mutually exclusive")
        return self
