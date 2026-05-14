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
from docvault.core.vault import DocVault
from docvault.core.store import DeployDocSpec, DeployStoreInput, StoreCreateInput
from docvault.exceptions import DocVaultError

app = typer.Typer(
    name="docvault", help="Git-backed JSON document manager", no_args_is_help=True
)
docs_app = typer.Typer(help="Manage documents", no_args_is_help=True)
stores_app = typer.Typer(help="Manage stores", no_args_is_help=True)
vault_app = typer.Typer(help="Manage vault metadata", no_args_is_help=True)
config_app = typer.Typer(help="Config and key management", no_args_is_help=True)
tools_app = typer.Typer(help="Host-system tools (deploy, etc.)", no_args_is_help=True)

app.add_typer(docs_app, name="docs")
app.add_typer(stores_app, name="stores")
app.add_typer(vault_app, name="vault")
app.add_typer(config_app, name="config")
app.add_typer(tools_app, name="tools")

out = Console()
err = Console(stderr=True, style="bold red")

_CFG_OPT = typer.Option(
    None, "--config", "-c", help="Path to docvault.json config file"
)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _load_json(file: Path) -> Any:
    text = sys.stdin.read() if str(file) == "-" else file.read_text("utf-8")
    return json.loads(text)


async def _open(config_file: Path | None) -> DocVault:
    vault = DocVault(load_config(config_file))
    await vault.open()
    return vault


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
        vault = DocVault(cfg)
        _run(vault.init())
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
    creator: str | None = typer.Option(None, "--creator", help="Filter by creator"),
    keywords: str | None = typer.Option(
        None, "--keywords", help="Comma-separated keywords"
    ),
    config_file: Path | None = _CFG_OPT,
) -> None:
    """List root-level (free-floating) documents."""
    try:
        kw = [k.strip() for k in keywords.split(",")] if keywords else None

        async def _go() -> None:
            vault = await _open(config_file)
            metas = await vault.list_docs(keywords=kw, creator=creator)
            if not metas:
                out.print("[dim]No documents found.[/dim]")
                return
            table = Table("ID", "Creator", "Binary", "Summary", "Updated")
            for m in metas:
                summary = (
                    (m.summary[:48] + "…") if len(m.summary) > 50 else m.summary or "-"
                )
                table.add_row(
                    m.id[:12],
                    m.creator,
                    "yes" if m.is_binary else "-",
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
            vault = await _open(config_file)
            doc = await vault.get_doc(doc_id)
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
    summary: str = typer.Option("", "--summary", "-s"),
    keywords: str | None = typer.Option(None, "--keywords", help="Comma-separated"),
    config_file: Path | None = _CFG_OPT,
) -> None:
    """Create a root document from a JSON file."""
    try:
        content = _load_json(file)
        kw = [k.strip() for k in keywords.split(",")] if keywords else []
        inp = CreateDocInput(
            content=content,
            creator=creator,
            summary=summary,
            keywords=kw,
        )

        async def _go() -> None:
            vault = await _open(config_file)
            doc = await vault.create_doc(inp)
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
    """Replace a root document's content."""
    try:
        content = _load_json(file)
        kw = [k.strip() for k in keywords.split(",")] if keywords else None
        inp = UpdateDocInput(content=content, summary=summary, keywords=kw)

        async def _go() -> None:
            vault = await _open(config_file)
            doc = await vault.update_doc(doc_id, inp)
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
    """Delete a root document."""
    if not force:
        typer.confirm(f"Delete document {doc_id!r}?", abort=True)
    try:

        async def _go() -> None:
            vault = await _open(config_file)
            await vault.delete_doc(doc_id)
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
            vault = await _open(config_file)
            commits = await vault.get_doc_history(doc_id, max_count=max_count)
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
    """Show a root document at a specific git ref."""
    try:

        async def _go() -> None:
            vault = await _open(config_file)
            doc = await vault.get_doc_at_ref(doc_id, ref)
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
            vault = await _open(config_file)
            doc = await vault.summarize_doc(doc_id, overwrite=overwrite)
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
    """Infer summary and keywords for every root document that is missing them."""
    try:

        async def _go() -> None:
            vault = await _open(config_file)
            docs = await vault.summarize_all(overwrite=overwrite)
            if not docs:
                out.print("[dim]Nothing to summarize.[/dim]")
                return
            out.print(f"[green]✓[/green] Summarized {len(docs)} document(s)")
            for doc in docs:
                out.print(f"  [bold]{doc.meta.id[:12]}[/bold]  {doc.meta.summary}")

        _run(_go())
    except DocVaultError as exc:
        _abort(exc)


