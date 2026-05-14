# Future imports (must occur at the beginning of the file):
from __future__ import annotations

# Third party imports:
import pytest

# Local imports:
from docvault.core.document import CreateDocInput, UpdateDocInput
from docvault.core.vault import DocVault
from docvault.core.store import (
    DeployDocSpec,
    DeployStoreInput,
    DocSlot,
    StoreCreateInput,
)
from docvault.exceptions import (
    DocumentNotFoundError,
    StoreNotFoundError,
    StoreValidationError,
)

# ── Store fixture ──────────────────────────────────────────────────────────────

_STRUCTURE = {
    "config/app": DocSlot(
        description="Application configuration",
        required=True,
        json_schema={
            "type": "object",
            "required": ["host"],
            "properties": {"host": {"type": "string"}, "port": {"type": "integer"}},
        },
    ),
    "config/database": DocSlot(description="Database settings", required=True),
    "docs/readme": DocSlot(description="Service README", required=False),
}


# ── Document CRUD ──────────────────────────────────────────────────────────────


async def test_create_and_get_doc(store: DocVault):
    inp = CreateDocInput(content={"title": "Hello", "body": "World"}, creator="alice")
    doc = await store.create_doc(inp)
    assert doc.meta.creator == "alice"

    fetched = await store.get_doc(doc.meta.id)
    assert fetched.content == doc.content
    assert fetched.meta.id == doc.meta.id


async def test_update_doc(store: DocVault):
    doc = await store.create_doc(CreateDocInput(content={"x": 1}, creator="alice"))
    updated = await store.update_doc(doc.meta.id, UpdateDocInput(content={"x": 2}))
    assert updated.content["x"] == 2
    assert updated.meta.updated_at >= doc.meta.updated_at


async def test_delete_doc(store: DocVault):
    doc = await store.create_doc(CreateDocInput(content={"x": 1}, creator="alice"))
    await store.delete_doc(doc.meta.id)
    with pytest.raises(DocumentNotFoundError):
        await store.get_doc(doc.meta.id)


async def test_list_docs_filters(store: DocVault):
    await store.create_doc(
        CreateDocInput(content={"n": 1}, creator="alice", keywords=["foo"])
    )
    await store.create_doc(
        CreateDocInput(content={"n": 2}, creator="bob", keywords=["foo", "bar"])
    )
    await store.create_doc(CreateDocInput(content={"n": 3}, creator="alice"))

    assert len(await store.list_docs()) == 3
    assert len(await store.list_docs(creator="alice")) == 2
    assert len(await store.list_docs(keywords=["foo"])) == 2
    assert len(await store.list_docs(keywords=["foo", "bar"])) == 1


async def test_doc_history(store: DocVault):
    doc = await store.create_doc(CreateDocInput(content={"v": 1}, creator="alice"))
    await store.update_doc(doc.meta.id, UpdateDocInput(content={"v": 2}))
    history = await store.get_doc_history(doc.meta.id)
    assert len(history) == 2
    assert history[0].message.startswith("Update")
    assert history[1].message.startswith("Create")


async def test_doc_at_ref(store: DocVault):
    doc = await store.create_doc(CreateDocInput(content={"v": 1}, creator="alice"))
    ref_v1 = (await store.get_doc_history(doc.meta.id))[0].sha

    await store.update_doc(doc.meta.id, UpdateDocInput(content={"v": 2}))

    at_v1 = await store.get_doc_at_ref(doc.meta.id, ref_v1)
    assert at_v1.content["v"] == 1


# ── Stores ──────────────────────────────────────────────────────────────────────


async def test_store_slot_content_validation(store: DocVault):
    store_obj = await store.create_store(StoreCreateInput(name="svc", structure=_STRUCTURE))

    doc = await store_obj.create_doc(
        CreateDocInput(
            content={"host": "localhost", "port": 8080},
            creator="alice",
            path="config/app",
        )
    )
    assert doc.meta.path == "config/app"

    with pytest.raises(StoreValidationError):
        await store_obj.create_doc(
            CreateDocInput(
                content={"port": 8080},  # missing required "host"
                creator="alice",
                path="config/app",
            )
        )

    # Path not defined in the store — allowed, will appear as 'extra' in validation
    unknown_doc = await store_obj.create_doc(
        CreateDocInput(content={"x": 1}, creator="alice", path="nonexistent/slot")
    )
    assert unknown_doc.meta.path == "nonexistent/slot"


async def test_store_validate_structure(store: DocVault):
    store_obj = await store.create_store(
        StoreCreateInput(name="svc", structure=_STRUCTURE)
    )

    result = await store.validate_store("svc")
    assert not result.valid
    assert "config/app" in result.missing
    assert "config/database" in result.missing

    await store_obj.create_doc(
        CreateDocInput(content={"host": "localhost"}, creator="alice", path="config/app")
    )
    result = await store.validate_store("svc")
    assert not result.valid
    assert "config/app" not in result.missing
    assert "config/database" in result.missing

    await store_obj.create_doc(
        CreateDocInput(
            content={"host": "db.internal"}, creator="alice", path="config/database"
        )
    )
    result = await store.validate_store("svc")
    assert result.valid
    assert result.missing == []
    assert "docs/readme" not in result.missing


