# Future imports (must occur at the beginning of the file):
from __future__ import annotations

# Third party imports:
from httpx import AsyncClient


async def test_health(client: AsyncClient):
    resp = await client.get("/api/v1/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_create_and_get_doc(client: AsyncClient):
    resp = await client.post(
        "/api/v1/docs",
        json={"content": {"title": "Test"}, "creator": "alice"},
    )
    assert resp.status_code == 201
    doc_id = resp.json()["meta"]["id"]

    resp = await client.get(f"/api/v1/docs/{doc_id}")
    assert resp.status_code == 200
    assert resp.json()["content"]["title"] == "Test"


async def test_update_doc(client: AsyncClient):
    doc_id = (
        await client.post(
            "/api/v1/docs", json={"content": {"x": 1}, "creator": "alice"}
        )
    ).json()["meta"]["id"]

    resp = await client.put(f"/api/v1/docs/{doc_id}", json={"content": {"x": 2}})
    assert resp.status_code == 200
    assert resp.json()["content"]["x"] == 2


async def test_delete_doc(client: AsyncClient):
    doc_id = (
        await client.post(
            "/api/v1/docs", json={"content": {"x": 1}, "creator": "alice"}
        )
    ).json()["meta"]["id"]

    assert (await client.delete(f"/api/v1/docs/{doc_id}")).status_code == 204
    assert (await client.get(f"/api/v1/docs/{doc_id}")).status_code == 404


async def test_list_docs(client: AsyncClient):
    for i in range(3):
        await client.post(
            "/api/v1/docs", json={"content": {"i": i}, "creator": "alice"}
        )

    resp = await client.get("/api/v1/docs")
    assert resp.status_code == 200
    assert len(resp.json()) == 3


async def test_doc_history(client: AsyncClient):
    doc_id = (
        await client.post(
            "/api/v1/docs", json={"content": {"v": 1}, "creator": "alice"}
        )
    ).json()["meta"]["id"]
    await client.put(f"/api/v1/docs/{doc_id}", json={"content": {"v": 2}})

    resp = await client.get(f"/api/v1/docs/{doc_id}/history")
    assert resp.status_code == 200
    assert len(resp.json()) == 2


# ── Stores ──────────────────────────────────────────────────────────────────────

_STRUCTURE = {
    "config/app": {
        "description": "App config",
        "required": True,
        "json_schema": {
            "type": "object",
            "required": ["host"],
            "properties": {"host": {"type": "string"}},
        },
    },
    "config/database": {"required": True},
    "docs/readme": {"required": False},
}


async def test_store_crud(client: AsyncClient):
    resp = await client.post(
        "/api/v1/stores",
        json={"name": "svc", "description": "Service docs", "structure": _STRUCTURE},
    )
    assert resp.status_code == 201
    ref = resp.json()
    assert ref["name"] == "svc"

    assert any(
        s["name"] == "svc" for s in (await client.get("/api/v1/stores")).json()
    )

    assert (await client.get("/api/v1/stores/svc")).status_code == 200

    assert (await client.delete("/api/v1/stores/svc")).status_code == 204
    assert (await client.get("/api/v1/stores/svc")).status_code == 404


async def test_store_validate_endpoint(client: AsyncClient):
    await client.post(
        "/api/v1/stores",
        json={"name": "svc", "structure": _STRUCTURE},
    )

    result = (await client.get("/api/v1/stores/svc/validate")).json()
    assert not result["valid"]
    assert "config/app" in result["missing"]
    assert "config/database" in result["missing"]

    await client.post(
        "/api/v1/stores/svc/docs",
        json={"content": {"host": "localhost"}, "creator": "alice", "path": "config/app"},
    )
    await client.post(
        "/api/v1/stores/svc/docs",
        json={"content": {"dsn": "postgres://..."}, "creator": "alice", "path": "config/database"},
    )

    result = (await client.get("/api/v1/stores/svc/validate")).json()
    assert result["valid"]
    assert result["missing"] == []


async def test_store_slot_content_validation_enforced(client: AsyncClient):
    await client.post(
        "/api/v1/stores",
        json={
            "name": "typed",
            "structure": {
                "data": {
                    "required": True,
                    "json_schema": {"type": "object", "required": ["name"]},
                }
            },
        },
    )

    resp = await client.post(
        "/api/v1/stores/typed/docs",
        json={"content": {"name": "Alice"}, "creator": "alice", "path": "data"},
    )
    assert resp.status_code == 201

    resp = await client.post(
        "/api/v1/stores/typed/docs",
        json={"content": {"no_name": True}, "creator": "alice", "path": "data"},
    )
    assert resp.status_code == 422


async def test_store_unknown_path_allowed_appears_as_extra(client: AsyncClient):
    await client.post("/api/v1/stores", json={"name": "svc", "structure": _STRUCTURE})

    resp = await client.post(
        "/api/v1/stores/svc/docs",
        json={"content": {"x": 1}, "creator": "alice", "path": "orphaned/doc"},
    )
    assert resp.status_code == 201
    assert resp.json()["meta"]["path"] == "orphaned/doc"

    result = (await client.get("/api/v1/stores/svc/validate")).json()
    assert "orphaned/doc" in result["extra"]


async def test_store_export_endpoint(client: AsyncClient):
    # Standard library imports:
    import io
    import json
    import zipfile

    await client.post("/api/v1/stores", json={"name": "svc", "structure": _STRUCTURE})
    await client.post(
        "/api/v1/stores/svc/docs",
        json={"content": {"host": "db"}, "creator": "alice", "path": "config/app"},
    )

    resp = await client.get("/api/v1/stores/svc/export")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    assert 'filename="svc.zip"' in resp.headers["content-disposition"]

    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        assert "_store.json" in zf.namelist()
        assert "config/app.json" in zf.namelist()
        meta_data = json.loads(zf.read("_store.json"))
        assert meta_data["name"] == "svc"


async def test_store_export_not_found(client: AsyncClient):
    resp = await client.get("/api/v1/stores/nonexistent/export")
    assert resp.status_code == 404


# ── Vault ──────────────────────────────────────────────────────────────────────


async def test_vault_endpoints(client: AsyncClient):
    resp = await client.get("/api/v1/vault")
    assert resp.status_code == 200
    assert resp.json()["version"]["major"] == 0

    resp = await client.post("/api/v1/vault/version/minor")
    assert resp.status_code == 200
    assert resp.json()["version"]["minor"] == 2

    resp = await client.get("/api/v1/vault/versions")
    assert resp.status_code == 200
    assert "v0.2.0" in resp.json()
