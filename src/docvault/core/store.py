from __future__ import annotations

import asyncio
import contextlib
import io
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from ..config import VaultConfig
from ..exceptions import (
    DocumentNotFoundError,
    TemplateNotFoundError,
    VaultNotFoundError,
)
from .document import CommitInfo, CreateDocInput, Document, DocumentMeta, UpdateDocInput
from .vault_meta import VaultMeta, VaultVersion
from .git_backend import GitBackend
from .summarizer import DocumentSummarizer
from .template import (
    DeployVaultInput,
    DocSlot,
    Template,
    TemplateCreateInput,
    TemplateIngestResult,
    TemplateValidation,
)


class DocVault:
    def __init__(self, config: VaultConfig) -> None:
        self.config = config
        self.git = GitBackend(config.vault_path)
        self._lock = asyncio.Lock()
        self._summarizer: DocumentSummarizer | None = (
            DocumentSummarizer(config.llm_api_key, config.llm_model)
            if config.llm_api_key
            else None
        )

    # ── Path helpers ───────────────────────────────────────────────────────

    @property
    def _docs_path(self) -> Path:
        return self.config.vault_path / "documents"

    @property
    def _meta_path(self) -> Path:
        return self.config.vault_path / "_meta" / "docs"

    @property
    def _templates_path(self) -> Path:
        return self.config.vault_path / "templates"

    @property
    def _vault_meta_path(self) -> Path:
        return self.config.vault_path / "_meta" / "vault.json"

    # ── JSON I/O ───────────────────────────────────────────────────────────

    async def _write_json(self, path: Path, data: dict[str, Any]) -> int:
        text = json.dumps(data, separators=(",", ":"))
        await asyncio.to_thread(path.write_text, text, "utf-8")
        return len(text.encode("utf-8"))

    async def _read_json(self, path: Path) -> dict[str, Any]:
        text = await asyncio.to_thread(path.read_text, "utf-8")
        return json.loads(text)

    # ── Lifecycle ──────────────────────────────────────────────────────────

    def _make_dirs(self) -> None:
        for d in (self._docs_path, self._meta_path, self._templates_path):
            d.mkdir(parents=True, exist_ok=True)
            keep = d / ".gitkeep"
            if not keep.exists():
                keep.touch()

    async def init(self) -> None:
        """Create vault if it doesn't exist; idempotent."""
        await asyncio.to_thread(self._make_dirs)
        await self.git.init()
        if not self._vault_meta_path.exists():
            fleet = VaultMeta(
                name=self.config.vault_name,
                description=self.config.vault_description,
            )
            await self._write_json(self._vault_meta_path, fleet.model_dump(mode="json"))
        async with self._lock:
            if await self.git.is_empty():
                await self.git.commit(
                    "Initialize docvault",
                    self.config.git_author_name,
                    self.config.git_author_email,
                )

    async def open(self) -> None:
        """Open an existing vault; raises VaultNotFoundError if missing."""
        if not (self.config.vault_path / ".git").exists():
            raise VaultNotFoundError(
                f"No vault at {self.config.vault_path}. Run 'docvault init <path>' to create one."
            )
        await self.git.init()

    # ── Internal summarization helper ──────────────────────────────────────

    async def _maybe_summarize(
        self,
        content: dict[str, Any],
        current_summary: str,
        current_keywords: list[str],
    ) -> tuple[str, list[str]]:
        """Run LLM inference when auto_summarize is on and no manual summary given."""
        if not (
            self._summarizer and self.config.auto_summarize and not current_summary
        ):
            return current_summary, current_keywords
        try:
            return await self._summarizer.infer_metadata(content)
        except Exception:
            return current_summary, current_keywords

    # ── Documents ──────────────────────────────────────────────────────────

    async def create_doc(self, inp: CreateDocInput) -> Document:
        content = inp.content
        if inp.template and inp.path:
            template = await self.get_template(inp.template)
            template.validate_slot_content(inp.path, content)

        summary, keywords = await self._maybe_summarize(
            content, inp.summary, inp.keywords
        )

        meta = DocumentMeta(
            creator=inp.creator or self.config.git_author_name,
            summary=summary,
            keywords=keywords,
            template=inp.template,
            path=inp.path,
            named_version=inp.named_version,
        )
        doc_path = self._docs_path / f"{meta.id}.json"
        meta_path = self._meta_path / f"{meta.id}.json"

        async with self._lock:
            size = await self._write_json(doc_path, content)
            meta = meta.model_copy(update={"size_bytes": size})
            await self._write_json(meta_path, meta.model_dump(mode="json"))
            msg = inp.commit_message or f"Create document {meta.id}"
            await self.git.commit(msg, inp.creator, f"{inp.creator}@docvault")

        return Document(meta=meta, content=content)

    async def get_doc(self, doc_id: str) -> Document:
        doc_path = self._docs_path / f"{doc_id}.json"
        meta_path = self._meta_path / f"{doc_id}.json"
        if not doc_path.exists():
            raise DocumentNotFoundError(f"Document {doc_id!r} not found")
        content, meta_data = await asyncio.gather(
            self._read_json(doc_path),
            self._read_json(meta_path),
        )
        return Document(meta=DocumentMeta(**meta_data), content=content)

    async def update_doc(self, doc_id: str, inp: UpdateDocInput) -> Document:
        content = inp.content
        doc_path = self._docs_path / f"{doc_id}.json"
        meta_path = self._meta_path / f"{doc_id}.json"

        async with self._lock:
            # Read inside the lock so a concurrent writer cannot truncate the
            # file between our read and our own write (race → JSONDecodeError).
            doc = await self.get_doc(doc_id)

            if doc.meta.template and doc.meta.path:
                template = await self.get_template(doc.meta.template)
                template.validate_slot_content(doc.meta.path, content)

            new_summary = inp.summary if inp.summary is not None else doc.meta.summary
            new_keywords = inp.keywords if inp.keywords is not None else doc.meta.keywords
            new_summary, new_keywords = await self._maybe_summarize(
                content, new_summary, new_keywords
            )

            meta = doc.meta.model_copy(
                update={
                    "updated_at": datetime.now(UTC),
                    "summary": new_summary,
                    "keywords": new_keywords,
                    "named_version": inp.named_version
                    if inp.named_version is not None
                    else doc.meta.named_version,
                }
            )

            size = await self._write_json(doc_path, content)
            meta = meta.model_copy(update={"size_bytes": size})
            await self._write_json(meta_path, meta.model_dump(mode="json"))
            msg = inp.commit_message or f"Update document {doc_id}"
            await self.git.commit(msg, meta.creator, f"{meta.creator}@docvault")

        return Document(meta=meta, content=content)

    async def delete_doc(self, doc_id: str) -> None:
        doc_path = self._docs_path / f"{doc_id}.json"
        meta_path = self._meta_path / f"{doc_id}.json"
        if not doc_path.exists():
            raise DocumentNotFoundError(f"Document {doc_id!r} not found")

        async with self._lock:
            await asyncio.to_thread(doc_path.unlink)
            await asyncio.to_thread(meta_path.unlink)
            await self.git.commit(
                f"Delete document {doc_id}",
                self.config.git_author_name,
                self.config.git_author_email,
            )

    async def list_docs(
        self,
        keywords: list[str] | None = None,
        creator: str | None = None,
        template: str | None = None,
    ) -> list[DocumentMeta]:
        def _read_all() -> list[DocumentMeta]:
            results: list[DocumentMeta] = []
            for f in self._meta_path.glob("*.json"):
                if f.name == ".gitkeep":
                    continue
                with contextlib.suppress(Exception):
                    results.append(DocumentMeta(**json.loads(f.read_text("utf-8"))))
            return results

        metas = await asyncio.to_thread(_read_all)

        if creator:
            metas = [m for m in metas if m.creator == creator]
        if template:
            metas = [m for m in metas if m.template == template]
        if keywords:
            kw_set = {k.lower() for k in keywords}
            metas = [m for m in metas if kw_set.issubset(k.lower() for k in m.keywords)]

        return sorted(metas, key=lambda m: m.created_at, reverse=True)

    async def get_doc_history(
        self, doc_id: str, max_count: int = 50
    ) -> list[CommitInfo]:
        if not (self._docs_path / f"{doc_id}.json").exists():
            raise DocumentNotFoundError(f"Document {doc_id!r} not found")
        return await self.git.log(path=f"documents/{doc_id}.json", max_count=max_count)

    async def get_doc_at_ref(self, doc_id: str, ref: str) -> Document:
        try:
            content_raw = await self.git.show(ref, f"documents/{doc_id}.json")
            meta_raw = await self.git.show(ref, f"_meta/docs/{doc_id}.json")
        except Exception as exc:
            raise DocumentNotFoundError(
                f"Document {doc_id!r} not found at ref {ref!r}: {exc}"
            ) from exc
        return Document(
            meta=DocumentMeta(**json.loads(meta_raw)),
            content=json.loads(content_raw),
        )

    # ── On-demand summarization ────────────────────────────────────────────

    async def summarize_doc(self, doc_id: str, overwrite: bool = False) -> Document:
        doc = await self.get_doc(doc_id)  # raises DocumentNotFoundError early
        if not self._summarizer:
            from ..exceptions import SummarizationError

            raise SummarizationError(
                "LLM summarization not configured. Set llm_api_key in config or "
                "DOCVAULT_LLM_API_KEY env var."
            )
        if doc.meta.summary and not overwrite:
            return doc

        summary, keywords = await self._summarizer.infer_metadata(doc.content)
        meta = doc.meta.model_copy(
            update={
                "summary": summary,
                "keywords": keywords,
                "updated_at": datetime.now(UTC),
            }
        )
        meta_path = self._meta_path / f"{doc_id}.json"

        async with self._lock:
            await self._write_json(meta_path, meta.model_dump(mode="json"))
            await self.git.commit(
                f"Infer metadata for {doc_id} via LLM",
                self.config.git_author_name,
                self.config.git_author_email,
            )

        return Document(meta=meta, content=doc.content)

    async def summarize_all(self, overwrite: bool = False) -> list[Document]:
        metas = await self.list_docs()
        results: list[Document] = []
        for meta in metas:
            if meta.summary and not overwrite:
                continue
            doc = await self.summarize_doc(meta.id, overwrite=overwrite)
            results.append(doc)
        return results

    # ── Templates ──────────────────────────────────────────────────────────

    async def create_template(
        self, inp: TemplateCreateInput, creator: str | None = None
    ) -> TemplateIngestResult:
        # Build structure: start from user-provided slots, then add discovered ones.
        structure = dict(inp.structure)
        _creator = creator or self.config.git_author_name

        discovered: list[tuple[str, dict[str, Any]]] = []
        if inp.folder_path is not None:

            def _scan() -> list[tuple[str, dict[str, Any]]]:
                results: list[tuple[str, dict[str, Any]]] = []
                for fp in sorted(inp.folder_path.rglob("*")):  # type: ignore[union-attr]
                    if not fp.is_file():
                        continue
                    rel = fp.relative_to(inp.folder_path)
                    slot_path = str(rel.with_suffix("")).replace("\\", "/")
                    try:
                        raw = fp.read_text("utf-8")
                    except (UnicodeDecodeError, OSError):
                        continue
                    try:
                        parsed = json.loads(raw)
                        content: dict[str, Any] = (
                            parsed if isinstance(parsed, dict) else {"value": parsed}
                        )
                    except json.JSONDecodeError:
                        content = {"content": raw, "_source_ext": fp.suffix.lstrip(".")}
                    results.append((slot_path, content))
                return results

            discovered = await asyncio.to_thread(_scan)
            for slot_path, _ in discovered:
                structure.setdefault(slot_path, DocSlot())

        # Version: caller-supplied > folder default (1.0.0) > model default (0.1.0)
        version = inp.version or (
            VaultVersion(major=1, minor=0, patch=0)
            if inp.folder_path is not None
            else None
        )
        template = Template(
            name=inp.name,
            description=inp.description,
            structure=structure,
            **({"version": version} if version is not None else {}),
        )
        tpl_path = self._templates_path / f"{template.name}.json"

        # Run LLM inference outside the lock (potentially slow).
        prepared: list[tuple[str, dict[str, Any], DocumentMeta]] = []
        for slot_path, content in discovered:
            summary, keywords = await self._maybe_summarize(content, "", [])
            meta = DocumentMeta(
                creator=_creator,
                summary=summary,
                keywords=keywords,
                template=inp.name,
                path=slot_path,
            )
            prepared.append((slot_path, content, meta))

        documents: list[Document] = []
        async with self._lock:
            await self._write_json(tpl_path, template.model_dump(mode="json"))
            await self.git.commit(
                f"Create template {template.name!r}",
                self.config.git_author_name,
                self.config.git_author_email,
            )
            for _slot, content, meta in prepared:
                size = await self._write_json(
                    self._docs_path / f"{meta.id}.json", content
                )
                meta = meta.model_copy(update={"size_bytes": size})
                await self._write_json(
                    self._meta_path / f"{meta.id}.json", meta.model_dump(mode="json")
                )
                documents.append(Document(meta=meta, content=content))
            if documents:
                await self.git.commit(
                    f"Ingest {len(documents)} document(s) from folder into {inp.name!r}",
                    self.config.git_author_name,
                    self.config.git_author_email,
                )

        return TemplateIngestResult(template=template, documents=documents)

    async def get_template(self, name: str) -> Template:
        path = self._templates_path / f"{name}.json"
        if not path.exists():
            raise TemplateNotFoundError(f"Template {name!r} not found")
        return Template(**await self._read_json(path))

    async def delete_template(self, name: str) -> None:
        path = self._templates_path / f"{name}.json"
        if not path.exists():
            raise TemplateNotFoundError(f"Template {name!r} not found")
        async with self._lock:
            await asyncio.to_thread(path.unlink)
            await self.git.commit(
                f"Delete template {name!r}",
                self.config.git_author_name,
                self.config.git_author_email,
            )

    async def list_templates(self) -> list[Template]:
        def _read_all() -> list[Template]:
            results: list[Template] = []
            for f in self._templates_path.glob("*.json"):
                if f.name == ".gitkeep":
                    continue
                with contextlib.suppress(Exception):
                    results.append(Template(**json.loads(f.read_text("utf-8"))))
            return results

        return await asyncio.to_thread(_read_all)

    async def validate_template(self, name: str) -> TemplateValidation:
        """Check which required slots in *name* are occupied by vault documents."""
        template = await self.get_template(name)
        metas = await self.list_docs(template=name)
        occupied = {m.path for m in metas if m.path is not None}
        return template.check_structure(occupied)

    async def export_template_zip(self, name: str) -> bytes:
        """Package a template and all its slot documents into an in-memory zip archive.

        The zip layout mirrors the template's folder structure::

            _template.json        – full Template metadata (id, version, structure …)
            config/app.json       – content of the document assigned to slot config/app
            config/database.json  – … and so on for every slot that has a document
        """
        template = await self.get_template(name)
        metas = await self.list_docs(template=name)

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(
                "_template.json",
                json.dumps(template.model_dump(mode="json"), indent=2),
            )
            for meta in sorted(metas, key=lambda m: m.path or ""):
                if meta.path is None:
                    continue
                doc = await self.get_doc(meta.id)
                source_ext = (
                    doc.content.get("_source_ext")
                    if isinstance(doc.content, dict)
                    else None
                )
                if source_ext:
                    entry_name = f"{meta.path}.{source_ext}"
                    entry_bytes = doc.content.get("content", "").encode("utf-8")
                    zf.writestr(entry_name, entry_bytes)
                else:
                    zf.writestr(
                        f"{meta.path}.json",
                        json.dumps(doc.content, indent=2),
                    )

        return buf.getvalue()

    async def deploy_vault(self, inp: DeployVaultInput) -> list[Document]:
        template = await self.get_template(inp.template_name)
        prepared: list[tuple[DocumentMeta, dict[str, Any]]] = []

        for spec in inp.documents:
            content = spec.content
            template.validate_slot_content(spec.path, content)
            summary, keywords = await self._maybe_summarize(
                content, spec.summary, spec.keywords
            )
            meta = DocumentMeta(
                creator=spec.creator,
                summary=summary,
                keywords=keywords,
                template=inp.template_name,
                path=spec.path,
                named_version=spec.named_version,
            )
            prepared.append((meta, content))

        documents: list[Document] = []
        async with self._lock:
            for meta, content in prepared:
                size = await self._write_json(
                    self._docs_path / f"{meta.id}.json", content
                )
                meta = meta.model_copy(update={"size_bytes": size})
                await self._write_json(
                    self._meta_path / f"{meta.id}.json", meta.model_dump(mode="json")
                )
                documents.append(Document(meta=meta, content=content))

            msg = inp.commit_message or (
                f"Deploy {len(documents)} documents from template {inp.template_name!r}"
            )
            await self.git.commit(
                msg, self.config.git_author_name, self.config.git_author_email
            )

        return documents

    # ── Vault metadata ─────────────────────────────────────────────────────

    async def get_vault(self) -> VaultMeta:
        return VaultMeta(**await self._read_json(self._vault_meta_path))

    async def bump_vault_version(
        self, bump_type: Literal["major", "minor", "patch"]
    ) -> VaultMeta:
        fleet = await self.get_vault()
        new_version = fleet.version.bump(bump_type)
        fleet = fleet.model_copy(
            update={"version": new_version, "updated_at": datetime.now(UTC)}
        )
        async with self._lock:
            await self._write_json(self._vault_meta_path, fleet.model_dump(mode="json"))
            await self.git.commit(
                f"Bump vault version to {new_version}",
                self.config.git_author_name,
                self.config.git_author_email,
            )
            await self.git.create_tag(f"v{new_version}", f"Vault version {new_version}")
        return fleet

    async def list_vault_versions(self) -> list[str]:
        tags = await self.git.list_tags()
        return [t for t in tags if t.startswith("v")]
