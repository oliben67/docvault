# DocVault REST API Reference

## Overview

| Property | Value |
|----------|-------|
| Base URL (standalone) | `http://localhost:8000` |
| Default route prefix | `/api/v1` |
| Content-Type | `application/json` |
| OpenAPI spec | `GET /openapi.json` |
| Swagger UI | `GET /docs` |
| ReDoc | `GET /redoc` |

All request bodies must be JSON. All responses are JSON (or empty for 204).
Timestamps are ISO 8601 UTC (e.g. `2024-01-15T10:30:00Z`).

---

## Interactive testing

The fastest way to explore the API is the built-in **Swagger UI**:

```bash
# Start the server
docvault serve

# Open in browser
open http://localhost:8000/docs
```

The Swagger UI lets you:
- Browse every endpoint with full schema documentation
- Click **Try it out** on any endpoint to edit the request body and fire a real request
- See the exact `curl` command that was executed
- Use the **Authorize** button to set your API key once for all requests

**ReDoc** (read-only, better for sharing): `http://localhost:8000/redoc`

### Exporting the spec

```bash
# From a running server
curl http://localhost:8000/openapi.json > docs/openapi.json

# Without a running server (via Taskfile)
task openapi

# Import into Swagger Editor
open https://editor.swagger.io   # paste the JSON content

# Import into Insomnia / Postman
# File → Import → select docs/openapi.json
```

---

## Authentication

Set via `auth_mode` in your config. The auth header is the same regardless of mode.

### None (default — development only)

No authentication required. All requests succeed.

### API key

```
X-API-Key: sk-your-key-here
```

Returns `401 Unauthorized` if the key is missing or invalid.

```bash
curl -H "X-API-Key: sk-your-key" http://localhost:8000/api/v1/docs
```

Generate a key:

```bash
docvault config generate-key
# Output: dK3mXq7vZ9... (URL-safe random 32-byte token)
```

### Passthrough / custom

When embedded via `DocVaultShim(auth_dep=...)`, authentication is handled entirely by your own dependency. The scheme is whatever your dep expects.

---

## Common response schemas

### Document

```json
{
  "meta": {
    "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
    "created_at": "2024-01-15T10:30:00Z",
    "updated_at": "2024-01-15T11:00:00Z",
    "creator": "alice",
    "summary": "Q1 planning document",
    "keywords": ["planning", "q1"],
    "size_bytes": 128,
    "template": "report",
    "named_version": "v1"
  },
  "content": {
    "title": "Q1 Report",
    "status": "draft"
  }
}
```

### DocumentMeta

Same as `Document.meta` — returned by `GET /docs` (list endpoint).

### CommitInfo

```json
{
  "sha": "a3f9c1d",
  "message": "Update document 3fa85f64",
  "author": "alice",
  "timestamp": "2024-01-15T11:00:00Z"
}
```

### Template

```json
{
  "name": "employee",
  "description": "Employee record",
  "json_schema": {
    "type": "object",
    "required": ["name", "role"],
    "properties": {
      "name": { "type": "string" },
      "role": { "type": "string" },
      "active": { "type": "boolean" }
    }
  },
  "defaults": { "active": true },
  "created_at": "2024-01-10T08:00:00Z"
}
```

### FleetMeta

```json
{
  "name": "production",
  "description": "Production document store",
  "version": { "major": 1, "minor": 2, "patch": 0 },
  "created_at": "2024-01-01T00:00:00Z",
  "updated_at": "2024-01-15T10:00:00Z"
}
```

### Error

```json
{ "detail": "Document '3fa85f64' not found" }
```

---

## Health

### `GET /api/v1/health`

Liveness probe. No authentication required.

**Response `200`**
```json
{ "status": "ok" }
```

**curl**
```bash
curl http://localhost:8000/api/v1/health
```

---

## Documents

### `GET /api/v1/docs`

List metadata for all documents, newest first. All query parameters are optional and AND-combined.

**Query parameters**

| Parameter | Type | Description |
|-----------|------|-------------|
| `keywords` | string | Comma-separated. Only documents that have **all** listed keywords are returned (case-insensitive). |
| `creator` | string | Exact match on `creator`. |
| `template` | string | Exact match on `template` name. |

**Response `200`** — `DocumentMeta[]`

**curl**
```bash
# All documents
curl http://localhost:8000/api/v1/docs

# Filter examples
curl "http://localhost:8000/api/v1/docs?creator=alice"
curl "http://localhost:8000/api/v1/docs?template=employee&keywords=engineering,platform"
```

---

### `POST /api/v1/docs`

Create a new document. Returns `201 Created`.

