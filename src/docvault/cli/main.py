from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from docvault.config import load_config
from docvault.core.document import CreateDocInput, UpdateDocInput
from docvault.core.store import DocVault
from docvault.core.template import DeployDocSpec, DeployVaultInput, TemplateCreateInput
from docvault.exceptions import DocVaultError

app = typer.Typer(
    name="docvault", help="Git-backed JSON document manager", no_args_is_help=True
)
docs_app = typer.Typer(help="Manage documents", no_args_is_help=True)
templates_app = typer.Typer(help="Manage templates", no_args_is_help=True)
vault_app = typer.Typer(help="Manage vault metadata", no_args_is_help=True)
config_app = typer.Typer(help="Config and key management", no_args_is_help=True)
tools_app = typer.Typer(help="Host-system tools (deploy, etc.)", no_args_is_help=True)

app.add_typer(docs_app, name="docs")
app.add_typer(templates_app, name="templates")
app.add_typer(vault_app, name="vault")
app.add_typer(config_app, name="config")
app.add_typer(tools_app, name="tools")

out = Console()
err = Console(stderr=True, style="bold red")

_CFG_OPT = typer.Option(
    None, "--config", "-c", help="Path to docvault.json config file"
)


# ── Helpers ─────────────────────────────────────────────────────────────────


def _load_json(file: Path) -> Any:
    text = sys.stdin.read() if str(file) == "-" else file.read_text("utf-8")
    return json.loads(text)


async def _open(config_file: Path | None) -> DocVault:
    store = DocVault(load_config(config_file))
    await store.open()
    return store


def _run(coro) -> Any:  # type: ignore[type-arg]
    return asyncio.run(coro)


def _abort(exc: Exception) -> None:
    err.print(f"Error: {exc}")
    raise typer.Exit(1)


# ── Top-level ────────────────────────────────────────────────────────────────


@app.command()
def init(
    path: Path = typer.Argument(
        Path("."), help="Vault directory (default: current dir)"
    ),
    config_file: Path | None = _CFG_OPT,
) -> None:
    """Initialize a new vault (idempotent)."""
    try:
        cfg = load_config(config_file)
        if str(path) != ".":
            cfg = cfg.model_copy(update={"vault_path": path.expanduser().resolve()})
        store = DocVault(cfg)
        _run(store.init())
        out.print(f"[green]✓[/green] Vault ready at [bold]{cfg.vault_path}[/bold]")
    except DocVaultError as exc:
        _abort(exc)


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind address"),
    port: int = typer.Option(8000, "--port", "-p", help="Port number"),
    config_file: Path | None = _CFG_OPT,
) -> None:
    """Start the REST API server."""
    try:
        import uvicorn

        from docvault.api.app import create_app

        uvicorn.run(create_app(load_config(config_file)), host=host, port=port)
    except DocVaultError as exc:
        _abort(exc)


# ── Documents ────────────────────────────────────────────────────────────────


@docs_app.command("list")
def docs_list(
    template: str | None = typer.Option(
        None, "--template", "-t", help="Filter by template"
    ),
    creator: str | None = typer.Option(None, "--creator", help="Filter by creator"),
    keywords: str | None = typer.Option(
        None, "--keywords", help="Comma-separated keywords"
    ),
    config_file: Path | None = _CFG_OPT,
) -> None:
    """List documents."""
    try:
        kw = [k.strip() for k in keywords.split(",")] if keywords else None

        async def _go() -> None:
            store = await _open(config_file)
            metas = await store.list_docs(
                keywords=kw, creator=creator, template=template
            )
            if not metas:
                out.print("[dim]No documents found.[/dim]")
                return
            table = Table("ID", "Template", "Creator", "Summary", "Updated")
            for m in metas:
                summary = (
                    (m.summary[:48] + "…") if len(m.summary) > 50 else m.summary or "-"
                )
                table.add_row(
                    m.id[:12],
                    m.template or "-",
                    m.creator,
                    summary,
                    m.updated_at.strftime("%Y-%m-%d %H:%M"),
                )
            out.print(table)

        _run(_go())
    except DocVaultError as exc:
        _abort(exc)


@docs_app.command("get")
def docs_get(
    doc_id: str = typer.Argument(..., help="Document ID"),
    config_file: Path | None = _CFG_OPT,
) -> None:
    """Print a document as JSON."""
    try:

        async def _go() -> None:
            store = await _open(config_file)
            doc = await store.get_doc(doc_id)
            out.print_json(json.dumps(doc.model_dump(mode="json")))

        _run(_go())
    except DocVaultError as exc:
        _abort(exc)


