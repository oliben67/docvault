# Future imports (must occur at the beginning of the file):
from __future__ import annotations

# Standard library imports:
from datetime import UTC, datetime
from typing import Literal

# Third party imports:
from pydantic import BaseModel, Field


class VaultVersion(BaseModel):
    major: int = 0
    minor: int = 1
    patch: int = 0

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"

    def bump(self, kind: Literal["major", "minor", "patch"]) -> VaultVersion:
        if kind == "major":
            return VaultVersion(major=self.major + 1, minor=0, patch=0)
        if kind == "minor":
            return VaultVersion(major=self.major, minor=self.minor + 1, patch=0)
        return VaultVersion(major=self.major, minor=self.minor, patch=self.patch + 1)


class VaultMeta(BaseModel):
    name: str
    description: str = ""
    version: VaultVersion = Field(default_factory=VaultVersion)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
