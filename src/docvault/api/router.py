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
from ..core.vault_meta import VaultMeta
from ..core.store import DocVault
from ..core.template import (
    DeployVaultInput,
    Template,
    TemplateCreateInput,
    TemplateRef,
    TemplateValidation,
)
from ..exceptions import (
    DocumentNotFoundError,
    SummarizationError,
    TemplateNotFoundError,
    TemplateValidationError,
)


def create_router(
    store: DocVault,
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

    # ── Documents ──────────────────────────────────────────────────────────

    @router.get(
        "/docs",
        response_model=list[DocumentMeta],
        tags=["Documents"],
        summary="List documents",
        response_description="Array of document metadata records, newest first",
    )
    async def list_docs(
        keywords: str | None = None,
        creator: str | None = None,
        template: str | None = None,
        _p: Any = Depends(auth_dep),
    ) -> list[DocumentMeta]:
        """List metadata for all documents.

        Results are sorted newest-first. All filters are AND-combined.

        - **keywords** — comma-separated list; only documents that have *all* listed
          keywords (case-insensitive) are returned.
        - **creator** — exact match on the creator field.
        - **template** — exact match on the template name.
        """
        kw_list = [k.strip() for k in keywords.split(",")] if keywords else None
        return await store.list_docs(
            keywords=kw_list, creator=creator, template=template
        )

    @router.post(
        "/docs",
        response_model=Document,
        status_code=status.HTTP_201_CREATED,
        tags=["Documents"],
        summary="Create a document",
        response_description="The newly created document including generated metadata",
        responses={
            404: {"description": "Named template not found"},
            422: {"description": "Content violates the template's JSON Schema"},
        },
    )
    async def create_doc(inp: CreateDocInput, _p: Any = Depends(auth_dep)) -> Document:
        """Create a new document and commit it to the vault.

        If **creator** is omitted, the authenticated user (or the shim's `app_name`)
        is used automatically.

        If **template** is set the content is validated against that template's JSON
        Schema and any declared defaults are merged in first.

        If `auto_summarize` is enabled in the vault config and no **summary** is
        provided, the LLM is called to infer a summary and keywords automatically.
        """
        if not inp.creator:
            inp = inp.model_copy(update={"creator": _p.get("user", "anonymous")})
        try:
            return await store.create_doc(inp)
        except TemplateNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except TemplateValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.get(
        "/docs/{doc_id}",
        response_model=Document,
        tags=["Documents"],
        summary="Get a document",
        response_description="Document with metadata and content",
        responses={404: {"description": "Document not found"}},
    )
    async def get_doc(doc_id: str, _p: Any = Depends(auth_dep)) -> Document:
        """Fetch the current content and metadata for a single document."""
        try:
            return await store.get_doc(doc_id)
        except DocumentNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.put(
        "/docs/{doc_id}",
        response_model=Document,
        tags=["Documents"],
        summary="Replace a document's content",
        response_description="Document after update",
        responses={
            404: {"description": "Document not found"},
            422: {"description": "Content violates the template's JSON Schema"},
        },
    )
    async def update_doc(
        doc_id: str, inp: UpdateDocInput, _p: Any = Depends(auth_dep)
    ) -> Document:
        """Replace the entire content of an existing document.

        Omit **summary** or **keywords** to keep the existing values.
        Providing an empty string for **summary** clears it.
        """
        try:
            return await store.update_doc(doc_id, inp)
        except DocumentNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except TemplateValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.delete(
        "/docs/{doc_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        tags=["Documents"],
        summary="Delete a document",
        responses={404: {"description": "Document not found"}},
    )
    async def delete_doc(doc_id: str, _p: Any = Depends(auth_dep)) -> None:
        """Permanently delete a document from the vault.

        The deletion is recorded as a git commit, so the document remains
        recoverable via `GET /docs/{id}/at/{ref}` using a historical commit SHA.
        """
        try:
            await store.delete_doc(doc_id)
        except DocumentNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get(
        "/docs/{doc_id}/history",
        response_model=list[CommitInfo],
        tags=["Documents"],
        summary="Document commit history",
        response_description="List of commits that touched this document, newest first",
        responses={404: {"description": "Document not found"}},
    )
    async def doc_history(
        doc_id: str, max_count: int = 50, _p: Any = Depends(auth_dep)
    ) -> list[CommitInfo]:
        """Return the git commit log for a single document.

        Each entry includes the commit SHA (use it with `GET /docs/{id}/at/{ref}`),
        author, timestamp, and message.
        """
        try:
            return await store.get_doc_history(doc_id, max_count=max_count)
        except DocumentNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get(
        "/docs/{doc_id}/at/{ref}",
        response_model=Document,
        tags=["Documents"],
        summary="Get a document at a historical git ref",
        response_description="Document as it existed at the given ref",
        responses={404: {"description": "Document or ref not found"}},
    )
    async def doc_at_ref(
        doc_id: str, ref: str, _p: Any = Depends(auth_dep)
    ) -> Document:
        """Retrieve a point-in-time snapshot of a document.

        **ref** can be any git ref: a full or abbreviated commit SHA, a tag name,
        or a branch name. Use `GET /docs/{id}/history` to discover valid SHAs.
        """
        try:
            return await store.get_doc_at_ref(doc_id, ref)
        except DocumentNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post(
        "/docs/{doc_id}/summarize",
        response_model=Document,
        tags=["Documents", "Summarization"],
        summary="Infer summary and keywords via LLM",
        response_description="Document with updated summary and keywords",
        responses={
            404: {"description": "Document not found"},
            503: {"description": "LLM not configured or call failed"},
        },
    )
    async def summarize_doc(
        doc_id: str,
        overwrite: bool = False,
        _p: Any = Depends(auth_dep),
    ) -> Document:
        """Call the LLM to generate a summary and keyword list for a document.

        Skips documents that already have a summary unless **overwrite=true**.
        Requires `llm_api_key` to be set in the vault configuration.
        """
        try:
            return await store.summarize_doc(doc_id, overwrite=overwrite)
        except DocumentNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except SummarizationError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @router.post(
        "/docs/summarize/all",
        response_model=list[Document],
        tags=["Documents", "Summarization"],
        summary="Infer metadata for all un-summarized documents",
        response_description="Documents that were updated",
        responses={503: {"description": "LLM not configured or call failed"}},
    )
    async def summarize_all(
        overwrite: bool = False, _p: Any = Depends(auth_dep)
    ) -> list[Document]:
        """Run LLM inference on every document that is missing a summary.

        Pass **overwrite=true** to re-run inference on documents that already
        have a summary. Only updated documents are returned.
        """
        try:
            return await store.summarize_all(overwrite=overwrite)
        except SummarizationError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    # ── Templates ──────────────────────────────────────────────────────────

    @router.get(
        "/templates",
        response_model=list[Template],
        tags=["Templates"],
        summary="List templates",
        response_description="All registered templates",
    )
    async def list_templates(_p: Any = Depends(auth_dep)) -> list[Template]:
        """Return all templates stored in the vault."""
        return await store.list_templates()

    @router.post(
        "/templates",
        response_model=TemplateRef,
        status_code=status.HTTP_201_CREATED,
        tags=["Templates"],
        summary="Create a template",
        response_description="Name and ID of the created template",
        responses={
            422: {
                "description": "A slot's json_schema is not a valid JSON Schema draft-7"
            }
        },
    )
    async def create_template(
        inp: TemplateCreateInput, _p: Any = Depends(auth_dep)
    ) -> TemplateRef:
        """Register a new template and return its name and ID.

        **structure** maps slot paths to :class:`DocSlot` definitions.

        **path** (optional server-side filesystem path) may point to a folder
        (scans all files recursively) or a single file (creates one flat slot).
        LLM summarization is applied to each ingested document when configured.

        Use the returned **id** for all subsequent operations on this template.
        """
        return await store.create_template(inp, creator=_p.get("user"))

    @router.get(
        "/templates/{template_id}",
        response_model=Template,
        tags=["Templates"],
        summary="Get a template",
        response_description="Template definition including the full slot structure",
        responses={404: {"description": "Template not found"}},
    )
    async def get_template(template_id: str, _p: Any = Depends(auth_dep)) -> Template:
        """Fetch a single template by its ID."""
        try:
            return await store.get_template(template_id)
        except TemplateNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get(
        "/templates/{template_id}/validate",
        response_model=TemplateValidation,
        tags=["Templates"],
        summary="Validate template structure against the vault",
        response_description="Which required slots are satisfied, missing, or have unexpected documents",
        responses={404: {"description": "Template not found"}},
    )
    async def validate_template(
        template_id: str, _p: Any = Depends(auth_dep)
    ) -> TemplateValidation:
        """Check whether all required slots in the template are occupied by vault documents."""
        try:
            return await store.validate_template(template_id)
        except TemplateNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get(
        "/templates/{template_id}/export",
        tags=["Templates"],
        summary="Export template as a zip archive",
        response_description=(
            "Zip file containing _template.json plus each slot document "
            "at its slot path (e.g. config/app.json)"
        ),
        responses={
            200: {"content": {"application/zip": {}}, "description": "Zip archive"},
            404: {"description": "Template not found"},
        },
    )
    async def export_template(
        template_id: str, _p: Any = Depends(auth_dep)
    ) -> Response:
        """Download the template and all its slot documents as a zip archive."""
        try:
            zip_bytes = await store.export_template_zip(template_id)
            tpl = await store.get_template(template_id)
        except TemplateNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return Response(
            content=zip_bytes,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{tpl.name}.zip"'},
        )

    @router.delete(
        "/templates/{template_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        tags=["Templates"],
        summary="Delete a template",
        responses={404: {"description": "Template not found"}},
    )
    async def delete_template(template_id: str, _p: Any = Depends(auth_dep)) -> None:
        """Delete a template. Existing documents that reference this template are unaffected."""
        try:
            await store.delete_template(template_id)
        except TemplateNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    # ── Vault metadata ─────────────────────────────────────────────────────

    @router.get(
        "/vault",
        response_model=VaultMeta,
        tags=["Vault"],
        summary="Get vault metadata",
        response_description="Current vault name, version, and timestamps",
    )
    async def get_vault(_p: Any = Depends(auth_dep)) -> VaultMeta:
        """Return the vault's current metadata including the semantic version."""
        return await store.get_vault()

    @router.get(
        "/vault/versions",
        response_model=list[str],
        tags=["Vault"],
        summary="List vault version tags",
        response_description="All git tags that begin with 'v'",
    )
    async def list_vault_versions(_p: Any = Depends(auth_dep)) -> list[str]:
        """Return all git tags that represent vault version snapshots (e.g. `v1.2.3`)."""
        return await store.list_vault_versions()

    @router.post(
        "/vault/version/{bump_type}",
        response_model=VaultMeta,
        tags=["Vault"],
        summary="Bump the vault version",
        response_description="Vault metadata after the version bump",
    )
    async def bump_vault_version(
        bump_type: Literal["major", "minor", "patch"],
        _p: Any = Depends(auth_dep),
    ) -> VaultMeta:
        """Increment the vault's semantic version and create a git tag.

        - `major` — resets minor and patch to 0 (e.g. 1.2.3 → 2.0.0)
        - `minor` — resets patch to 0 (e.g. 1.2.3 → 1.3.0)
        - `patch` — increments patch only (e.g. 1.2.3 → 1.2.4)
        """
        return await store.bump_vault_version(bump_type)

    @router.post(
        "/vault/deploy",
        response_model=list[Document],
        status_code=status.HTTP_201_CREATED,
        tags=["Vault"],
        summary="Batch-deploy documents from a template",
        response_description="All created documents",
        responses={
            404: {"description": "Named template not found"},
            422: {
                "description": "One or more documents violate the template's JSON Schema"
            },
        },
    )
    async def deploy_vault(
        inp: DeployVaultInput, _p: Any = Depends(auth_dep)
    ) -> list[Document]:
        """Create multiple documents in a single atomic git commit.

        All documents in the batch are written together and committed atomically.
        If any document fails schema validation the entire batch is rejected.
        """
        try:
            return await store.deploy_vault(inp)
        except TemplateNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except TemplateValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    return router