@docs_app.command("create")
def docs_create(
    creator: str = typer.Option(..., "--creator", help="Creator name"),
    file: Path = typer.Option(
        ..., "--file", "-f", help="JSON content file (- for stdin)"
    ),
    template: str | None = typer.Option(None, "--template", "-t"),
    summary: str = typer.Option("", "--summary", "-s"),
    keywords: str | None = typer.Option(None, "--keywords", help="Comma-separated"),
    config_file: Path | None = _CFG_OPT,
) -> None:
    """Create a document from a JSON file."""
    try:
        content = _load_json(file)
        kw = [k.strip() for k in keywords.split(",")] if keywords else []
        inp = CreateDocInput(
            content=content,
            creator=creator,
            template=template,
            summary=summary,
            keywords=kw,
        )

        async def _go() -> None:
            store = await _open(config_file)
            doc = await store.create_doc(inp)
            out.print(f"[green]✓[/green] Created [bold]{doc.meta.id}[/bold]")
            out.print_json(json.dumps(doc.model_dump(mode="json")))

        _run(_go())
    except (DocVaultError, json.JSONDecodeError) as exc:
        _abort(exc)


@docs_app.command("update")
def docs_update(
    doc_id: str = typer.Argument(..., help="Document ID"),
    file: Path = typer.Option(
        ..., "--file", "-f", help="JSON content file (- for stdin)"
    ),
    summary: str | None = typer.Option(None, "--summary", "-s"),
    keywords: str | None = typer.Option(None, "--keywords"),
    config_file: Path | None = _CFG_OPT,
) -> None:
    """Replace a document's content."""
    try:
        content = _load_json(file)
        kw = [k.strip() for k in keywords.split(",")] if keywords else None
        inp = UpdateDocInput(content=content, summary=summary, keywords=kw)

        async def _go() -> None:
            store = await _open(config_file)
            doc = await store.update_doc(doc_id, inp)
            out.print(f"[green]✓[/green] Updated [bold]{doc_id}[/bold]")
            out.print_json(json.dumps(doc.model_dump(mode="json")))

        _run(_go())
    except (DocVaultError, json.JSONDecodeError) as exc:
        _abort(exc)


