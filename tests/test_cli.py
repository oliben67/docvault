# Future imports (must occur at the beginning of the file):
from __future__ import annotations

# Standard library imports:
import json
from pathlib import Path

# Third party imports:
import pytest
from typer.testing import CliRunner

# Local imports:
from docvault.cli.main import app

runner = CliRunner()


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def vault(tmp_path):
    """Initialize a vault and return the path to its config file."""
    cfg_path = tmp_path / "docvault.json"
    cfg_path.write_text(
        json.dumps({"vault_path": str(tmp_path / "vault")}), encoding="utf-8"
    )
    result = runner.invoke(app, ["init", "--config", str(cfg_path)])
    assert result.exit_code == 0, result.output
    return cfg_path


def _cfg(cfg_path: Path) -> list[str]:
    return ["--config", str(cfg_path)]


def _create_doc(cfg_path: Path, content: dict, creator: str = "alice") -> str:
    """Helper: create a doc and return its ID."""
    content_file = cfg_path.parent / "content.json"
    content_file.write_text(json.dumps(content), encoding="utf-8")
    result = runner.invoke(
        app,
        ["docs", "create", "--creator", creator, "--file", str(content_file)]
        + _cfg(cfg_path),
    )
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    json_start = next((i for i, line in enumerate(lines) if line.strip() == "{"), None)
    if json_start is not None:
        obj = json.loads("\n".join(lines[json_start:]))
        return obj["meta"]["id"]
    raise ValueError(f"Could not find doc ID in output:\n{result.output}")


# ── init ─────────────────────────────────────────────────────────────────────


def test_init_creates_vault(tmp_path):
    vault_path = tmp_path / "new-vault"
    result = runner.invoke(app, ["init", str(vault_path)])
    assert result.exit_code == 0
    assert "Vault ready" in result.output
    assert (vault_path / ".git").exists()


def test_init_is_idempotent(tmp_path):
    vault_path = tmp_path / "vault"
    runner.invoke(app, ["init", str(vault_path)])
    result = runner.invoke(app, ["init", str(vault_path)])
    assert result.exit_code == 0


# ── docs list ────────────────────────────────────────────────────────────────


def test_docs_list_empty_vault(vault):
    result = runner.invoke(app, ["docs", "list"] + _cfg(vault))
    assert result.exit_code == 0
    assert "No documents" in result.output


def test_docs_list_shows_created_doc(vault, tmp_path):
    content_file = tmp_path / "c.json"
    content_file.write_text('{"key": "value"}', encoding="utf-8")
    runner.invoke(
        app,
        ["docs", "create", "--creator", "alice", "--file", str(content_file)]
        + _cfg(vault),
    )
    result = runner.invoke(app, ["docs", "list"] + _cfg(vault))
    assert result.exit_code == 0
    assert "alice" in result.output


# ── docs create ──────────────────────────────────────────────────────────────


def test_docs_create_success(vault, tmp_path):
    content_file = tmp_path / "c.json"
    content_file.write_text('{"host": "localhost"}', encoding="utf-8")
    result = runner.invoke(
        app,
        ["docs", "create", "--creator", "alice", "--file", str(content_file)]
        + _cfg(vault),
    )
    assert result.exit_code == 0
    assert "Created" in result.output


def test_docs_create_with_keywords(vault, tmp_path):
    content_file = tmp_path / "c.json"
    content_file.write_text('{"x": 1}', encoding="utf-8")
    result = runner.invoke(
        app,
        [
            "docs",
            "create",
            "--creator",
            "alice",
            "--file",
            str(content_file),
            "--keywords",
            "alpha,beta",
        ]
        + _cfg(vault),
    )
    assert result.exit_code == 0
    assert "Created" in result.output


def test_docs_create_invalid_json_exits_1(vault, tmp_path):
    bad_file = tmp_path / "bad.json"
    bad_file.write_text("not json", encoding="utf-8")
    result = runner.invoke(
        app,
        ["docs", "create", "--creator", "alice", "--file", str(bad_file)] + _cfg(vault),
    )
    assert result.exit_code == 1


# ── docs get ─────────────────────────────────────────────────────────────────


