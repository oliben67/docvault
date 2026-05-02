"""
Standalone HTTP client example.

Shows how to interact with a running DocVault server purely over HTTP
using a thin `DocVaultClient` wrapper around `httpx.AsyncClient`.

Usage (two terminals):

    Terminal 1 — start a server:
        docvault init
        docvault serve

    Terminal 2 — run this script:
        uv run python examples/standalone-client/main.py

If the server isn't available the script spins up an in-process ASGI
app instead, so it always runs stand-alone.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Any


# ── Thin client wrapper ────────────────────────────────────────────────────────

class DocVaultClient:
    """Minimal async HTTP client for DocVault's REST API."""

    def __init__(self, base_url: str = "http://localhost:8000", api_key: str | None = None):
        import httpx

        headers: dict[str, str] = {}
        if api_key:
            headers["X-API-Key"] = api_key
        self._client = httpx.AsyncClient(base_url=base_url, headers=headers, timeout=10.0)

    async def __aenter__(self) -> "DocVaultClient":
        await self._client.__aenter__()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self._client.__aexit__(*args)

    def _raise(self, resp: Any) -> Any:
        resp.raise_for_status()
        return resp

    async def health(self) -> dict:
        return self._raise(await self._client.get("/api/v1/health")).json()

    async def create_doc(
        self,
        content: dict,
        creator: str = "client",
        summary: str | None = None,
        keywords: list[str] | None = None,
        template: str | None = None,
    ) -> dict:
        body: dict[str, Any] = {"content": content, "creator": creator}
        if summary:
            body["summary"] = summary
        if keywords:
            body["keywords"] = keywords
        if template:
            body["template"] = template
        return self._raise(await self._client.post("/api/v1/docs", json=body)).json()

    async def get_doc(self, doc_id: str) -> dict:
        return self._raise(await self._client.get(f"/api/v1/docs/{doc_id}")).json()

    async def update_doc(self, doc_id: str, content: dict, **kwargs: Any) -> dict:
        body: dict[str, Any] = {"content": content, **kwargs}
        return self._raise(await self._client.put(f"/api/v1/docs/{doc_id}", json=body)).json()

    async def delete_doc(self, doc_id: str) -> None:
        self._raise(await self._client.delete(f"/api/v1/docs/{doc_id}"))

    async def list_docs(
        self,
        creator: str | None = None,
        keywords: str | None = None,
        template: str | None = None,
    ) -> list[dict]:
        params: dict[str, str] = {}
        if creator:
            params["creator"] = creator
        if keywords:
            params["keywords"] = keywords
        if template:
            params["template"] = template
        return self._raise(await self._client.get("/api/v1/docs", params=params)).json()

    async def doc_history(self, doc_id: str, max_count: int = 50) -> list[dict]:
        return self._raise(
            await self._client.get(f"/api/v1/docs/{doc_id}/history", params={"max_count": max_count})
        ).json()

    async def doc_at_ref(self, doc_id: str, ref: str) -> dict:
        return self._raise(await self._client.get(f"/api/v1/docs/{doc_id}/at/{ref}")).json()

    async def create_template(self, name: str, structure: dict | None = None) -> dict:
        body: dict[str, Any] = {"name": name}
        if structure:
            body["structure"] = structure
        return self._raise(await self._client.post("/api/v1/templates", json=body)).json()

    async def list_templates(self) -> list[dict]:
        return self._raise(await self._client.get("/api/v1/templates")).json()

    async def vault_info(self) -> dict:
        return self._raise(await self._client.get("/api/v1/vault")).json()

    async def bump_vault_version(self, kind: str) -> dict:
        return self._raise(await self._client.post(f"/api/v1/vault/version/{kind}")).json()


# ── Demo ──────────────────────────────────────────────────────────────────────

async def run_demo(client: DocVaultClient) -> None:
    # Health check
    health = await client.health()
    print(f"Health: {health}")

    # Create documents
    doc1 = await client.create_doc(
        {"service": "auth", "replicas": 3, "cpu": "500m"},
        creator="infra-bot",
        keywords=["kubernetes", "auth"],
        summary="Auth service deployment config",
    )
    print(f"\nCreated:  {doc1['meta']['id'][:12]}  service={doc1['content']['service']}")

    doc2 = await client.create_doc(
        {"service": "gateway", "replicas": 5, "cpu": "1000m"},
        creator="infra-bot",
        keywords=["kubernetes", "gateway"],
        summary="API gateway deployment config",
    )
    print(f"Created:  {doc2['meta']['id'][:12]}  service={doc2['content']['service']}")

    # List all docs
    docs = await client.list_docs()
    print(f"\nAll docs ({len(docs)}):")
    for d in docs:
        print(f"  {d['id'][:12]}  creator={d['creator']}  keywords={d['keywords']}")

    # Filter by keyword
    gateway_docs = await client.list_docs(keywords="gateway")
    print(f"\nDocs with keyword 'gateway': {len(gateway_docs)}")

    # Update a document
    updated = await client.update_doc(doc1["meta"]["id"], {"service": "auth", "replicas": 5, "cpu": "500m"})
    print(f"\nUpdated:  {updated['meta']['id'][:12]}  replicas={updated['content']['replicas']}")

    # View history
    history = await client.doc_history(doc1["meta"]["id"])
    print(f"\nHistory for {doc1['meta']['id'][:12]} ({len(history)} commits):")
    for h in history:
        print(f"  {h['sha'][:10]}  {h['message']}")

    # Fetch at oldest revision
    if len(history) >= 2:
        original = await client.doc_at_ref(doc1["meta"]["id"], history[-1]["sha"])
        print(f"\nOriginal replicas (at oldest commit): {original['content']['replicas']}")

    # Vault info and version bump
    vault = await client.vault_info()
    v = vault["version"]
    print(f"\nVault: {vault['name']} v{v['major']}.{v['minor']}.{v['patch']}")

    vault = await client.bump_vault_version("minor")
    v = vault["version"]
    print(f"After minor bump: v{v['major']}.{v['minor']}.{v['patch']}")

    # Delete one document
    await client.delete_doc(doc2["meta"]["id"])
    remaining = await client.list_docs()
    print(f"\nAfter delete: {len(remaining)} doc(s) remain")


async def main() -> None:
    with tempfile.TemporaryDirectory() as d:
        # Spin up an in-process ASGI app so the example runs without a server
        from contextlib import AsyncExitStack

        from httpx import ASGITransport, AsyncClient

        from docvault.api.app import create_app
        from docvault.config import VaultConfig

        cfg = VaultConfig(vault_path=Path(d) / "vault")
        app = create_app(cfg)

        async with AsyncExitStack() as stack:
            await stack.enter_async_context(app.router.lifespan_context(app))
            http = await stack.enter_async_context(
                AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
            )

            # Wrap in our client (bypass the base_url / api_key logic — inject client directly)
            client = DocVaultClient.__new__(DocVaultClient)
            client._client = http  # type: ignore[attr-defined]
            await run_demo(client)


if __name__ == "__main__":
    asyncio.run(main())