**Request body**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `content` | object | yes | Arbitrary JSON object — the document payload |
| `creator` | string | yes | Who is creating the document |
| `summary` | string | no | Human-readable description (default: `""`) |
| `keywords` | string[] | no | Tags for filtering (default: `[]`) |
| `template` | string | no | Template name — tags the document as belonging to that template's folder structure |
| `path` | string | no | Slot path within the template (e.g. `"config/app"`). Required to validate content against the slot's `json_schema`. |
| `named_version` | string | no | Arbitrary version label stored in metadata |
| `commit_message` | string | no | Custom git commit message |

When **both** `template` and `path` are provided:
- If the slot has a `json_schema`, the content is validated against it (`422` on failure).
- If the path does not match any slot in the template, the document is created anyway and will appear as `extra` in `GET /templates/{name}/validate`.

**Response `201`** — `Document`

**Errors**
- `404` — named template not found
- `422` — content violates the slot's `json_schema`

**curl**
```bash
# Bare document — no template
curl -X POST http://localhost:8000/api/v1/docs \
  -H "Content-Type: application/json" \
  -d '{
    "content": { "title": "Q1 Report", "status": "draft" },
    "creator": "alice",
    "summary": "First quarter planning document",
    "keywords": ["planning", "q1"]
  }'

# Document filling a template slot
curl -X POST http://localhost:8000/api/v1/docs \
  -H "Content-Type: application/json" \
  -d '{
    "content": { "host": "api.example.com", "port": 8080 },
    "creator": "platform-bot",
    "template": "microservice",
    "path": "config/app"
  }'
```

---

### `GET /api/v1/docs/{doc_id}`

Fetch the current content and metadata for a single document.

**Path parameters**

| Parameter | Description |
|-----------|-------------|
| `doc_id` | Document UUID |

**Response `200`** — `Document`

**Errors**
- `404` — document not found

**curl**
```bash
curl http://localhost:8000/api/v1/docs/3fa85f64-5717-4562-b3fc-2c963f66afa6
```

---

### `PUT /api/v1/docs/{doc_id}`

Replace the entire content of an existing document. The `creator` is inherited from the original document's metadata.

**Path parameters**

| Parameter | Description |
|-----------|-------------|
| `doc_id` | Document UUID |

**Request body**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `content` | object | yes | New document payload (replaces existing content entirely) |
| `summary` | string | no | New summary. Omit to keep the existing value. |
| `keywords` | string[] | no | New keyword list. Omit to keep the existing value. |
| `named_version` | string | no | New version label. Omit to keep the existing value. |
| `commit_message` | string | no | Custom git commit message |

**Response `200`** — `Document`

**Errors**
- `404` — document not found
- `422` — content violates the template's JSON Schema

**curl**
```bash
curl -X PUT http://localhost:8000/api/v1/docs/3fa85f64-5717-4562-b3fc-2c963f66afa6 \
  -H "Content-Type: application/json" \
  -d '{
    "content": { "title": "Q1 Report", "status": "approved", "owner": "alice" },
    "summary": "Approved Q1 planning document",
    "named_version": "approved"
  }'
```

---

### `DELETE /api/v1/docs/{doc_id}`

Delete a document. Returns `204 No Content`.

The deletion is recorded as a git commit — the document remains recoverable via `GET /docs/{id}/at/{ref}` using a historical commit SHA.

**Path parameters**

| Parameter | Description |
|-----------|-------------|
| `doc_id` | Document UUID |

**Response `204`** — no body

**Errors**
- `404` — document not found

**curl**
```bash
curl -X DELETE http://localhost:8000/api/v1/docs/3fa85f64-5717-4562-b3fc-2c963f66afa6
```

---

### `GET /api/v1/docs/{doc_id}/history`

Return the git commit log for a document, newest first.

**Path parameters**

| Parameter | Description |
|-----------|-------------|
| `doc_id` | Document UUID |

**Query parameters**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `max_count` | int | `50` | Maximum number of commits to return |

**Response `200`** — `CommitInfo[]`

**Errors**
- `404` — document not found

**curl**
```bash
curl "http://localhost:8000/api/v1/docs/3fa85f64-5717-4562-b3fc-2c963f66afa6/history?max_count=10"
```

**Example response**
```json
[
  {
    "sha": "a3f9c1d",
    "message": "Update document 3fa85f64-...",
    "author": "alice",
    "timestamp": "2024-01-15T11:00:00Z"
  },
  {
    "sha": "b7e2f4a",
    "message": "Create document 3fa85f64-...",
    "author": "alice",
    "timestamp": "2024-01-15T10:30:00Z"
  }
]
```