def test_docs_get_existing(vault, tmp_path):
    content_file = tmp_path / "c.json"
    content_file.write_text('{"greet": "hello"}', encoding="utf-8")
    create_result = runner.invoke(
        app,
        ["docs", "create", "--creator", "alice", "--file", str(content_file)]
        + _cfg(vault),
    )
    output_json = None
    lines = create_result.output.splitlines()
    for i, line in enumerate(lines):
        if line.strip() == "{":
            try:
                output_json = json.loads("\n".join(lines[i:]))
                break
            except json.JSONDecodeError:
                pass
    assert output_json is not None
    doc_id = output_json["meta"]["id"]

    result = runner.invoke(app, ["docs", "get", doc_id] + _cfg(vault))
    assert result.exit_code == 0
    assert "greet" in result.output


def test_docs_get_nonexistent_exits_1(vault):
    result = runner.invoke(app, ["docs", "get", "no-such-id"] + _cfg(vault))
    assert result.exit_code == 1


# ── docs update ──────────────────────────────────────────────────────────────


def test_docs_update_success(vault, tmp_path):
    f = tmp_path / "c.json"
    f.write_text('{"v": 1}', encoding="utf-8")
    create_r = runner.invoke(
        app, ["docs", "create", "--creator", "alice", "--file", str(f)] + _cfg(vault)
    )
    lines = create_r.output.splitlines()
    doc_id = None
    for i, line in enumerate(lines):
        if line.strip() == "{":
            try:
                obj = json.loads("\n".join(lines[i:]))
                doc_id = obj["meta"]["id"]
                break
            except Exception:
                pass
    assert doc_id

    f.write_text('{"v": 2}', encoding="utf-8")
    result = runner.invoke(
        app, ["docs", "update", doc_id, "--file", str(f)] + _cfg(vault)
    )
    assert result.exit_code == 0
    assert "Updated" in result.output


def test_docs_update_nonexistent_exits_1(vault, tmp_path):
    f = tmp_path / "c.json"
    f.write_text('{"v": 99}', encoding="utf-8")
    result = runner.invoke(
        app, ["docs", "update", "no-such-id", "--file", str(f)] + _cfg(vault)
    )
    assert result.exit_code == 1


# ── docs delete ──────────────────────────────────────────────────────────────


def test_docs_delete_with_force(vault, tmp_path):
    f = tmp_path / "c.json"
    f.write_text('{"x": 1}', encoding="utf-8")
    create_r = runner.invoke(
        app, ["docs", "create", "--creator", "alice", "--file", str(f)] + _cfg(vault)
    )
    lines = create_r.output.splitlines()
    doc_id = None
    for i, line in enumerate(lines):
        if line.strip() == "{":
            try:
                doc_id = json.loads("\n".join(lines[i:]))["meta"]["id"]
                break
            except Exception:
                pass
    assert doc_id

    result = runner.invoke(app, ["docs", "delete", doc_id, "--force"] + _cfg(vault))
    assert result.exit_code == 0
    assert "Deleted" in result.output


def test_docs_delete_nonexistent_exits_1(vault):
    result = runner.invoke(
        app, ["docs", "delete", "no-such-id", "--force"] + _cfg(vault)
    )
    assert result.exit_code == 1


# ── docs history ─────────────────────────────────────────────────────────────


def test_docs_history_shows_commits(vault, tmp_path):
    f = tmp_path / "c.json"
    f.write_text('{"v": 1}', encoding="utf-8")
    create_r = runner.invoke(
        app, ["docs", "create", "--creator", "alice", "--file", str(f)] + _cfg(vault)
    )
    lines = create_r.output.splitlines()
    doc_id = None
    for i, line in enumerate(lines):
        if line.strip() == "{":
            try:
                doc_id = json.loads("\n".join(lines[i:]))["meta"]["id"]
                break
            except Exception:
                pass
    assert doc_id

    result = runner.invoke(app, ["docs", "history", doc_id] + _cfg(vault))
    assert result.exit_code == 0
    assert len(result.output) > 10


def test_docs_history_nonexistent_exits_1(vault):
    result = runner.invoke(app, ["docs", "history", "no-such-id"] + _cfg(vault))
    assert result.exit_code == 1