@docs_app.command("delete")
def docs_delete(
    doc_id: str = typer.Argument(..., help="Document ID"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation prompt"),
    config_file: Path | None = _CFG_OPT,
) -> None:
    """Delete a document."""
    if not force:
        typer.confirm(f"Delete document {doc_id!r}?", abort=True)
    try:

        async def _go() -> None:
            store = await _open(config_file)
            await store.delete_doc(doc_id)
            out.print(f"[green]✓[/green] Deleted [bold]{doc_id}[/bold]")

        _run(_go())
    except DocVaultError as exc:
        _abort(exc)


@docs_app.command("history")
def docs_history(
    doc_id: str = typer.Argument(..., help="Document ID"),
    max_count: int = typer.Option(20, "--max", "-n", help="Maximum commits"),
    config_file: Path | None = _CFG_OPT,
) -> None:
    """Show a document's git commit history."""
    try:

        async def _go() -> None:
            store = await _open(config_file)
            commits = await store.get_doc_history(doc_id, max_count=max_count)
            if not commits:
                out.print("[dim]No history found.[/dim]")
                return
            table = Table("SHA", "Author", "Timestamp", "Message")
            for c in commits:
                table.add_row(
                    c.sha,
                    c.author,
                    c.timestamp.strftime("%Y-%m-%d %H:%M"),
                    c.message[:70],
                )
            out.print(table)

        _run(_go())
    except DocVaultError as exc:
        _abort(exc)


@docs_app.command("at")
def docs_at(
    doc_id: str = typer.Argument(..., help="Document ID"),
    ref: str = typer.Argument(..., help="Git ref (commit SHA, tag, branch)"),
    config_file: Path | None = _CFG_OPT,
) -> None:
    """Show a document at a specific git ref."""
    try:

        async def _go() -> None:
            store = await _open(config_file)
            doc = await store.get_doc_at_ref(doc_id, ref)
            out.print_json(json.dumps(doc.model_dump(mode="json")))

        _run(_go())
    except DocVaultError as exc:
        _abort(exc)


@docs_app.command("summarize")
def docs_summarize(
    doc_id: str = typer.Argument(..., help="Document ID"),
    overwrite: bool = typer.Option(
        False, "--overwrite", help="Overwrite existing summary"
    ),
    config_file: Path | None = _CFG_OPT,
) -> None:
    """Infer summary and keywords for a document using the LLM."""
    try:

        async def _go() -> None:
            store = await _open(config_file)
            doc = await store.summarize_doc(doc_id, overwrite=overwrite)
            out.print(f"[green]Summary:[/green]  {doc.meta.summary}")
            out.print(f"[green]Keywords:[/green] {', '.join(doc.meta.keywords)}")

        _run(_go())
    except DocVaultError as exc:
        _abort(exc)


@docs_app.command("summarize-all")
def docs_summarize_all(
    overwrite: bool = typer.Option(
        False, "--overwrite", help="Re-summarize docs that already have a summary"
    ),
    config_file: Path | None = _CFG_OPT,
) -> None:
    """Infer summary and keywords for every document that is missing them."""
    try:

        async def _go() -> None:
            store = await _open(config_file)
            docs = await store.summarize_all(overwrite=overwrite)
            if not docs:
                out.print("[dim]Nothing to summarize.[/dim]")
                return
            out.print(f"[green]✓[/green] Summarized {len(docs)} document(s)")
            for doc in docs:
                out.print(f"  [bold]{doc.meta.id[:12]}[/bold]  {doc.meta.summary}")

        _run(_go())
    except DocVaultError as exc:
        _abort(exc)


# ── Templates ────────────────────────────────────────────────────────────────


@templates_app.command("list")
def templates_list(config_file: Path | None = _CFG_OPT) -> None:
    """List all templates."""
    try:

        async def _go() -> None:
            store = await _open(config_file)
            templates = await store.list_templates()
            if not templates:
                out.print("[dim]No templates found.[/dim]")
                return
            table = Table("Name", "ID", "Description", "Created")
            for t in templates:
                table.add_row(
                    t.name,
                    t.id,
                    t.description or "-",
                    t.created_at.strftime("%Y-%m-%d"),
                )
            out.print(table)

        _run(_go())
    except DocVaultError as exc:
        _abort(exc)


@templates_app.command("get")
def templates_get(
    template_id: str = typer.Argument(..., help="Template ID"),
    config_file: Path | None = _CFG_OPT,
) -> None:
    """Print a template as JSON."""
    try:

        async def _go() -> None:
            store = await _open(config_file)
            template = await store.get_template(template_id)
            out.print_json(json.dumps(template.model_dump(mode="json")))

        _run(_go())
    except DocVaultError as exc:
        _abort(exc)


@templates_app.command("create")
def templates_create(
    name: str = typer.Argument(..., help="Template name"),
    file: Path | None = typer.Option(
        None, "--file", "-f", help="JSON file with structure dict (slot-path → DocSlot)"
    ),
    path: Path | None = typer.Option(
        None, "--path", "-p", help="File or folder to ingest as template slots"
    ),
    description: str = typer.Option("", "--description", "-d"),
    config_file: Path | None = _CFG_OPT,
) -> None:
    """Create a template from a structure file or by ingesting a file/folder."""
    if file is None and path is None:
        err.print("Error: provide --file or --path")
        raise typer.Exit(1)
    try:
        structure = _load_json(file) if file else {}
        inp = TemplateCreateInput(
            name=name,
            description=description,
            structure=structure,
            path=path,
        )

        async def _go() -> None:
            store = await _open(config_file)
            ref = await store.create_template(inp)
            out.print(
                f"[green]✓[/green] Created template [bold]{ref.name}[/bold]"
                f"  id={ref.id}"
            )

        _run(_go())
    except (DocVaultError, json.JSONDecodeError, ValueError) as exc:
        _abort(exc)


@templates_app.command("delete")
def templates_delete(
    template_id: str = typer.Argument(..., help="Template ID"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
    config_file: Path | None = _CFG_OPT,
) -> None:
    """Delete a template."""
    if not force:
        typer.confirm(f"Delete template {template_id!r}?", abort=True)
    try:

        async def _go() -> None:
            store = await _open(config_file)
            await store.delete_template(template_id)
            out.print(f"[green]✓[/green] Deleted template [bold]{template_id}[/bold]")

        _run(_go())
    except DocVaultError as exc:
        _abort(exc)


# ── Vault metadata ────────────────────────────────────────────────────────────


@vault_app.command("info")
def vault_info(config_file: Path | None = _CFG_OPT) -> None:
    """Show vault metadata."""
    try:

        async def _go() -> None:
            store = await _open(config_file)
            vault = await store.get_vault()
            out.print(f"Name:    {vault.name}")
            out.print(f"Version: [bold]{vault.version}[/bold]")
            out.print(f"Desc:    {vault.description or '-'}")
            out.print(f"Updated: {vault.updated_at.strftime('%Y-%m-%d %H:%M')}")

        _run(_go())
    except DocVaultError as exc:
        _abort(exc)


@vault_app.command("versions")
def vault_versions(config_file: Path | None = _CFG_OPT) -> None:
    """List version tags."""
    try:

        async def _go() -> None:
            store = await _open(config_file)
            versions = await store.list_vault_versions()
            if not versions:
                out.print("[dim]No version tags found.[/dim]")
                return
            for v in versions:
                out.print(v)

        _run(_go())
    except DocVaultError as exc:
        _abort(exc)


@vault_app.command("bump")
def vault_bump(
    kind: str = typer.Argument(
        "patch", help="Version part to bump: major, minor, or patch"
    ),
    config_file: Path | None = _CFG_OPT,
) -> None:
    """Bump the vault version and create a git tag."""
    if kind not in ("major", "minor", "patch"):
        err.print("kind must be one of: major, minor, patch")
        raise typer.Exit(1)
    try:

        async def _go() -> None:
            store = await _open(config_file)
            vault = await store.bump_vault_version(kind)
            out.print(f"[green]✓[/green] Vault version → [bold]{vault.version}[/bold]")

        _run(_go())
    except DocVaultError as exc:
        _abort(exc)


@vault_app.command("deploy")
def vault_deploy(
    template_name: str = typer.Option(..., "--template", "-t", help="Template name"),
    file: Path = typer.Option(
        ..., "--file", "-f", help="JSON file: list of DeployDocSpec objects"
    ),
    config_file: Path | None = _CFG_OPT,
) -> None:
    """Batch-create documents from a template."""
    try:
        raw = _load_json(file)
        specs = [
            DeployDocSpec(**item) for item in (raw if isinstance(raw, list) else [raw])
        ]
        inp = DeployVaultInput(template_name=template_name, documents=specs)

        async def _go() -> None:
            store = await _open(config_file)
            docs = await store.deploy_vault(inp)
            out.print(f"[green]✓[/green] Deployed {len(docs)} document(s)")
            for doc in docs:
                out.print(f"  [dim]{doc.meta.id}[/dim]")

        _run(_go())
    except (DocVaultError, json.JSONDecodeError) as exc:
        _abort(exc)


# ── Config ────────────────────────────────────────────────────────────────────


@config_app.command("show")
def config_show(config_file: Path | None = _CFG_OPT) -> None:
    """Print the resolved configuration (sensitive values masked)."""
    cfg = load_config(config_file)
    rows = [
        ("vault_path", str(cfg.vault_path)),
        ("vault_name", cfg.vault_name),
        ("vault_description", cfg.vault_description or "-"),
        ("auth_mode", cfg.auth_mode.value),
        ("api_keys", f"{len(cfg.api_keys)} key(s) configured"),
        ("default_creator", cfg.default_creator),
        ("git_author", f"{cfg.git_author_name} <{cfg.git_author_email}>"),
        ("llm_model", cfg.llm_model),
        ("llm_api_key", "***" if cfg.llm_api_key else "(not set)"),
        ("auto_summarize", str(cfg.auto_summarize)),
    ]
    table = Table("Setting", "Value", show_header=True)
    for k, v in rows:
        table.add_row(k, v)
    out.print(table)


@config_app.command("generate-key")
def config_generate_key(
    count: int = typer.Option(1, "--count", "-n", help="Number of keys to generate"),
) -> None:
    """Print one or more random API keys for use with api_key auth mode."""
    import secrets as _secrets

    for _ in range(count):
        out.print(_secrets.token_urlsafe(32))


# ── Tools ─────────────────────────────────────────────────────────────────────


@tools_app.command("deploy")
def tools_deploy(
    template_id: str = typer.Argument(
        ..., help="Template name or full ID (name/vX.Y.Z/hash)"
    ),
    target: Path = typer.Argument(..., help="Local directory to deploy into"),
    server: str = typer.Option(
        "http://localhost:8000", "--server", "-s", help="DocVault server base URL"
    ),
    api_key: str | None = typer.Option(
        None, "--api-key", envvar="DOCVAULT_API_KEY", help="API key (if required)"
    ),
    overwrite: bool = typer.Option(
        False, "--overwrite", help="Overwrite existing files"
    ),
) -> None:
    """Deploy a template from a running DocVault server to a local directory.

    Downloads the template export zip and extracts each slot document at its
    path inside TARGET, recreating the template's folder structure on disk.
    """
    from docvault.tools import deploy_template

    try:
        files = deploy_template(
            template_id,
            target_path=target,
            base_url=server,
            api_key=api_key,
            overwrite=overwrite,
        )
    except Exception as exc:
        _abort(exc)
        return

    if not files:
        out.print(
            "[dim]Nothing written (all files already exist; use --overwrite).[/dim]"
        )
        return

    out.print(
        f"[green]✓[/green] Deployed {len(files)} file(s) to [bold]{target}[/bold]"
    )
    for f in files:
        out.print(f"  [dim]{f}[/dim]")
