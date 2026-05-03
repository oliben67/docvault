"""
Template validation example.

Shows how to:
- Define a template with per-slot JSON Schema constraints
- Deploy documents that satisfy the schema (accepted)
- Attempt to deploy documents that violate the schema (rejected)
- Mix required and optional slots in one template
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from docvault.config import VaultConfig
from docvault.core.store import DocVault
from docvault.core.template import DeployDocSpec, DeployVaultInput, DocSlot, TemplateCreateInput
from docvault.exceptions import TemplateValidationError


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
        store = DocVault(cfg)
        await store.init()

        # Create a template with two slots, each with its own JSON Schema
        result = await store.create_template(
            TemplateCreateInput(
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
        print(f"Template created: {result.name} (id={result.id})")

        # --- Valid deploy: all required slots, content matches schema ---
        print("\n[1] Valid deploy (all constraints satisfied):")
        docs = await store.deploy_vault(
            DeployVaultInput(
                template_name="microservice",
                documents=[
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
                ],
            )
        )
        for doc in docs:
            print(f"  ✓  {doc.meta.path}  (id={doc.meta.id[:12]})")

        # --- Invalid deploy: port out of range ---
        print("\n[2] Invalid deploy (port out of schema range):")
        try:
            await store.deploy_vault(
                DeployVaultInput(
                    template_name="microservice",
                    documents=[
                        DeployDocSpec(
                            path="service",
                            content={"host": "api.internal", "port": 99999},
                            creator="ci-bot",
                        ),
                    ],
                )
            )
        except TemplateValidationError as exc:
            print(f"  ✗  Rejected: {exc}")

        # --- Invalid deploy: wrong type for required field ---
        print("\n[3] Invalid deploy (host is a number, not a string):")
        try:
            await store.deploy_vault(
                DeployVaultInput(
                    template_name="microservice",
                    documents=[
                        DeployDocSpec(
                            path="service",
                            content={"host": 12345, "port": 443},
                            creator="ci-bot",
                        ),
                    ],
                )
            )
        except TemplateValidationError as exc:
            print(f"  ✗  Rejected: {exc}")

        # --- Invalid deploy: bad DB URL scheme ---
        print("\n[4] Invalid deploy (unsupported DB URL scheme):")
        try:
            await store.deploy_vault(
                DeployVaultInput(
                    template_name="microservice",
                    documents=[
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
                    ],
                )
            )
        except TemplateValidationError as exc:
            print(f"  ✗  Rejected: {exc}")

        print("\nDone.")


if __name__ == "__main__":
    asyncio.run(main())