---

### `GET /api/v1/docs/{doc_id}/at/{ref}`

Retrieve a point-in-time snapshot of a document.

**Path parameters**

| Parameter | Description |
|-----------|-------------|
| `doc_id` | Document UUID |
| `ref` | Git ref: commit SHA (full or abbreviated), tag name, or branch name |

**Response `200`** — `Document` (as it existed at that ref)

**Errors**
- `404` — document not found, or ref does not exist / did not include this document

**curl**
```bash
# Using an abbreviated commit SHA from the history endpoint
curl http://localhost:8000/api/v1/docs/3fa85f64-5717-4562-b3fc-2c963f66afa6/at/b7e2f4a

# Using a fleet version tag
curl http://localhost:8000/api/v1/docs/3fa85f64-5717-4562-b3fc-2c963f66afa6/at/v1.2.0
```

---

### `POST /api/v1/docs/{doc_id}/summarize`

Call the LLM to infer a `summary` and `keywords` for a document. Skips documents that already have a summary unless `overwrite=true`.

Requires `llm_api_key` in the vault config.

**Path parameters**

| Parameter | Description |
|-----------|-------------|
| `doc_id` | Document UUID |

**Query parameters**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `overwrite` | bool | `false` | Re-run inference even if a summary already exists |

**Response `200`** — `Document` with updated `meta.summary` and `meta.keywords`

**Errors**
- `404` — document not found
- `503` — LLM not configured, or the LLM call failed

**curl**
```bash
curl -X POST "http://localhost:8000/api/v1/docs/3fa85f64-5717-4562-b3fc-2c963f66afa6/summarize"

# Force re-summarize
curl -X POST "http://localhost:8000/api/v1/docs/3fa85f64-5717-4562-b3fc-2c963f66afa6/summarize?overwrite=true"
```

---

### `POST /api/v1/docs/summarize/all`

Run LLM inference on every document that is missing a summary. Returns only the documents that were updated.

**Query parameters**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `overwrite` | bool | `false` | Re-run inference on all documents, even those with an existing summary |

**Response `200`** — `Document[]` (only updated documents)

**Errors**
- `503` — LLM not configured, or a call failed

**curl**
```bash
curl -X POST http://localhost:8000/api/v1/docs/summarize/all

# Re-summarize everything
curl -X POST "http://localhost:8000/api/v1/docs/summarize/all?overwrite=true"
```

---

## Templates

A template describes a **folder structure** — a named set of document slots organised into logical paths (e.g. `"config/app"`, `"docs/readme"`). Each slot can optionally define a JSON Schema that documents placed in that slot must satisfy.

A template is **satisfied** (valid) when every `required=true` slot has at least one document in the vault whose `(template, path)` matches. Check satisfaction with `GET /templates/{name}/validate`.

### Key concepts

| Term | Meaning |
|------|---------|
| **Slot** | A named position in the template's folder structure, e.g. `"config/app"` |
| **Satisfied slot** | At least one vault document has `template == name` and `path == slot_key` |
| **Required slot** | Must be satisfied for the template to be valid (`required: true`, the default) |
| **Optional slot** | `required: false` — contributes to `satisfied` if filled but never blocks validity |
| **Extra document** | A document whose `(template, path)` pair matches no defined slot |

### Common response schema — `Template`

```json
{
  "name": "microservice",
  "description": "Standard microservice document set",
  "structure": {
    "config/app": {
      "description": "Application config",
      "required": true,
      "json_schema": {
        "type": "object",
        "required": ["host"],
        "properties": { "host": { "type": "string" }, "port": { "type": "integer" } }
      }
    },
    "config/database": { "description": "Database settings", "required": true },
    "docs/readme": { "description": "Service README", "required": false }
  },
  "created_at": "2024-01-10T08:00:00Z"
}
```

### Common response schema — `TemplateValidation`

```json
{
  "template_name": "microservice",
  "valid": false,
  "satisfied": ["config/app"],
  "missing": ["config/database"],
  "extra": []
}
```

---

### `GET /api/v1/templates`

List all templates.

**Response `200`** — `Template[]`

**curl**
```bash
curl http://localhost:8000/api/v1/templates
```

---

### `POST /api/v1/templates`

Register a new template. Returns `201 Created`.

**Request body**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | yes | Unique template name — used as the `template` field on documents |
| `description` | string | no | Human-readable description |
| `structure` | object | no | Map of slot path → `DocSlot` (default: `{}`) |

**`DocSlot`**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `description` | string | `""` | Human-readable slot description |
| `required` | bool | `true` | Whether the slot must be filled for the template to be valid |
| `json_schema` | object | `null` | JSON Schema (draft-7) the document's content must conform to |

