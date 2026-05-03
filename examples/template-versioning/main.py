"""
Template versioning example.

Shows how template versions work when folder content changes:
- First creation → version 1
- Content changes → version increments (2, 3, ...)
- Content reverts to a known snapshot → version reverts to the original number
- Same content as current → no-op, same ID returned
- Two templates with same name + same content are treated as copies
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from docvault.config import VaultConfig
from docvault.core.store import DocVault
from docvault.core.template import TemplateCreateInput


async def main() -> None:
    with tempfile.TemporaryDirectory() as d:
        cfg = VaultConfig(vault_path=Path(d) / "vault")
        store = DocVault(cfg)
        await store.init()

        # Set up a folder that will act as the template source
        src = Path(d) / "config-template"
        src.mkdir()
        (src / "app.json").write_text('{"host": "localhost", "port": 8080}', "utf-8")
        (src / "database.json").write_text('{"url": "postgres://db/app"}', "utf-8")

        # ── Creation (version 1) ──────────────────────────────────────────────
        ref = await store.create_template(
            TemplateCreateInput(name="platform-config", path=src)
        )
        tpl = await store.get_template(ref.id)
        print(f"[create]  version={tpl.version}  id={ref.id}")

        # ── No-op: same folder, same content ─────────────────────────────────
        ref2 = await store.create_template(
            TemplateCreateInput(name="platform-config", path=src)
        )
        assert ref2.id == ref.id, "Same content must return same ID"
        print(f"[no-op]   version={tpl.version}  id={ref2.id}  (unchanged)")

        # ── Content change → version 2 ────────────────────────────────────────
        (src / "app.json").write_text(
            '{"host": "api.prod.example.com", "port": 443}', "utf-8"
        )
        ref_v2 = await store.create_template(
            TemplateCreateInput(name="platform-config", path=src)
        )
        tpl_v2 = await store.get_template(ref_v2.id)
        print(f"[update]  version={tpl_v2.version}  id={ref_v2.id}")
        assert tpl_v2.version == 2

        # ── Another change → version 3 ────────────────────────────────────────
        (src / "database.json").write_text(
            '{"url": "postgres://prod-db/app", "pool": 20}', "utf-8"
        )
        ref_v3 = await store.create_template(
            TemplateCreateInput(name="platform-config", path=src)
        )
        tpl_v3 = await store.get_template(ref_v3.id)
        print(f"[update]  version={tpl_v3.version}  id={ref_v3.id}")
        assert tpl_v3.version == 3

        # ── Revert to v1 content → version goes back to 1 ────────────────────
        (src / "app.json").write_text('{"host": "localhost", "port": 8080}', "utf-8")
        (src / "database.json").write_text('{"url": "postgres://db/app"}', "utf-8")
        ref_reverted = await store.create_template(
            TemplateCreateInput(name="platform-config", path=src)
        )
        tpl_reverted = await store.get_template(ref_reverted.id)
        print(f"[revert]  version={tpl_reverted.version}  id={ref_reverted.id}  (back to v1)")
        assert tpl_reverted.version == 1
        assert ref_reverted.id == ref.id

        # ── Copies: different template name, same structure ───────────────────
        ref_copy = await store.create_template(
            TemplateCreateInput(
                name="platform-config-staging",
                structure=dict(tpl_reverted.structure),
            )
        )
        tpl_copy = await store.get_template(ref_copy.id)
        print(
            f"\n[copy]    name={tpl_copy.name}  version={tpl_copy.version}"
        )
        # Same structure deployed again under a different name is an independent template
        assert tpl_copy.name == "platform-config-staging"
        assert tpl_copy.version == 1

        # Redeploy same copy structure → no-op
        ref_copy2 = await store.create_template(
            TemplateCreateInput(
                name="platform-config-staging",
                structure=dict(tpl_reverted.structure),
            )
        )
        assert ref_copy2.id == ref_copy.id
        print(f"[no-op]   name={tpl_copy.name}  id={ref_copy2.id}  (unchanged)")

        # ── Summary ───────────────────────────────────────────────────────────
        templates = await store.list_templates()
        print(f"\nVault contains {len(templates)} template(s):")
        for t in templates:
            print(
                f"  {t.name:<28} version={t.version}"
                f"  history_entries={len(t.version_history)}"
            )


if __name__ == "__main__":
    asyncio.run(main())
