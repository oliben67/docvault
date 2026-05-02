from __future__ import annotations

import asyncio

import pytest
import pytest_asyncio

from docvault.config import VaultConfig
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
    SummarizationError,
    TemplateNotFoundError,
    TemplateValidationError,
    VaultNotFoundError,
)


@pytest_asyncio.fixture
async def store(tmp_path):
    cfg = VaultConfig(vault_path=tmp_path / "vault")
    s = DocVault(cfg)
    await s.init()
    return s


# ── Lifecycle ────────────────────────────────────────────────────────────────


async def test_open_existing_vault(tmp_path):
    cfg = VaultConfig(vault_path=tmp_path / "vault")
    s = DocVault(cfg)
    await s.init()
    # open() should work fine on an existing vault
    await s.open()


async def test_open_missing_vault_raises(tmp_path):
    cfg = VaultConfig(vault_path=tmp_path / "nonexistent")
    s = DocVault(cfg)
    with pytest.raises(VaultNotFoundError):
        await s.open()


async def test_init_is_idempotent(tmp_path):
    cfg = VaultConfig(vault_path=tmp_path / "vault")
    s = DocVault(cfg)
    await s.init()
    await s.init()  # second call must not raise


# ── Document edge cases ───────────────────────────────────────────────────────


async def test_create_doc_with_named_version(store: DocVault):
    inp = CreateDocInput(content={"v": 1}, creator="alice", named_version="release-1.0")
    doc = await store.create_doc(inp)
    assert doc.meta.named_version == "release-1.0"


async def test_delete_doc_nonexistent_raises(store: DocVault):
    with pytest.raises(DocumentNotFoundError):
        await store.delete_doc("nonexistent-id")


async def test_update_doc_nonexistent_raises(store: DocVault):
    with pytest.raises(DocumentNotFoundError):
        await store.update_doc("nonexistent-id", UpdateDocInput(content={"x": 1}))


async def test_update_doc_preserves_summary_when_not_provided(store: DocVault):
    doc = await store.create_doc(
        CreateDocInput(content={"k": "v"}, creator="alice", summary="Original summary")
    )
    updated = await store.update_doc(doc.meta.id, UpdateDocInput(content={"k": "new"}))
    assert updated.meta.summary == "Original summary"


async def test_update_doc_preserves_keywords_when_not_provided(store: DocVault):
    doc = await store.create_doc(
        CreateDocInput(content={"k": "v"}, creator="alice", keywords=["a", "b"])
    )
    updated = await store.update_doc(doc.meta.id, UpdateDocInput(content={"k": "new"}))
    assert updated.meta.keywords == ["a", "b"]


async def test_update_doc_replaces_keywords_when_provided(store: DocVault):
    doc = await store.create_doc(
        CreateDocInput(content={"k": "v"}, creator="alice", keywords=["old"])
    )
    updated = await store.update_doc(
        doc.meta.id, UpdateDocInput(content={"k": "new"}, keywords=["new"])
    )
    assert updated.meta.keywords == ["new"]


# ── list_docs filters ────────────────────────────────────────────────────────


async def test_list_docs_empty_vault(store: DocVault):
    metas = await store.list_docs()
    assert metas == []


async def test_list_docs_template_filter(store: DocVault):
    await store.create_template(TemplateCreateInput(name="tpl"))
    doc_with = await store.create_doc(
        CreateDocInput(content={"x": 1}, creator="alice", template="tpl")
    )
    doc_without = await store.create_doc(
        CreateDocInput(content={"y": 2}, creator="alice")
    )
    metas = await store.list_docs(template="tpl")
    ids = {m.id for m in metas}
    assert doc_with.meta.id in ids
    assert doc_without.meta.id not in ids


# ── History ──────────────────────────────────────────────────────────────────


async def test_get_doc_history_max_count(store: DocVault):
    doc = await store.create_doc(CreateDocInput(content={"v": 0}, creator="alice"))
    for i in range(1, 4):
        await store.update_doc(doc.meta.id, UpdateDocInput(content={"v": i}))
    history = await store.get_doc_history(doc.meta.id, max_count=2)
    assert len(history) == 2


async def test_get_doc_at_ref_invalid_raises(store: DocVault):
    doc = await store.create_doc(CreateDocInput(content={"v": 1}, creator="alice"))
    with pytest.raises(Exception):
        await store.get_doc_at_ref(doc.meta.id, "deadbeefdeadbeef")


# ── Templates ────────────────────────────────────────────────────────────────


async def test_list_templates(store: DocVault):
    await store.create_template(TemplateCreateInput(name="alpha"))
    await store.create_template(TemplateCreateInput(name="beta"))
    await store.create_template(TemplateCreateInput(name="gamma"))
    templates = await store.list_templates()
    names = {t.name for t in templates}
    assert {"alpha", "beta", "gamma"} == names


async def test_get_template_nonexistent_raises(store: DocVault):
    with pytest.raises(TemplateNotFoundError):
        await store.get_template("no-such-template")


async def test_export_template_zip_nonexistent_raises(store: DocVault):
    with pytest.raises(TemplateNotFoundError):
        await store.export_template_zip("no-such-template")


async def test_deploy_vault_schema_violation_raises(store: DocVault):
    structure = {
        "slot": DocSlot(
            required=True,
            json_schema={
                "type": "object",
                "required": ["name"],
                "properties": {"name": {"type": "string"}},
            },
        )
    }
    await store.create_template(TemplateCreateInput(name="strict", structure=structure))
    inp = DeployVaultInput(
        template_name="strict",
        documents=[
            DeployDocSpec(
                path="slot",
                content={"wrong_field": 123},
                creator="bot",
            )
        ],
    )
    with pytest.raises(TemplateValidationError):
        await store.deploy_vault(inp)


# ── Vault version ─────────────────────────────────────────────────────────────


async def test_bump_vault_version_patch(store: DocVault):
    vault = await store.get_vault()
    assert str(vault.version) == "0.1.0"
    bumped = await store.bump_vault_version("patch")
    assert str(bumped.version) == "0.1.1"
    # major/minor unchanged
    assert bumped.version.major == 0
    assert bumped.version.minor == 1


# ── Summarize without LLM ────────────────────────────────────────────────────


async def test_summarize_doc_without_llm_raises(store: DocVault):
    doc = await store.create_doc(CreateDocInput(content={"k": "v"}, creator="alice"))
    with pytest.raises(SummarizationError):
        await store.summarize_doc(doc.meta.id)


# ── Concurrency ───────────────────────────────────────────────────────────────


async def test_concurrent_creates_all_succeed(store: DocVault):
    inputs = [CreateDocInput(content={"i": i}, creator="bot") for i in range(15)]
    docs = await asyncio.gather(*[store.create_doc(inp) for inp in inputs])
    ids = {d.meta.id for d in docs}
    assert len(ids) == 15  # all unique


async def test_concurrent_updates_all_succeed(store: DocVault):
    doc = await store.create_doc(CreateDocInput(content={"v": 0}, creator="alice"))
    updates = [UpdateDocInput(content={"v": i}) for i in range(1, 8)]
    results = await asyncio.gather(*[store.update_doc(doc.meta.id, u) for u in updates])
    assert len(results) == 7
    # Final state should be one of the updates
    final = await store.get_doc(doc.meta.id)
    assert "v" in final.content
