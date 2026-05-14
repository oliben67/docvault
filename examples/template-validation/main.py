"""
Store validation example.

Shows how to:
- Define a store with per-slot JSON Schema constraints
- Deploy documents that satisfy the schema (accepted)
- Attempt to deploy documents that violate the schema (rejected)
- Mix required and optional slots in one store
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from docvault.config import VaultConfig
from docvault.core.vault import DocVault
from docvault.core.store import DeployDocSpec, DocSlot, StoreCreateInput
from docvault.exceptions import StoreValidationError


SERVICE_SCHEMA = {
    "type": "object",
    "required": ["host", "port"],
    "properties": {
        "host": {"type": "string"},
        "port": {"type": "integer", "minimum": 1, "maximum": 65535},
        "tls": {"type": "boolean"},
    },
    "additionalProperties": False,
}

DATABASE_SCHEMA = {
    "type": "object",
    "required": ["url"],
    "properties": {
        "url": {"type": "string", "pattern": r"^(postgres|mysql|sqlite)://"},
        "pool_size": {"type": "integer", "minimum": 1},
    },
}


async def main() -> None:
    with tempfile.TemporaryDirectory() as d:
        cfg = VaultConfig(vault_path=Path(d) / "vault")
        vault = DocVault(cfg)
        await vault.init()

        # Create a store with two slots, each with its own JSON Schema
        store_obj = await vault.create_store(
            StoreCreateInput(
                name="microservice",
                description="Required configs for a microservice deployment",
                structure={
                    "service": DocSlot(
                        required=True,
                        description="Service network config",
                        json_schema=SERVICE_SCHEMA,
                    ),
                    "database": DocSlot(
                        required=False,
                        description="Optional database connection",
                        json_schema=DATABASE_SCHEMA,
                    ),
                },
            )
        )
        meta = await store_obj.get_meta()
        print(f"Store created: {meta.name} (id={meta.id})")

        # --- Valid deploy: all required slots, content matches schema ---
        print("\n[1] Valid deploy (all constraints satisfied):")
        docs = await store_obj.deploy(
            [
                DeployDocSpec(
                    path="service",
                    content={"host": "api.internal", "port": 8080, "tls": True},
                    creator="ci-bot",
                ),
                DeployDocSpec(
                    path="database",
                    content={"url": "postgres://db:5432/app", "pool_size": 10},
                    creator="ci-bot",
                ),
            ]
        )
        for doc in docs:
            print(f"  ✓  {doc.meta.path}  (id={doc.meta.id[:12]})")

        # --- Invalid deploy: port out of range ---
        print("\n[2] Invalid deploy (port out of schema range):")
        try:
            await store_obj.deploy(
                [
                    DeployDocSpec(
                        path="service",
                        content={"host": "api.internal", "port": 99999},
                        creator="ci-bot",
                    ),
                ]
            )
        except StoreValidationError as exc:
            print(f"  ✗  Rejected: {exc}")

        # --- Invalid deploy: wrong type for required field ---
        print("\n[3] Invalid deploy (host is a number, not a string):")
        try:
            await store_obj.deploy(
                [
                    DeployDocSpec(
                        path="service",
                        content={"host": 12345, "port": 443},
                        creator="ci-bot",
                    ),
                ]
            )
        except StoreValidationError as exc:
            print(f"  ✗  Rejected: {exc}")

        # --- Invalid deploy: bad DB URL scheme ---
        print("\n[4] Invalid deploy (unsupported DB URL scheme):")
        try:
            await store_obj.deploy(
                [
                    DeployDocSpec(
                        path="service",
                        content={"host": "api.internal", "port": 80},
                        creator="ci-bot",
                    ),
                    DeployDocSpec(
                        path="database",
                        content={"url": "mongodb://db:27017/app"},
                        creator="ci-bot",
                    ),
                ]
            )
        except StoreValidationError as exc:
            print(f"  ✗  Rejected: {exc}")

        print("\nDone.")


if __name__ == "__main__":
    asyncio.run(main())
