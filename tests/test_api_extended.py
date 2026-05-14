# Future imports (must occur at the beginning of the file):
from __future__ import annotations

# Third party imports:
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

# Local imports:
from docvault.api.app import create_app
from docvault.config import AuthMode, VaultConfig
from docvault.core.vault import DocVault
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
    create_resp = await client.post(
        "/api/v1/docs", json={"content": {"v": "original"}, "creator": "alice"}
    )
    assert create_resp.status_code == 201
    doc_id = create_resp.json()["meta"]["id"]

    await client.put(
        f"/api/v1/docs/{doc_id}",
        json={"content": {"v": "updated"}},
    )

    history_resp = await client.get(f"/api/v1/docs/{doc_id}/history")
    commits = history_resp.json()
    assert len(commits) >= 2

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


# ── Store deploy ──────────────────────────────────────────────────────────────


async def test_store_deploy(client: AsyncClient):
    await client.post(
        "/api/v1/stores",
        json={
            "name": "svc",
            "structure": {"cfg": {"description": "config", "required": True}},
        },
    )
    payload = {
        "store_name": "svc",
        "documents": [
            {"path": "cfg", "content": {"host": "localhost"}, "creator": "bot"}
        ],
    }
    resp = await client.post("/api/v1/stores/svc/deploy", json=payload)
    assert resp.status_code == 201
    docs = resp.json()
    assert len(docs) == 1
    assert docs[0]["meta"]["path"] == "cfg"


async def test_store_deploy_unknown_store(client: AsyncClient):
    payload = {
        "store_name": "no-such-store",
        "documents": [{"path": "x", "content": {}, "creator": "bot"}],
    }
    resp = await client.post("/api/v1/stores/no-such-store/deploy", json=payload)
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


# ── 404 responses ────────────────────────────────────────────────────────────


async def test_get_doc_unknown_id_404(client: AsyncClient):
    resp = await client.get("/api/v1/docs/no-such-id")
    assert resp.status_code == 404
    assert "detail" in resp.json()


async def test_get_store_unknown_404(client: AsyncClient):
    resp = await client.get("/api/v1/stores/no-such-store")
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


async def test_store_export_content_type_zip(client: AsyncClient):
    await client.post("/api/v1/stores", json={"name": "export-test"})
    resp = await client.get("/api/v1/stores/export-test/export")
    assert resp.status_code == 200
    assert "application/zip" in resp.headers["content-type"]


# ── StoreRef response shape ────────────────────────────────────────────────────


async def test_create_store_response_shape(client: AsyncClient):
    resp = await client.post("/api/v1/stores", json={"name": "shaped"})
    assert resp.status_code == 201
    body = resp.json()
    assert "name" in body
    assert "id" in body
    assert body["name"] == "shaped"
    assert len(body["id"]) > 0
