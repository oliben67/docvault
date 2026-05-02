from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class DocumentMeta(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    creator: str
    summary: str = ""
    keywords: list[str] = Field(default_factory=list)
    size_bytes: int = 0
    template: str | None = None
    path: str | None = None
    named_version: str | None = None


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
                "template": "microservice",
                "path": "config/app",
                "named_version": None,
                "commit_message": None,
            }
        }
    )

    content: dict[str, Any]
    creator: str | None = None
    summary: str = ""
    keywords: list[str] = Field(default_factory=list)
    template: str | None = None
    path: str | None = None
    named_version: str | None = None
    commit_message: str | None = None


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

    content: dict[str, Any]
    summary: str | None = None
    keywords: list[str] | None = None
    named_version: str | None = None
    commit_message: str | None = None