# ── Stores ────────────────────────────────────────────────────────────────────


@stores_app.command("list")
def stores_list(config_file: Path | None = _CFG_OPT) -> None:
    """List all stores in the vault."""
    try:

        async def _go() -> None:
            vault = await _open(config_file)
            stores = await vault.list_stores()
            if not stores:
                out.print("[dim]No stores found.[/dim]")
                return
            table = Table("Name", "Version", "Locked", "Slots", "Description", "Created")
            for s in stores:
                table.add_row(
                    s.name,
                    str(s.version),
                    "🔒" if s.locked else "-",
                    str(len(s.structure)),
                    s.description or "-",
                    s.created_at.strftime("%Y-%m-%d"),
                )
            out.print(table)

        _run(_go())
    except DocVaultError as exc:
        _abort(exc)


@stores_app.command("get")
def stores_get(
    name: str = typer.Argument(..., help="Store name"),
    config_file: Path | None = _CFG_OPT,
) -> None:
    """Print a store's metadata as JSON."""
    try:

        async def _go() -> None:
            vault = await _open(config_file)
            store_obj = await vault.get_store(name)
            meta = await store_obj.get_meta()
            out.print_json(json.dumps(meta.model_dump(mode="json")))

        _run(_go())
    except DocVaultError as exc:
        _abort(exc)


@stores_app.command("create")
def stores_create(
    name: str = typer.Argument(..., help="Store name"),
    file: Path | None = typer.Option(
        None, "--file", "-f", help="JSON file with structure dict (slot-path → DocSlot)"
    ),
    path: Path | None = typer.Option(
        None, "--path", "-p", help="File or folder to ingest as store slots"
    ),
    description: str = typer.Option("", "--description", "-d"),
    locked: bool = typer.Option(False, "--locked", help="Lock store to deploy-only updates"),
    config_file: Path | None = _CFG_OPT,
) -> None:
    """Create a store (optionally ingesting a folder as slot documents)."""
    if file is None and path is None:
        structure: dict = {}
    else:
        structure = _load_json(file) if file else {}
    try:
        inp = StoreCreateInput(
            name=name,
            description=description,
            structure=structure,
            path=path,
            locked=locked,
        )

        async def _go() -> None:
            vault = await _open(config_file)
            store_obj = await vault.create_store(inp)
            meta = await store_obj.get_meta()
            out.print(
                f"[green]✓[/green] Store [bold]{meta.name}[/bold]  "
                f"v{meta.version}  id={meta.id}"
            )

        _run(_go())
    except (DocVaultError, json.JSONDecodeError, ValueError) as exc:
        _abort(exc)