# ── docs summarize ────────────────────────────────────────────────────────────


def test_docs_summarize_no_llm_exits_1(vault, tmp_path):
    f = tmp_path / "c.json"
    f.write_text('{"k": "v"}', encoding="utf-8")
    create_r = runner.invoke(
        app, ["docs", "create", "--creator", "alice", "--file", str(f)] + _cfg(vault)
    )
    lines = create_r.output.splitlines()
    doc_id = None
    for i, line in enumerate(lines):
        if line.strip() == "{":
            try:
                doc_id = json.loads("\n".join(lines[i:]))["meta"]["id"]
                break
            except Exception:
                pass
    assert doc_id
    result = runner.invoke(app, ["docs", "summarize", doc_id] + _cfg(vault))
    assert result.exit_code == 1


# ── docs summarize-all ────────────────────────────────────────────────────────


def test_docs_summarize_all_no_llm_no_crash(vault):
    result = runner.invoke(app, ["docs", "summarize-all"] + _cfg(vault))
    assert result.exit_code in (0, 1)


# ── stores list ──────────────────────────────────────────────────────────────


def test_stores_list_empty(vault):
    result = runner.invoke(app, ["stores", "list"] + _cfg(vault))
    assert result.exit_code == 0
    assert "No stores" in result.output


def test_stores_list_shows_created(vault, tmp_path):
    struct_file = tmp_path / "struct.json"
    struct_file.write_text(
        '{"cfg": {"description": "config", "required": true}}', encoding="utf-8"
    )
    runner.invoke(
        app, ["stores", "create", "mystore", "--file", str(struct_file)] + _cfg(vault)
    )
    result = runner.invoke(app, ["stores", "list"] + _cfg(vault))
    assert result.exit_code == 0
    assert "mystore" in result.output


# ── stores get ────────────────────────────────────────────────────────────────


def test_stores_get_existing(vault, tmp_path):
    struct_file = tmp_path / "struct.json"
    struct_file.write_text('{"cfg": {"required": true}}', encoding="utf-8")
    runner.invoke(
        app, ["stores", "create", "mystore", "--file", str(struct_file)] + _cfg(vault)
    )
    result = runner.invoke(app, ["stores", "get", "mystore"] + _cfg(vault))
    assert result.exit_code == 0
    assert "mystore" in result.output


def test_stores_get_nonexistent_exits_1(vault):
    result = runner.invoke(app, ["stores", "get", "no-such-store"] + _cfg(vault))
    assert result.exit_code == 1


# ── stores create ─────────────────────────────────────────────────────────────


def test_stores_create_minimal(vault):
    result = runner.invoke(app, ["stores", "create", "minimal"] + _cfg(vault))
    assert result.exit_code == 0
    assert "minimal" in result.output


def test_stores_create_with_path_folder(vault, tmp_path):
    folder = tmp_path / "src"
    folder.mkdir()
    (folder / "config.json").write_text('{"port": 8080}', encoding="utf-8")
    result = runner.invoke(
        app,
        ["stores", "create", "folder-st", "--path", str(folder)] + _cfg(vault),
    )
    assert result.exit_code == 0
    assert "folder-st" in result.output


def test_stores_create_with_path_single_file(vault, tmp_path):
    single = tmp_path / "settings.json"
    single.write_text('{"debug": true}', encoding="utf-8")
    result = runner.invoke(
        app,
        ["stores", "create", "file-st", "--path", str(single)] + _cfg(vault),
    )
    assert result.exit_code == 0
    assert "file-st" in result.output


def test_stores_create_with_description(vault, tmp_path):
    folder = tmp_path / "s"
    folder.mkdir()
    (folder / "a.json").write_text('{"x": 1}', encoding="utf-8")
    result = runner.invoke(
        app,
        [
            "stores",
            "create",
            "described-st",
            "--path",
            str(folder),
            "--description",
            "My description",
        ]
        + _cfg(vault),
    )
    assert result.exit_code == 0


def test_stores_create_locked(vault):
    result = runner.invoke(
        app, ["stores", "create", "locked-st", "--locked"] + _cfg(vault)
    )
    assert result.exit_code == 0
    assert "locked-st" in result.output