async def test_store_validate_extra_docs(store: DocVault):
    store_obj = await store.create_store(
        StoreCreateInput(name="svc", structure=_STRUCTURE)
    )
    await store_obj.create_doc(
        CreateDocInput(content={"x": 1}, creator="alice", path="unknown/slot")
    )
    result = await store.validate_store("svc")
    assert "unknown/slot" in result.extra


async def test_store_no_path_no_validation(store: DocVault):
    """A document with no path bypasses slot validation."""
    store_obj = await store.create_store(StoreCreateInput(name="svc", structure=_STRUCTURE))
    doc = await store_obj.create_doc(
        CreateDocInput(content={"anything": True}, creator="alice")
    )
    assert doc.meta.path is None


async def test_update_store_doc_validates_slot(store: DocVault):
    store_obj = await store.create_store(StoreCreateInput(name="svc", structure=_STRUCTURE))
    doc = await store_obj.create_doc(
        CreateDocInput(content={"host": "localhost"}, creator="alice", path="config/app")
    )

    updated = await store_obj.update_doc(
        doc.meta.id, UpdateDocInput(content={"host": "prod.example.com"})
    )
    assert updated.content["host"] == "prod.example.com"

    with pytest.raises(StoreValidationError):
        await store_obj.update_doc(doc.meta.id, UpdateDocInput(content={"port": 9090}))


async def test_delete_store(store: DocVault):
    await store.create_store(StoreCreateInput(name="tmp", structure={}))
    await store.delete_store("tmp")
    with pytest.raises(StoreNotFoundError):
        await store.get_store("tmp")


# ── Vault ──────────────────────────────────────────────────────────────────────


async def test_create_store_from_folder(store: DocVault, tmp_path):
    folder = tmp_path / "docs"
    (folder / "config").mkdir(parents=True)
    (folder / "config" / "app.json").write_text('{"host": "localhost"}', "utf-8")
    (folder / "config" / "db.json").write_text('{"dsn": "postgres://..."}', "utf-8")
    (folder / "readme.json").write_text('{"text": "Hello"}', "utf-8")

    store_obj = await store.create_store(StoreCreateInput(name="auto", path=folder), creator="admin")

    assert store_obj.name == "auto"
    meta = await store_obj.get_meta()
    assert set(meta.structure.keys()) == {"config/app", "config/db", "readme"}

    docs = await store_obj.list_docs()
    assert len(docs) == 3
    assert all(d.creator == "admin" for d in docs)
    paths = {d.path for d in docs}
    assert paths == {"config/app", "config/db", "readme"}

    validation = await store.validate_store("auto")
    assert validation.valid


async def test_store_id_format(store: DocVault):
    # Standard library imports:
    import re

    store_obj = await store.create_store(
        StoreCreateInput(name="svc", structure=_STRUCTURE)
    )
    meta = await store_obj.get_meta()
    assert re.match(r"^[^:]+:[0-9a-f]{32}:[0-9a-f]{32}$", meta.id)
    assert meta.id.startswith("svc:")


async def test_folder_ingestion_defaults_to_v1(store: DocVault, tmp_path):
    folder = tmp_path / "f"
    folder.mkdir()
    (folder / "item.json").write_text('{"x": 1}', "utf-8")

    store_obj = await store.create_store(StoreCreateInput(name="v1st", path=folder))
    meta = await store_obj.get_meta()
    assert meta.version == 1


async def test_store_version_increments_on_content_change(store: DocVault, tmp_path):
    folder = tmp_path / "tpl"
    folder.mkdir()
    (folder / "a.json").write_text('{"v": 1}', "utf-8")

    store_obj1 = await store.create_store(StoreCreateInput(name="versioned", path=folder))
    meta1 = await store_obj1.get_meta()
    assert meta1.version == 1

    (folder / "a.json").write_text('{"v": 2}', "utf-8")
    store_obj2 = await store.create_store(StoreCreateInput(name="versioned", path=folder))
    meta2 = await store_obj2.get_meta()
    assert meta2.version == 2
    assert meta2.id != meta1.id


async def test_store_version_reverts_on_known_content(store: DocVault, tmp_path):
    folder = tmp_path / "tpl"
    folder.mkdir()
    (folder / "a.json").write_text('{"v": 1}', "utf-8")
    store_obj1 = await store.create_store(StoreCreateInput(name="revert", path=folder))
    meta1 = await store_obj1.get_meta()

    (folder / "a.json").write_text('{"v": 2}', "utf-8")
    store_obj2 = await store.create_store(StoreCreateInput(name="revert", path=folder))
    meta2 = await store_obj2.get_meta()
    assert meta2.version == 2

    # Revert to original content → version should go back to 1
    (folder / "a.json").write_text('{"v": 1}', "utf-8")
    store_obj3 = await store.create_store(StoreCreateInput(name="revert", path=folder))
    meta3 = await store_obj3.get_meta()
    assert meta3.version == 1
    assert meta3.id == meta1.id


