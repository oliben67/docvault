"""Deploy the template-source folder to template-deployment.

Run this while the shim-integration server is up:

    # Terminal 1
    uv run uvicorn main:app --reload --port 54321

    # Terminal 2
    uv run python demo.py
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from docvault.config import VaultConfig
from docvault.core.store import DocVault
from docvault.core.template import TemplateCreateInput
from docvault.exceptions import TemplateNotFoundError
from docvault.tools import deploy_template

VAULT_DIR = Path(__file__).parent / ".demo-vault"
TEMPLATE_SOURCE = Path(__file__).parent / "template-source"
TEMPLATE_DEPLOYMENT = Path(__file__).parent / "template-deployment"
TEMPLATE_NAME = "project"
SERVER_BASE_URL = "http://localhost:54321"


async def _load_template_id() -> str:
    config = VaultConfig(vault_path=VAULT_DIR, vault_name="hello-world-vault")
    store = DocVault(config)
    await store.init()
    try:
        tpl = await store.get_template(TEMPLATE_NAME)
        return tpl.id
    except TemplateNotFoundError:
        result = await store.create_template(
            TemplateCreateInput(
                name=TEMPLATE_NAME,
                description="Loaded from template-source folder",
                folder_path=TEMPLATE_SOURCE,
            ),
            creator="demo",
        )
        return result.template.id


def main() -> None:
    template_id = asyncio.run(_load_template_id())
    print(f"Template: {template_id}")

    extracted = deploy_template(
        template_id,
        TEMPLATE_DEPLOYMENT,
        SERVER_BASE_URL,
        overwrite=True,
    )

    print(f"Deployed {len(extracted)} file(s) to {TEMPLATE_DEPLOYMENT}:")
    for path in extracted:
        print(f"  {path.relative_to(TEMPLATE_DEPLOYMENT)}")


if __name__ == "__main__":
    main()
