# DocVault Manual

Comprehensive reference for the DocVault library — data models, Python API, HTTP API, CLI, and integration patterns.

---

## Table of contents

- [Core concepts](#core-concepts)
- [Data models](#data-models)
  - [VaultConfig](#vaultconfig)
  - [Document and DocumentMeta](#document-and-documentmeta)
  - [CreateDocInput / UpdateDocInput](#createdocinput--updatedocinput)
  - [StoreMeta and DocSlot](#storemeta-and-docslot)
  - [StoreCreateInput](#storecreateinput)
  - [DeployDocSpec and DeployStoreInput](#deploydocspec-and-deploystoreinput)
  - [VaultMeta and VaultVersion](#vaultmeta-and-vaultvault)
  - [StoreValidation](#storevalidation)
- [DocVault — root vault](#docvault--root-vault)
  - [Initialization](#initialization)
  - [Document CRUD](#document-crud)
  - [Store management](#store-management)
  - [Vault metadata](#vault-metadata)
- [Store — named document collection](#store--named-document-collection)
  - [Accessing a store](#accessing-a-store)
  - [Document CRUD in a store](#document-crud-in-a-store)
  - [Deploy](#deploy)
  - [Export](#export)
  - [Validation](#validation)
  - [Sub-stores](#sub-stores)
- [Store versioning](#store-versioning)
- [Locked stores](#locked-stores)
- [Binary documents](#binary-documents)
- [Point-in-time retrieval](#point-in-time-retrieval)
- [Vault versioning](#vault-versioning)
- [LLM summarization](#llm-summarization)
- [Exceptions](#exceptions)
- [HTTP API](#http-api)
  - [Vault endpoints](#vault-endpoints)
  - [Document endpoints](#document-endpoints)
  - [Store endpoints](#store-endpoints)
  - [Store document endpoints](#store-document-endpoints)
  - [Request / response shapes](#request--response-shapes)
- [CLI reference](#cli-reference)
- [Shim integration](#shim-integration)
- [Tools module](#tools-module)
- [Testing patterns](#testing-patterns)

---

## Core concepts

**Vault** — the root container. Backed by a single bare git repository on disk. Holds documents and stores. Carries a semantic version. Created and operated via `DocVault`.

**Store** — a named, optionally structured collection inside a vault. Stores have a slot structure (paths + JSON Schema constraints), version history, and a locked flag. Accessed via `vault.create_store()` / `vault.get_store()`. Returns a `Store` object.

**Document** — the atomic unit of storage. JSON content (or binary, stored as base64). Identified by a UUID. Carries `creator`, `summary`, `keywords`, `path`, `is_binary`, `mime_type`, and `named_version`.

**_VaultNode** — the shared base class for both `DocVault` and `Store`. Provides document CRUD, sub-store management, and binary helpers. You interact with it through `DocVault` and `Store`, never directly.

**GitBackend** — thin async wrapper around `pygit2`. Wraps blocking git operations in `asyncio.to_thread`. All writes produce commits with author, email, and message.

**Content-addressable store IDs** — store IDs encode `{name}:{md5(path)}:{md5(content)}`. The same name + same content hash always yields the same ID. Changing the source folder changes the ID.

---

## Data models

### VaultConfig

```python
from docvault.config import VaultConfig, AuthMode

config = VaultConfig(
    vault_path="./vault",              # required: directory for git repo
    vault_name="my-vault",             # default: "default"
    vault_description="...",           # default: ""
    auth_mode=AuthMode.NONE,           # none | api_key | passthrough
    api_keys=["sk-..."],               # for auth_mode=api_key
    default_creator="system",
    git_author_name="docvault",
    git_author_email="docvault@localhost",
    llm_api_key=None,                  # Anthropic API key
    llm_model="claude-haiku-4-5-20251001",
    auto_summarize=False,
)
```

Load from file and environment:

```python
from docvault.config import load_config

config = load_config()              # reads ./docvault.json + env vars
config = load_config("./prod.json") # reads specific file + env vars
```

### Document and DocumentMeta

`Document` is what you get back from create/get/update calls.

```python
doc.meta          # DocumentMeta
doc.content       # dict  (for JSON documents)
doc.binary_content  # bytes | None  (for binary documents)
```

`DocumentMeta` fields:

| Field | Type | Description |
|-------|------|-------------|
| `id` | `str` | UUID4 |
| `creator` | `str` | Who created this document |
| `created_at` | `datetime` | UTC creation time |
| `updated_at` | `datetime` | UTC last-update time |
| `summary` | `str \| None` | One-sentence description (manual or LLM) |
| `keywords` | `list[str]` | Tags for filtering |
| `path` | `str \| None` | Logical path within a store (e.g. `"config/app"`) |
| `named_version` | `str \| None` | Human-readable version label |
| `is_binary` | `bool` | Whether the document holds binary data |
| `mime_type` | `str \| None` | MIME type for binary documents |
| `size_bytes` | `int` | Size of the content in bytes |

### CreateDocInput / UpdateDocInput

```python
from docvault.core.document import CreateDocInput, UpdateDocInput

# JSON document (content defaults to {})
CreateDocInput(
    content={"host": "api.example.com", "port": 8080},
    creator="ci-bot",
    path="config/app",
    summary="Application config",
    keywords=["production"],
    named_version="v2.3.1",
    commit_message="Deploy app config",
)

# Binary document
CreateDocInput(
    binary_content=b"\x89PNG\r\n...",
    mime_type="image/png",
    creator="uploader",
    path="assets/logo",
)

# Update (all fields optional)
UpdateDocInput(
    content={"host": "api.example.com", "port": 443},
    summary="Updated app config",
    keywords=["production", "tls"],
    commit_message="Enable TLS",
)
```

### StoreMeta and DocSlot

`StoreMeta` is returned from `store.get_meta()` and `vault.list_stores()`.

| Field | Type | Description |
|-------|------|-------------|
| `id` | `str` | Content-addressable ID (`name:path_md5:content_md5`) |
| `name` | `str` | Store name (primary identifier for lookups) |
| `description` | `str \| None` | Human-readable description |
| `structure` | `dict[str, DocSlot]` | Slot path → slot definition |
| `version` | `int` | Current integer version |
| `version_history` | `dict[str, int]` | Content-hash → version number |
| `locked` | `bool` | Whether direct doc writes are blocked |
| `created_at` | `datetime` | UTC creation time |
| `updated_at` | `datetime` | UTC last-update time |

`DocSlot` defines a single slot:

```python
from docvault.core.store import DocSlot

DocSlot(
    required=True,          # whether this slot must be filled
    description="App config",
    json_schema={           # optional JSON Schema draft-7 constraint
        "type": "object",
        "required": ["host", "port"],
        "properties": {
            "host": {"type": "string"},
            "port": {"type": "integer", "minimum": 1, "maximum": 65535},
        },
    },
)
```

### StoreCreateInput

```python
from docvault.core.store import StoreCreateInput, DocSlot

# From explicit structure
StoreCreateInput(
    name="microservice",
    description="Service configs",
    structure={
        "config/app": DocSlot(required=True, json_schema={...}),
        "config/database": DocSlot(required=True),
        "docs/readme": DocSlot(required=False),
    },
    locked=False,       # default False
)

# From a folder on disk
StoreCreateInput(
    name="project-docs",
    path=Path("./docs-folder"),
)

# Empty store (no slots defined)
StoreCreateInput(name="scratch")
```

### DeployDocSpec and DeployStoreInput

```python
from docvault.core.store import DeployDocSpec, DeployStoreInput

# Spec for one document in a deploy batch
DeployDocSpec(
    path="config/app",
    content={"host": "api.example.com", "port": 8080},
    creator="ci-bot",
    summary="App config",
    keywords=["production"],
    named_version="v1.2.0",
)

# Used by the HTTP API body for POST /stores/{name}/deploy
DeployStoreInput(
    store_name="microservice",
    documents=[
        DeployDocSpec(path="config/app", content={...}, creator="ci-bot"),
        DeployDocSpec(path="config/database", content={...}, creator="ci-bot"),
    ],
)
```

### VaultMeta and VaultVersion

```python
vault_meta = await vault.get_vault()
vault_meta.name         # str
vault_meta.description  # str | None
vault_meta.version      # VaultVersion (semantic version)
vault_meta.updated_at   # datetime

str(vault_meta.version)  # "1.2.3"
```

### StoreValidation

Returned by `vault.validate_store(name)`:

```python
result = await vault.validate_store("microservice")
result.valid      # bool
result.missing    # list[str]  — required slots with no document
result.satisfied  # list[str]  — slots with a document
result.extra      # list[str]  — docs at paths not in the structure
```

---

## DocVault — root vault

### Initialization

```python
from docvault.core.vault import DocVault
from docvault.config import VaultConfig

config = VaultConfig(vault_path="./vault")
vault = DocVault(config)
await vault.init()   # idempotent — creates git repo if needed
```

### Document CRUD

```python
from docvault.core.document import CreateDocInput, UpdateDocInput

# Create
doc = await vault.create_doc(
    CreateDocInput(content={"key": "value"}, creator="alice")
)
print(doc.meta.id)

# Read
doc = await vault.get_doc(doc_id)

# Update
doc = await vault.update_doc(doc_id, UpdateDocInput(content={"key": "updated"}))

# Delete
await vault.delete_doc(doc_id)

# List (all filters optional)
metas = await vault.list_docs(
    keywords=["production"],
    creator="alice",
)
```

### Store management

```python
from docvault.core.store import StoreCreateInput, DocSlot

# Create or upsert
store = await vault.create_store(StoreCreateInput(name="svc", ...))
meta = await store.get_meta()

# Get existing store by name
store = await vault.get_store("svc")

# List all stores
store_metas = await vault.list_stores()   # list[StoreMeta]

# Delete
await vault.delete_store("svc")

# Validate
result = await vault.validate_store("svc")

# Export as zip bytes
zip_bytes = await vault.export_store_zip("svc")

# Deploy (upsert documents into a store in one commit)
from docvault.core.store import DeployStoreInput, DeployDocSpec
docs = await vault.deploy_store(
    DeployStoreInput(
        store_name="svc",
        documents=[
            DeployDocSpec(path="config/app", content={...}, creator="bot"),
        ],
    )
)
```

### Vault metadata

```python
# Get vault info
vault_meta = await vault.get_vault()

# List all versions
versions = await vault.list_versions()

# Bump semantic version and create a git tag
vault_meta = await vault.bump_version("patch")   # "major" | "minor" | "patch"
```

---

## Store — named document collection

### Accessing a store

`create_store` is an upsert — safe to call repeatedly with the same name and content:

```python
store = await vault.create_store(StoreCreateInput(name="svc"))
# or
store = await vault.get_store("svc")
```

Both return a `Store` object. Store objects are lightweight — they hold no state beyond the vault reference and store name.

### Document CRUD in a store

`Store` has the same document methods as `DocVault`:

```python
doc = await store.create_doc(
    CreateDocInput(
        content={"host": "api.internal", "port": 8080},
        creator="platform-bot",
        path="config/app",
    )
)
doc = await store.get_doc(doc.meta.id)
doc = await store.update_doc(doc.meta.id, UpdateDocInput(content={...}))
await store.delete_doc(doc.meta.id)
metas = await store.list_docs(keywords=["production"])
```

Note: `update_doc` and `delete_doc` raise `StoreValidationError` on a locked store.

### Deploy

`deploy()` writes many documents in a single git commit. It bypasses the locked flag — this is the authorized write path:

```python
from docvault.core.store import DeployDocSpec

docs = await store.deploy(
    [
        DeployDocSpec(path="config/app",      content={...}, creator="ci"),
        DeployDocSpec(path="config/database", content={...}, creator="ci"),
    ]
)
# docs is list[Document]
```

If the store has `json_schema` constraints on any slot, each document's content is validated before the commit. The entire batch is rejected atomically if any slot fails validation — `StoreValidationError` is raised.

### Export

```python
zip_bytes = await vault.export_store_zip("svc")
Path("svc.zip").write_bytes(zip_bytes)
```

The zip contains `_store.json` (the `StoreMeta` serialized) and all current document content files.

### Validation

```python
result = await vault.validate_store("svc")
if not result.valid:
    print("Missing slots:", result.missing)
```

`valid` is `True` when all `required=True` slots contain a document.

### Sub-stores

Every `Store` is a `_VaultNode` and can contain its own sub-stores:

```python
platform = await vault.create_store(StoreCreateInput(name="platform"))
monitoring = await platform.create_store(StoreCreateInput(name="monitoring"))
await monitoring.create_doc(CreateDocInput(content={"alerts": True}, creator="ops"))

# Retrieve sub-store
monitoring = await platform.get_store("monitoring")
metas = await monitoring.list_docs()
```

Sub-stores are stored at `<vault_path>/stores/platform/stores/monitoring/` and participate in the same git history.

---

## Store versioning

Store versioning is automatic. `create_store` is the upsert that drives version changes:

| Scenario | Version |
|----------|---------|
| First creation | `1` |
| Same name, same content | No-op, returns same `Store` |
| Same name, new content | `max(history) + 1` |
| Same name, content matches a past snapshot | Reverts to that snapshot's number |

```python
# v1
store = await vault.create_store(StoreCreateInput(name="svc", path=folder))
meta = await store.get_meta()
assert meta.version == 1

# Modify folder, create again → v2
store = await vault.create_store(StoreCreateInput(name="svc", path=folder))
meta = await store.get_meta()
assert meta.version == 2

# Restore folder to original content → back to v1
store = await vault.create_store(StoreCreateInput(name="svc", path=folder))
meta = await store.get_meta()
assert meta.version == 1
assert len(meta.version_history) == 2  # both hashes remembered
```

The `version_history` dict maps `{content_hash: version_number}` and is never pruned.

---

## Locked stores

Locking prevents ad-hoc document modifications while allowing controlled deploys:

```python
store = await vault.create_store(
    StoreCreateInput(name="prod-config", locked=True)
)
meta = await store.get_meta()
assert meta.locked is True
```

**Blocked on locked stores:**

```python
from docvault.exceptions import StoreValidationError

try:
    await store.update_doc(doc_id, UpdateDocInput(content={...}))
except StoreValidationError:
    print("Direct update blocked — use deploy()")

try:
    await store.delete_doc(doc_id)
except StoreValidationError:
    print("Direct delete blocked")
```

**Always allowed, even on locked stores:**

```python
await store.deploy([DeployDocSpec(path="config/app", content={...}, creator="ci")])
```

---

## Binary documents

Binary files are stored with base64 encoding inside a JSON envelope. The API is symmetric — pass bytes in, get bytes back:

```python
# Store binary
pdf = Path("report.pdf").read_bytes()
doc = await store.create_doc(
    CreateDocInput(
        binary_content=pdf,
        mime_type="application/pdf",
        creator="uploader",
        path="reports/q1",
    )
)
assert doc.meta.is_binary is True
assert doc.meta.mime_type == "application/pdf"

# Retrieve binary
doc = await store.get_doc(doc.meta.id)
assert doc.binary_content == pdf   # original bytes restored
```

Any MIME type is accepted. Common ones: `image/png`, `image/jpeg`, `application/pdf`, `application/octet-stream`.

The on-disk representation is `{"_binary": true, "_mime": "application/pdf", "_data": "<base64>"}`. You never need to construct or parse this envelope manually.

---

## Point-in-time retrieval

Every document has a full commit history. You can read the document as it was at any git ref (SHA, tag, branch):

```python
# Get commit history
history = await vault.get_doc_history(doc_id)
# history: list[CommitRecord] — newest first
for commit in history:
    print(commit.sha[:10], commit.message, commit.author, commit.timestamp)

# Retrieve document at a specific commit
old_doc = await vault.get_doc_at_ref(doc_id, history[-1].sha)  # oldest version

# Works on stores too
history = await store.get_doc_history(doc_id)
old_doc = await store.get_doc_at_ref(doc_id, "v1.0.0")  # vault version tag
```

---

## Vault versioning

The vault itself carries a semantic version. Bumping the version creates a git tag, giving you a permanent, retrievable snapshot of the entire collection:

```python
vault_meta = await vault.get_vault()
print(vault_meta.version)  # e.g. VaultVersion(1, 0, 0)

# Bump patch (1.0.0 → 1.0.1)
vault_meta = await vault.bump_version("patch")
# Creates git tag "v1.0.1"

# Available levels: "major", "minor", "patch"
vault_meta = await vault.bump_version("minor")  # 1.0.1 → 1.1.0

# List all past versions
versions = await vault.list_versions()
```

After bumping, you can retrieve any document at any past vault version using the tag as the git ref:

```python
doc_at_v1 = await vault.get_doc_at_ref(doc_id, "v1.0.0")
```

---

## LLM summarization

DocVault integrates with Anthropic's Claude to generate `summary` and `keywords` from document content.

**Configuration:**

```python
config = VaultConfig(
    vault_path="./vault",
    llm_api_key="sk-ant-...",
    llm_model="claude-haiku-4-5-20251001",
    auto_summarize=True,   # run on every create/update
)
```

**Manual summarization:**

```python
doc = await vault.summarize_doc(doc_id, overwrite=False)
# overwrite=True re-summarizes even if summary already exists
```

**Summarize all documents:**

```python
await vault.summarize_all(overwrite=False)
```

**Via HTTP API:**

```
POST /api/v1/docs/{id}/summarize?overwrite=true
POST /api/v1/docs/summarize/all
```

**Via CLI:**

```bash
docvault docs summarize <DOC_ID> [--overwrite]
docvault docs summarize-all [--overwrite]
```

If `auto_summarize=True`, summarization runs as a background task after every create/update. It never blocks the main operation.

---

## Exceptions

All exceptions inherit from `DocVaultError`.

| Exception | When raised |
|-----------|-------------|
| `DocumentNotFoundError` | `get_doc`, `update_doc`, `delete_doc` for unknown ID |
| `StoreNotFoundError` | `get_store`, `delete_store`, `validate_store` for unknown name |
| `StoreValidationError` | Schema validation failure in `deploy()`; write attempt on locked store |
| `VaultLockedError` | Concurrent write attempted while git lock is held |
| `DocVaultError` | Base class for all library errors |

```python
from docvault.exceptions import (
    DocumentNotFoundError,
    StoreNotFoundError,
    StoreValidationError,
    VaultLockedError,
)
```

---

## HTTP API

All routes are served under the `/api/v1` prefix by default. The prefix is configurable via `DocVaultShim(prefix=...)`.

### Vault endpoints

#### `GET /api/v1/health`

Returns `{"status": "ok"}`.

#### `GET /api/v1/vault`

Returns vault metadata.

```json
{
  "name": "my-vault",
  "description": "...",
  "version": "1.2.3",
  "updated_at": "2026-01-15T12:00:00Z"
}
```

#### `GET /api/v1/vault/versions`

Returns list of all version records.

#### `POST /api/v1/vault/bump`

Body: `{"level": "patch"}` — bumps vault version.

### Document endpoints

#### `POST /api/v1/docs`

Create a root document.

Body:

```json
{
  "content": {"host": "api.example.com"},
  "creator": "alice",
  "summary": "App host config",
  "keywords": ["production"],
  "path": "config/app"
}
```

Returns `201` with `DocumentMeta`.

#### `GET /api/v1/docs`

List root documents. Query params: `keywords` (comma-separated), `creator`.

Returns `200` with `list[DocumentMeta]`.

#### `GET /api/v1/docs/{id}`

Get a document by ID. Returns `200` with `{meta: DocumentMeta, content: dict}`.

#### `PUT /api/v1/docs/{id}`

Update a document. Body mirrors `POST /api/v1/docs`.

Returns `200` with `DocumentMeta`.

#### `DELETE /api/v1/docs/{id}`

Delete a document. Returns `204`.

#### `GET /api/v1/docs/{id}/history`

Query params: `max` (default 50).

Returns list of `{sha, message, author, timestamp}`.

#### `GET /api/v1/docs/{id}/at/{ref}`

Returns document content at git ref (SHA, tag, or branch).

#### `POST /api/v1/docs/{id}/summarize`

Query params: `overwrite` (bool).

Returns `DocumentMeta` with updated `summary` and `keywords`.

#### `POST /api/v1/docs/summarize/all`

Query params: `overwrite` (bool).

Summarizes all root documents. Returns list of updated `DocumentMeta`.

### Store endpoints

#### `POST /api/v1/stores`

Create or upsert a store.

Body:

```json
{
  "name": "microservice",
  "description": "Service configs",
  "structure": {
    "config/app": {
      "required": true,
      "description": "App config",
      "json_schema": {
        "type": "object",
        "required": ["host"],
        "properties": {"host": {"type": "string"}}
      }
    }
  },
  "locked": false
}
```

Returns `201` with `StoreMeta`.

#### `GET /api/v1/stores`

List all stores. Returns `list[StoreMeta]`.

#### `GET /api/v1/stores/{name}`

Get a store by name. Returns `StoreMeta`.

#### `DELETE /api/v1/stores/{name}`

Delete a store. Returns `204`.

#### `GET /api/v1/stores/{name}/validate`

Returns store validation result:

```json
{
  "valid": false,
  "missing": ["config/database"],
  "satisfied": ["config/app"],
  "extra": []
}
```

#### `GET /api/v1/stores/{name}/export`

Returns the store as a zip file (`application/zip`). The zip contains `_store.json` and all current document content.

#### `POST /api/v1/stores/{name}/deploy`

Batch deploy documents to a store. Validates against slot schemas. Atomic — all succeed or none.

Body:

```json
{
  "store_name": "microservice",
  "documents": [
    {"path": "config/app", "content": {"host": "api.internal", "port": 8080}, "creator": "ci"},
    {"path": "config/database", "content": {"url": "postgres://..."}, "creator": "ci"}
  ]
}
```

Returns `200` with `list[DocumentMeta]`.

### Store document endpoints

#### `GET /api/v1/stores/{name}/docs`

List documents in a store. Query params: `keywords`, `creator`.

#### `POST /api/v1/stores/{name}/docs`

Create a document in a store. Same body as `POST /api/v1/docs`.

#### `GET /api/v1/stores/{name}/docs/{id}`

Get a document in a store.

#### `PUT /api/v1/stores/{name}/docs/{id}`

Update a document in a store. Raises `400` if the store is locked.

#### `DELETE /api/v1/stores/{name}/docs/{id}`

Delete a document in a store. Raises `400` if the store is locked.

### Request / response shapes

**DocumentMeta** (response):

```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "creator": "alice",
  "created_at": "2026-01-15T12:00:00Z",
  "updated_at": "2026-01-15T12:00:00Z",
  "summary": "App host config",
  "keywords": ["production"],
  "path": "config/app",
  "named_version": null,
  "is_binary": false,
  "mime_type": null,
  "size_bytes": 42
}
```

**StoreMeta** (response):

```json
{
  "id": "microservice:abc123:def456",
  "name": "microservice",
  "description": "Service configs",
  "structure": {
    "config/app": {"required": true, "description": "App config", "json_schema": null}
  },
  "version": 2,
  "version_history": {"<hash_v1>": 1, "<hash_v2>": 2},
  "locked": false,
  "created_at": "2026-01-15T12:00:00Z",
  "updated_at": "2026-01-16T09:00:00Z"
}
```

---

## CLI reference

### `docvault init [PATH]`

Create a new vault (idempotent). Uses `vault_path` from config if `PATH` is omitted.

```bash
docvault init ./my-vault
```

### `docvault serve`

Start the REST API server.

```bash
docvault serve --host 0.0.0.0 --port 8000
```

### `docvault docs`

```bash
# List root documents
docvault docs list [--creator alice] [--keywords production,api]

# Get a document (prints JSON)
docvault docs get <DOC_ID>

# Create from file
docvault docs create --creator alice --file content.json
docvault docs create --creator alice --file -         # stdin

# Create with metadata
docvault docs create --creator alice --file content.json \
    --summary "App config" --keywords production,api

# Update
docvault docs update <DOC_ID> --file updated.json

# Delete (--force skips confirmation prompt)
docvault docs delete <DOC_ID> [--force]

# Revision history
docvault docs history <DOC_ID> [--max 20]

# Point-in-time read
docvault docs at <DOC_ID> <REF>      # REF: git SHA, tag, or branch

# LLM summarization
docvault docs summarize <DOC_ID> [--overwrite]
docvault docs summarize-all [--overwrite]
```

### `docvault stores`

```bash
# List all stores
docvault stores list

# Get store info
docvault stores get <STORE_NAME>

# Create / upsert a store
docvault stores create mystore                          # empty store
docvault stores create mystore --file schema.json       # from structure JSON
docvault stores create mystore --path ./folder          # from folder
docvault stores create mystore --description "..." --locked

# Delete
docvault stores delete <STORE_NAME> [--force]

# Validate slot satisfaction
docvault stores validate <STORE_NAME>

# List documents in a store
docvault stores docs list <STORE_NAME> [--keywords kw1,kw2]

# Deploy documents to a store
docvault stores docs deploy <STORE_NAME> --file specs.json
```

`schema.json` format (structure-based store):

```json
{
  "config/app": {"required": true, "description": "App config"},
  "config/database": {"required": true},
  "docs/readme": {"required": false}
}
```

`specs.json` format (deploy):

```json
[
  {"path": "config/app",      "content": {"host": "api.example.com", "port": 8080}, "creator": "ci"},
  {"path": "config/database", "content": {"url": "postgres://db/app"},              "creator": "ci"}
]
```

### `docvault vault`

```bash
docvault vault info                    # print vault metadata
docvault vault versions                # list all versions
docvault vault bump [major|minor|patch]  # default: patch
```

### `docvault config`

```bash
docvault config show               # print resolved config (API keys masked)
docvault config generate-key       # generate a random API key
docvault config generate-key -n 3  # generate 3 keys
```

---

## Shim integration

`DocVaultShim` mounts DocVault inside an existing FastAPI application with zero route conflicts.

```python
from docvault.api import DocVaultShim
from docvault.config import VaultConfig

config = VaultConfig(vault_path="./vault")
shim = DocVaultShim(config, prefix="/api/v1")
```

The shim exposes:

- `shim.router` — `APIRouter` to include in your app
- `shim.store` — the `DocVault` instance (available after startup)
- `shim.lifespan()` — async context manager for lifecycle management
- `shim.wrap_lifespan(existing_lifespan?)` — compose with existing lifespan
- `shim.startup()` — explicit startup (idempotent)

### Accessing the vault in your own routes

```python
@app.get("/my-endpoint")
async def my_endpoint():
    vault = shim.store            # DocVault, ready after startup
    docs = await vault.list_docs()
    return {"count": len(docs)}
```

### Loading a store on startup

```python
from docvault.exceptions import StoreNotFoundError

@asynccontextmanager
async def lifespan(app: FastAPI):
    async with shim.lifespan():
        try:
            store = await shim.store.get_store("project")
        except StoreNotFoundError:
            store = await shim.store.create_store(
                StoreCreateInput(name="project", path=Path("./template-source"))
            )
        meta = await store.get_meta()
        app.state.store_id = meta.id
        yield
```

See [examples/shim-integration/main.py](examples/shim-integration/main.py) for a complete working example.

---

## Tools module

`docvault.tools.deploy` provides `deploy_store`, a utility for fetching a store's export from a running DocVault instance and writing it to a local filesystem path.

```python
from docvault.tools.deploy import deploy_store

await deploy_store(
    store_name="microservice",
    target_path=Path("./deployed-configs"),
    base_url="http://localhost:8000",
    api_key="sk-your-key",   # optional, for api_key auth mode
)
```

This calls `GET /api/v1/stores/{store_name}/export`, downloads the zip, and extracts documents to `target_path`. The `_store.json` manifest is written alongside the document files.

**MCP / agent usage**: `deploy_store` is designed for use from agent tooling or CI pipelines where you want to hydrate a local config directory from the canonical vault.

---

## Testing patterns

### Isolated vault per test

```python
import tempfile
import pytest
from pathlib import Path
from docvault.core.vault import DocVault
from docvault.config import VaultConfig

@pytest.fixture
async def vault(tmp_path):
    cfg = VaultConfig(vault_path=tmp_path / "vault")
    v = DocVault(cfg)
    await v.init()
    return v
```

### Testing with the HTTP API

```python
import pytest
from httpx import AsyncClient, ASGITransport
from asgi_lifespan import LifespanManager
from docvault.api import DocVaultShim
from fastapi import FastAPI

@pytest.fixture
async def client(tmp_path):
    cfg = VaultConfig(vault_path=tmp_path / "vault")
    shim = DocVaultShim(cfg)
    app = FastAPI(lifespan=shim.wrap_lifespan())
    app.include_router(shim.router)

    async with LifespanManager(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            yield c
```

Or use the `asgi_lifespan_client` helper from the test suite's `conftest.py`:

```python
from tests.conftest import asgi_lifespan_client

async def test_my_feature():
    shim = DocVaultShim(cfg)
    app = FastAPI(lifespan=shim.wrap_lifespan())
    app.include_router(shim.router)

    async with asgi_lifespan_client(app) as client:
        resp = await client.post("/api/v1/stores", json={"name": "test"})
        assert resp.status_code == 201
```

### Testing locked stores

```python
async def test_locked_store_blocks_direct_write(vault):
    store = await vault.create_store(StoreCreateInput(name="prod", locked=True))
    doc = await store.deploy([DeployDocSpec(path="cfg", content={}, creator="ci")])

    with pytest.raises(StoreValidationError):
        await store.update_doc(doc[0].meta.id, UpdateDocInput(content={"x": 1}))

    # deploy() still works
    await store.deploy([DeployDocSpec(path="cfg", content={"x": 1}, creator="ci")])
```

### Testing binary documents

```python
async def test_binary_roundtrip(vault):
    data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
    doc = await vault.create_doc(
        CreateDocInput(binary_content=data, mime_type="image/png", creator="test")
    )
    assert doc.meta.is_binary is True

    retrieved = await vault.get_doc(doc.meta.id)
    assert retrieved.binary_content == data
```
