"""
Document lifecycle example.

Part 1 — JSON document
  Creates a deployment config, pushes 5 revisions, then shows a
  key-level changeset between each consecutive pair.

Part 2 — Text document
  Creates a plain-text release note, pushes 5 revisions, then shows a
  line-level diff between each consecutive pair.

Changeset legend (JSON):
  +  key added
  -  key removed
  ~  value changed  (old → new)

Diff legend (text):
  +  line added
  -  line removed
     line unchanged (shown for context)
"""

from __future__ import annotations

import asyncio
import difflib
import tempfile
from pathlib import Path
from typing import Any

from docvault.config import VaultConfig
from docvault.core.document import CreateDocInput, UpdateDocInput
from docvault.core.vault import DocVault


# ─────────────────────────────────────────────────────────────────────────────
# Part 1 — JSON document revisions
# ─────────────────────────────────────────────────────────────────────────────

JSON_REVISIONS: list[dict[str, Any]] = [
    # v1 — initial staging deploy
    {"service": "payment-api", "env": "staging",    "replicas": 1, "image_tag": "v0.9.0", "debug": True},
    # v2 — promote to production, scale up
    {"service": "payment-api", "env": "production", "replicas": 3, "image_tag": "v0.9.0", "debug": False},
    # v3 — new image, add health-check
    {"service": "payment-api", "env": "production", "replicas": 3, "image_tag": "v1.0.0", "debug": False, "health_check": "/api/health"},
    # v4 — scale out, add timeout
    {"service": "payment-api", "env": "production", "replicas": 6, "image_tag": "v1.0.0", "debug": False, "health_check": "/api/health", "timeout_seconds": 30},
    # v5 — hotfix image, drop stale debug flag
    {"service": "payment-api", "env": "production", "replicas": 6, "image_tag": "v1.0.1-hotfix", "health_check": "/api/health", "timeout_seconds": 30},
]