# ── stores delete ─────────────────────────────────────────────────────────────


def test_stores_delete_with_force(vault, tmp_path):
    runner.invoke(app, ["stores", "create", "to-delete"] + _cfg(vault))
    result = runner.invoke(
        app, ["stores", "delete", "to-delete", "--force"] + _cfg(vault)
    )
    assert result.exit_code == 0
    assert "Deleted" in result.output


def test_stores_delete_nonexistent_exits_1(vault):
    result = runner.invoke(
        app, ["stores", "delete", "no-such-store", "--force"] + _cfg(vault)
    )
    assert result.exit_code == 1


# ── vault info ────────────────────────────────────────────────────────────────


def test_vault_info_shows_version(vault):
    result = runner.invoke(app, ["vault", "info"] + _cfg(vault))
    assert result.exit_code == 0
    assert "Version" in result.output
    assert "0.1.0" in result.output


# ── vault versions ────────────────────────────────────────────────────────────


def test_vault_versions_empty(vault):
    result = runner.invoke(app, ["vault", "versions"] + _cfg(vault))
    assert result.exit_code == 0
    assert "No version tags" in result.output


def test_vault_versions_after_bump(vault):
    runner.invoke(app, ["vault", "bump", "minor"] + _cfg(vault))
    result = runner.invoke(app, ["vault", "versions"] + _cfg(vault))
    assert result.exit_code == 0
    assert "v0.2.0" in result.output


# ── vault bump ────────────────────────────────────────────────────────────────


def test_vault_bump_minor(vault):
    result = runner.invoke(app, ["vault", "bump", "minor"] + _cfg(vault))
    assert result.exit_code == 0
    assert "0.2.0" in result.output


def test_vault_bump_major(vault):
    result = runner.invoke(app, ["vault", "bump", "major"] + _cfg(vault))
    assert result.exit_code == 0
    assert "1.0.0" in result.output


def test_vault_bump_invalid_kind_exits_1(vault):
    result = runner.invoke(app, ["vault", "bump", "super"] + _cfg(vault))
    assert result.exit_code == 1


# ── stores docs deploy ────────────────────────────────────────────────────────


def test_stores_docs_deploy(vault, tmp_path):
    runner.invoke(app, ["stores", "create", "deploy-st"] + _cfg(vault))

    specs = [{"path": "slot1", "content": {"k": "v"}, "creator": "bot"}]
    spec_file = tmp_path / "specs.json"
    spec_file.write_text(json.dumps(specs), encoding="utf-8")

    result = runner.invoke(
        app,
        ["stores", "docs", "deploy", "deploy-st", "--file", str(spec_file)]
        + _cfg(vault),
    )
    assert result.exit_code == 0
    assert "Deployed 1" in result.output


# ── config show ──────────────────────────────────────────────────────────────


def test_config_show(vault):
    result = runner.invoke(app, ["config", "show"] + _cfg(vault))
    assert result.exit_code == 0
    assert "vault_path" in result.output
    assert "auth_mode" in result.output
    assert "none" in result.output


def test_config_show_masks_api_key(tmp_path):
    cfg_path = tmp_path / "docvault.json"
    cfg_path.write_text(
        json.dumps(
            {
                "vault_path": str(tmp_path / "vault"),
                "llm_api_key": "sk-super-secret",
            }
        ),
        encoding="utf-8",
    )
    result = runner.invoke(app, ["config", "show", "--config", str(cfg_path)])
    assert result.exit_code == 0
    assert "sk-super-secret" not in result.output
    assert "***" in result.output


# ── config generate-key ───────────────────────────────────────────────────────


def test_config_generate_key_one(vault):
    result = runner.invoke(app, ["config", "generate-key"])
    assert result.exit_code == 0
    key = result.output.strip()
    assert len(key) == 43


def test_config_generate_key_multiple(vault):
    result = runner.invoke(app, ["config", "generate-key", "-n", "5"])
    assert result.exit_code == 0
    keys = [line.strip() for line in result.output.strip().splitlines() if line.strip()]
    assert len(keys) == 5
    assert len(set(keys)) == 5
