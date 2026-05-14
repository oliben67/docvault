from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response

from ..core.document import (
    CommitInfo,
    CreateDocInput,
    Document,
    DocumentMeta,
    UpdateDocInput,
)
from ..core.vault import DocVault
from ..core.store import (
    DeployStoreInput,
    StoreMeta,
    StoreCreateInput,
    StoreRef,
    StoreValidation,
)
from ..core.vault_meta import VaultMeta
from ..exceptions import (
    DocumentNotFoundError,
    SummarizationError,
    StoreNotFoundError,
    StoreValidationError,
)


def create_router(
    vault: DocVault,
    auth_dep: Callable,
    prefix: str = "/api/v1",
) -> APIRouter:
    router = APIRouter(prefix=prefix)

    # ── Health ─────────────────────────────────────────────────────────────

    @router.get(
        "/health",
        tags=["Health"],
        summary="Health check",
        response_description="Server is running",
    )
    async def health() -> dict[str, str]:
        """Returns `{"status": "ok"}` unconditionally. No authentication required."""
        return {"status": "ok"}

    # ── Documents (root-level, free-floating) ──────────────────────────────

    @router.get(
        "/docs",
        response_model=list[DocumentMeta],
        tags=["Documents"],
        summary="List root documents",
        response_description="Array of document metadata records, newest first",
    )
    async def list_docs(
        keywords: str | None = None,
        creator: str | None = None,
        _p: Any = Depends(auth_dep),
    ) -> list[DocumentMeta]:
        """List metadata for all free-floating (root-level) documents.

        Results are sorted newest-first. All filters are AND-combined.

        - **keywords** — comma-separated list; only documents that have *all* listed
          keywords (case-insensitive) are returned.
        - **creator** — exact match on the creator field.

        Documents stored inside a :class:`~docvault.core.vault.Store` are accessed
        via ``GET /stores/{name}/docs``.
        """
        kw_list = [k.strip() for k in keywords.split(",")] if keywords else None
        return await vault.list_docs(keywords=kw_list, creator=creator)

    @router.post(
        "/docs",
        response_model=Document,
        status_code=status.HTTP_201_CREATED,
        tags=["Documents"],
        summary="Create a root document",
        response_description="The newly created document including generated metadata",
    )
    async def create_doc(inp: CreateDocInput, _p: Any = Depends(auth_dep)) -> Document:
        """Create a new free-floating document and commit it to the vault.

        If **creator** is omitted the authenticated user is used automatically.
        If `auto_summarize` is enabled and no **summary** is provided, the LLM
        infers summary and keywords.
        """
        if not inp.creator:
            inp = inp.model_copy(update={"creator": _p.get("user", "anonymous")})
        try:
            return await vault.create_doc(inp)
        except StoreValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.get(
        "/docs/{doc_id}",
        response_model=Document,
        tags=["Documents"],
        summary="Get a root document",
        response_description="Document with metadata and content",
        responses={404: {"description": "Document not found"}},
    )
    async def get_doc(doc_id: str, _p: Any = Depends(auth_dep)) -> Document:
        """Fetch the current content and metadata for a single root document."""
        try:
            return await vault.get_doc(doc_id)
        except DocumentNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.put(
        "/docs/{doc_id}",
        response_model=Document,
        tags=["Documents"],
        summary="Replace a root document's content",
        response_description="Document after update",
        responses={404: {"description": "Document not found"}},
    )
    async def update_doc(
        doc_id: str, inp: UpdateDocInput, _p: Any = Depends(auth_dep)
    ) -> Document:
        """Replace the entire content of an existing root document."""
        try:
            return await vault.update_doc(doc_id, inp)
        except DocumentNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.delete(
        "/docs/{doc_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        tags=["Documents"],
        summary="Delete a root document",
        responses={404: {"description": "Document not found"}},
    )
    async def delete_doc(doc_id: str, _p: Any = Depends(auth_dep)) -> None:
        """Permanently delete a document. The deletion is recorded as a git commit."""
        try:
            await vault.delete_doc(doc_id)
        except DocumentNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get(
        "/docs/{doc_id}/history",
        response_model=list[CommitInfo],
        tags=["Documents"],
        summary="Document commit history",
        responses={404: {"description": "Document not found"}},
    )
    async def doc_history(
        doc_id: str, max_count: int = 50, _p: Any = Depends(auth_dep)
    ) -> list[CommitInfo]:
        """Return the git commit log for a single document."""
        try:
            return await vault.get_doc_history(doc_id, max_count=max_count)
        except DocumentNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get(
        "/docs/{doc_id}/at/{ref}",
        response_model=Document,
        tags=["Documents"],
        summary="Get a document at a historical git ref",
        responses={404: {"description": "Document or ref not found"}},
    )
    async def doc_at_ref(
        doc_id: str, ref: str, _p: Any = Depends(auth_dep)
    ) -> Document:
        """Retrieve a point-in-time snapshot of a document."""
        try:
            return await vault.get_doc_at_ref(doc_id, ref)
        except DocumentNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post(
        "/docs/{doc_id}/summarize",
        response_model=Document,
        tags=["Documents", "Summarization"],
        summary="Infer summary and keywords via LLM",
        responses={
            404: {"description": "Document not found"},
            503: {"description": "LLM not configured or call failed"},
        },
    )
    async def summarize_doc(
        doc_id: str, overwrite: bool = False, _p: Any = Depends(auth_dep)
    ) -> Document:
        """Call the LLM to generate a summary and keyword list for a document."""
        try:
            return await vault.summarize_doc(doc_id, overwrite=overwrite)
        except DocumentNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except SummarizationError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @router.post(
        "/docs/summarize/all",
        response_model=list[Document],
        tags=["Documents", "Summarization"],
        summary="Infer metadata for all un-summarized documents",
        responses={503: {"description": "LLM not configured or call failed"}},
    )
    async def summarize_all(
        overwrite: bool = False, _p: Any = Depends(auth_dep)
    ) -> list[Document]:
        """Run LLM inference on every root document that is missing a summary."""
        try:
            return await vault.summarize_all(overwrite=overwrite)
        except SummarizationError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    # ── Stores ─────────────────────────────────────────────────────────────

    @router.get(
        "/stores",
        response_model=list[StoreMeta],
        tags=["Stores"],
        summary="List stores",
        response_description="All registered stores in this vault",
    )
    async def list_stores(_p: Any = Depends(auth_dep)) -> list[StoreMeta]:
        """Return metadata for all stores in this vault."""
        return await vault.list_stores()

    @router.post(
        "/stores",
        response_model=StoreRef,
        status_code=status.HTTP_201_CREATED,
        tags=["Stores"],
        summary="Create a store",
        response_description="Name and ID of the created store",
        responses={
            422: {
                "description": "A slot's json_schema is not a valid JSON Schema draft-7"
            }
        },
    )
    async def create_store(
        inp: StoreCreateInput, _p: Any = Depends(auth_dep)
    ) -> StoreRef:
        """Register or update a store and return its name and ID.

        **structure** maps slot paths to :class:`~docvault.core.store.DocSlot` definitions.

        **locked** (optional, default false) — when true, documents in this store can
        only be updated via the batch ``deploy`` method; individual ``PUT /stores/{name}/docs/{id}``
        calls are rejected.

        If a store with the same name already exists and the structure is identical
        (same content hash) the call is a no-op and returns the existing ref.
        A changed structure increments the version counter.
        """
        store_obj = await vault.create_store(inp, creator=_p.get("user"))
        meta = await store_obj.get_meta()
        return StoreRef(name=meta.name, id=meta.id)

    @router.get(
        "/stores/{store_name}",
        response_model=StoreMeta,
        tags=["Stores"],
        summary="Get a store",
        response_description="Store metadata including structure and version",
        responses={404: {"description": "Store not found"}},
    )
    async def get_store(store_name: str, _p: Any = Depends(auth_dep)) -> StoreMeta:
        """Fetch a store's metadata by name."""
        try:
            store_obj = await vault.get_store(store_name)
            return await store_obj.get_meta()
        except StoreNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get(
        "/stores/{store_name}/validate",
        response_model=StoreValidation,
        tags=["Stores"],
        summary="Validate store slot coverage",
        responses={404: {"description": "Store not found"}},
    )
    async def validate_store(
        store_name: str, _p: Any = Depends(auth_dep)
    ) -> StoreValidation:
        """Check whether all required slots in the store are occupied by documents."""
        try:
            return await vault.validate_store(store_name)
        except StoreNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get(
        "/stores/{store_name}/export",
        tags=["Stores"],
        summary="Export store as a zip archive",
        responses={
            200: {"content": {"application/zip": {}}, "description": "Zip archive"},
            404: {"description": "Store not found"},
        },
    )
    async def export_store(
        store_name: str, _p: Any = Depends(auth_dep)
    ) -> Response:
        """Download the store's metadata and all its documents as a zip archive."""
        try:
            zip_bytes = await vault.export_store_zip(store_name)
        except StoreNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return Response(
            content=zip_bytes,
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="{store_name}.zip"'
            },
        )

    @router.delete(
        "/stores/{store_name}",
        status_code=status.HTTP_204_NO_CONTENT,
        tags=["Stores"],
        summary="Delete a store",
        responses={404: {"description": "Store not found"}},
    )
    async def delete_store(store_name: str, _p: Any = Depends(auth_dep)) -> None:
        """Delete a store and all its documents."""
        try:
            await vault.delete_store(store_name)
        except StoreNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    # ── Store documents ────────────────────────────────────────────────────

    @router.get(
        "/stores/{store_name}/docs",
        response_model=list[DocumentMeta],
        tags=["Stores", "Documents"],
        summary="List documents in a store",
        responses={404: {"description": "Store not found"}},
    )
    async def list_store_docs(
        store_name: str,
        keywords: str | None = None,
        creator: str | None = None,
        _p: Any = Depends(auth_dep),
    ) -> list[DocumentMeta]:
        """List all documents in a named store."""
        try:
            store_obj = await vault.get_store(store_name)
        except StoreNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        kw_list = [k.strip() for k in keywords.split(",")] if keywords else None
        return await store_obj.list_docs(keywords=kw_list, creator=creator)

    @router.post(
        "/stores/{store_name}/docs",
        response_model=Document,
        status_code=status.HTTP_201_CREATED,
        tags=["Stores", "Documents"],
        summary="Create a document in a store",
        responses={
            404: {"description": "Store not found"},
            422: {"description": "Content violates the store's slot JSON Schema"},
        },
    )
    async def create_store_doc(
        store_name: str, inp: CreateDocInput, _p: Any = Depends(auth_dep)
    ) -> Document:
        """Create a document inside a named store."""
        if not inp.creator:
            inp = inp.model_copy(update={"creator": _p.get("user", "anonymous")})
        try:
            store_obj = await vault.get_store(store_name)
            return await store_obj.create_doc(inp)
        except StoreNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except StoreValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.get(
        "/stores/{store_name}/docs/{doc_id}",
        response_model=Document,
        tags=["Stores", "Documents"],
        summary="Get a document in a store",
        responses={404: {"description": "Store or document not found"}},
    )
    async def get_store_doc(
        store_name: str, doc_id: str, _p: Any = Depends(auth_dep)
    ) -> Document:
        """Fetch a document from a named store."""
        try:
            store_obj = await vault.get_store(store_name)
            return await store_obj.get_doc(doc_id)
        except (StoreNotFoundError, DocumentNotFoundError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.put(
        "/stores/{store_name}/docs/{doc_id}",
        response_model=Document,
        tags=["Stores", "Documents"],
        summary="Update a document in a store",
        responses={
            404: {"description": "Store or document not found"},
            422: {
                "description": "Content violates the store's slot JSON Schema, "
                "or store is locked"
            },
        },
    )
    async def update_store_doc(
        store_name: str,
        doc_id: str,
        inp: UpdateDocInput,
        _p: Any = Depends(auth_dep),
    ) -> Document:
        """Replace a document's content inside a named store.

        Raises 422 if the store was created with ``locked=true``.
        Use ``POST /stores/{name}/deploy`` to update locked stores.
        """
        try:
            store_obj = await vault.get_store(store_name)
            return await store_obj.update_doc(doc_id, inp)
        except (StoreNotFoundError, DocumentNotFoundError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except StoreValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.delete(
        "/stores/{store_name}/docs/{doc_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        tags=["Stores", "Documents"],
        summary="Delete a document in a store",
        responses={
            404: {"description": "Store or document not found"},
            422: {"description": "Store is locked"},
        },
    )
    async def delete_store_doc(
        store_name: str, doc_id: str, _p: Any = Depends(auth_dep)
    ) -> None:
        """Delete a document from a named store."""
        try:
            store_obj = await vault.get_store(store_name)
            await store_obj.delete_doc(doc_id)
        except (StoreNotFoundError, DocumentNotFoundError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except StoreValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.get(
        "/stores/{store_name}/docs/{doc_id}/history",
        response_model=list[CommitInfo],
        tags=["Stores", "Documents"],
        summary="Document commit history (store)",
        responses={404: {"description": "Store or document not found"}},
    )
    async def store_doc_history(
        store_name: str,
        doc_id: str,
        max_count: int = 50,
        _p: Any = Depends(auth_dep),
    ) -> list[CommitInfo]:
        """Return the git commit log for a document inside a store."""
        try:
            store_obj = await vault.get_store(store_name)
            return await store_obj.get_doc_history(doc_id, max_count=max_count)
        except (StoreNotFoundError, DocumentNotFoundError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get(
        "/stores/{store_name}/docs/{doc_id}/at/{ref}",
        response_model=Document,
        tags=["Stores", "Documents"],
        summary="Get a store document at a historical git ref",
        responses={404: {"description": "Store, document, or ref not found"}},
    )
    async def store_doc_at_ref(
        store_name: str, doc_id: str, ref: str, _p: Any = Depends(auth_dep)
    ) -> Document:
        """Retrieve a point-in-time snapshot of a store document."""
        try:
            store_obj = await vault.get_store(store_name)
            return await store_obj.get_doc_at_ref(doc_id, ref)
        except (StoreNotFoundError, DocumentNotFoundError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post(
        "/stores/{store_name}/deploy",
        response_model=list[Document],
        status_code=status.HTTP_201_CREATED,
        tags=["Stores"],
        summary="Batch-deploy documents into a store",
        responses={
            404: {"description": "Store not found"},
            422: {
                "description": "One or more documents violate the store's slot JSON Schema"
            },
        },
    )
    async def deploy_store(
        store_name: str, inp: DeployStoreInput, _p: Any = Depends(auth_dep)
    ) -> list[Document]:
        """Create multiple documents in a store in a single atomic git commit.

        This is the correct way to update a ``locked`` store.
        All documents are written and committed atomically; if any document
        fails schema validation the entire batch is rejected.
        """
        try:
            store_obj = await vault.get_store(store_name)
            return await store_obj.deploy(inp.documents, commit_message=inp.commit_message)
        except StoreNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except StoreValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    # ── Vault metadata ─────────────────────────────────────────────────────

    @router.get(
        "/vault",
        response_model=VaultMeta,
        tags=["Vault"],
        summary="Get vault metadata",
    )
    async def get_vault(_p: Any = Depends(auth_dep)) -> VaultMeta:
        """Return the vault's current metadata including the semantic version."""
        return await vault.get_vault()

    @router.get(
        "/vault/versions",
        response_model=list[str],
        tags=["Vault"],
        summary="List vault version tags",
    )
    async def list_vault_versions(_p: Any = Depends(auth_dep)) -> list[str]:
        """Return all git tags that represent vault version snapshots (e.g. `v1.2.3`)."""
        return await vault.list_vault_versions()

    @router.post(
        "/vault/version/{bump_type}",
        response_model=VaultMeta,
        tags=["Vault"],
        summary="Bump the vault version",
    )
    async def bump_vault_version(
        bump_type: Literal["major", "minor", "patch"],
        _p: Any = Depends(auth_dep),
    ) -> VaultMeta:
        """Increment the vault's semantic version and create a git tag."""
        return await vault.bump_vault_version(bump_type)

    return router
