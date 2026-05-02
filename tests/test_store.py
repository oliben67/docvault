from __future__ import annotations

import pytest

from docvault.core.document import CreateDocInput, UpdateDocInput
from docvault.core.store import DocVault
from docvault.core.template import (
    DeployDocSpec,
    DeployVaultInput,
    DocSlot,
    TemplateCreateInput,
)
from docvault.exceptions import (
    DocumentNotFoundError,
    TemplateNotFoundError,
    TemplateValidationError,
)

# ── Template fixture ───────────────────────────────────────────────────────────

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


# ── Templates ──────────────────────────────────────────────────────────────────


async def test_template_slot_content_validation(store: DocVault):
    await store.create_template(TemplateCreateInput(name="svc", structure=_STRUCTURE))

    # Valid content for a slot with json_schema
    doc = await store.create_doc(
        CreateDocInput(
            content={"host": "localhost", "port": 8080},
            creator="alice",
            template="svc",
            path="config/app",
        )
    )
    assert doc.meta.template == "svc"
    assert doc.meta.path == "config/app"

    # Content that violates the slot's json_schema
    with pytest.raises(TemplateValidationError):
        await store.create_doc(
            CreateDocInput(
                content={"port": 8080},  # missing required "host"
                creator="alice",
                template="svc",
                path="config/app",
            )
        )

    # Path not defined in the template — allowed, will appear as 'extra' in validation
    unknown_doc = await store.create_doc(
        CreateDocInput(
            content={"x": 1}, creator="alice", template="svc", path="nonexistent/slot"
        )
    )
    assert unknown_doc.meta.path == "nonexistent/slot"


async def test_template_validate_structure(store: DocVault):
    await store.create_template(TemplateCreateInput(name="svc", structure=_STRUCTURE))

    # Initially unsatisfied — both required slots are missing
    result = await store.validate_template("svc")
    assert not result.valid
    assert "config/app" in result.missing
    assert "config/database" in result.missing

    # Fill config/app
    await store.create_doc(
        CreateDocInput(
            content={"host": "localhost"},
            creator="alice",
            template="svc",
            path="config/app",
        )
    )
    result = await store.validate_template("svc")
    assert not result.valid
    assert "config/app" not in result.missing
    assert "config/database" in result.missing

    # Fill config/database — optional docs/readme never blocks validity
    await store.create_doc(
        CreateDocInput(
            content={"host": "db.internal"},
            creator="alice",
            template="svc",
            path="config/database",
        )
    )
    result = await store.validate_template("svc")
    assert result.valid
    assert result.missing == []
    assert "docs/readme" not in result.missing


async def test_template_validate_extra_docs(store: DocVault):
    await store.create_template(TemplateCreateInput(name="svc", structure=_STRUCTURE))

    await store.create_doc(
        CreateDocInput(
            content={"x": 1}, creator="alice", template="svc", path="unknown/slot"
        )
    )

    result = await store.validate_template("svc")
    assert "unknown/slot" in result.extra


async def test_template_no_path_skips_validation(store: DocVault):
    """A document tagged with a template but no path bypasses slot validation."""
    await store.create_template(TemplateCreateInput(name="svc", structure=_STRUCTURE))

    doc = await store.create_doc(
        CreateDocInput(content={"anything": True}, creator="alice", template="svc")
    )
    assert doc.meta.template == "svc"
    assert doc.meta.path is None


async def test_update_doc_validates_slot(store: DocVault):
    await store.create_template(TemplateCreateInput(name="svc", structure=_STRUCTURE))
    doc = await store.create_doc(
        CreateDocInput(
            content={"host": "localhost"},
            creator="alice",
            template="svc",
            path="config/app",
        )
    )

    updated = await store.update_doc(
        doc.meta.id, UpdateDocInput(content={"host": "prod.example.com"})
    )
    assert updated.content["host"] == "prod.example.com"

    with pytest.raises(TemplateValidationError):
        await store.update_doc(doc.meta.id, UpdateDocInput(content={"port": 9090}))


async def test_delete_template(store: DocVault):
    await store.create_template(TemplateCreateInput(name="tmp", structure={}))
    await store.delete_template("tmp")
    with pytest.raises(TemplateNotFoundError):
        await store.get_template("tmp")


# ── Vault ──────────────────────────────────────────────────────────────────────