@stores_app.command("delete")
def stores_delete(
    name: str = typer.Argument(..., help="Store name"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
    config_file: Path | None = _CFG_OPT,
) -> None:
    """Delete a store and all its documents."""
    if not force:
        typer.confirm(f"Delete store {name!r} and all its documents?", abort=True)
    try:

        async def _go() -> None:
            vault = await _open(config_file)
            await vault.delete_store(name)
            out.print(f"[green]✓[/green] Deleted store [bold]{name}[/bold]")

        _run(_go())
    except DocVaultError as exc:
        _abort(exc)


@stores_app.command("validate")
def stores_validate(
    name: str = typer.Argument(..., help="Store name"),
    config_file: Path | None = _CFG_OPT,
) -> None:
    """Check slot coverage for a store."""
    try:

        async def _go() -> None:
            vault = await _open(config_file)
            result = await vault.validate_store(name)
            status = "[green]✓ satisfied[/green]" if result.valid else "[red]✗ incomplete[/red]"
            out.print(f"Store [bold]{name}[/bold]: {status}")
            for slot in result.satisfied:
                out.print(f"  [green]●[/green] {slot}")
            for slot in result.missing:
                out.print(f"  [red]○[/red] {slot}  [dim](missing)[/dim]")
            if result.extra:
                out.print(f"  [dim]extra: {', '.join(result.extra)}[/dim]")

        _run(_go())
    except DocVaultError as exc:
        _abort(exc)


# ── Store docs sub-commands ───────────────────────────────────────────────────

store_docs_app = typer.Typer(help="Manage documents within a store", no_args_is_help=True)
stores_app.add_typer(store_docs_app, name="docs")


@store_docs_app.command("list")
def store_docs_list(
    name: str = typer.Argument(..., help="Store name"),
    creator: str | None = typer.Option(None, "--creator", help="Filter by creator"),
    keywords: str | None = typer.Option(None, "--keywords", help="Comma-separated"),
    config_file: Path | None = _CFG_OPT,
) -> None:
    """List documents in a store."""
    try:
        kw = [k.strip() for k in keywords.split(",")] if keywords else None

        async def _go() -> None:
            vault = await _open(config_file)
            store_obj = await vault.get_store(name)
            metas = await store_obj.list_docs(keywords=kw, creator=creator)
            if not metas:
                out.print("[dim]No documents found.[/dim]")
                return
            table = Table("ID", "Path", "Creator", "Binary", "Summary", "Updated")
            for m in metas:
                summary = (
                    (m.summary[:40] + "…") if len(m.summary) > 42 else m.summary or "-"
                )
                table.add_row(
                    m.id[:12],
                    m.path or "-",
                    m.creator,
                    "yes" if m.is_binary else "-",
                    summary,
                    m.updated_at.strftime("%Y-%m-%d %H:%M"),
                )
            out.print(table)

        _run(_go())
    except DocVaultError as exc:
        _abort(exc)


@store_docs_app.command("deploy")
def store_docs_deploy(
    name: str = typer.Argument(..., help="Store name"),
    file: Path = typer.Option(
        ..., "--file", "-f", help="JSON file: list of DeployDocSpec objects"
    ),
    config_file: Path | None = _CFG_OPT,
) -> None:
    """Batch-deploy documents into a store (required for locked stores)."""
    try:
        raw = _load_json(file)
        specs = [
            DeployDocSpec(**item) for item in (raw if isinstance(raw, list) else [raw])
        ]
        inp = DeployStoreInput(store_name=name, documents=specs)

        async def _go() -> None:
            vault = await _open(config_file)
            docs = await vault.deploy_store(inp)
            out.print(f"[green]✓[/green] Deployed {len(docs)} document(s) into store [bold]{name}[/bold]")
            for doc in docs:
                out.print(f"  [dim]{doc.meta.id}[/dim]")

        _run(_go())
    except (DocVaultError, json.JSONDecodeError) as exc:
        _abort(exc)


# ── Vault metadata ────────────────────────────────────────────────────────────


@vault_app.command("info")
def vault_info(config_file: Path | None = _CFG_OPT) -> None:
    """Show vault metadata."""
    try:

        async def _go() -> None:
            vault = await _open(config_file)
            v = await vault.get_vault()
            out.print(f"Name:    {v.name}")
            out.print(f"Version: [bold]{v.version}[/bold]")
            out.print(f"Desc:    {v.description or '-'}")
            out.print(f"Updated: {v.updated_at.strftime('%Y-%m-%d %H:%M')}")

        _run(_go())
    except DocVaultError as exc:
        _abort(exc)


@vault_app.command("versions")
def vault_versions(config_file: Path | None = _CFG_OPT) -> None:
    """List version tags."""
    try:

        async def _go() -> None:
            vault = await _open(config_file)
            versions = await vault.list_vault_versions()
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
            vault = await _open(config_file)
            v = await vault.bump_vault_version(kind)
            out.print(f"[green]✓[/green] Vault version → [bold]{v.version}[/bold]")

        _run(_go())
    except DocVaultError as exc:
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
    store_name: str = typer.Argument(
        ..., help="Store name or full ID (name:hash:hash)"
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
    """Deploy a store from a running DocVault server to a local directory."""
    from docvault.tools import deploy_store

    try:
        files = deploy_store(
            store_name,
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