def json_changeset(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for key in sorted(set(before) | set(after)):
        if key not in before:
            lines.append(f"  +  {key}: {after[key]!r}")
        elif key not in after:
            lines.append(f"  -  {key}: {before[key]!r}")
        elif before[key] != after[key]:
            lines.append(f"  ~  {key}: {before[key]!r}  →  {after[key]!r}")
    return lines or ["  (no changes)"]


# ─────────────────────────────────────────────────────────────────────────────
# Part 2 — Text document revisions
# ─────────────────────────────────────────────────────────────────────────────

TEXT_REVISIONS: list[str] = [
    # v1
    """\
# Release Notes — payment-api

## v0.9.0 (staging)
- Initial release candidate
- Basic payment flow implemented
- Known issue: timeout not configurable
""",
    # v2
    """\
# Release Notes — payment-api

## v0.9.0 (production)
- Promoted from staging after QA sign-off
- Basic payment flow implemented
- Known issue: timeout not configurable
""",
    # v3
    """\
# Release Notes — payment-api

## v1.0.0 (production)
- Promoted from staging after QA sign-off
- Basic payment flow implemented
- Added /api/health endpoint for load-balancer probes
- Fixed: timeout is now configurable via environment variable
""",
    # v4
    """\
# Release Notes — payment-api

## v1.0.0 (production)
- Promoted from staging after QA sign-off
- Basic payment flow implemented
- Added /api/health endpoint for load-balancer probes
- Fixed: timeout is now configurable via environment variable

## Deployment notes
- Scaled to 6 replicas on 2024-01-20
- Timeout set to 30 s across all instances
""",
    # v5
    """\
# Release Notes — payment-api

## v1.0.1-hotfix (production)
- Hotfix: corrected currency rounding error in EUR transactions
- Basic payment flow implemented
- Added /api/health endpoint for load-balancer probes
- Fixed: timeout is now configurable via environment variable

## Deployment notes
- Scaled to 6 replicas on 2024-01-20
- Timeout set to 30 s across all instances
""",
]


def text_doc(body: str) -> dict[str, Any]:
    """Wrap plain text in the vault's text-content envelope."""
    return {"content": body, "_source_ext": "md"}


def text_diff(before: str, after: str) -> list[str]:
    """Line-level diff, showing only changed lines plus one context line each side."""
    before_lines = before.splitlines(keepends=True)
    after_lines  = after.splitlines(keepends=True)

    output: list[str] = []
    matcher = difflib.SequenceMatcher(None, before_lines, after_lines)

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            # Show at most one context line on each side of a change block.
            # We defer the context decision to the surrounding change blocks,
            # so here we just keep a small window for readability.
            context = before_lines[i1:i2]
            if len(context) <= 2:
                for line in context:
                    output.append(f"     {line.rstrip()}")
            else:
                output.append(f"     {context[0].rstrip()}")
                output.append(f"     ··· {i2 - i1 - 2} unchanged line(s) ···")
                output.append(f"     {context[-1].rstrip()}")
        elif tag in ("replace", "delete"):
            for line in before_lines[i1:i2]:
                output.append(f"  -  {line.rstrip()}")
        if tag in ("replace", "insert"):
            for line in after_lines[j1:j2]:
                output.append(f"  +  {line.rstrip()}")

    return output or ["  (no changes)"]


# ─────────────────────────────────────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────────────────────────────────────

async def push_revisions(
    store: DocVault,
    first_content: dict[str, Any],
    rest: list[dict[str, Any]],
    creator: str,
    label: str,
) -> str:
    """Create a document then push len(rest) updates. Returns the document ID."""
    doc = await store.create_doc(CreateDocInput(content=first_content, creator=creator))
    doc_id = doc.meta.id
    print(f"  Created  {doc_id[:12]}…")
    for i, content in enumerate(rest, start=2):
        await store.update_doc(
            doc_id,
            UpdateDocInput(
                content=content,
                commit_message=f"{label} — revision {i}",
            ),
        )
        print(f"  Pushed revision {i}")
    return doc_id


async def show_changesets(
    store: DocVault,
    doc_id: str,
    diff_fn,          # callable(before, after) → list[str]
    extract_fn,       # callable(content_dict) → comparable value
    section_title: str,
) -> None:
    history = list(reversed(await store.get_doc_history(doc_id)))

    print(f"\nCommit history ({len(history)} entries, oldest → newest):")
    for i, commit in enumerate(history, start=1):
        print(f"  v{i}  {commit.sha[:10]}  {commit.message}")

    print("\n" + "═" * 64)
    print(f"  {section_title}")
    print("═" * 64)

    snapshots = [
        extract_fn((await store.get_doc_at_ref(doc_id, c.sha)).content)
        for c in history
    ]

    for i in range(len(snapshots) - 1):
        sha_a = history[i].sha[:10]
        sha_b = history[i + 1].sha[:10]
        print(f"\nv{i + 1} → v{i + 2}  ({sha_a} → {sha_b})")
        for line in diff_fn(snapshots[i], snapshots[i + 1]):
            print(line)


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

async def main() -> None:
    with tempfile.TemporaryDirectory() as d:
        cfg = VaultConfig(vault_path=Path(d) / "vault")
        store = DocVault(cfg)
        await store.init()

        # ══════════════════════════════════════════════════════════════════════
        # Part 1 — JSON document
        # ══════════════════════════════════════════════════════════════════════
        print("━" * 64)
        print("  Part 1 — JSON deployment config")
        print("━" * 64)

        json_id = await push_revisions(
            store,
            first_content=JSON_REVISIONS[0],
            rest=JSON_REVISIONS[1:],
            creator="ci-bot",
            label="payment-api config",
        )

        await show_changesets(
            store,
            json_id,
            diff_fn=json_changeset,
            extract_fn=lambda c: c,          # content is already the dict
            section_title="Key-level changesets (JSON)",
        )

        print("\n" + "═" * 64)
        current = await store.get_doc(json_id)
        print(f"Current state (v{len(JSON_REVISIONS)}):")
        for key, value in current.content.items():
            print(f"  {key}: {value!r}")

        # ══════════════════════════════════════════════════════════════════════
        # Part 2 — Text document
        # ══════════════════════════════════════════════════════════════════════
        print("\n\n" + "━" * 64)
        print("  Part 2 — Plain-text release notes")
        print("━" * 64)

        text_id = await push_revisions(
            store,
            first_content=text_doc(TEXT_REVISIONS[0]),
            rest=[text_doc(t) for t in TEXT_REVISIONS[1:]],
            creator="tech-writer",
            label="release notes",
        )

        await show_changesets(
            store,
            text_id,
            diff_fn=text_diff,
            extract_fn=lambda c: c["content"],   # unwrap the text envelope
            section_title="Line-level diff (text)",
        )

        print("\n" + "═" * 64)
        current = await store.get_doc(text_id)
        print(f"Current state (v{len(TEXT_REVISIONS)}):")
        print(current.content["content"])


if __name__ == "__main__":
    asyncio.run(main())