async def test_create_template_from_folder(store: DocVault, tmp_path):
    folder = tmp_path / "docs"
    (folder / "config").mkdir(parents=True)
    (folder / "config" / "app.json").write_text('{"host": "localhost"}', "utf-8")
    (folder / "config" / "db.json").write_text('{"dsn": "postgres://..."}', "utf-8")
    (folder / "readme.json").write_text('{"text": "Hello"}', "utf-8")

    inp = TemplateCreateInput(name="auto", folder_path=folder)
    result = await store.create_template(inp, creator="admin")

    assert result.template.name == "auto"
    assert set(result.template.structure.keys()) == {
        "config/app",
        "config/db",
        "readme",
    }
    assert len(result.documents) == 3
    assert all(d.meta.creator == "admin" for d in result.documents)
    assert all(d.meta.template == "auto" for d in result.documents)

    paths = {d.meta.path for d in result.documents}
    assert paths == {"config/app", "config/db", "readme"}

    # Template should be satisfied immediately after ingestion
    validation = await store.validate_template("auto")
    assert validation.valid


async def test_template_id_format(store: DocVault):
    result = await store.create_template(
        TemplateCreateInput(name="svc", structure=_STRUCTURE)
    )
    tpl = result.template
    parts = tpl.id.split("/")
    assert parts[0] == "svc"
    assert parts[1] == "v0.1.0"  # default version, no folder_path
    assert len(parts[2]) == 64  # full SHA-256 hex string


async def test_folder_ingestion_defaults_to_v1(store: DocVault, tmp_path):
    folder = tmp_path / "f"
    folder.mkdir()
    (folder / "item.json").write_text('{"x": 1}', "utf-8")

    result = await store.create_template(
        TemplateCreateInput(name="v1tpl", folder_path=folder)
    )
    assert result.template.id.startswith("v1tpl/v1.0.0/")


async def test_template_version_override(store: DocVault, tmp_path):
    from docvault.core.vault_meta import VaultVersion

    folder = tmp_path / "f"
    folder.mkdir()
    (folder / "a.json").write_text('{"a": 1}', "utf-8")

    inp = TemplateCreateInput(
        name="custom",
        folder_path=folder,
        version=VaultVersion(major=2, minor=3, patch=4),
    )
    result = await store.create_template(inp)
    assert result.template.id.startswith("custom/v2.3.4/")


async def test_export_template_zip(store: DocVault, tmp_path):
    import io
    import json
    import zipfile

    await store.create_template(TemplateCreateInput(name="svc", structure=_STRUCTURE))
    await store.create_doc(
        CreateDocInput(
            content={"host": "localhost"},
            creator="alice",
            template="svc",
            path="config/app",
        )
    )
    await store.create_doc(
        CreateDocInput(
            content={"dsn": "postgres://db"},
            creator="alice",
            template="svc",
            path="config/database",
        )
    )

    zip_bytes = await store.export_template_zip("svc")
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = zf.namelist()
        assert "_template.json" in names
        assert "config/app.json" in names
        assert "config/database.json" in names
        # docs/readme has no document yet — not in zip
        assert "docs/readme.json" not in names

        meta = json.loads(zf.read("_template.json"))
        assert meta["name"] == "svc"
        assert meta["id"].startswith("svc/v0.1.0/")

        content = json.loads(zf.read("config/app.json"))
        assert content["host"] == "localhost"


async def test_export_preserves_source_extension(store: DocVault, tmp_path):
    import io
    import zipfile

    folder = tmp_path / "src"
    folder.mkdir()
    (folder / "readme.txt").write_text("Hello world", "utf-8")
    (folder / "data.csv").write_text("a,b\n1,2", "utf-8")
    (folder / "config.json").write_text('{"port": 8080}', "utf-8")

    result = await store.create_template(
        TemplateCreateInput(name="mixed", folder_path=folder), creator="test"
    )
    assert result.template.name == "mixed"

    zip_bytes = await store.export_template_zip("mixed")
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = zf.namelist()
        assert "readme.txt" in names
        assert "data.csv" in names
        assert "config.json" in names

        assert zf.read("readme.txt").decode() == "Hello world"
        assert zf.read("data.csv").decode() == "a,b\n1,2"

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


async def test_deploy_vault(store: DocVault):
    structure = {
        "items/a": DocSlot(
            required=True,
            json_schema={"type": "object", "required": ["title"]},
        ),
        "items/b": DocSlot(required=True),
    }
    await store.create_template(TemplateCreateInput(name="widget", structure=structure))

    inp = DeployVaultInput(
        template_name="widget",
        documents=[
            DeployDocSpec(path="items/a", content={"title": "W1"}, creator="alice"),
            DeployDocSpec(path="items/b", content={"title": "W2"}, creator="alice"),
        ],
    )
    docs = await store.deploy_vault(inp)
    assert len(docs) == 2
    assert all(d.meta.template == "widget" for d in docs)
    assert {d.meta.path for d in docs} == {"items/a", "items/b"}

    result = await store.validate_template("widget")
    assert result.valid