**Response `201`** — `Template`

**Errors**
- `422` — a slot's `json_schema` is not a valid JSON Schema draft-7 document

**curl**
```bash
curl -X POST http://localhost:8000/api/v1/templates \
  -H "Content-Type: application/json" \
  -d '{
    "name": "microservice",
    "description": "Standard microservice document set",
    "structure": {
      "config/app": {
        "description": "Application configuration",
        "required": true,
        "json_schema": {
          "type": "object",
          "required": ["host"],
          "properties": {
            "host": { "type": "string" },
            "port": { "type": "integer" }
          }
        }
      },
      "config/database": {
        "description": "Database connection settings",
        "required": true
      },
      "docs/readme": {
        "description": "Service README",
        "required": false
      }
    }
  }'
```

---

### `GET /api/v1/templates/{name}`

Fetch a single template by name.

**Path parameters**

| Parameter | Description |
|-----------|-------------|
| `name` | Template name |

**Response `200`** — `Template`

**Errors**
- `404` — template not found

**curl**
```bash
curl http://localhost:8000/api/v1/templates/microservice
```

---

### `GET /api/v1/templates/{name}/validate`

Check whether all required slots are satisfied by current vault documents.

A slot is satisfied when at least one document exists with `template == name` and `path == slot_key`.

**Path parameters**

| Parameter | Description |
|-----------|-------------|
| `name` | Template name |

**Response `200`** — `TemplateValidation`

| Field | Type | Description |
|-------|------|-------------|
| `valid` | bool | `true` when no required slots are missing |
| `satisfied` | string[] | Slot paths with at least one matching document |
| `missing` | string[] | Required slot paths with no matching document |
| `extra` | string[] | Document paths that reference this template but match no defined slot |

**Errors**
- `404` — template not found

**curl**
```bash
curl http://localhost:8000/api/v1/templates/microservice/validate
```

**Example responses**

*Before any documents are created:*
```json
{
  "template_name": "microservice",
  "valid": false,
  "satisfied": [],
  "missing": ["config/app", "config/database"],
  "extra": []
}
```

*After required slots are filled:*
```json
{
  "template_name": "microservice",
  "valid": true,
  "satisfied": ["config/app", "config/database"],
  "missing": [],
  "extra": []
}
```

---

### `DELETE /api/v1/templates/{name}`

Delete a template. Returns `204 No Content`.

Existing documents that reference this template are unaffected — they retain the `template` and `path` fields in their metadata.

**Path parameters**

| Parameter | Description |
|-----------|-------------|
| `name` | Template name |

**Response `204`** — no body

**Errors**
- `404` — template not found

**curl**
```bash
curl -X DELETE http://localhost:8000/api/v1/templates/microservice
```

---

## Fleet

Fleet endpoints manage vault-level metadata and semantic versioning. Bumping the version creates a git tag (`v{major}.{minor}.{patch}`) that acts as a snapshot of the entire vault at that point.

### `GET /api/v1/fleet`

Return the fleet's current metadata.

**Response `200`** — `FleetMeta`

**curl**
```bash
curl http://localhost:8000/api/v1/fleet
```

**Example response**
```json
{
  "name": "production",
  "description": "Production document store",
  "version": { "major": 1, "minor": 2, "patch": 0 },
  "created_at": "2024-01-01T00:00:00Z",
  "updated_at": "2024-01-15T10:00:00Z"
}
```

---

### `GET /api/v1/fleet/versions`

List all git tags that represent fleet version snapshots (tags beginning with `v`).

**Response `200`** — `string[]`

**curl**
```bash
curl http://localhost:8000/api/v1/fleet/versions
```

**Example response**
```json
["v1.2.0", "v1.1.0", "v1.0.0"]
```

---

### `POST /api/v1/fleet/version/{bump_type}`

Increment the fleet's semantic version and create a git tag.

**Path parameters**

| Parameter | Values | Description |
|-----------|--------|-------------|
| `bump_type` | `major`, `minor`, `patch` | Which part of the version to increment |

Bump behaviour:
- `major` — resets minor and patch to 0 (e.g. `1.2.3` → `2.0.0`)
- `minor` — resets patch to 0 (e.g. `1.2.3` → `1.3.0`)
- `patch` — increments patch only (e.g. `1.2.3` → `1.2.4`)

**Response `200`** — `FleetMeta` with the updated version

**curl**
```bash
curl -X POST http://localhost:8000/api/v1/fleet/version/minor
curl -X POST http://localhost:8000/api/v1/fleet/version/patch
curl -X POST http://localhost:8000/api/v1/fleet/version/major
```

