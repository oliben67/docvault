from __future__ import annotations

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from docvault.api.app import create_app
from docvault.config import AuthMode, VaultConfig
from docvault.core.store import DocVault
from tests.conftest import asgi_lifespan_client


@pytest_asyncio.fixture
async def client(tmp_path):
    cfg = VaultConfig(vault_path=tmp_path / "vault")
    s = DocVault(cfg)
    await s.init()
    app = create_app(cfg)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


@pytest_asyncio.fixture
async def authed_client(tmp_path):
    cfg = VaultConfig(
        vault_path=tmp_path / "vault",
        auth_mode=AuthMode.API_KEY,
        api_keys=["test-key-abc"],
    )
    app = create_app(cfg)
    async with asgi_lifespan_client(app) as c:
        yield c


# ── Point-in-time retrieval ───────────────────────────────────────────────────


async def test_docs_at_ref(client: AsyncClient):
    # Create then update a document
    create_resp = await client.post(
        "/api/v1/docs", json={"content": {"v": "original"}, "creator": "alice"}
    )
    assert create_resp.status_code == 201
    doc_id = create_resp.json()["meta"]["id"]

    await client.put(
        f"/api/v1/docs/{doc_id}",
        json={"content": {"v": "updated"}},
    )

    # Get commit history
    history_resp = await client.get(f"/api/v1/docs/{doc_id}/history")
    commits = history_resp.json()
    assert len(commits) >= 2

    # Fetch at the oldest commit (last in history list)
    first_sha = commits[-1]["sha"]
    at_resp = await client.get(f"/api/v1/docs/{doc_id}/at/{first_sha}")
    assert at_resp.status_code == 200
    assert at_resp.json()["content"]["v"] == "original"


async def test_docs_at_ref_invalid_returns_404_or_422(client: AsyncClient):
    create_resp = await client.post(
        "/api/v1/docs", json={"content": {"x": 1}, "creator": "alice"}
    )
    doc_id = create_resp.json()["meta"]["id"]
    resp = await client.get(f"/api/v1/docs/{doc_id}/at/deadbeefdeadbeef")
    assert resp.status_code in (404, 422, 500)


# ── Summarize endpoints (no LLM configured) ───────────────────────────────────


async def test_summarize_doc_no_llm(client: AsyncClient):
    create_resp = await client.post(
        "/api/v1/docs", json={"content": {"k": "v"}, "creator": "alice"}
    )
    doc_id = create_resp.json()["meta"]["id"]
    resp = await client.post(f"/api/v1/docs/{doc_id}/summarize")
    assert resp.status_code in (503, 500, 400)


async def test_summarize_doc_unknown_id(client: AsyncClient):
    resp = await client.post("/api/v1/docs/nonexistent-id/summarize")
    assert resp.status_code == 404


async def test_summarize_all_no_llm(client: AsyncClient):
    resp = await client.post("/api/v1/docs/summarize/all")
    assert resp.status_code in (200, 503, 500)


# ── Vault deploy ──────────────────────────────────────────────────────────────


async def test_vault_deploy(client: AsyncClient):
    await client.post(
        "/api/v1/templates",
        json={
            "name": "svc",
            "structure": {"cfg": {"description": "config", "required": True}},
        },
    )
    payload = {
        "template_name": "svc",
        "documents": [
            {"path": "cfg", "content": {"host": "localhost"}, "creator": "bot"}
        ],
    }
    resp = await client.post("/api/v1/vault/deploy", json=payload)
    assert resp.status_code == 201
    docs = resp.json()
    assert len(docs) == 1
    assert docs[0]["meta"]["template"] == "svc"


async def test_vault_deploy_unknown_template(client: AsyncClient):
    payload = {
        "template_name": "no-such-template",
        "documents": [{"path": "x", "content": {}, "creator": "bot"}],
    }
    resp = await client.post("/api/v1/vault/deploy", json=payload)
    assert resp.status_code == 404


# ── list_docs query params ────────────────────────────────────────────────────


async def test_list_docs_filter_by_creator(client: AsyncClient):
    await client.post("/api/v1/docs", json={"content": {"a": 1}, "creator": "alice"})
    await client.post("/api/v1/docs", json={"content": {"b": 2}, "creator": "bob"})

    resp = await client.get("/api/v1/docs?creator=alice")
    assert resp.status_code == 200
    docs = resp.json()
    assert all(d["creator"] == "alice" for d in docs)


