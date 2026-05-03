from __future__ import annotations

import glob as _glob
import hashlib
import json as _json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import jsonschema
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..exceptions import TemplateValidationError


def create_id(path: str | Path) -> str:
    """Hash a file or folder tree into a content-addressable ID string.

    Returns ``{name}:{md5(path)}:{md5(content)}``.
    """
    path = str(Path(path).absolute())

    def _create_file_id(file_path: str) -> str:
        with open(file_path, "rb") as f:
            content = f.read()
        return (
            f"{Path(file_path).name}"
            f":{hashlib.md5(file_path.encode('utf-8')).hexdigest()}"
            f":{hashlib.md5(content).hexdigest()}"
        )

    if not os.path.isdir(path) and os.path.exists(path):
        return _create_file_id(path)
    else:
        hashes = []
        search_pattern = os.path.join(path, "**", "*")
        for file_path in _glob.glob(search_pattern, recursive=True):
            if os.path.isfile(file_path):
                hashes.append(_create_file_id(file_path))
        if not hashes:
            return (
                f"{Path(path).name}"
                f":{hashlib.md5(path.encode('utf-8')).hexdigest()}"
                f":{hashlib.md5(path.encode('utf-8')).hexdigest()}"
            )
    return (
        f"{Path(path).name}"
        f":{hashlib.md5(path.encode('utf-8')).hexdigest()}"
        f":{hashlib.md5(''.join(sorted(hashes)).encode('utf-8')).hexdigest()}"
    )


def create_id_from_structure(name: str, structure: dict) -> str:
    """Hash a template's in-memory structure into a content-addressable ID.

    Returns ``{name}:{md5(name)}:{md5(structure_json)}``.
    """
    content = _json.dumps(
        {
            k: (v.model_dump(mode="json") if hasattr(v, "model_dump") else v)
            for k, v in structure.items()
        },
        sort_keys=True,
    )
    return (
        f"{name}"
        f":{hashlib.md5(name.encode()).hexdigest()}"
        f":{hashlib.md5(content.encode()).hexdigest()}"
    )


class DocSlot(BaseModel):
    """One named document slot within a template's folder structure."""

    description: str = ""
    required: bool = True
    json_schema: dict[str, Any] | None = None

    @field_validator("json_schema")
    @classmethod
    def _check_schema(cls, v: dict[str, Any] | None) -> dict[str, Any] | None:
        if v is not None:
            try:
                jsonschema.Draft7Validator.check_schema(v)
            except jsonschema.SchemaError as e:
                raise ValueError(f"Invalid JSON Schema in slot: {e.message}") from e
        return v


class TemplateValidation(BaseModel):
    """Result of checking whether a template's required slots are all occupied."""

    template_name: str
    valid: bool
    satisfied: list[str]
    missing: list[str]
    extra: list[str]


class Template(BaseModel):
    """A folder structure blueprint made up of named document slots.

    ``id`` uses the format ``{name}:{md5(path)}:{md5(content)}`` and is
    computed externally by the store based on the source path or structure.

    ``version`` starts at ``1`` and increments each time the content changes.
    ``version_history`` maps content-hash → version number so the server can
    revert to a previous version when the content matches a known snapshot.
    """

    id: str = ""
    name: str
    description: str = ""
    version: int = 1
    version_history: dict[str, int] = Field(default_factory=dict)
    structure: dict[str, DocSlot] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def validate_slot_content(self, path: str, content: dict[str, Any]) -> None:
        """Raise :exc:`TemplateValidationError` if *content* violates the slot's schema."""
        slot = self.structure.get(path)
        if slot is None:
            return
        if slot.json_schema is not None:
            try:
                jsonschema.validate(instance=content, schema=slot.json_schema)
            except jsonschema.ValidationError as e:
                raise TemplateValidationError(
                    f"Content at slot {path!r} failed validation: {e.message}"
                ) from e

    def check_structure(self, occupied_paths: set[str]) -> TemplateValidation:
        """Return a :class:`TemplateValidation` against the given set of occupied slot paths."""
        satisfied = sorted(p for p in self.structure if p in occupied_paths)
        missing = sorted(
            p
            for p, slot in self.structure.items()
            if slot.required and p not in occupied_paths
        )
        extra = sorted(occupied_paths - set(self.structure))
        return TemplateValidation(
            template_name=self.name,
            valid=len(missing) == 0,
            satisfied=satisfied,
            missing=missing,
            extra=extra,
        )


class TemplateRef(BaseModel):
    """Lightweight reference returned when a template is created."""

    name: str
    id: str


class TemplateCreateInput(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "name": "microservice",
                "description": "Standard microservice document set",
                "structure": {
                    "config/app": {
                        "description": "Application configuration",
                        "required": True,
                        "json_schema": {
                            "type": "object",
                            "required": ["host", "port"],
                            "properties": {
                                "host": {"type": "string"},
                                "port": {"type": "integer"},
                            },
                        },
                    },
                    "config/database": {
                        "description": "Database connection settings",
                        "required": True,
                    },
                },
            }
        }
    )

    name: str
    description: str = ""
    structure: dict[str, DocSlot] = Field(default_factory=dict)
    # Input-only: path to a folder (scans all files) or a single file (one flat slot).
    path: Path | None = Field(default=None, exclude=True)

    @field_validator("path")
    @classmethod
    def _check_path(cls, v: Path | None) -> Path | None:
        if v is not None and not v.exists():
            raise ValueError(f"path {v!r} does not exist")
        return v


class DeployDocSpec(BaseModel):
    path: str
    content: dict[str, Any]
    creator: str
    summary: str = ""
    keywords: list[str] = Field(default_factory=list)
    named_version: str | None = None


class DeployVaultInput(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "template_name": "microservice",
                "documents": [
                    {
                        "path": "config/app",
                        "content": {"host": "api.example.com", "port": 8080},
                        "creator": "platform-bot",
                    },
                    {
                        "path": "config/database",
                        "content": {"host": "db.internal", "port": 5432},
                        "creator": "platform-bot",
                    },
                ],
                "commit_message": "Deploy microservice config docs",
            }
        }
    )

    template_name: str
    documents: list[DeployDocSpec]
    commit_message: str | None = None
