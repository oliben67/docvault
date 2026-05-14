"""Core vault machinery — _VaultNode base, Store (recursive sub-vault), DocVault."""
from __future__ import annotations

import asyncio
import base64
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
    StoreNotFoundError,
    StoreValidationError,
    VaultNotFoundError,
)
from .document import CommitInfo, CreateDocInput, Document, DocumentMeta, UpdateDocInput
from .git_backend import GitBackend
from .store import (
    DeployDocSpec,
    DeployStoreInput,
    DocSlot,
    StoreMeta,
    StoreRef,
    StoreValidation,
    StoreCreateInput,
    create_id,
    create_id_from_structure,
)
from .summarizer import DocumentSummarizer
from .vault_meta import VaultMeta


# ── _VaultNode — shared base for DocVault and Store ───────────────────────────


class _VaultNode:
    """All document and sub-store management logic, parameterised by base_path.

    Both :class:`DocVault` and :class:`Store` extend this class.
    They share a single git repository and asyncio lock so concurrent writes
    across stores are safely serialised.
    """

    def __init__(
        self,
        base_path: Path,
        vault_root: Path,
        git: GitBackend,
        lock: asyncio.Lock,
        config: VaultConfig,
    ) -> None:
        self._base_path = base_path
        self._vault_root = vault_root  # git repo root — needed for relative path calc
        self._git = git
        self._lock = lock
        self._config = config
        self._summarizer: DocumentSummarizer | None = (
            DocumentSummarizer(config.llm_api_key, config.llm_model)
            if config.llm_api_key
            else None
        )

    # ── Path helpers ───────────────────────────────────────────────────────

    @property
    def _docs_path(self) -> Path:
        return self._base_path / "documents"

    @property
    def _meta_path(self) -> Path:
        return self._base_path / "_meta" / "docs"

    @property
    def _stores_path(self) -> Path:
        return self._base_path / "stores"

    @property
    def _git_doc_prefix(self) -> str:
        return str(self._docs_path.relative_to(self._vault_root)).replace("\\", "/")

    @property
    def _git_meta_prefix(self) -> str:
        return str(self._meta_path.relative_to(self._vault_root)).replace("\\", "/")

    # ── JSON I/O ───────────────────────────────────────────────────────────

    async def _write_json(self, path: Path, data: dict[str, Any]) -> int:
        text = json.dumps(data, separators=(",", ":"))
        await asyncio.to_thread(path.write_text, text, "utf-8")
        return len(text.encode("utf-8"))

    async def _read_json(self, path: Path) -> dict[str, Any]:
        text = await asyncio.to_thread(path.read_text, "utf-8")
        return json.loads(text)

    def _make_dirs(self) -> None:
        for d in (self._docs_path, self._meta_path, self._stores_path):
            d.mkdir(parents=True, exist_ok=True)
            keep = d / ".gitkeep"
            if not keep.exists():
                keep.touch()

    # ── Summarization ──────────────────────────────────────────────────────

    async def _maybe_summarize(
        self,
        content: dict[str, Any],
        current_summary: str,
        current_keywords: list[str],
    ) -> tuple[str, list[str]]:
        if not (
            self._summarizer and self._config.auto_summarize and not current_summary
        ):
            return current_summary, current_keywords
        try:
            return await self._summarizer.infer_metadata(content)
        except Exception:  # noqa: BLE001
            return current_summary, current_keywords

    # ── Slot validation hook (overridden by Store) ─────────────────────────

    async def _validate_slot_content(
        self, path: str | None, content: dict[str, Any]
    ) -> None:
        """DocVault has no structure — subclasses override for validation."""

    # ── Binary helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _encode_binary(data: bytes, mime_type: str) -> dict[str, Any]:
        return {
            "_binary": True,
            "_mime": mime_type,
            "_data": base64.b64encode(data).decode("ascii"),
        }

    @staticmethod
    def _decode_binary(content: dict[str, Any]) -> tuple[bytes, str]:
        return (
            base64.b64decode(content["_data"]),
            content.get("_mime", "application/octet-stream"),
        )

    # ── Documents ──────────────────────────────────────────────────────────

    async def create_doc(self, inp: CreateDocInput) -> Document:
        if inp.binary_content is not None:
            content: dict[str, Any] = self._encode_binary(
                inp.binary_content, inp.mime_type or "application/octet-stream"
            )
            is_binary = True
            mime_type = inp.mime_type or "application/octet-stream"
        else:
            content = inp.content or {}
            is_binary = False
            mime_type = None
            if not is_binary:
                await self._validate_slot_content(inp.path, content)

        summary, keywords = await self._maybe_summarize(
            content, inp.summary, inp.keywords
        )

        meta = DocumentMeta(
            creator=inp.creator or self._config.git_author_name,
            summary=summary,
            keywords=keywords,
            path=inp.path,
            named_version=inp.named_version,
            is_binary=is_binary,
            mime_type=mime_type,
        )
        doc_path = self._docs_path / f"{meta.id}.json"
        meta_path = self._meta_path / f"{meta.id}.json"

        async with self._lock:
            size = await self._write_json(doc_path, content)
            meta = meta.model_copy(update={"size_bytes": size})
            await self._write_json(meta_path, meta.model_dump(mode="json"))
            msg = inp.commit_message or f"Create document {meta.id}"
            await self._git.commit(msg, meta.creator, f"{meta.creator}@docvault")

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
        doc_path = self._docs_path / f"{doc_id}.json"
        meta_path = self._meta_path / f"{doc_id}.json"

        async with self._lock:
            doc = await self.get_doc(doc_id)

            if inp.binary_content is not None:
                content: dict[str, Any] = self._encode_binary(
                    inp.binary_content,
                    inp.mime_type or doc.meta.mime_type or "application/octet-stream",
                )
                is_binary = True
                new_mime = inp.mime_type or doc.meta.mime_type
            else:
                content = inp.content if inp.content is not None else doc.content
                is_binary = doc.meta.is_binary
                new_mime = doc.meta.mime_type
                if not is_binary:
                    await self._validate_slot_content(doc.meta.path, content)

            new_summary = inp.summary if inp.summary is not None else doc.meta.summary
            new_keywords = (
                inp.keywords if inp.keywords is not None else doc.meta.keywords
            )
            new_summary, new_keywords = await self._maybe_summarize(
                content, new_summary, new_keywords
            )

            meta = doc.meta.model_copy(
                update={
                    "updated_at": datetime.now(UTC),
                    "summary": new_summary,
                    "keywords": new_keywords,
                    "is_binary": is_binary,
                    "mime_type": new_mime,
                    "named_version": inp.named_version
                    if inp.named_version is not None
                    else doc.meta.named_version,
                }
            )

            size = await self._write_json(doc_path, content)
            meta = meta.model_copy(update={"size_bytes": size})
            await self._write_json(meta_path, meta.model_dump(mode="json"))
            msg = inp.commit_message or f"Update document {doc_id}"
            await self._git.commit(msg, meta.creator, f"{meta.creator}@docvault")

        return Document(meta=meta, content=content)

    async def delete_doc(self, doc_id: str) -> None:
        doc_path = self._docs_path / f"{doc_id}.json"
        meta_path = self._meta_path / f"{doc_id}.json"
        if not doc_path.exists():
            raise DocumentNotFoundError(f"Document {doc_id!r} not found")
        async with self._lock:
            await asyncio.to_thread(doc_path.unlink)
            await asyncio.to_thread(meta_path.unlink)
            await self._git.commit(
                f"Delete document {doc_id}",
                self._config.git_author_name,
                self._config.git_author_email,
            )

    async def list_docs(
        self,
        keywords: list[str] | None = None,
        creator: str | None = None,
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
        if keywords:
            kw_set = {k.lower() for k in keywords}
            metas = [m for m in metas if kw_set.issubset(k.lower() for k in m.keywords)]

        return sorted(metas, key=lambda m: m.created_at, reverse=True)

    async def get_doc_history(
        self, doc_id: str, max_count: int = 50
    ) -> list[CommitInfo]:
        if not (self._docs_path / f"{doc_id}.json").exists():
            raise DocumentNotFoundError(f"Document {doc_id!r} not found")
        return await self._git.log(
            path=f"{self._git_doc_prefix}/{doc_id}.json",
            max_count=max_count,
        )

    async def get_doc_at_ref(self, doc_id: str, ref: str) -> Document:
        try:
            content_raw = await self._git.show(
                ref, f"{self._git_doc_prefix}/{doc_id}.json"
            )
            meta_raw = await self._git.show(
                ref, f"{self._git_meta_prefix}/{doc_id}.json"
            )
        except Exception as exc:
            raise DocumentNotFoundError(
                f"Document {doc_id!r} not found at ref {ref!r}: {exc}"
            ) from exc
        return Document(
            meta=DocumentMeta(**json.loads(meta_raw)),
            content=json.loads(content_raw),
        )

    async def get_doc_binary(self, doc_id: str) -> tuple[bytes, str]:
        """Retrieve raw bytes and MIME type for a binary document."""
        doc = await self.get_doc(doc_id)
        if not doc.meta.is_binary:
            raise ValueError(f"Document {doc_id!r} is not a binary document")
        return self._decode_binary(doc.content)

    async def summarize_doc(self, doc_id: str, overwrite: bool = False) -> Document:
        doc = await self.get_doc(doc_id)
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
            await self._git.commit(
                f"Infer metadata for {doc_id} via LLM",
                self._config.git_author_name,
                self._config.git_author_email,
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

    # ── Sub-store management ───────────────────────────────────────────────

    async def create_store(
        self, inp: StoreCreateInput, creator: str | None = None
    ) -> "Store":
        """Create (or upsert) a named sub-store and return its :class:`Store` object."""
        structure = dict(inp.structure)
        _creator = creator or self._config.git_author_name

        discovered: list[tuple[str, dict[str, Any], bool, str | None]] = []
        if inp.path is not None:
            src = inp.path

            def _scan() -> list[tuple[str, dict[str, Any], bool, str | None]]:
                files = [src] if src.is_file() else sorted(src.rglob("*"))
                results: list[tuple[str, dict[str, Any], bool, str | None]] = []
                for fp in files:
                    if not fp.is_file():
                        continue
                    if src.is_file():
                        slot_path = fp.stem
                    else:
                        rel = fp.relative_to(src)
                        slot_path = str(rel.with_suffix("")).replace("\\", "/")
                    try:
                        raw_bytes = fp.read_bytes()
                        try:
                            text = raw_bytes.decode("utf-8")
                            try:
                                parsed = json.loads(text)
                                doc_content: dict[str, Any] = (
                                    parsed if isinstance(parsed, dict) else {"value": parsed}
                                )
                            except json.JSONDecodeError:
                                doc_content = {
                                    "content": text,
                                    "_source_ext": fp.suffix.lstrip("."),
                                }
                            results.append((slot_path, doc_content, False, None))
                        except UnicodeDecodeError:
                            import mimetypes
                            mt, _ = mimetypes.guess_type(fp.name)
                            mime = mt or "application/octet-stream"
                            doc_content = _VaultNode._encode_binary(raw_bytes, mime)
                            results.append((slot_path, doc_content, True, mime))
                    except OSError:
                        continue
                return results

            discovered = await asyncio.to_thread(_scan)
            for slot_path, _, _, _ in discovered:
                structure.setdefault(slot_path, DocSlot())

        # Compute content-addressable ID.
        if inp.path is not None:
            raw_id = create_id(inp.path)
            parts = raw_id.split(":", 2)
            new_id = f"{inp.name}:{parts[1]}:{parts[2]}"
        else:
            new_id = create_id_from_structure(inp.name, structure)

        new_content_hash = new_id.split(":")[-1]
        store_base = self._stores_path / inp.name
        meta_file = store_base / "_meta" / "store.json"

        existing: StoreMeta | None = None
        if meta_file.exists():
            existing = StoreMeta(**await self._read_json(meta_file))

        if existing is not None:
            existing_content_hash = existing.id.split(":")[-1]
            if existing_content_hash == new_content_hash:
                return Store(
                    name=inp.name,
                    base_path=store_base,
                    vault_root=self._vault_root,
                    git=self._git,
                    lock=self._lock,
                    config=self._config,
                )
            if new_content_hash in existing.version_history:
                new_version = existing.version_history[new_content_hash]
            else:
                new_version = max(existing.version_history.values(), default=0) + 1
            new_version_history = dict(existing.version_history)
            new_version_history[new_content_hash] = new_version
            store_meta = StoreMeta(
                id=new_id,
                name=inp.name,
                description=inp.description or existing.description,
                version=new_version,
                version_history=new_version_history,
                structure=structure,
                locked=inp.locked,
                created_at=existing.created_at,
                updated_at=datetime.now(UTC),
            )
            commit_msg = f"Update store {inp.name!r} to v{new_version}"
        else:
            store_meta = StoreMeta(
                id=new_id,
                name=inp.name,
                description=inp.description,
                version=1,
                version_history={new_content_hash: 1},
                structure=structure,
                locked=inp.locked,
            )
            commit_msg = f"Create store {inp.name!r}"

        # Build the Store object to use its document infrastructure.
        store_obj = Store(
            name=inp.name,
            base_path=store_base,
            vault_root=self._vault_root,
            git=self._git,
            lock=self._lock,
            config=self._config,
        )

        prepared: list[tuple[str, dict[str, Any], DocumentMeta]] = []
        for slot_path, doc_content, is_bin, mime in discovered:
            summary, keywords = await self._maybe_summarize(doc_content, "", [])
            doc_meta = DocumentMeta(
                creator=_creator,
                summary=summary,
                keywords=keywords,
                path=slot_path,
                is_binary=is_bin,
                mime_type=mime,
            )
            prepared.append((slot_path, doc_content, doc_meta))

        async with self._lock:
            await asyncio.to_thread(store_obj._make_dirs)
            await self._write_json(meta_file, store_meta.model_dump(mode="json"))
            await self._git.commit(
                commit_msg,
                self._config.git_author_name,
                self._config.git_author_email,
            )
            for _slot, doc_content, doc_meta in prepared:
                size = await self._write_json(
                    store_obj._docs_path / f"{doc_meta.id}.json", doc_content
                )
                doc_meta = doc_meta.model_copy(update={"size_bytes": size})
                await self._write_json(
                    store_obj._meta_path / f"{doc_meta.id}.json",
                    doc_meta.model_dump(mode="json"),
                )
            if prepared:
                await self._git.commit(
                    f"Ingest {len(prepared)} document(s) into store {inp.name!r}",
                    self._config.git_author_name,
                    self._config.git_author_email,
                )

        return store_obj

    async def get_store(self, name: str) -> "Store":
        """Return a :class:`Store` object for a named sub-store."""
        store_base = self._stores_path / name
        meta_file = store_base / "_meta" / "store.json"
        if not meta_file.exists():
            raise StoreNotFoundError(f"Store {name!r} not found")
        return Store(
            name=name,
            base_path=store_base,
            vault_root=self._vault_root,
            git=self._git,
            lock=self._lock,
            config=self._config,
        )

    async def _get_store_meta_by_name(self, name: str) -> StoreMeta:
        """Read and return the :class:`StoreMeta` for a sub-store by name."""
        meta_file = self._stores_path / name / "_meta" / "store.json"
        if not meta_file.exists():
            raise StoreNotFoundError(f"Store {name!r} not found")
        return StoreMeta(**await self._read_json(meta_file))

    async def delete_store(self, name: str) -> None:
        """Permanently delete a named sub-store and all its documents."""
        store_base = self._stores_path / name
        meta_file = store_base / "_meta" / "store.json"
        if not meta_file.exists():
            raise StoreNotFoundError(f"Store {name!r} not found")
        import shutil
        async with self._lock:
            await asyncio.to_thread(shutil.rmtree, store_base)
            await self._git.commit(
                f"Delete store {name!r}",
                self._config.git_author_name,
                self._config.git_author_email,
            )

    async def list_stores(self) -> list[StoreMeta]:
        """Return metadata for all direct sub-stores."""
        def _read_all() -> list[StoreMeta]:
            results: list[StoreMeta] = []
            if not self._stores_path.exists():
                return results
            for d in self._stores_path.iterdir():
                if not d.is_dir():
                    continue
                meta_file = d / "_meta" / "store.json"
                if not meta_file.exists():
                    continue
                with contextlib.suppress(Exception):
                    results.append(StoreMeta(**json.loads(meta_file.read_text("utf-8"))))
            return results
        return await asyncio.to_thread(_read_all)

    async def validate_store(self, name: str) -> StoreValidation:
        """Check which required slots in *name* are occupied."""
        store_obj = await self.get_store(name)
        store_meta = await self._get_store_meta_by_name(name)
        metas = await store_obj.list_docs()
        occupied = {m.path for m in metas if m.path is not None}
        return store_meta.check_structure(occupied)

    async def export_store_zip(self, name: str) -> bytes:
        """Package a store and all its documents into an in-memory zip archive."""
        store_meta = await self._get_store_meta_by_name(name)
        store_obj = await self.get_store(name)
        metas = await store_obj.list_docs()

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(
                "_store.json",
                json.dumps(store_meta.model_dump(mode="json"), indent=2),
            )
            for meta in sorted(metas, key=lambda m: m.path or ""):
                if meta.path is None:
                    continue
                doc = await store_obj.get_doc(meta.id)
                if meta.is_binary:
                    raw_bytes, _mime = self._decode_binary(doc.content)
                    ext = (meta.mime_type or "bin").split("/")[-1]
                    zf.writestr(f"{meta.path}.{ext}", raw_bytes)
                else:
                    source_ext = (
                        doc.content.get("_source_ext")
                        if isinstance(doc.content, dict)
                        else None
                    )
                    if source_ext:
                        zf.writestr(
                            f"{meta.path}.{source_ext}",
                            doc.content.get("content", "").encode("utf-8"),
                        )
                    else:
                        zf.writestr(
                            f"{meta.path}.json",
                            json.dumps(doc.content, indent=2),
                        )
        return buf.getvalue()

    async def deploy(
        self,
        documents: list[DeployDocSpec],
        commit_message: str | None = None,
    ) -> list[Document]:
        """Batch-create documents in a single atomic git commit."""
        prepared: list[tuple[DocumentMeta, dict[str, Any]]] = []
        for spec in documents:
            content = spec.content
            await self._validate_slot_content(spec.path, content)
            summary, keywords = await self._maybe_summarize(
                content, spec.summary, spec.keywords
            )
            meta = DocumentMeta(
                creator=spec.creator,
                summary=summary,
                keywords=keywords,
                path=spec.path,
                named_version=spec.named_version,
            )
            prepared.append((meta, content))

        results: list[Document] = []
        async with self._lock:
            for meta, content in prepared:
                size = await self._write_json(
                    self._docs_path / f"{meta.id}.json", content
                )
                meta = meta.model_copy(update={"size_bytes": size})
                await self._write_json(
                    self._meta_path / f"{meta.id}.json", meta.model_dump(mode="json")
                )
                results.append(Document(meta=meta, content=content))
            msg = commit_message or f"Deploy {len(results)} document(s)"
            await self._git.commit(
                msg, self._config.git_author_name, self._config.git_author_email
            )
        return results


# ── Store — named sub-vault ────────────────────────────────────────────────────


class Store(_VaultNode):
    """A named, structured document collection within a vault.

    Stores can be nested: each Store can contain its own sub-stores, forming
    a tree of document namespaces that share the parent vault's git repository.

    Create via :meth:`DocVault.create_store` or :meth:`Store.create_store`.
    """

    def __init__(
        self,
        name: str,
        base_path: Path,
        vault_root: Path,
        git: GitBackend,
        lock: asyncio.Lock,
        config: VaultConfig,
    ) -> None:
        super().__init__(
            base_path=base_path,
            vault_root=vault_root,
            git=git,
            lock=lock,
            config=config,
        )
        self.name = name

    @property
    def _store_meta_file(self) -> Path:
        return self._base_path / "_meta" / "store.json"

    async def _validate_slot_content(
        self, path: str | None, content: dict[str, Any]
    ) -> None:
        """Validate *content* against this store's slot schema (if any)."""
        if path:
            meta = await self.get_meta()
            meta.validate_slot_content(path, content)

    async def _check_not_locked(self) -> None:
        """Raise StoreValidationError if this store is locked."""
        meta = await self.get_meta()
        if meta.locked:
            raise StoreValidationError(
                f"Store {self.name!r} is locked; use deploy() to update documents"
            )

    async def update_doc(self, doc_id: str, inp: UpdateDocInput) -> Document:
        await self._check_not_locked()
        return await super().update_doc(doc_id, inp)

    async def delete_doc(self, doc_id: str) -> None:
        await self._check_not_locked()
        return await super().delete_doc(doc_id)

    async def get_meta(self) -> StoreMeta:
        """Return this store's metadata."""
        if not self._store_meta_file.exists():
            raise StoreNotFoundError(f"Store {self.name!r} metadata not found")
        return StoreMeta(**await self._read_json(self._store_meta_file))

    async def bump_version(
        self, bump_type: Literal["major", "minor", "patch"]
    ) -> StoreMeta:
        """Increment this store's semantic version counter."""
        meta = await self.get_meta()
        v = meta.version
        if bump_type == "major":
            new_v = (v // 10000 + 1) * 10000
        elif bump_type == "minor":
            new_v = v + 100
        else:
            new_v = v + 1
        content_hash = meta.id.split(":")[-1]
        new_history = dict(meta.version_history)
        new_history[content_hash] = new_v
        new_meta = meta.model_copy(
            update={
                "version": new_v,
                "version_history": new_history,
                "updated_at": datetime.now(UTC),
            }
        )
        async with self._lock:
            await self._write_json(
                self._store_meta_file, new_meta.model_dump(mode="json")
            )
            await self._git.commit(
                f"Bump store {self.name!r} version to {new_v}",
                self._config.git_author_name,
                self._config.git_author_email,
            )
        return new_meta

    async def validate(self) -> StoreValidation:
        """Check whether all required slots in this store are occupied."""
        meta = await self.get_meta()
        metas = await self.list_docs()
        occupied = {m.path for m in metas if m.path is not None}
        return meta.check_structure(occupied)

    async def export_zip(self) -> bytes:
        """Package this store and all its documents into an in-memory zip archive."""
        return await self._parent_export_zip(self.name)

    # Internal helper so export can be called from the parent node.
    async def _parent_export_zip(self, name: str) -> bytes:
        return await super().export_store_zip(name)


# ── DocVault — root vault ──────────────────────────────────────────────────────


class DocVault(_VaultNode):
    """Git-backed document vault.

    The top-level container that owns the git repository, configuration,
    and vault-level metadata.  Free-floating documents live directly in the
    vault; named :class:`Store` sub-vaults provide structured namespaces.
    """

    def __init__(self, config: VaultConfig) -> None:
        super().__init__(
            base_path=config.vault_path,
            vault_root=config.vault_path,
            git=GitBackend(config.vault_path),
            lock=asyncio.Lock(),
            config=config,
        )

    @property
    def _vault_meta_path(self) -> Path:
        return self._base_path / "_meta" / "vault.json"

    def _make_vault_dirs(self) -> None:
        self._make_dirs()
        self._vault_meta_path.parent.mkdir(parents=True, exist_ok=True)

    async def init(self) -> None:
        """Create vault if it doesn't exist; idempotent."""
        await asyncio.to_thread(self._make_vault_dirs)
        await self._git.init()
        if not self._vault_meta_path.exists():
            vault = VaultMeta(
                name=self._config.vault_name,
                description=self._config.vault_description,
            )
            await self._write_json(
                self._vault_meta_path, vault.model_dump(mode="json")
            )
        async with self._lock:
            if await self._git.is_empty():
                await self._git.commit(
                    "Initialize docvault",
                    self._config.git_author_name,
                    self._config.git_author_email,
                )

    async def open(self) -> None:
        """Open an existing vault; raises VaultNotFoundError if missing."""
        if not (self._config.vault_path / ".git").exists():
            raise VaultNotFoundError(
                f"No vault at {self._config.vault_path}. "
                "Run 'docvault init <path>' to create one."
            )
        await self._git.init()

    async def get_vault(self) -> VaultMeta:
        """Return vault-level metadata."""
        return VaultMeta(**await self._read_json(self._vault_meta_path))

    async def bump_vault_version(
        self, bump_type: Literal["major", "minor", "patch"]
    ) -> VaultMeta:
        """Bump the vault's semantic version and create a git tag."""
        vault = await self.get_vault()
        new_version = vault.version.bump(bump_type)
        vault = vault.model_copy(
            update={"version": new_version, "updated_at": datetime.now(UTC)}
        )
        async with self._lock:
            await self._write_json(
                self._vault_meta_path, vault.model_dump(mode="json")
            )
            await self._git.commit(
                f"Bump vault version to {new_version}",
                self._config.git_author_name,
                self._config.git_author_email,
            )
            await self._git.create_tag(
                f"v{new_version}", f"Vault version {new_version}"
            )
        return vault

    async def list_vault_versions(self) -> list[str]:
        """Return all git tags that represent vault version snapshots."""
        tags = await self._git.list_tags()
        return [t for t in tags if t.startswith("v")]

    async def deploy_store(self, inp: DeployStoreInput) -> list[Document]:
        """Batch-create documents in a named store in one atomic git commit."""
        store_obj = await self.get_store(inp.store_name)
        return await store_obj.deploy(inp.documents, commit_message=inp.commit_message)