async def test_list_docs_filter_by_keywords(client: AsyncClient):
    await client.post(
        "/api/v1/docs",
        json={"content": {"x": 1}, "creator": "alice", "keywords": ["python", "api"]},
    )
    await client.post(
        "/api/v1/docs",
        json={"content": {"y": 2}, "creator": "alice", "keywords": ["java"]},
    )
    resp = await client.get("/api/v1/docs?keywords=python")
    assert resp.status_code == 200
    docs = resp.json()
    assert len(docs) == 1


async def test_list_docs_filter_by_template(client: AsyncClient):
    await client.post("/api/v1/templates", json={"name": "mytemplate"})
    await client.post(
        "/api/v1/docs",
        json={"content": {"x": 1}, "creator": "alice", "template": "mytemplate"},
    )
    await client.post("/api/v1/docs", json={"content": {"y": 2}, "creator": "alice"})

    resp = await client.get("/api/v1/docs?template=mytemplate")
    assert resp.status_code == 200
    docs = resp.json()
    assert len(docs) == 1
    assert docs[0]["template"] == "mytemplate"


# ── 404 responses ────────────────────────────────────────────────────────────


async def test_get_doc_unknown_id_404(client: AsyncClient):
    resp = await client.get("/api/v1/docs/no-such-id")
    assert resp.status_code == 404
    assert "detail" in resp.json()


async def test_get_template_unknown_404(client: AsyncClient):
    resp = await client.get("/api/v1/templates/no-such-template")
    assert resp.status_code == 404


async def test_delete_doc_then_get_404(client: AsyncClient):
    create_resp = await client.post(
        "/api/v1/docs", json={"content": {"x": 1}, "creator": "alice"}
    )
    doc_id = create_resp.json()["meta"]["id"]
    await client.delete(f"/api/v1/docs/{doc_id}")
    resp = await client.get(f"/api/v1/docs/{doc_id}")
    assert resp.status_code == 404


# ── 422 validation errors ────────────────────────────────────────────────────


async def test_create_doc_missing_content_422(client: AsyncClient):
    resp = await client.post("/api/v1/docs", json={"creator": "alice"})
    assert resp.status_code == 422


async def test_create_doc_non_object_content_422(client: AsyncClient):
    resp = await client.post(
        "/api/v1/docs", json={"content": "not-an-object", "creator": "alice"}
    )
    assert resp.status_code == 422


# ── API key auth ──────────────────────────────────────────────────────────────


async def test_api_key_auth_valid_key_works(authed_client: AsyncClient):
    resp = await authed_client.get(
        "/api/v1/health", headers={"X-API-Key": "test-key-abc"}
    )
    assert resp.status_code == 200


async def test_api_key_auth_missing_key_401(authed_client: AsyncClient):
    resp = await authed_client.get("/api/v1/docs")
    assert resp.status_code == 401


async def test_api_key_auth_wrong_key_401(authed_client: AsyncClient):
    resp = await authed_client.get("/api/v1/docs", headers={"X-API-Key": "wrong-key"})
    assert resp.status_code == 401


# ── Response content types ────────────────────────────────────────────────────


async def test_create_doc_response_is_json(client: AsyncClient):
    resp = await client.post(
        "/api/v1/docs", json={"content": {"x": 1}, "creator": "alice"}
    )
    assert "application/json" in resp.headers["content-type"]


async def test_template_export_content_type_zip(client: AsyncClient):
    ref = (await client.post("/api/v1/templates", json={"name": "export-test"})).json()
    resp = await client.get(f"/api/v1/templates/{ref['id']}/export")
    assert resp.status_code == 200
    assert "application/zip" in resp.headers["content-type"]


# ── TemplateRef response shape ────────────────────────────────────────────────


async def test_create_template_response_shape(client: AsyncClient):
    resp = await client.post("/api/v1/templates", json={"name": "shaped"})
    assert resp.status_code == 201
    body = resp.json()
    assert "name" in body
    assert "id" in body
    assert body["name"] == "shaped"
    assert len(body["id"]) > 0
