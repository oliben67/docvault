from __future__ import annotations

import hashlib
import json as _json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import jsonschema
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..exceptions import TemplateValidationError
from .vault_meta import VaultVersion


def _hash_structure(structure: dict[str, Any]) -> str:
    """Hash a template's slot structure using the nested-file-hash algorithm.

    Each slot's JSON representation is hashed individually, then the hashes are
    assembled into a nested dict that mirrors the slot-path hierarchy.  The final
    hash is SHA-256 of that nested dict serialised as indented JSON — identical to
    how ``get_nested_hashes`` works for files on disk.
    """
    root: dict[str, Any] = {}
    for slot_path in sorted(structure):
        slot = structure[slot_path]
        slot_bytes = _json.dumps(
            slot.model_dump(mode="json") if hasattr(slot, "model_dump") else slot,
            sort_keys=True,
        ).encode("utf-8")
        slot_hash = hashlib.sha256(slot_bytes).hexdigest()

        parts = slot_path.split("/")
        node = root
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = slot_hash

    return hashlib.sha256(_json.dumps(root, indent=4).encode("utf-8")).hexdigest()


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

    ``structure`` maps slot paths (e.g. ``"config/app"``, ``"docs/readme"``)
    to :class:`DocSlot` definitions.  Paths use ``"/"`` as a separator to
    express folder nesting; the vault itself stores documents flat, but the
    path is preserved in document metadata so the structure can be validated.

    A template is **satisfied** when every ``required=True`` slot has at least
    one document in the vault whose ``(template, path)`` matches.

    ``id`` is auto-computed as ``{name}/v{version}/{hash}`` where the hash is a
    SHA-256 over the nested structure — identical to ``get_nested_hashes`` run on
    the equivalent folder of files.  It is stored alongside the template so it
    remains stable even if the hashing algorithm changes in the future.
    """

    id: str = ""
    name: str
    description: str = ""
    version: VaultVersion = Field(default_factory=VaultVersion)
    structure: dict[str, DocSlot] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def _compute_id(self) -> Template:
        if not self.id:
            self.id = f"{self.name}/v{self.version}/{_hash_structure(self.structure)}"
        return self

    def validate_slot_content(self, path: str, content: dict[str, Any]) -> None:
        """Raise :exc:`TemplateValidationError` if *content* violates the slot's schema.

        Unknown paths (not defined in the structure) are silently allowed — they
        will appear in the ``extra`` field of :meth:`check_structure`.  This lets
        documents be created freely while the template defines only what *must*
        exist, not what *can* exist.
        """
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


class TemplateIngestResult(BaseModel):
    """Result of creating a template; *documents* is non-empty only for folder ingestion."""

    template: Template
    documents: list[Any]  # list[Document] — typed as Any to avoid circular import


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
                    "docs/readme": {
                        "description": "Service README",
                        "required": False,
                    },
                },
            }
        }
    )

    name: str
    description: str = ""
    structure: dict[str, DocSlot] = Field(default_factory=dict)
    # Both fields are input-only and excluded from model_dump() / stored JSON.
    folder_path: Path | None = Field(default=None, exclude=True)
    version: VaultVersion | None = Field(
        default=None,
        exclude=True,
        description=(
            "Override the template version. "
            "Defaults to v1.0.0 when folder_path is given, v0.1.0 otherwise."
        ),
    )

    @field_validator("folder_path")
    @classmethod
    def _check_folder(cls, v: Path | None) -> Path | None:
        if v is not None and not v.is_dir():
            raise ValueError(f"folder_path {v!r} is not an existing directory")
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