async def test_store_same_content_is_noop(store: DocVault, tmp_path):
    folder = tmp_path / "tpl"
    folder.mkdir()
    (folder / "a.json").write_text('{"v": 1}', "utf-8")

    store_obj1 = await store.create_store(StoreCreateInput(name="idempotent", path=folder))
    meta1 = await store_obj1.get_meta()
    store_obj2 = await store.create_store(StoreCreateInput(name="idempotent", path=folder))
    meta2 = await store_obj2.get_meta()
    assert meta1.id == meta2.id


async def test_export_store_zip(store: DocVault, tmp_path):
    # Standard library imports:
    import io
    import json
    import zipfile

    store_obj = await store.create_store(
        StoreCreateInput(name="svc", structure=_STRUCTURE)
    )
    await store_obj.create_doc(
        CreateDocInput(content={"host": "localhost"}, creator="alice", path="config/app")
    )
    await store_obj.create_doc(
        CreateDocInput(content={"dsn": "postgres://db"}, creator="alice", path="config/database")
    )

    zip_bytes = await store.export_store_zip("svc")
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = zf.namelist()
        assert "_store.json" in names
        assert "config/app.json" in names
        assert "config/database.json" in names
        assert "docs/readme.json" not in names

        meta_data = json.loads(zf.read("_store.json"))
        assert meta_data["name"] == "svc"

        content = json.loads(zf.read("config/app.json"))
        assert content["host"] == "localhost"


async def test_export_preserves_source_extension(store: DocVault, tmp_path):
    # Standard library imports:
    import io
    import zipfile

    folder = tmp_path / "src"
    folder.mkdir()
    (folder / "readme.txt").write_text("Hello world", "utf-8")
    (folder / "data.csv").write_text("a,b\n1,2", "utf-8")
    (folder / "config.json").write_text('{"port": 8080}', "utf-8")

    store_obj = await store.create_store(
        StoreCreateInput(name="mixed", path=folder), creator="test"
    )
    assert store_obj.name == "mixed"

    zip_bytes = await store.export_store_zip("mixed")
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = zf.namelist()
        assert "readme.txt" in names
        assert "data.csv" in names
        assert "config.json" in names

        assert zf.read("readme.txt").decode() == "Hello world"
        assert zf.read("data.csv").decode() == "a,b\n1,2"

        # Standard library imports:
        import json

        assert json.loads(zf.read("config.json"))["port"] == 8080


async def test_vault_lifecycle(store: DocVault):
    vault = await store.get_vault()
    assert str(vault.version) == "0.1.0"

    vault = await store.bump_vault_version("minor")
    assert str(vault.version) == "0.2.0"

    vault = await store.bump_vault_version("major")
    assert str(vault.version) == "1.0.0"

    versions = await store.list_vault_versions()
    assert "v0.2.0" in versions
    assert "v1.0.0" in versions


async def test_deploy_store(store: DocVault):
    structure = {
        "items/a": DocSlot(
            required=True,
            json_schema={"type": "object", "required": ["title"]},
        ),
        "items/b": DocSlot(required=True),
    }
    await store.create_store(StoreCreateInput(name="widget", structure=structure))

    inp = DeployStoreInput(
        store_name="widget",
        documents=[
            DeployDocSpec(path="items/a", content={"title": "W1"}, creator="alice"),
            DeployDocSpec(path="items/b", content={"title": "W2"}, creator="alice"),
        ],
    )
    docs = await store.deploy_store(inp)
    assert len(docs) == 2
    assert {d.meta.path for d in docs} == {"items/a", "items/b"}

    result = await store.validate_store("widget")
    assert result.valid


async def test_locked_store_rejects_direct_update(store: DocVault):
    store_obj = await store.create_store(
        StoreCreateInput(name="locked", structure=_STRUCTURE, locked=True)
    )
    docs = await store_obj.deploy(
        [DeployDocSpec(path="config/app", content={"host": "x"}, creator="bot")]
    )
    doc_id = docs[0].meta.id

    with pytest.raises(StoreValidationError):
        await store_obj.update_doc(doc_id, UpdateDocInput(content={"host": "y"}))


async def test_locked_store_deploy_succeeds(store: DocVault):
    store_obj = await store.create_store(
        StoreCreateInput(name="locked2", structure={}, locked=True)
    )
    docs = await store_obj.deploy(
        [DeployDocSpec(path="item", content={"v": 1}, creator="bot")]
    )
    assert len(docs) == 1