---

### `POST /api/v1/fleet/deploy`

Batch-create multiple documents from a single template in one atomic git commit.

All documents are validated first. If any fail schema validation the entire batch is rejected and no documents are written.

**Request body**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `template_name` | string | yes | Template to validate each document against |
| `documents` | `DeployDocSpec[]` | yes | Documents to create (see below) |
| `commit_message` | string | no | Custom git commit message for the batch |

**`DeployDocSpec`**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `path` | string | yes | Slot path within the template (e.g. `"config/app"`) |
| `content` | object | yes | Document payload |
| `creator` | string | yes | Creator identifier |
| `summary` | string | no | Summary |
| `keywords` | string[] | no | Keywords |
| `named_version` | string | no | Version label |

Each document's content is validated against the slot's `json_schema` (if defined). All documents must reference valid (or at least existing) paths — content validation is per-slot.

**Response `201`** — `Document[]` (all created documents)

**Errors**
- `404` — template not found
- `422` — one or more documents violate their slot's `json_schema`

**curl**
```bash
curl -X POST http://localhost:8000/api/v1/fleet/deploy \
  -H "Content-Type: application/json" \
  -d '{
    "template_name": "microservice",
    "documents": [
      {
        "path": "config/app",
        "content": { "host": "api.example.com", "port": 8080 },
        "creator": "platform-bot"
      },
      {
        "path": "config/database",
        "content": { "host": "db.internal", "port": 5432 },
        "creator": "platform-bot"
      }
    ],
    "commit_message": "Deploy microservice config docs"
  }'
```

---

## Error reference

| Status | When |
|--------|------|
| `401 Unauthorized` | Missing or invalid API key (`auth_mode = api_key`) |
| `403 Forbidden` | Custom `auth_dep` raised a 403 |
| `404 Not Found` | Document, template, or git ref does not exist |
| `422 Unprocessable Entity` | Request body fails validation (Pydantic) **or** document content violates the template's JSON Schema |
| `503 Service Unavailable` | LLM summarization requested but not configured, or the LLM API call failed |

All error responses use the standard FastAPI body:

```json
{ "detail": "Human-readable error message" }
```

For Pydantic validation errors (422), `detail` is an array:

```json
{
  "detail": [
    {
      "type": "missing",
      "loc": ["body", "creator"],
      "msg": "Field required"
    }
  ]
}
```

---

## Complete workflow example

```bash
BASE="http://localhost:8000/api/v1"

# 1. Create a template with a folder structure
curl -s -X POST $BASE/templates \
  -H "Content-Type: application/json" \
  -d '{
    "name": "microservice",
    "structure": {
      "config/app":      {"required": true,  "json_schema": {"type":"object","required":["host"]}},
      "config/database": {"required": true},
      "docs/readme":     {"required": false}
    }
  }' | jq .

# 2. Check validation — all required slots missing
curl -s $BASE/templates/microservice/validate | jq .

# 3. Fill required slots
curl -s -X POST $BASE/docs \
  -H "Content-Type: application/json" \
  -d '{"content":{"host":"api.example.com","port":8080},"creator":"alice","template":"microservice","path":"config/app"}' \
  | jq .meta.path

curl -s -X POST $BASE/docs \
  -H "Content-Type: application/json" \
  -d '{"content":{"host":"db.internal"},"creator":"alice","template":"microservice","path":"config/database"}' \
  | jq .meta.path

# 4. Template is now satisfied
curl -s $BASE/templates/microservice/validate | jq '{valid, missing}'

# 5. Create a freestanding document (no template)
DOC_ID=$(curl -s -X POST $BASE/docs \
  -H "Content-Type: application/json" \
  -d '{"content":{"title":"Fix login bug"},"creator":"alice","keywords":["bug","auth"]}' \
  | jq -r '.meta.id')

# 6. Update it
curl -s -X PUT $BASE/docs/$DOC_ID \
  -H "Content-Type: application/json" \
  -d '{"content":{"title":"Fix login bug","status":"closed"},"named_version":"done"}' \
  | jq .meta

# 7. View history
curl -s $BASE/docs/$DOC_ID/history | jq '.[] | {sha, message}'

# 8. Retrieve the original version
FIRST_SHA=$(curl -s $BASE/docs/$DOC_ID/history | jq -r '.[-1].sha')
curl -s $BASE/docs/$DOC_ID/at/$FIRST_SHA | jq .content

# 9. Bump the fleet version
curl -s -X POST $BASE/fleet/version/minor | jq .version

# 8. List all version tags
curl -s $BASE/fleet/versions
```
