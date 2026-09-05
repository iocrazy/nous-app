# Unified Prompts Library (P3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One backend read model gathers every prompt the user has (prompt assets, prompted images, albums with per-slide prompts) and two surfaces show it — the resource library's Prompts page (text-first) and the canvas Library panel's Prompts page (pick, insert, apply, promote).

**Architecture:** Phase A adds `resources.prompt_origin` (stamped by every writer, backfilled once) and `GET /api/v1/prompts` + `/prompts/counts`, built by a pure entry builder over two ORM statements. Phase B swaps the empty asset-library Prompts tab for `PromptsShelf`, fed by `promptsService.ts`. Phase C replaces the panel's stub with `LibraryPromptsPage` (list + preview, four actions, per-slide album insertion), rewires the two bookshelf buttons and `⌘K`, and deletes the old 420px picker with its browser-side Supabase query.

**Tech Stack:** FastAPI + SQLAlchemy 2 async (ORM `select`, `read_scope()` / `system_request_scope()`), DBOS backfill workflow, pytest; React 19 + TypeScript + zustand v5 + react-i18next + vitest/RTL; Tailwind with canvas tokens.

**Spec:** `docs/superpowers/specs/2026-09-05-unified-prompts-library-design.md` (binding). Mockup: `docs/superpowers/specs/2026-09-05-canvas-library-prompts-page-mockup.html`. Predecessor: `docs/superpowers/specs/2026-09-03-canvas-library-panel-design.md`.

## Global Constraints

- UI copy is English, Title Case for labels; every user-visible string goes through `t('<key>', '<English default>')` with the key as a single-quoted literal (the `libraryI18n.test.ts` scanner needs that shape). Every new `canvas.library.*` and `prompts.*` key must exist in BOTH `frontend/public/locales/en.json` and `zh.json`.
- No new raw SQL (`text()`); ORM `select` only. Reads of `Resources` for a whole team scope run inside `system_request_scope("<reason>")` because production sets `SCOPE_ENFORCE_RESOURCES=True` and a user scope would inject `creator_id = me` (hiding teammates' uploads); membership is already gated by `_gate`.
- New backend module reading prompts must never query Supabase from the browser; delete `fetchPromptAssets` in Phase C.
- Semantic colour tokens only (`ok/warn/danger/info`, `--accent-*`, `canvas-*`); no `indigo/amber/red/emerald` class names. Lucide icons, never emoji.
- `prompt_origin ∈ {typed, extracted, captioned}`; rule: **origin follows the last writer of the positive text**.
- Migration number **453** (`git fetch origin master` first; if 453 is taken, renumber and update every reference in this plan's Task A1).
- Panel widths: Prompts page 600px, Media page 340px. Album insertion is per slide. System segment renders only when `counts.system > 0`. Captioned rows sort last and render muted.
- Commit after every task; never `git add -A` — add the files the task names.
- No `docker compose` / production commands in tests. Frontend tests: `cd frontend && npx vitest run <path>`; backend: `cd backend && uv run pytest <path> -q`.

## File Structure

**Backend (Phase A)**
- Create `supabase/migrations/453_resources_prompt_origin.sql` — the column + CHECK.
- Modify `backend/app/models/media.py` — `Resources.prompt_origin` + CheckConstraint in `__table_args__` (schema-drift gate is two-way).
- Create `backend/app/services/prompts/__init__.py`, `origin.py` — `PROMPT_ORIGINS`, `stamp_origin`, `derive_origin` (pure).
- Modify the five writers: `backend/app/api/resources_crud_router.py` (PATCH → typed), `backend/app/workflows/upload_postprocess.py`, `backend/app/workflows/backfill_resource_gen_params.py`, `backend/app/services/library/promote_generated_media_service.py` (→ extracted), `backend/app/workflows/caption_asset.py`, `backend/app/workflows/caption_slide.py` (→ captioned).
- Create `backend/app/workflows/backfill_resource_prompt_origin.py`; register in `backend/app/api/admin/backfill_router.py::_BACKFILLS`.
- Create `backend/app/schemas/prompts.py` — `PromptThumb`, `PromptSlide`, `PromptEntry`, `PromptPage`, `PromptCounts`.
- Create `backend/app/services/prompts/entries.py` — pure builders `entry_from_asset`, `entry_from_resource`, `sort_entries`, `matches_query`, `title_from_filename`.
- Create `backend/app/repositories/prompt_catalog_repository.py` — `_prompted_resources_stmt`, `list_prompted_resources`, `_example_files_stmt`, `example_file_ids`.
- Create `backend/app/services/prompts/catalog_service.py` — `PromptCatalogService.list` / `.counts`.
- Create `backend/app/api/prompts_router.py`; mount in `backend/app/api/__init__.py`.

**Frontend (Phase B)**
- Create `frontend/services/promptsService.ts` — `PromptEntry` types, `fetchPrompts`, `fetchPromptCounts`, `promptText`, `paramChips`, `ratioFromParams`, `saveAsTemplate`.
- Modify `frontend/services/assetsService.ts` — `AssetCreateBody` gains the four `prompt_*` fields and `tags`.
- Create `frontend/components/prompts/PromptThumbs.tsx`, `PromptTags.tsx`, `TemplateForm.tsx` — shared atoms.
- Create `frontend/components/resources/prompts/promptFilters.ts`, `PromptCard.tsx`, `PromptAlbumCard.tsx`, `PromptsShelf.tsx`; modify `frontend/components/resources/assets/AssetsView.tsx` (branch on `prompt`).
- Modify `frontend/contexts/ResourcesContext.tsx` — override `assetCounts.prompt` from `/prompts/counts`.

**Frontend (Phase C)**
- Modify `frontend/features/canvas-core/library/libraryStore.ts` — `promptLang`, `promptForm`, `promptSegment`, width switch in `setPage`.
- Modify `frontend/features/canvas-core/library/mentionHandles.ts` + `mentionLibraryItems.ts` (`MentionInserters.insertText`) + `smart/nodes/PromptNodeView.tsx` registration.
- Create `frontend/features/canvas-core/library/promptActions.ts` (pure: `buildApplyAllPatch`, `appendPositive`), `usePromptCatalog.ts`, `LibraryPromptList.tsx`, `LibraryPromptPreview.tsx`, `LibraryPromptsPage.tsx`; modify `LibraryPanel.tsx`.
- Modify `frontend/features/canvas-core/palette/commands.ts` (`library-add`), `smart/nodes/PromptNodeView.tsx` (bookshelf → panel), `smart/nodes/AttachedComposerPanel.tsx` (bookshelf → panel, drop picker).
- Delete `smart/nodes/AssetPromptPicker.tsx` (+test), `smart/loadPromptAsset.ts` (+test), `resourceService.fetchPromptAssets` + `PromptAsset` (+ `resourceService.promptAssets.test.ts`), rewrite `PromptNodeView.library.test.tsx`; extend `libraryRemovals.test.ts`.

---

## Phase A — data: origin column, writers, backfill, read model

### Task 1 (A1): `resources.prompt_origin` column (migration + ORM)

**Files:**
- Create: `supabase/migrations/453_resources_prompt_origin.sql`
- Modify: `backend/app/models/media.py` (Resources: `__table_args__` at ~line 247, columns near `gen_prompt_json` ~line 393)
- Test: `backend/tests/models/test_resources_prompt_origin_column.py`

**Interfaces:**
- Produces: ORM attribute `Resources.prompt_origin: Mapped[str | None]`, CHECK `resources_prompt_origin_check`.

- [ ] **Step 1: Confirm the migration number is free**

Run: `git fetch -q origin master && git ls-tree --name-only origin/master supabase/migrations/ | sort | tail -1`
Expected: `supabase/migrations/452_realtime_publication_frontend_tables.sql`. If a 453 exists, use the next free number everywhere in this task.

- [ ] **Step 2: Write the failing model test**

```python
# backend/tests/models/test_resources_prompt_origin_column.py
"""mig 453: the ORM must carry the column AND its CHECK, or schema-drift goes red."""
from sqlalchemy import CheckConstraint

from app.models import Resources


def test_prompt_origin_column_exists_and_is_nullable_text():
    col = Resources.__table__.c["prompt_origin"]
    assert col.nullable is True
    assert col.type.python_type is str


def test_prompt_origin_check_constraint_is_declared():
    names = {
        c.name for c in Resources.__table__.constraints if isinstance(c, CheckConstraint)
    }
    assert "resources_prompt_origin_check" in names
```

- [ ] **Step 3: Run it to verify it fails**

Run: `cd backend && uv run pytest tests/models/test_resources_prompt_origin_column.py -q`
Expected: FAIL with `KeyError: 'prompt_origin'`. (Create `backend/tests/models/__init__.py` if the directory does not exist.)

- [ ] **Step 4: Write the migration**

```sql
-- 453: resources.prompt_origin — WHO wrote the prompt text on this row.
--
-- Three writers share gen_prompt / gen_prompt_zh / slide_prompts and until now
-- nothing recorded which one wrote last: PNG-metadata extraction and the
-- canvas's own generations ("extracted"), AI captioning ("captioned"), and the
-- user typing in the resource detail page ("typed"). Their reuse value differs
-- a lot, and the unified Prompts view (spec 2026-09-05-unified-prompts-library)
-- sorts and labels by it. Rule: origin follows the last writer of the positive
-- text; every writer stamps it in the same patch that writes the text.
--
-- Nullable: rows that predate this migration are backfilled by the
-- `resource_prompt_origin` admin backfill (dry-run first), not here — the
-- derivation looks at four columns and belongs in reviewable Python, and a
-- NULL is an honest "unknown" until it runs.

ALTER TABLE public.resources
    ADD COLUMN IF NOT EXISTS prompt_origin text
    CONSTRAINT resources_prompt_origin_check
        CHECK (prompt_origin IN ('typed', 'extracted', 'captioned'));

COMMENT ON COLUMN public.resources.prompt_origin IS
    'Last writer of gen_prompt/gen_prompt_zh/slide_prompts: typed | extracted | captioned (mig 453)';

-- PostgREST schema cache (see mig 284 for why NOTIFY rather than a restart).
NOTIFY pgrst, 'reload schema';
```

- [ ] **Step 5: Add the ORM column and constraint**

In `backend/app/models/media.py`, inside `class Resources`, directly after the `gen_prompt_json` column:

```python
    prompt_origin: Mapped[str | None] = mapped_column(
        Text,
        comment="Last writer of the prompt text: typed | extracted | captioned (mig 453)",
    )
```

and add to the existing `__table_args__` tuple of `Resources` (next to `resources_rating_check`):

```python
        CheckConstraint(
            "prompt_origin IN ('typed', 'extracted', 'captioned')",
            name="resources_prompt_origin_check",
        ),
```

- [ ] **Step 6: Run the test and the model import smoke**

Run: `cd backend && uv run pytest tests/models/test_resources_prompt_origin_column.py -q && uv run python -c "from app.models import Resources; print(Resources.__table__.c.prompt_origin.type)"`
Expected: `2 passed` and `TEXT`.

- [ ] **Step 7: Commit**

```bash
git add supabase/migrations/453_resources_prompt_origin.sql backend/app/models/media.py backend/tests/models/test_resources_prompt_origin_column.py backend/tests/models/__init__.py
git commit -m "feat(db): resources.prompt_origin — who wrote the prompt text (mig 453)"
```

### Task 2 (A2): origin helper + stamp every writer

**Files:**
- Create: `backend/app/services/prompts/__init__.py` (empty), `backend/app/services/prompts/origin.py`
- Modify: `backend/app/api/resources_crud_router.py:878-900`, `backend/app/workflows/upload_postprocess.py:196-212`, `backend/app/workflows/backfill_resource_gen_params.py` (`build_patch`), `backend/app/services/library/promote_generated_media_service.py:214-221`, `backend/app/workflows/caption_asset.py:180-200`, `backend/app/workflows/caption_slide.py:140`
- Test: `backend/tests/services/prompts/__init__.py` (empty), `backend/tests/services/prompts/test_origin.py`, `backend/tests/services/prompts/test_origin_wiring.py`

**Interfaces:**
- Produces: `PROMPT_ORIGINS: tuple[str, ...]`, `PROMPT_TEXT_KEYS: frozenset[str]`, `stamp_origin(patch: dict, origin: str) -> dict` (mutates and returns `patch`; adds `prompt_origin` only when a text key is present), `derive_origin(row: Mapping) -> str | None` (backfill rule from spec §3.2).

- [ ] **Step 1: Write the failing unit tests**

```python
# backend/tests/services/prompts/test_origin.py
import pytest

from app.services.prompts.origin import PROMPT_ORIGINS, derive_origin, stamp_origin


def test_origins_are_the_three_the_check_allows():
    assert PROMPT_ORIGINS == ("typed", "extracted", "captioned")


def test_stamp_adds_origin_only_when_text_is_written():
    assert stamp_origin({"gen_prompt": "x"}, "typed") == {"gen_prompt": "x", "prompt_origin": "typed"}
    assert stamp_origin({"gen_prompt_zh": "x"}, "captioned")["prompt_origin"] == "captioned"
    assert stamp_origin({"slide_prompts": {"a.jpg": {"en": "x"}}}, "captioned")["prompt_origin"] == "captioned"
    # Negative-only or params-only writes do not change who wrote the positive text.
    assert stamp_origin({"gen_prompt_negative": "x"}, "typed") == {"gen_prompt_negative": "x"}
    assert stamp_origin({"gen_params": {"steps": 1}}, "extracted") == {"gen_params": {"steps": 1}}


def test_stamp_refuses_unknown_origin():
    with pytest.raises(ValueError):
        stamp_origin({"gen_prompt": "x"}, "guessed")


@pytest.mark.parametrize(
    "row, expected",
    [
        ({"gen_prompt": "", "gen_prompt_zh": None, "slide_prompts": None}, None),
        ({"gen_prompt": "[]"}, None),
        ({"gen_prompt": "a", "gen_params": {"steps": 20}}, "extracted"),
        ({"gen_prompt": "a", "gen_params": {}, "gen_prompt_json": '{"subject":"x"}'}, "captioned"),
        ({"gen_prompt": None, "slide_prompts": {"002.jpg": {"en": "a"}}}, "captioned"),
        ({"gen_prompt": "a", "gen_params": None, "gen_prompt_json": None}, "typed"),
        ({"gen_prompt_zh": "中文", "gen_prompt_json": "null"}, "typed"),
    ],
)
def test_derive_origin_backfill_rule(row, expected):
    assert derive_origin(row) == expected
```

```python
# backend/tests/services/prompts/test_origin_wiring.py
"""Every writer of the prompt text stamps prompt_origin in the same patch.

A grep guard, not a behaviour test: the six writers live in five modules with
five different fixture stories, and the failure mode being pinned is "a new or
edited writer forgot the stamp" — which is visible in source.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3] / "app"

WRITERS = {
    "api/resources_crud_router.py": 'stamp_origin(update_data, "typed")',
    "workflows/upload_postprocess.py": 'stamp_origin(patch, "extracted")',
    "workflows/backfill_resource_gen_params.py": 'stamp_origin(patch, "extracted")',
    "services/library/promote_generated_media_service.py": '"prompt_origin": "extracted"',
    "workflows/caption_asset.py": 'stamp_origin(update, "captioned")',
    "workflows/caption_slide.py": '{"prompt_origin": "captioned"}',
}


def test_every_prompt_writer_stamps_origin():
    missing = [rel for rel, needle in WRITERS.items() if needle not in (ROOT / rel).read_text()]
    assert missing == [], f"writers without an origin stamp: {missing}"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd backend && uv run pytest tests/services/prompts -q`
Expected: `ModuleNotFoundError: app.services.prompts` for the first file; the wiring test fails listing all six writers.

- [ ] **Step 3: Write `origin.py`**

```python
# backend/app/services/prompts/origin.py
"""Who wrote the prompt text on a resource (mig 453).

RULE: origin follows the LAST writer of the positive text. Every writer of
``gen_prompt`` / ``gen_prompt_zh`` / ``slide_prompts`` calls :func:`stamp_origin`
on the patch it is about to persist; ``tests/services/prompts/test_origin_wiring.py``
pins that each known writer does.
"""
from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

PROMPT_ORIGINS: tuple[str, ...] = ("typed", "extracted", "captioned")

#: The columns whose write means "the positive text changed hands". Negative
#: prompts and params are deliberately not here — editing a negative does not
#: make the positive yours.
PROMPT_TEXT_KEYS: frozenset[str] = frozenset({"gen_prompt", "gen_prompt_zh", "slide_prompts"})

_BLANK_TEXT = {"", "[]", '""', "null", "{}"}


def stamp_origin(patch: Dict[str, Any], origin: str) -> Dict[str, Any]:
    """Add ``prompt_origin`` to ``patch`` iff it writes prompt text. Returns ``patch``."""
    if origin not in PROMPT_ORIGINS:
        raise ValueError(f"unknown prompt origin {origin!r}; expected one of {PROMPT_ORIGINS}")
    if PROMPT_TEXT_KEYS & set(patch):
        patch["prompt_origin"] = origin
    return patch


def _nonblank(value: Any) -> bool:
    return isinstance(value, str) and value.strip() not in _BLANK_TEXT


def _nonempty_object(value: Any) -> bool:
    return isinstance(value, dict) and len(value) > 0


def derive_origin(row: Mapping[str, Any]) -> Optional[str]:
    """Backfill rule for rows written before mig 453 (spec §3.2).

    ``None`` when the row carries no prompt text at all — such rows are not
    prompts and must stay NULL rather than be labelled.
    """
    has_positive = _nonblank(row.get("gen_prompt")) or _nonblank(row.get("gen_prompt_zh"))
    has_slides = _nonempty_object(row.get("slide_prompts"))
    if not (has_positive or has_slides):
        return None
    if _nonempty_object(row.get("gen_params")):
        return "extracted"
    if _nonblank(row.get("gen_prompt_json")):
        return "captioned"
    if has_slides and not has_positive:
        # caption_slide is the only backend writer of slide_prompts.
        return "captioned"
    return "typed"
```

- [ ] **Step 4: Stamp the six writers**

`backend/app/api/resources_crud_router.py` — add the import next to the other `app.services` imports:

```python
from app.services.prompts.origin import stamp_origin
```

and in `update_resource`, right after `update_data.pop("trashed_at", None)`:

```python
        # mig 453: a PATCH that carries prompt text is the user typing. The
        # column is not in ResourceUpdate on purpose — clients do not get to
        # claim an origin, the server derives it from what was written.
        stamp_origin(update_data, "typed")
```

`backend/app/workflows/upload_postprocess.py` — import `from app.services.prompts.origin import stamp_origin` at the top with the other `app.` imports, and change the block at ~line 210 from

```python
                        if patch:
                            await svc.repo.update_resource(resource_id, patch)
```
to
```python
                        if patch:
                            stamp_origin(patch, "extracted")
                            await svc.repo.update_resource(resource_id, patch)
```

`backend/app/workflows/backfill_resource_gen_params.py` — import the same; at the end of `build_patch`, replace `return patch` with:

```python
    return stamp_origin(patch, "extracted")
```

`backend/app/services/library/promote_generated_media_service.py` — in the `create_resource({...})` dict at ~line 219, directly after `"gen_prompt": gen.get("prompt"),` add:

```python
                    # mig 453: a generation's prompt is machine-recorded text.
                    "prompt_origin": "extracted" if gen.get("prompt") else None,
```

`backend/app/workflows/caption_asset.py` — import `stamp_origin`; directly before `await repo.update_resource(resource_id, update)` (~line 201):

```python
            stamp_origin(update, "captioned")
```

`backend/app/workflows/caption_slide.py` — directly after `await repo.merge_slide_prompt(resource_id, slide_name, entry)` (~line 140):

```python
            # mig 453: the row-level origin follows the last writer of any
            # slide's text (spec §8 — per-slide origin is deferred).
            await repo.update_resource(resource_id, {"prompt_origin": "captioned"})
```

- [ ] **Step 5: Run the tests**

Run: `cd backend && uv run pytest tests/services/prompts tests/workflows -q -k "origin or caption or upload_postprocess or backfill_resource_gen_params or promote"`
Expected: all pass (the existing workflow tests still pass with the extra key; if one asserts an exact patch dict, extend its expectation with `"prompt_origin"` — that is the change, not a regression).

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/prompts backend/tests/services/prompts backend/app/api/resources_crud_router.py backend/app/workflows/upload_postprocess.py backend/app/workflows/backfill_resource_gen_params.py backend/app/services/library/promote_generated_media_service.py backend/app/workflows/caption_asset.py backend/app/workflows/caption_slide.py
git commit -m "feat(prompts): stamp prompt_origin on every writer of prompt text"
```

### Task 3 (A3): backfill workflow for existing rows

**Files:**
- Create: `backend/app/workflows/backfill_resource_prompt_origin.py`
- Modify: `backend/app/api/admin/backfill_router.py:46-56` (`_BACKFILLS`)
- Test: `backend/tests/workflows/test_backfill_resource_prompt_origin.py`

**Interfaces:**
- Consumes: `derive_origin` (A2), `has_prompt_expr()` from `app.repositories.media_repository`.
- Produces: `backfill_resource_prompt_origin_workflow(dry_run=True, limit=2000, run_user_id=None) -> dict` with keys `dry_run, scanned, would_fix, fixed, by_origin{typed,extracted,captioned}, fixed_ids`; registry key `"resource_prompt_origin"`.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/workflows/test_backfill_resource_prompt_origin.py
from app.api.admin.backfill_router import _BACKFILLS
from app.workflows.backfill_resource_prompt_origin import (
    backfill_resource_prompt_origin_workflow,
    plan_row,
)


def test_registered_under_the_documented_name():
    assert _BACKFILLS["resource_prompt_origin"] is backfill_resource_prompt_origin_workflow


def test_plan_row_returns_origin_or_none():
    assert plan_row({"gen_prompt": "a", "gen_params": {"steps": 1}}) == "extracted"
    assert plan_row({"gen_prompt": "", "slide_prompts": {}}) is None


def test_workflow_signature_takes_run_user_id():
    import inspect

    params = inspect.signature(inspect.unwrap(backfill_resource_prompt_origin_workflow)).parameters
    assert {"dry_run", "limit", "run_user_id"} <= set(params)
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && uv run pytest tests/workflows/test_backfill_resource_prompt_origin.py -q`
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Write the workflow**

```python
# backend/app/workflows/backfill_resource_prompt_origin.py
"""backfill_resource_prompt_origin — label rows that predate mig 453.

THE BACKFILL PARADIGM (see ``backfill_resource_gen_params.py``): DBOS workflow,
``dry_run=True`` by default, row-wise idempotent (only rows with
``prompt_origin IS NULL`` are touched), failures raise. Takes the dispatching
admin's ``run_user_id`` so the run shows in Task Center.

Rule per row: :func:`app.services.prompts.origin.derive_origin` (spec §3.2).
Rows it returns ``None`` for carry no prompt text and are left NULL.
"""
from __future__ import annotations

from typing import Any, Mapping, Optional

from dbos import DBOS
from loguru import logger
from sqlalchemy import select, update

from app.db.session import read_scope, write_scope
from app.models import Resources
from app.repositories.media_repository import has_prompt_expr
from app.services.prompts.origin import derive_origin

SYSTEM_RUN_USER_ID = "00000000-0000-0000-0000-000000000000"
_AUDIT_IDS_CAP = 200


def plan_row(row: Mapping[str, Any]) -> Optional[str]:
    """The origin this row gets, or None (no prompt text → stays NULL)."""
    return derive_origin(row)


@DBOS.workflow()
async def backfill_resource_prompt_origin_workflow(
    dry_run: bool = True,
    limit: int = 2000,
    run_user_id: Optional[str] = None,
) -> dict[str, Any]:
    from app.services.infra.unified_task_manager import get_task_manager

    manager = get_task_manager()
    task_id = DBOS.workflow_id
    owner = run_user_id or SYSTEM_RUN_USER_ID
    try:
        await manager.create(
            user_id=owner,
            task_type="backfill",
            title="Backfill: resource prompt origin (mig 453)",
            subtitle=f"dry_run={dry_run} limit={limit}",
            dbos_workflow_id=task_id,
            metadata={"backfill": "resource_prompt_origin", "dry_run": dry_run, "limit": limit},
        )
    except Exception as e:
        logger.warning(f"[backfill-prompt-origin] create task_tracking failed (non-fatal): {e}")
    try:
        await manager.start(task_id, user_id=owner, task_type="backfill")
    except Exception as e:
        logger.warning(f"[backfill-prompt-origin] start {task_id}: {e}")

    result: dict[str, Any] = {
        "dry_run": dry_run,
        "scanned": 0,
        "would_fix": 0,
        "fixed": 0,
        "by_origin": {"typed": 0, "extracted": 0, "captioned": 0},
        "fixed_ids": [],
    }
    try:
        async with read_scope() as session:
            rows = (
                (
                    await session.execute(
                        select(
                            Resources.id,
                            Resources.gen_prompt,
                            Resources.gen_prompt_zh,
                            Resources.gen_prompt_json,
                            Resources.gen_params,
                            Resources.slide_prompts,
                        )
                        .where(Resources.prompt_origin.is_(None), has_prompt_expr())
                        .order_by(Resources.id)
                        .limit(limit)
                    )
                )
                .mappings()
                .all()
            )
        for row in rows:
            result["scanned"] += 1
            origin = plan_row(row)
            if origin is None:
                continue
            result["by_origin"][origin] += 1
            if dry_run:
                result["would_fix"] += 1
                continue
            async with write_scope() as session:
                await session.execute(
                    update(Resources)
                    .where(Resources.id == row["id"], Resources.prompt_origin.is_(None))
                    .values(prompt_origin=origin)
                )
            result["fixed"] += 1
            if len(result["fixed_ids"]) < _AUDIT_IDS_CAP:
                result["fixed_ids"].append(str(row["id"]))
    except Exception:
        try:
            await manager.patch_metadata(task_id, result)
        except Exception as e:  # keep the original failure the visible one
            logger.warning(f"[backfill-prompt-origin] patch_metadata {task_id}: {e}")
        raise
    await manager.patch_metadata(task_id, result)
    await manager.complete(task_id, result=result)
    return result
```

Then in `backend/app/api/admin/backfill_router.py`, import it beside the other backfill imports and add to `_BACKFILLS`:

```python
    "resource_prompt_origin": backfill_resource_prompt_origin_workflow,
```

(If `manager.complete`'s keyword differs in `backfill_resource_gen_params.py` lines 215–240, copy that file's exact finishing calls — the two workflows must end the same way.)

- [ ] **Step 4: Run the tests**

Run: `cd backend && uv run pytest tests/workflows/test_backfill_resource_prompt_origin.py tests/api/admin -q`
Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/workflows/backfill_resource_prompt_origin.py backend/app/api/admin/backfill_router.py backend/tests/workflows/test_backfill_resource_prompt_origin.py
git commit -m "feat(prompts): admin backfill for resources.prompt_origin"
```

### Task 4 (A4): PromptEntry schema + pure entry builders

**Files:**
- Create: `backend/app/schemas/prompts.py`, `backend/app/services/prompts/entries.py`
- Test: `backend/tests/services/prompts/test_entries.py`

**Interfaces:**
- Produces (schemas): `PromptForm = Literal["template","image","album"]`, `PromptOrigin = Literal["typed","extracted","captioned"]`, `PromptThumb{url:str, kind:Literal["image"]}`, `PromptSlide{name, url, positive_en, positive_zh, negative_en, negative_zh}`, `PromptEntry{key, form, origin, title, tags, positive_en, positive_zh, negative_en, negative_zh, params, thumbs, slides, source{store,id}, updated_at}`, `PromptPage{items, total, by_form, by_origin}`, `PromptCounts{mine:int, project:Optional[int], system:int}`.
- Produces (builders): `entry_from_asset(row: dict, *, example_resource_ids: list[int]) -> dict`, `entry_from_resource(row: dict) -> dict` (decides image vs album by non-empty `slide_prompts`), `title_from_filename(name: str) -> str`, `matches_query(entry: dict, q: str) -> bool`, `sort_entries(entries: list[dict]) -> list[dict]` (captioned last, then `updated_at` desc).

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/services/prompts/test_entries.py
from app.schemas.prompts import PromptEntry
from app.services.prompts.entries import (
    entry_from_asset,
    entry_from_resource,
    matches_query,
    sort_entries,
    title_from_filename,
)

ASSET = {
    "id": 11, "scope_id": 9000, "asset_type": "prompt", "name": "Rain-soaked close-up",
    "prompt_positive": "hero close-up, rain", "prompt_negative": "flare",
    "prompt_positive_zh": None, "prompt_negative_zh": None,
    "platform_params": {}, "cover_file_id": 501, "tags": {"group": ["Lighting"], "mood": ["cold"]},
    "is_system_preset": False, "updated_at": "2026-09-05T00:00:00+00:00",
}
IMAGE = {
    "id": 346256147694349, "filename": "bicycle-oranges.png", "media_id": None,
    "gen_prompt": "cheerful woman, oranges", "gen_prompt_zh": None,
    "gen_prompt_negative": "lens flare", "gen_prompt_negative_zh": None,
    "gen_params": {"tool": "a1111", "width": 832, "height": 1216, "steps": 28},
    "slide_prompts": None, "prompt_origin": "extracted",
    "updated_at": "2026-09-03T00:00:00+00:00",
}
ALBUM = {
    **IMAGE, "id": 7, "filename": "orange-harvest", "media_id": 99, "gen_prompt": None,
    "gen_params": None, "prompt_origin": "captioned",
    "slide_prompts": {"003.jpg": {"en": "leaning on bicycle"}, "002.jpg": {"en": "winking", "zh": "眨眼", "neg_en": "blur"}},
}


def test_template_entry_shape_and_thumbs():
    e = entry_from_asset(ASSET, example_resource_ids=[601, 602])
    PromptEntry.model_validate(e)
    assert e["key"] == "template:11" and e["form"] == "template" and e["origin"] == "typed"
    assert e["tags"] == ["Lighting", "cold"]
    # cover first, then examples, capped at 3
    assert [t["url"] for t in e["thumbs"]] == [
        "/api/v1/resources/501/cover", "/api/v1/resources/601/cover", "/api/v1/resources/602/cover",
    ]
    assert e["source"] == {"store": "assets", "id": "11"}


def test_template_without_cover_uses_examples_only():
    e = entry_from_asset({**ASSET, "cover_file_id": None}, example_resource_ids=[])
    assert e["thumbs"] == []


def test_image_entry_shape():
    e = entry_from_resource(IMAGE)
    PromptEntry.model_validate(e)
    assert e["key"] == "image:346256147694349" and e["form"] == "image"
    assert e["title"] == "bicycle-oranges" and e["origin"] == "extracted"
    assert e["positive_en"] == "cheerful woman, oranges" and e["negative_en"] == "lens flare"
    assert e["params"]["width"] == 832
    assert e["thumbs"] == [{"url": "/api/v1/resources/346256147694349/cover", "kind": "image"}]
    assert e["slides"] is None and e["source"] == {"store": "uploads", "id": "346256147694349"}


def test_album_entry_lists_slides_sorted_by_name_with_urls():
    e = entry_from_resource(ALBUM)
    PromptEntry.model_validate(e)
    assert e["form"] == "album" and e["key"] == "album:7"
    assert [s["name"] for s in e["slides"]] == ["002.jpg", "003.jpg"]
    assert e["slides"][0]["url"] == "/api/v1/media/99/slides/002.jpg"
    assert e["slides"][0]["positive_zh"] == "眨眼" and e["slides"][0]["negative_en"] == "blur"
    assert e["slides"][1]["positive_zh"] is None
    # the album's own positive is the first slide's, so a list row has a first line
    assert e["positive_en"] == "winking"
    assert [t["url"] for t in e["thumbs"]] == ["/api/v1/media/99/slides/002.jpg", "/api/v1/media/99/slides/003.jpg"]


def test_album_without_media_id_has_no_slide_urls_but_still_lists_text():
    e = entry_from_resource({**ALBUM, "media_id": None})
    assert e["slides"][0]["url"] is None and e["thumbs"] == []


def test_title_from_filename():
    assert title_from_filename("62d1e7d1f9d4b634.PNG") == "62d1e7d1f9d4b634"
    assert title_from_filename("no-extension") == "no-extension"
    assert title_from_filename("") == "Untitled"


def test_matches_query_looks_at_title_text_and_tags():
    e = entry_from_asset(ASSET, example_resource_ids=[])
    assert matches_query(e, "RAIN") and matches_query(e, "lighting") and not matches_query(e, "tiger")
    assert matches_query(e, "")


def test_sort_puts_captioned_last_then_recent_first():
    a = {"origin": "extracted", "updated_at": "2026-09-01T00:00:00+00:00", "key": "a"}
    b = {"origin": "captioned", "updated_at": "2026-09-09T00:00:00+00:00", "key": "b"}
    c = {"origin": None, "updated_at": "2026-09-05T00:00:00+00:00", "key": "c"}
    d = {"origin": "typed", "updated_at": "2026-09-04T00:00:00+00:00", "key": "d"}
    assert [x["key"] for x in sort_entries([a, b, c, d])] == ["d", "a", "b", "c"]
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && uv run pytest tests/services/prompts/test_entries.py -q`
Expected: `ModuleNotFoundError: app.schemas.prompts`.

- [ ] **Step 3: Write the schema module**

```python
# backend/app/schemas/prompts.py
"""Wire shape of the unified prompt catalog (spec 2026-09-05 §3.1).

ONE shape for three storage places. Both surfaces (resource library Prompts
page, canvas Library panel) read this and nothing else; neither knows which
table a row came from beyond ``source.store``.
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

PromptForm = Literal["template", "image", "album"]
PromptOrigin = Literal["typed", "extracted", "captioned"]
PromptSegment = Literal["mine", "project", "system"]


class PromptThumb(BaseModel):
    url: str
    kind: Literal["image"] = "image"


class PromptSlide(BaseModel):
    name: str
    #: None when the album's parsed_media row is gone — the text still shows.
    url: Optional[str] = None
    positive_en: Optional[str] = None
    positive_zh: Optional[str] = None
    negative_en: Optional[str] = None
    negative_zh: Optional[str] = None


class PromptSource(BaseModel):
    store: Literal["assets", "uploads"]
    id: str


class PromptEntry(BaseModel):
    key: str
    form: PromptForm
    #: None only for image/album rows the backfill has not labelled yet.
    origin: Optional[PromptOrigin] = None
    title: str
    tags: List[str] = Field(default_factory=list)
    positive_en: Optional[str] = None
    positive_zh: Optional[str] = None
    negative_en: Optional[str] = None
    negative_zh: Optional[str] = None
    params: Optional[Dict[str, Any]] = None
    thumbs: List[PromptThumb] = Field(default_factory=list)
    slides: Optional[List[PromptSlide]] = None
    source: PromptSource
    updated_at: str


class PromptPage(BaseModel):
    items: List[PromptEntry]
    total: int
    by_form: Dict[str, int]
    by_origin: Dict[str, int]


class PromptCounts(BaseModel):
    mine: int
    #: Only when the request named a project.
    project: Optional[int] = None
    system: int
```

- [ ] **Step 4: Write the builders**

```python
# backend/app/services/prompts/entries.py
"""Pure builders: one storage row → one PromptEntry dict (spec §3.1).

No I/O. The repository hands in plain dicts; the service hands the results to
Pydantic. Keeping the mapping here means the resource-library page and the
canvas panel can never disagree about what a row's title or thumbnail is.
"""
from __future__ import annotations

import os
from typing import Any, Dict, Iterable, List, Mapping, Optional

_THUMB_CAP = 3
_BLANK = {"", "[]", '""', "null", "{}"}


def _text(value: Any) -> Optional[str]:
    if isinstance(value, str) and value.strip() not in _BLANK:
        return value
    return None


def _iso(value: Any) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value or "")


def title_from_filename(name: Optional[str]) -> str:
    stem = os.path.splitext(name or "")[0].strip()
    return stem or "Untitled"


def _cover(resource_id: Any) -> Dict[str, str]:
    return {"url": f"/api/v1/resources/{resource_id}/cover", "kind": "image"}


def entry_from_asset(row: Mapping[str, Any], *, example_resource_ids: Iterable[Any]) -> Dict[str, Any]:
    """A prompt asset → template entry. Thumbs: cover first, then example files."""
    thumbs: List[Dict[str, str]] = []
    if row.get("cover_file_id"):
        thumbs.append(_cover(row["cover_file_id"]))
    for rid in example_resource_ids:
        if len(thumbs) >= _THUMB_CAP:
            break
        if str(rid) != str(row.get("cover_file_id")):
            thumbs.append(_cover(rid))
    tags: List[str] = []
    for values in (row.get("tags") or {}).values():
        if isinstance(values, list):
            tags.extend(str(v) for v in values)
        elif isinstance(values, str):
            tags.append(values)
    return {
        "key": f"template:{row['id']}",
        "form": "template",
        "origin": "typed",
        "title": row.get("name") or "Untitled",
        "tags": tags,
        "positive_en": _text(row.get("prompt_positive")),
        "positive_zh": _text(row.get("prompt_positive_zh")),
        "negative_en": _text(row.get("prompt_negative")),
        "negative_zh": _text(row.get("prompt_negative_zh")),
        "params": (row.get("platform_params") or None),
        "thumbs": thumbs,
        "slides": None,
        "source": {"store": "assets", "id": str(row["id"])},
        "updated_at": _iso(row.get("updated_at")),
    }


def _slides(row: Mapping[str, Any]) -> List[Dict[str, Any]]:
    media_id = row.get("media_id")
    out: List[Dict[str, Any]] = []
    for name in sorted((row.get("slide_prompts") or {}).keys()):
        entry = row["slide_prompts"].get(name) or {}
        if not isinstance(entry, dict):
            entry = {}
        out.append(
            {
                "name": name,
                "url": f"/api/v1/media/{media_id}/slides/{name}" if media_id else None,
                "positive_en": _text(entry.get("en")),
                "positive_zh": _text(entry.get("zh")),
                "negative_en": _text(entry.get("neg_en")),
                "negative_zh": _text(entry.get("neg_zh")),
            }
        )
    return out


def entry_from_resource(row: Mapping[str, Any]) -> Dict[str, Any]:
    """A prompted resource → image entry, or album entry when slide_prompts is non-empty."""
    slide_map = row.get("slide_prompts")
    is_album = isinstance(slide_map, dict) and len(slide_map) > 0
    rid = row["id"]
    base = {
        "origin": row.get("prompt_origin"),
        "title": title_from_filename(row.get("filename")),
        "tags": [],
        "params": (row.get("gen_params") or None),
        "source": {"store": "uploads", "id": str(rid)},
        "updated_at": _iso(row.get("updated_at")),
    }
    if not is_album:
        return {
            **base,
            "key": f"image:{rid}",
            "form": "image",
            "positive_en": _text(row.get("gen_prompt")),
            "positive_zh": _text(row.get("gen_prompt_zh")),
            "negative_en": _text(row.get("gen_prompt_negative")),
            "negative_zh": _text(row.get("gen_prompt_negative_zh")),
            "thumbs": [_cover(rid)],
            "slides": None,
        }
    slides = _slides(row)
    first = slides[0] if slides else {}
    return {
        **base,
        "key": f"album:{rid}",
        "form": "album",
        # The album's own line is its first slide's text: the row-level
        # gen_prompt is a caption of the cover at best, never the slide's.
        "positive_en": first.get("positive_en"),
        "positive_zh": first.get("positive_zh"),
        "negative_en": first.get("negative_en"),
        "negative_zh": first.get("negative_zh"),
        "thumbs": [{"url": s["url"], "kind": "image"} for s in slides[:_THUMB_CAP] if s["url"]],
        "slides": slides,
    }


def matches_query(entry: Mapping[str, Any], q: str) -> bool:
    needle = (q or "").strip().lower()
    if not needle:
        return True
    hay = [entry.get("title") or "", entry.get("positive_en") or "", entry.get("positive_zh") or ""]
    hay.extend(entry.get("tags") or [])
    for s in entry.get("slides") or []:
        hay.extend([s.get("positive_en") or "", s.get("positive_zh") or ""])
    return any(needle in str(h).lower() for h in hay)


def _ts(value: Any) -> float:
    """ISO-8601 → sortable float; unparsable → 0 (sorts last within its rank)."""
    from datetime import datetime

    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0.0


def sort_entries(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Captioned (and unlabelled) rows after every authored one; recent first within."""

    def rank(e: Mapping[str, Any]) -> int:
        return 1 if e.get("origin") in ("captioned", None) else 0

    return sorted(entries, key=lambda e: (rank(e), -_ts(e.get("updated_at"))))
```

- [ ] **Step 5: Run the tests**

Run: `cd backend && uv run pytest tests/services/prompts/test_entries.py -q`
Expected: `8 passed`.

- [ ] **Step 6: Commit**

```bash
git add backend/app/schemas/prompts.py backend/app/services/prompts/entries.py backend/tests/services/prompts/test_entries.py
git commit -m "feat(prompts): PromptEntry wire shape + pure builders for template/image/album"
```

### Task 5 (A5): catalog repository (ORM statements)

**Files:**
- Create: `backend/app/repositories/prompt_catalog_repository.py`
- Test: `backend/tests/services/prompts/test_prompt_catalog_repository_sql.py`

**Interfaces:**
- Consumes: `has_prompt_expr()` (`app.repositories.media_repository`), models `Resources`, `ResourceItems` (`app.models.media`), `Canvases`, `CanvasResourceRefs` (`app.models.canvas`), `AssetFiles` (`app.models.assets`).
- Produces: `PromptCatalogRepository` with `_prompted_resources_stmt(scope_id: int, *, project_id: int | None) -> Select`, `async list_prompted_resources(scope_id, *, project_id=None) -> list[dict]` (keys: id, filename, media_id, gen_prompt, gen_prompt_zh, gen_prompt_negative, gen_prompt_negative_zh, gen_params, slide_prompts, prompt_origin, updated_at), `_example_files_stmt(asset_ids: list[int]) -> Select`, `async example_file_ids(asset_ids) -> dict[int, list[int]]` (slot `examples`, ordered by `sort_order`, capped 3 per asset in Python).

- [ ] **Step 1: Write the failing compiled-SQL tests**

```python
# backend/tests/services/prompts/test_prompt_catalog_repository_sql.py
"""Compiled-SQL pins (same technique as test_assets_repository_sql.py): the
statements are ORM, so what a unit test CAN prove is the predicates they carry."""
from sqlalchemy.dialects import postgresql

from app.repositories.prompt_catalog_repository import PromptCatalogRepository

SCOPE = 331438215859255


def _sql(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect()))


def test_mine_joins_resource_items_on_scope_and_requires_a_prompt():
    sql = _sql(PromptCatalogRepository()._prompted_resources_stmt(SCOPE, project_id=None))
    assert "JOIN resource_items" in sql and "resource_items.scope_id" in sql
    assert "is_trashed" in sql
    assert "gen_prompt" in sql and "slide_prompts" in sql  # has_prompt_expr
    assert "canvas_resource_refs" not in sql


def test_project_segment_goes_through_canvas_refs():
    sql = _sql(PromptCatalogRepository()._prompted_resources_stmt(SCOPE, project_id=55))
    assert "canvas_resource_refs" in sql and "canvases.project_id" in sql


def test_example_files_stmt_filters_slot_examples():
    sql = _sql(PromptCatalogRepository()._example_files_stmt([1, 2]))
    assert "asset_files.slot" in sql and "asset_files.sort_order" in sql
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && uv run pytest tests/services/prompts/test_prompt_catalog_repository_sql.py -q`
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Write the repository**

```python
# backend/app/repositories/prompt_catalog_repository.py
"""ORM statements behind the unified prompt catalog (spec 2026-09-05 §3.1).

Templates come from ``AssetsRepository.list`` (unchanged); this repository owns
the OTHER half — resources whose rows carry prompt text — plus the example-file
lookup that gives a template its thumbnails.

Scope: callers run these inside ``system_request_scope`` (membership is gated
at the router). Under a plain user scope the ``Resources`` SELECT would get
``creator_id = me`` injected and a teammate's uploads would vanish silently.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy import select

from app.db.session import read_scope
from app.models.assets import AssetFiles
from app.models.canvas import Canvases, CanvasResourceRefs
from app.models.media import ResourceItems, Resources
from app.repositories.media_repository import has_prompt_expr

_EXAMPLES_PER_ASSET = 3

_COLUMNS = (
    Resources.id,
    Resources.filename,
    Resources.media_id,
    Resources.gen_prompt,
    Resources.gen_prompt_zh,
    Resources.gen_prompt_negative,
    Resources.gen_prompt_negative_zh,
    Resources.gen_params,
    Resources.slide_prompts,
    Resources.prompt_origin,
    Resources.updated_at,
)


class PromptCatalogRepository:
    def _prompted_resources_stmt(self, scope_id: int, *, project_id: Optional[int]):
        stmt = (
            select(*_COLUMNS)
            .join(ResourceItems, ResourceItems.resource_id == Resources.id)
            .where(ResourceItems.scope_id == int(scope_id))
            .where(Resources.is_trashed.is_(False))
            .where(has_prompt_expr())
            .distinct()
        )
        if project_id is not None:
            # "This project" for a picture = referenced by one of its canvases
            # (spec §8 — not "uploaded into its library").
            referenced = (
                select(CanvasResourceRefs.resource_id)
                .join(Canvases, Canvases.id == CanvasResourceRefs.canvas_id)
                .where(Canvases.project_id == int(project_id))
            )
            stmt = stmt.where(Resources.id.in_(referenced))
        return stmt.order_by(Resources.updated_at.desc())

    async def list_prompted_resources(
        self, scope_id: int, *, project_id: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        stmt = self._prompted_resources_stmt(scope_id, project_id=project_id)
        async with read_scope() as session:
            rows = (await session.execute(stmt)).mappings().all()
        return [dict(r) for r in rows]

    def _example_files_stmt(self, asset_ids: List[int]):
        return (
            select(AssetFiles.asset_id, AssetFiles.resource_id)
            .where(AssetFiles.asset_id.in_([int(a) for a in asset_ids]))
            .where(AssetFiles.slot == "examples")
            .order_by(AssetFiles.asset_id, AssetFiles.sort_order, AssetFiles.attached_at)
        )

    async def example_file_ids(self, asset_ids: List[int]) -> Dict[int, List[int]]:
        if not asset_ids:
            return {}
        async with read_scope() as session:
            rows = (await session.execute(self._example_files_stmt(asset_ids))).all()
        out: Dict[int, List[int]] = {}
        for asset_id, resource_id in rows:
            bucket = out.setdefault(int(asset_id), [])
            if len(bucket) < _EXAMPLES_PER_ASSET:
                bucket.append(int(resource_id))
        return out
```

- [ ] **Step 4: Run the tests**

Run: `cd backend && uv run pytest tests/services/prompts/test_prompt_catalog_repository_sql.py -q`
Expected: `3 passed`. If `Resources.is_trashed` compiles to a different column name, read `backend/app/models/media.py` around line 233 and use the attribute the model declares.

- [ ] **Step 5: Commit**

```bash
git add backend/app/repositories/prompt_catalog_repository.py backend/tests/services/prompts/test_prompt_catalog_repository_sql.py
git commit -m "feat(prompts): catalog repository — prompted resources by scope/project, template example files"
```

### Task 6 (A6): catalog service + `/prompts` router

**Files:**
- Create: `backend/app/services/prompts/catalog_service.py`, `backend/app/api/prompts_router.py`
- Modify: `backend/app/api/__init__.py` (import + `api_router.include_router(router=prompts_api_router, tags=["Prompts"])` directly after the `assets_api_router` include line — find it with `grep -n "assets_api_router" backend/app/api/__init__.py`)
- Test: `backend/tests/services/prompts/test_catalog_service.py`, `backend/tests/api/test_prompts_router.py`

**Interfaces:**
- Consumes: `AssetsRepository.list(scope_id, asset_type=..., project_id=..., library=..., sort=..., limit=..., offset=...)` (existing; returns native rows), `AssetsRepository._accessible_stmt` is NOT used; `PromptCatalogRepository` (A5); builders (A4); `system_request_scope` (`app.db.scope`); `_gate`, `_ok`, `_err`, `ScopeIdQuery`, `OptSnowflakeQuery` imported from `app.api.assets_router`; `AssetError`.
- Produces: `PromptCatalogService(assets_repo=None, catalog_repo=None)` with `async list(scope_id: int, *, segment: str, project_id: int | None, form: str | None, origin: str | None, q: str | None, limit: int, offset: int) -> dict` (PromptPage shape) and `async counts(scope_id: int, *, project_id: int | None) -> dict` (PromptCounts shape); routes `GET /prompts`, `GET /prompts/counts`.

- [ ] **Step 1: Write the failing service test**

```python
# backend/tests/services/prompts/test_catalog_service.py
import pytest

from app.services.prompts.catalog_service import PromptCatalogService

NOW = "2026-09-05T00:00:00+00:00"
SCOPE = 331438215859255


class FakeAssets:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    async def list(self, scope_id, **kw):
        self.calls.append((scope_id, kw))
        presets = kw.get("_presets")  # never passed; kept simple
        return [r for r in self.rows if r["asset_type"] == kw.get("asset_type")]


class FakeCatalog:
    def __init__(self, resources, examples=None):
        self.resources = resources
        self.examples = examples or {}
        self.calls = []

    async def list_prompted_resources(self, scope_id, *, project_id=None):
        self.calls.append(("resources", scope_id, project_id))
        return list(self.resources)

    async def example_file_ids(self, asset_ids):
        return {a: self.examples.get(a, []) for a in asset_ids}


def asset(id_, name, **over):
    row = {
        "id": id_, "scope_id": SCOPE, "asset_type": "prompt", "name": name,
        "prompt_positive": "p", "prompt_negative": None, "prompt_positive_zh": None,
        "prompt_negative_zh": None, "platform_params": {}, "cover_file_id": None,
        "tags": {}, "is_system_preset": False, "in_library": True, "updated_at": NOW,
    }
    row.update(over)
    return row


def resource(id_, **over):
    row = {
        "id": id_, "filename": f"r{id_}.png", "media_id": None, "gen_prompt": "rain on the visor",
        "gen_prompt_zh": None, "gen_prompt_negative": None, "gen_prompt_negative_zh": None,
        "gen_params": {"steps": 20}, "slide_prompts": None, "prompt_origin": "extracted", "updated_at": NOW,
    }
    row.update(over)
    return row


@pytest.mark.asyncio
async def test_mine_merges_templates_and_pictures_with_counts():
    svc = PromptCatalogService(
        assets_repo=FakeAssets([asset(1, "Tpl")]),
        catalog_repo=FakeCatalog([resource(10), resource(11, gen_params=None, gen_prompt_json='{"x":1}', prompt_origin="captioned")]),
    )
    page = await svc.list(SCOPE, segment="mine", project_id=None, form=None, origin=None, q=None, limit=60, offset=0)
    assert page["total"] == 3
    assert page["by_form"] == {"template": 1, "image": 2, "album": 0}
    assert page["by_origin"] == {"typed": 1, "extracted": 1, "captioned": 1}
    assert [e["key"] for e in page["items"]] == ["template:1", "image:10", "image:11"]  # captioned last


@pytest.mark.asyncio
async def test_form_origin_query_filters_apply_after_counting():
    svc = PromptCatalogService(
        assets_repo=FakeAssets([asset(1, "Rain template")]),
        catalog_repo=FakeCatalog([resource(10), resource(12, gen_prompt="tiger")]),
    )
    page = await svc.list(SCOPE, segment="mine", project_id=None, form="image", origin=None, q="rain", limit=60, offset=0)
    assert [e["key"] for e in page["items"]] == ["image:10"]
    # counts describe the UNFILTERED segment so the chips can show what a filter would reveal
    assert page["total"] == 1 and page["by_form"]["template"] == 1


@pytest.mark.asyncio
async def test_project_segment_passes_project_to_both_repos():
    assets = FakeAssets([]); catalog = FakeCatalog([])
    svc = PromptCatalogService(assets_repo=assets, catalog_repo=catalog)
    await svc.list(SCOPE, segment="project", project_id=55, form=None, origin=None, q=None, limit=10, offset=0)
    assert assets.calls[0][1]["project_id"] == 55 and assets.calls[0][1]["library"] == "all"
    assert catalog.calls == [("resources", SCOPE, 55)]


@pytest.mark.asyncio
async def test_system_segment_only_returns_presets_and_no_pictures():
    assets = FakeAssets([asset(2, "Preset", is_system_preset=True), asset(3, "Mine")])
    catalog = FakeCatalog([resource(10)])
    svc = PromptCatalogService(assets_repo=assets, catalog_repo=catalog)
    page = await svc.list(SCOPE, segment="system", project_id=None, form=None, origin=None, q=None, limit=10, offset=0)
    assert [e["key"] for e in page["items"]] == ["template:2"]
    assert catalog.calls == []


@pytest.mark.asyncio
async def test_project_segment_without_project_id_is_422():
    from app.services.assets.assets_service import AssetError
    svc = PromptCatalogService(assets_repo=FakeAssets([]), catalog_repo=FakeCatalog([]))
    with pytest.raises(AssetError) as ei:
        await svc.list(SCOPE, segment="project", project_id=None, form=None, origin=None, q=None, limit=10, offset=0)
    assert ei.value.status == 422 and ei.value.code == "project_required"


@pytest.mark.asyncio
async def test_counts():
    svc = PromptCatalogService(
        assets_repo=FakeAssets([asset(1, "Mine"), asset(2, "Preset", is_system_preset=True)]),
        catalog_repo=FakeCatalog([resource(10)]),
    )
    assert await svc.counts(SCOPE, project_id=None) == {"mine": 2, "project": None, "system": 1}
```

- [ ] **Step 2: Write the failing router test**

```python
# backend/tests/api/test_prompts_router.py
"""/prompts: envelope, filter pass-through, scope gate (no DB)."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api import prompts_router as pr
from app.core.deps import get_auth

USER = "11111111-1111-1111-1111-111111111111"


class _Auth:
    user_id = USER


class _FakeService:
    def __init__(self):
        self.calls = []

    async def list(self, scope_id, **kw):
        self.calls.append(("list", scope_id, kw))
        return {"items": [], "total": 0, "by_form": {"template": 0, "image": 0, "album": 0},
                "by_origin": {"typed": 0, "extracted": 0, "captioned": 0}}

    async def counts(self, scope_id, **kw):
        self.calls.append(("counts", scope_id, kw))
        return {"mine": 3, "project": None, "system": 0}


@pytest.fixture
def app(monkeypatch):
    application = FastAPI()
    application.include_router(pr.router, prefix="/api/v1")

    async def _fake_auth():
        return _Auth()

    async def _member_ok(scope_id, user_id):
        return scope_id != "666"

    application.dependency_overrides[get_auth] = _fake_auth
    monkeypatch.setattr(pr, "_is_member", _member_ok)
    fake = _FakeService()
    monkeypatch.setattr(pr, "_service", lambda: fake)
    application.state.fake = fake
    return application


@pytest.mark.asyncio
async def test_list_passes_filters(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/api/v1/prompts?scope_id=9000&segment=project&project_id=55&form=album&origin=captioned&q=rain&limit=20&offset=40")
    assert r.status_code == 200 and r.json()["success"] is True
    _, scope, kw = app.state.fake.calls[0]
    assert scope == 9000 and kw == {"segment": "project", "project_id": 55, "form": "album",
                                    "origin": "captioned", "q": "rain", "limit": 20, "offset": 40}


@pytest.mark.asyncio
async def test_bad_segment_is_422(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/api/v1/prompts?scope_id=9000&segment=everything")
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_non_member_403_in_envelope(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/api/v1/prompts?scope_id=666")
    assert r.status_code == 403 and r.json()["error"]["code"] == "not_a_member"


@pytest.mark.asyncio
async def test_counts(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/api/v1/prompts/counts?scope_id=9000")
    assert r.status_code == 200 and r.json()["data"] == {"mine": 3, "project": None, "system": 0}
```

- [ ] **Step 3: Run both to verify failure**

Run: `cd backend && uv run pytest tests/services/prompts/test_catalog_service.py tests/api/test_prompts_router.py -q`
Expected: `ModuleNotFoundError` for both modules.

- [ ] **Step 4: Write the service**

```python
# backend/app/services/prompts/catalog_service.py
"""Unified prompt catalog (spec 2026-09-05 §3.1): templates ∪ prompted pictures.

Filtering by form / origin / q happens in Python AFTER the counts are taken, so
the chips describe the whole segment ("Images 12") rather than the current
narrowing. The corpus is small by construction (one scope's prompts); if it
ever is not, the count queries move to SQL and this comment goes with them.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.db.scope import system_request_scope
from app.repositories.assets_repository import AssetsRepository
from app.repositories.prompt_catalog_repository import PromptCatalogRepository
from app.services.assets.assets_service import AssetError
from app.services.prompts.entries import (
    entry_from_asset,
    entry_from_resource,
    matches_query,
    sort_entries,
)

FORMS = ("template", "image", "album")
ORIGINS = ("typed", "extracted", "captioned")
_TEMPLATE_PAGE = 500  # every template in the scope; the shelf paginates in Python


class PromptCatalogService:
    def __init__(
        self,
        assets_repo: Optional[AssetsRepository] = None,
        catalog_repo: Optional[PromptCatalogRepository] = None,
    ):
        self.assets = assets_repo or AssetsRepository()
        self.catalog = catalog_repo or PromptCatalogRepository()

    async def _templates(self, scope_id: int, *, segment: str, project_id: Optional[int]) -> List[Dict[str, Any]]:
        rows = await self.assets.list(
            scope_id,
            asset_type="prompt",
            project_id=project_id if segment == "project" else None,
            library="all" if segment == "project" else "in",
            sort="recent",
            limit=_TEMPLATE_PAGE,
            offset=0,
        )
        if segment == "system":
            rows = [r for r in rows if r.get("is_system_preset")]
        else:
            rows = [r for r in rows if not r.get("is_system_preset")]
        examples = await self.catalog.example_file_ids([int(r["id"]) for r in rows])
        return [entry_from_asset(r, example_resource_ids=examples.get(int(r["id"]), [])) for r in rows]

    async def _pictures(self, scope_id: int, *, segment: str, project_id: Optional[int]) -> List[Dict[str, Any]]:
        if segment == "system":
            return []
        async with system_request_scope("prompt catalog: scope membership gated by /prompts"):
            rows = await self.catalog.list_prompted_resources(
                scope_id, project_id=project_id if segment == "project" else None
            )
        return [entry_from_resource(r) for r in rows]

    async def _segment(self, scope_id: int, segment: str, project_id: Optional[int]) -> List[Dict[str, Any]]:
        if segment == "project" and project_id is None:
            raise AssetError(422, "project_required", "segment=project needs project_id")
        templates = await self._templates(scope_id, segment=segment, project_id=project_id)
        pictures = await self._pictures(scope_id, segment=segment, project_id=project_id)
        return sort_entries(templates + pictures)

    async def list(
        self,
        scope_id: int,
        *,
        segment: str,
        project_id: Optional[int],
        form: Optional[str],
        origin: Optional[str],
        q: Optional[str],
        limit: int,
        offset: int,
    ) -> Dict[str, Any]:
        entries = await self._segment(scope_id, segment, project_id)
        by_form = {f: sum(1 for e in entries if e["form"] == f) for f in FORMS}
        by_origin = {o: sum(1 for e in entries if e["origin"] == o) for o in ORIGINS}
        narrowed = [
            e
            for e in entries
            if (form is None or e["form"] == form)
            and (origin is None or e["origin"] == origin)
            and matches_query(e, q or "")
        ]
        return {
            "items": narrowed[offset : offset + limit],
            "total": len(entries),
            "by_form": by_form,
            "by_origin": by_origin,
        }

    async def counts(self, scope_id: int, *, project_id: Optional[int]) -> Dict[str, Any]:
        mine = len(await self._segment(scope_id, "mine", None))
        system = len(await self._segment(scope_id, "system", None))
        project = len(await self._segment(scope_id, "project", project_id)) if project_id else None
        return {"mine": mine, "project": project, "system": system}
```

- [ ] **Step 5: Write the router**

```python
# backend/app/api/prompts_router.py
"""GET /prompts — the unified prompt catalog (spec 2026-09-05 §3.1).

Same gate, same envelope, same error shape as /assets: the two are read by the
same pages and a client must not need two error parsers.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query

from app.api.assets_router import OptSnowflakeQuery, ScopeIdQuery, _err, _ok
from app.api.assets_router import _is_member as _assets_is_member
from app.core.deps import AuthDep
from app.schemas.assets import Envelope, ErrorEnvelope
from app.schemas.prompts import PromptCounts, PromptPage
from app.services.assets.assets_service import AssetError
from app.services.prompts.catalog_service import PromptCatalogService

router = APIRouter(tags=["prompts"])
_ERRORS = {403: {"model": ErrorEnvelope}, 422: {"model": ErrorEnvelope}}


def _service() -> PromptCatalogService:
    return PromptCatalogService()


async def _is_member(scope_id: str, user_id: str) -> bool:
    """Module-local indirection so THIS router's tests can patch the gate."""
    return await _assets_is_member(scope_id, user_id)


async def _gate(scope_id: str, auth) -> int:
    if not await _is_member(scope_id, auth.user_id):
        raise AssetError(403, "not_a_member", "You are not a member of this scope")
    return int(scope_id)


@router.get("/prompts", response_model=Envelope[PromptPage], responses=_ERRORS)
async def list_prompts(
    auth: AuthDep,
    scope_id: ScopeIdQuery,
    segment: str = Query("mine", pattern="^(mine|project|system)$"),
    project_id: OptSnowflakeQuery = None,
    form: Optional[str] = Query(None, pattern="^(template|image|album)$"),
    origin: Optional[str] = Query(None, pattern="^(typed|extracted|captioned)$"),
    q: Optional[str] = Query(None, max_length=200),
    limit: int = Query(60, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    try:
        sid = await _gate(scope_id, auth)
        page = await _service().list(
            sid,
            segment=segment,
            project_id=int(project_id) if project_id else None,
            form=form,
            origin=origin,
            q=q,
            limit=limit,
            offset=offset,
        )
    except AssetError as e:
        return _err(e)
    return _ok(page)


@router.get("/prompts/counts", response_model=Envelope[PromptCounts], responses=_ERRORS)
async def prompt_counts(
    auth: AuthDep,
    scope_id: ScopeIdQuery,
    project_id: OptSnowflakeQuery = None,
):
    try:
        sid = await _gate(scope_id, auth)
        data = await _service().counts(sid, project_id=int(project_id) if project_id else None)
    except AssetError as e:
        return _err(e)
    return _ok(data)
```

- [ ] **Step 6: Mount the router**

In `backend/app/api/__init__.py`, next to `from app.api.assets_router import router as assets_api_router`, add:

```python
from app.api.prompts_router import router as prompts_api_router
```

and directly after the line `api_router.include_router(router=assets_api_router, ...)` add:

```python
api_router.include_router(router=prompts_api_router, tags=["Prompts"])
```

- [ ] **Step 7: Run the tests and the app import**

Run: `cd backend && uv run pytest tests/services/prompts tests/api/test_prompts_router.py -q && uv run python -c "from app.api import api_router; print([r.path for r in api_router.routes if 'prompts' in r.path])"`
Expected: all pass; prints `['/prompts', '/prompts/counts']`.

- [ ] **Step 8: Commit**

```bash
git add backend/app/services/prompts/catalog_service.py backend/app/api/prompts_router.py backend/app/api/__init__.py backend/tests/services/prompts/test_catalog_service.py backend/tests/api/test_prompts_router.py
git commit -m "feat(api): GET /prompts + /prompts/counts — unified prompt catalog"
```

### Task 7 (A7): Phase A real-stack acceptance (no code)

- [ ] **Step 1: After merge + deploy, run the backfill dry-run** via the admin backfill endpoint (`POST /api/v1/admin/backfills/resource_prompt_origin` with `{"dry_run": true}` — check the exact path in `backend/app/api/admin/backfill_router.py`). Expected `by_origin` ≈ `{extracted: 7, captioned: 6, typed: 0}` (spec §1; captioned includes the album). Then run live and verify with the debug account's scope:

```bash
docker exec nous-db psql -U postgres -p 55434 -d postgres -Atc "SELECT prompt_origin, count(*) FROM public.resources WHERE coalesce(is_trashed,false)=false AND (btrim(coalesce(gen_prompt,''))<>'' OR btrim(coalesce(gen_prompt_zh,''))<>'' OR (slide_prompts IS NOT NULL AND slide_prompts::text NOT IN ('{}','null','[]'))) GROUP BY 1;"
```
Expected: no `NULL` bucket.

- [ ] **Step 2: Probe the endpoint** with the debug account token (mint via `E2E_MINT_TOKEN=1` per `frontend/e2e-prod/README.md`): `GET https://cn.nous.ink:88/api/v1/prompts?scope_id=331438215859255&segment=mine` → `total ≥ 1`, `by_origin` sums to `total`, every item validates against §3.1 (form/origin/thumbs/slides).

---

## Phase B — resource library → Prompts page

### Task 8 (B1): `promptsService.ts` (types, fetch, helpers, save-as-template)

**Files:**
- Create: `frontend/services/promptsService.ts`
- Modify: `frontend/services/assetsService.ts:129-165` (`AssetCreateBody` gains prompt fields + tags)
- Test: `frontend/services/promptsService.test.ts`

**Interfaces:**
- Consumes: `envelopeFetch`, `jsonHeaders` (`./apiEnvelope`), `getAuthHeaders` (`./parserService`), `getApiUrl` (`../utils/apiConfig`), `createAsset`, `attachFile`, `updateAsset` (`./assetsService`).
- Produces:
  ```ts
  export type PromptForm = 'template' | 'image' | 'album';
  export type PromptOrigin = 'typed' | 'extracted' | 'captioned';
  export type PromptSegment = 'mine' | 'project' | 'system';
  export type PromptLang = 'en' | 'zh';
  export interface PromptThumb { url: string; kind: 'image' }
  export interface PromptSlide { name: string; url: string | null; positive_en: string | null; positive_zh: string | null; negative_en: string | null; negative_zh: string | null }
  export interface PromptEntry { key: string; form: PromptForm; origin: PromptOrigin | null; title: string; tags: string[]; positive_en: string | null; positive_zh: string | null; negative_en: string | null; negative_zh: string | null; params: Record<string, unknown> | null; thumbs: PromptThumb[]; slides: PromptSlide[] | null; source: { store: 'assets' | 'uploads'; id: string }; updated_at: string }
  export interface PromptPage { items: PromptEntry[]; total: number; by_form: Record<PromptForm, number>; by_origin: Record<PromptOrigin, number> }
  export interface PromptCounts { mine: number; project: number | null; system: number }
  export interface FetchPromptsOptions { segment?: PromptSegment; projectId?: string | null; form?: PromptForm | null; origin?: PromptOrigin | null; q?: string; limit?: number; offset?: number }
  export async function fetchPrompts(scopeId: string, opts?: FetchPromptsOptions): Promise<PromptPage>
  export async function fetchPromptCounts(scopeId: string, projectId?: string | null): Promise<PromptCounts>
  export function promptText(src: { positive_en: string | null; positive_zh: string | null; negative_en: string | null; negative_zh: string | null }, lang: PromptLang): { positive: string; negative: string | null; shownLang: PromptLang | null }
  export function langAvailability(src: { positive_en: string | null; positive_zh: string | null }): 'both' | 'en' | 'zh' | 'none'
  export function ratioFromParams(params: Record<string, unknown> | null): string | null   // '832x1216' → '2:3'; null when width/height missing
  export function paramChips(params: Record<string, unknown> | null): string[]           // ordered: ratio, WxH, 'steps N', 'cfg N', 'seed N', sampler, model — only those present
  export function thumbSrc(url: string | null | undefined): string                        // '/api/...' → absolute
  export interface SaveAsTemplateInput { title: string; group: string; positive: string; negative: string; positiveZh?: string; negativeZh?: string; exampleResourceIds: string[] }
  export async function saveAsTemplate(scopeId: string, input: SaveAsTemplateInput): Promise<{ assetId: string }>
  ```

- [ ] **Step 1: Write the failing tests**

```ts
// frontend/services/promptsService.test.ts
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('./parserService', () => ({ getAuthHeaders: vi.fn(async () => ({ Authorization: 'Bearer t' })) }));
vi.mock('../utils/apiConfig', () => ({ getApiUrl: () => 'https://api.test' }));
const createAsset = vi.fn(async () => ({ id: '900' }));
const attachFile = vi.fn(async () => ({}));
const updateAsset = vi.fn(async () => ({}));
vi.mock('./assetsService', () => ({ createAsset: (...a: unknown[]) => createAsset(...a), attachFile: (...a: unknown[]) => attachFile(...a), updateAsset: (...a: unknown[]) => updateAsset(...a) }));

import {
  fetchPrompts, langAvailability, paramChips, promptText, ratioFromParams, saveAsTemplate, thumbSrc,
} from './promptsService';

const ok = (data: unknown) => new Response(JSON.stringify({ success: true, data }), { status: 200, headers: { 'Content-Type': 'application/json' } });

describe('fetchPrompts', () => {
  beforeEach(() => { vi.restoreAllMocks(); createAsset.mockClear(); attachFile.mockClear(); updateAsset.mockClear(); });

  it('builds the query from options and unwraps the envelope', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(ok({ items: [], total: 0, by_form: {}, by_origin: {} }));
    await fetchPrompts('9000', { segment: 'project', projectId: '55', form: 'album', origin: 'captioned', q: 'rain', limit: 20, offset: 40 });
    const url = new URL(String(spy.mock.calls[0][0]));
    expect(url.pathname).toBe('/api/v1/prompts');
    expect(Object.fromEntries(url.searchParams)).toEqual({ scope_id: '9000', segment: 'project', project_id: '55', form: 'album', origin: 'captioned', q: 'rain', limit: '20', offset: '40' });
  });

  it('omits empty filters', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(ok({ items: [], total: 0, by_form: {}, by_origin: {} }));
    await fetchPrompts('9000', { q: '   ', form: null });
    const url = new URL(String(spy.mock.calls[0][0]));
    expect(Object.fromEntries(url.searchParams)).toEqual({ scope_id: '9000', segment: 'mine' });
  });
});

describe('helpers', () => {
  const src = { positive_en: 'rain', positive_zh: null, negative_en: null, negative_zh: '模糊' };
  it('promptText falls back to the other language and says which it showed', () => {
    expect(promptText(src, 'en')).toEqual({ positive: 'rain', negative: '模糊', shownLang: 'en' });
    expect(promptText(src, 'zh')).toEqual({ positive: 'rain', negative: '模糊', shownLang: 'en' });
    expect(promptText({ positive_en: null, positive_zh: null, negative_en: null, negative_zh: null }, 'en')).toEqual({ positive: '', negative: null, shownLang: null });
  });
  it('langAvailability', () => {
    expect(langAvailability(src)).toBe('en');
    expect(langAvailability({ positive_en: 'a', positive_zh: 'b' })).toBe('both');
    expect(langAvailability({ positive_en: null, positive_zh: null })).toBe('none');
  });
  it('ratioFromParams reduces width/height', () => {
    expect(ratioFromParams({ width: 832, height: 1216 })).toBe('13:19');
    expect(ratioFromParams({ width: 1920, height: 1080 })).toBe('16:9');
    expect(ratioFromParams({ width: 1024, height: 1024 })).toBe('1:1');
    expect(ratioFromParams({ steps: 3 })).toBeNull();
    expect(ratioFromParams(null)).toBeNull();
  });
  it('paramChips keeps a fixed order and skips absent keys', () => {
    expect(paramChips({ model: 'krea', steps: 28, cfg: 7, width: 1920, height: 1080, sampler: 'DPM++ 2M', seed: 8813 }))
      .toEqual(['16:9', '1920×1080', 'steps 28', 'cfg 7', 'seed 8813', 'DPM++ 2M', 'krea']);
    expect(paramChips({})).toEqual([]);
    expect(paramChips(null)).toEqual([]);
  });
  it('thumbSrc absolutizes api paths only', () => {
    expect(thumbSrc('/api/v1/resources/1/cover')).toBe('https://api.test/api/v1/resources/1/cover');
    expect(thumbSrc('https://x/y.png')).toBe('https://x/y.png');
    expect(thumbSrc(null)).toBe('');
  });
});

describe('saveAsTemplate', () => {
  it('creates the asset, attaches examples to the examples slot, sets the cover', async () => {
    const r = await saveAsTemplate('9000', { title: 'Courier dawn', group: 'Lighting', positive: 'p', negative: 'n', exampleResourceIds: ['501', '502'] });
    expect(r).toEqual({ assetId: '900' });
    expect(createAsset).toHaveBeenCalledWith('9000', {
      asset_type: 'prompt', name: 'Courier dawn', source: 'manual',
      prompt_positive: 'p', prompt_negative: 'n', prompt_positive_zh: null, prompt_negative_zh: null,
      tags: { group: ['Lighting'] },
    });
    expect(attachFile.mock.calls.map((c) => c[2])).toEqual([{ resource_id: '501', slot: 'examples' }, { resource_id: '502', slot: 'examples' }]);
    expect(updateAsset).toHaveBeenCalledWith('9000', '900', { cover_file_id: '501' });
  });
  it('empty group means no tags and no cover call without examples', async () => {
    await saveAsTemplate('9000', { title: 't', group: '  ', positive: 'p', negative: '', exampleResourceIds: [] });
    expect(createAsset.mock.calls[0][1]).toMatchObject({ tags: {}, prompt_negative: null });
    expect(attachFile).not.toHaveBeenCalled();
    expect(updateAsset).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `cd frontend && npx vitest run services/promptsService.test.ts`
Expected: fails to resolve `./promptsService`.

- [ ] **Step 3: Extend `AssetCreateBody`**

In `frontend/services/assetsService.ts`, inside `export interface AssetCreateBody` after `description?: string;` add:

```ts
  /** Prompt-type assets carry their body here (server: `AssetCreate.prompt_*`). */
  prompt_positive?: string | null;
  prompt_negative?: string | null;
  prompt_positive_zh?: string | null;
  prompt_negative_zh?: string | null;
  /** jsonb object of group → values, e.g. `{ group: ['Lighting'] }` — the shape
   *  `_tag_match` (assets_repository.py) searches with `?tag=`. */
  tags?: Record<string, string[]>;
```

- [ ] **Step 4: Write the service**

```ts
// frontend/services/promptsService.ts
//
// The unified prompt catalog (spec 2026-09-05 §3.1). ONE client for both
// surfaces — the resource library's Prompts page and the canvas Library panel.
// Nothing here talks to Supabase; the browser-side prompt query this replaces
// (`fetchPromptAssets`) returned an empty list on RLS errors and is gone.
import { getApiUrl } from '../utils/apiConfig';
import { attachFile, createAsset, updateAsset } from './assetsService';
import { envelopeFetch } from './apiEnvelope';
import { getAuthHeaders } from './parserService';

export type PromptForm = 'template' | 'image' | 'album';
export type PromptOrigin = 'typed' | 'extracted' | 'captioned';
export type PromptSegment = 'mine' | 'project' | 'system';
export type PromptLang = 'en' | 'zh';
export const PROMPT_FORMS: readonly PromptForm[] = ['template', 'image', 'album'];
export const PROMPT_ORIGINS: readonly PromptOrigin[] = ['typed', 'extracted', 'captioned'];

export interface PromptThumb { url: string; kind: 'image' }
export interface PromptSlide {
  name: string;
  url: string | null;
  positive_en: string | null;
  positive_zh: string | null;
  negative_en: string | null;
  negative_zh: string | null;
}
export interface PromptTextSides {
  positive_en: string | null;
  positive_zh: string | null;
  negative_en: string | null;
  negative_zh: string | null;
}
export interface PromptEntry extends PromptTextSides {
  key: string;
  form: PromptForm;
  origin: PromptOrigin | null;
  title: string;
  tags: string[];
  params: Record<string, unknown> | null;
  thumbs: PromptThumb[];
  slides: PromptSlide[] | null;
  source: { store: 'assets' | 'uploads'; id: string };
  updated_at: string;
}
export interface PromptPage {
  items: PromptEntry[];
  total: number;
  by_form: Record<PromptForm, number>;
  by_origin: Record<PromptOrigin, number>;
}
export interface PromptCounts { mine: number; project: number | null; system: number }

export interface FetchPromptsOptions {
  segment?: PromptSegment;
  projectId?: string | null;
  form?: PromptForm | null;
  origin?: PromptOrigin | null;
  q?: string;
  limit?: number;
  offset?: number;
}

const BASE = () => `${getApiUrl()}/api/v1/prompts`;

export async function fetchPrompts(scopeId: string, opts: FetchPromptsOptions = {}): Promise<PromptPage> {
  const qs = new URLSearchParams({ scope_id: scopeId, segment: opts.segment ?? 'mine' });
  if (opts.projectId) qs.set('project_id', opts.projectId);
  if (opts.form) qs.set('form', opts.form);
  if (opts.origin) qs.set('origin', opts.origin);
  const q = (opts.q ?? '').trim();
  if (q) qs.set('q', q);
  if (opts.limit !== undefined) qs.set('limit', String(opts.limit));
  if (opts.offset !== undefined) qs.set('offset', String(opts.offset));
  return envelopeFetch<PromptPage>(`${BASE()}?${qs}`, { headers: await getAuthHeaders() });
}

export async function fetchPromptCounts(scopeId: string, projectId?: string | null): Promise<PromptCounts> {
  const qs = new URLSearchParams({ scope_id: scopeId });
  if (projectId) qs.set('project_id', projectId);
  return envelopeFetch<PromptCounts>(`${BASE()}/counts?${qs}`, { headers: await getAuthHeaders() });
}

/** The text to show/insert for a language, falling back to the other side.
 *  `shownLang` says which side actually supplied the positive (null = none). */
export function promptText(src: PromptTextSides, lang: PromptLang): { positive: string; negative: string | null; shownLang: PromptLang | null } {
  const order: PromptLang[] = lang === 'zh' ? ['zh', 'en'] : ['en', 'zh'];
  const shownLang = order.find((l) => (l === 'en' ? src.positive_en : src.positive_zh)) ?? null;
  const positive = shownLang === 'en' ? src.positive_en! : shownLang === 'zh' ? src.positive_zh! : '';
  const negOrder = shownLang ? [shownLang, ...order.filter((l) => l !== shownLang)] : order;
  const negLang = negOrder.find((l) => (l === 'en' ? src.negative_en : src.negative_zh)) ?? null;
  const negative = negLang === 'en' ? src.negative_en : negLang === 'zh' ? src.negative_zh : null;
  return { positive, negative: negative ?? null, shownLang };
}

export function langAvailability(src: { positive_en: string | null; positive_zh: string | null }): 'both' | 'en' | 'zh' | 'none' {
  const en = !!src.positive_en, zh = !!src.positive_zh;
  return en && zh ? 'both' : en ? 'en' : zh ? 'zh' : 'none';
}

function num(v: unknown): number | null {
  const n = typeof v === 'number' ? v : typeof v === 'string' ? Number(v) : NaN;
  return Number.isFinite(n) && n > 0 ? n : null;
}
function gcd(a: number, b: number): number { return b === 0 ? a : gcd(b, a % b); }

export function ratioFromParams(params: Record<string, unknown> | null): string | null {
  const w = num(params?.width), h = num(params?.height);
  if (!w || !h) return null;
  const g = gcd(Math.round(w), Math.round(h));
  return `${Math.round(w) / g}:${Math.round(h) / g}`;
}

export function paramChips(params: Record<string, unknown> | null): string[] {
  if (!params) return [];
  const out: string[] = [];
  const ratio = ratioFromParams(params);
  if (ratio) out.push(ratio);
  const w = num(params.width), h = num(params.height);
  if (w && h) out.push(`${w}×${h}`);
  if (num(params.steps)) out.push(`steps ${params.steps}`);
  if (num(params.cfg)) out.push(`cfg ${params.cfg}`);
  if (params.seed !== undefined && params.seed !== null && params.seed !== '') out.push(`seed ${params.seed}`);
  if (typeof params.sampler === 'string' && params.sampler) out.push(params.sampler);
  if (typeof params.model === 'string' && params.model) out.push(params.model);
  return out;
}

export function thumbSrc(url: string | null | undefined): string {
  if (!url) return '';
  return url.startsWith('/api/') ? `${getApiUrl()}${url}` : url;
}

export interface SaveAsTemplateInput {
  title: string;
  group: string;
  positive: string;
  negative: string;
  positiveZh?: string;
  negativeZh?: string;
  exampleResourceIds: string[];
}

/** Spec §3.5: create the prompt asset, hang the pictures on its `examples`
 *  slot, make the first one the cover. Files do not move. Not rolled back on
 *  a partial failure — a visible half-made asset beats a silent one. */
export async function saveAsTemplate(scopeId: string, input: SaveAsTemplateInput): Promise<{ assetId: string }> {
  const group = input.group.trim();
  const created = await createAsset(scopeId, {
    asset_type: 'prompt',
    name: input.title.trim(),
    source: 'manual',
    prompt_positive: input.positive || null,
    prompt_negative: input.negative || null,
    prompt_positive_zh: input.positiveZh || null,
    prompt_negative_zh: input.negativeZh || null,
    tags: group ? { group: [group] } : {},
  });
  const assetId = String(created.id);
  for (const rid of input.exampleResourceIds) {
    await attachFile(scopeId, assetId, { resource_id: rid, slot: 'examples' });
  }
  if (input.exampleResourceIds.length > 0) {
    await updateAsset(scopeId, assetId, { cover_file_id: input.exampleResourceIds[0] });
  }
  return { assetId };
}
```

- [ ] **Step 5: Run tests + typecheck**

Run: `cd frontend && npx vitest run services/promptsService.test.ts && npx tsc --noEmit -p . 2>&1 | grep -E "promptsService|assetsService" ; true`
Expected: tests pass; no type errors in the two files. If `AssetUpdateBody` lacks `cover_file_id`, add `cover_file_id?: string | null;` to it in `assetsService.ts` (the server's `AssetUpdate` accepts it).

- [ ] **Step 6: Commit**

```bash
git add frontend/services/promptsService.ts frontend/services/promptsService.test.ts frontend/services/assetsService.ts
git commit -m "feat(prompts): promptsService — catalog client, text/param helpers, save-as-template"
```

### Task 9 (B2): shared prompt atoms (thumbs, tags, template form)

**Files:**
- Create: `frontend/components/prompts/PromptThumbs.tsx`, `frontend/components/prompts/PromptTags.tsx`, `frontend/components/prompts/TemplateForm.tsx`
- Test: `frontend/components/prompts/PromptThumbs.test.tsx`, `frontend/components/prompts/TemplateForm.test.tsx`
- Modify: `frontend/public/locales/en.json`, `frontend/public/locales/zh.json` (new top-level `prompts` object — see Step 4)

**Interfaces:**
- Produces:
  ```tsx
  // PromptThumbs: one thumb, a stack with a count, or a dashed empty square
  export function PromptThumbs({ thumbs, count, size = 64, testId }: { thumbs: PromptThumb[]; count?: number; size?: number; testId?: string }): JSX.Element
  // PromptTags: <FormTag form> and <OriginTag origin params?>
  export function FormTag({ form }: { form: PromptForm }): JSX.Element
  export function OriginTag({ origin, params }: { origin: PromptOrigin | null; params?: Record<string, unknown> | null }): JSX.Element | null
  // TemplateForm: controlled form body shared by the shelf dialog and the panel's inline form
  export interface TemplateFormValue { title: string; group: string; positive: string; negative: string; exampleIds: string[] }
  export function TemplateForm({ value, onChange, examples, groups, disabled }: { value: TemplateFormValue; onChange: (v: TemplateFormValue) => void; examples: Array<{ id: string; url: string | null }>; groups: string[]; disabled?: boolean }): JSX.Element
  ```

- [ ] **Step 1: Write the failing tests**

```tsx
// frontend/components/prompts/PromptThumbs.test.tsx
import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
vi.mock('../../utils/apiConfig', () => ({ getApiUrl: () => 'https://api.test' }));
import { PromptThumbs } from './PromptThumbs';

describe('PromptThumbs', () => {
  it('renders a dashed placeholder when there is nothing to show', () => {
    render(<PromptThumbs thumbs={[]} testId="t" />);
    expect(screen.getByTestId('t')).toHaveAttribute('data-empty', 'true');
  });
  it('renders one image for one thumb', () => {
    render(<PromptThumbs thumbs={[{ url: '/api/v1/resources/1/cover', kind: 'image' }]} testId="t" />);
    expect(screen.getAllByRole('img')).toHaveLength(1);
    expect(screen.getByRole('img')).toHaveAttribute('src', 'https://api.test/api/v1/resources/1/cover');
  });
  it('stacks up to three and shows the count', () => {
    const thumbs = [1, 2, 3].map((i) => ({ url: `/api/v1/media/9/slides/00${i}.jpg`, kind: 'image' as const }));
    render(<PromptThumbs thumbs={thumbs} count={6} testId="t" />);
    expect(screen.getAllByRole('img')).toHaveLength(3);
    expect(screen.getByText('6')).toBeInTheDocument();
  });
});
```

```tsx
// frontend/components/prompts/TemplateForm.test.tsx
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (_k: string, d?: unknown) => (typeof d === 'string' ? d : (d as { defaultValue?: string })?.defaultValue ?? _k) }) }));
vi.mock('../../utils/apiConfig', () => ({ getApiUrl: () => 'https://api.test' }));
import { TemplateForm, type TemplateFormValue } from './TemplateForm';

const value: TemplateFormValue = { title: 'T', group: '', positive: 'p', negative: '', exampleIds: ['1'] };

describe('TemplateForm', () => {
  it('reports edits through onChange without owning state', () => {
    const onChange = vi.fn();
    render(<TemplateForm value={value} onChange={onChange} examples={[{ id: '1', url: '/api/v1/resources/1/cover' }, { id: '2', url: null }]} groups={['Lighting']} />);
    fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'New' } });
    expect(onChange).toHaveBeenLastCalledWith({ ...value, title: 'New' });
    fireEvent.click(screen.getByRole('button', { name: 'Lighting' }));
    expect(onChange).toHaveBeenLastCalledWith({ ...value, group: 'Lighting' });
  });
  it('ticks and unticks examples', () => {
    const onChange = vi.fn();
    render(<TemplateForm value={value} onChange={onChange} examples={[{ id: '1', url: null }, { id: '2', url: null }]} groups={[]} />);
    fireEvent.click(screen.getByTestId('template-example-2'));
    expect(onChange).toHaveBeenLastCalledWith({ ...value, exampleIds: ['1', '2'] });
    fireEvent.click(screen.getByTestId('template-example-1'));
    expect(onChange).toHaveBeenLastCalledWith({ ...value, exampleIds: [] });
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `cd frontend && npx vitest run components/prompts`
Expected: cannot resolve the modules.

- [ ] **Step 3: Write the three components**

```tsx
// frontend/components/prompts/PromptThumbs.tsx
import React from 'react';
import { ImageOff } from 'lucide-react';
import { thumbSrc, type PromptThumb } from '../../services/promptsService';

export function PromptThumbs({
  thumbs, count, size = 64, testId,
}: { thumbs: PromptThumb[]; count?: number; size?: number; testId?: string }): React.ReactElement {
  const shown = thumbs.slice(0, 3);
  if (shown.length === 0) {
    return (
      <div
        data-testid={testId}
        data-empty="true"
        style={{ width: size, height: size }}
        className="flex shrink-0 items-center justify-center rounded-md border border-dashed border-line text-content-3"
      >
        <ImageOff size={Math.max(12, size / 4)} />
      </div>
    );
  }
  if (shown.length === 1) {
    return (
      <img data-testid={testId} src={thumbSrc(shown[0].url)} alt="" style={{ width: size, height: size }} className="shrink-0 rounded-md object-cover" />
    );
  }
  return (
    <div data-testid={testId} className="relative shrink-0" style={{ width: size, height: size }}>
      {shown.map((t, i) => (
        <img
          key={t.url}
          src={thumbSrc(t.url)}
          alt=""
          className="absolute inset-0 rounded-md object-cover"
          style={{ width: size, height: size, transform: `translate(${i * 3}px, ${-i * 3}px)`, zIndex: shown.length - i, opacity: 1 - i * 0.15 }}
        />
      ))}
      {count !== undefined && (
        <span className="absolute -bottom-1 -right-1 z-10 rounded-full bg-content px-1.5 text-[9px] font-semibold leading-4 text-card">{count}</span>
      )}
    </div>
  );
}
```

```tsx
// frontend/components/prompts/PromptTags.tsx
import React from 'react';
import { useTranslation } from 'react-i18next';
import type { PromptForm, PromptOrigin } from '../../services/promptsService';

const FORM_CLASS: Record<PromptForm, string> = {
  template: 'bg-ok-soft text-ok',
  image: 'bg-info-soft text-info',
  album: 'bg-warn-soft text-warn',
};

export function FormTag({ form }: { form: PromptForm }): React.ReactElement {
  const { t } = useTranslation();
  const label =
    form === 'template' ? t('prompts.form.template', 'Template')
    : form === 'image' ? t('prompts.form.image', 'Image')
    : t('prompts.form.album', 'Album');
  return <span data-testid="prompt-form-tag" className={`rounded-full px-1.5 text-[9.5px] leading-4 ${FORM_CLASS[form]}`}>{label}</span>;
}

/** Origin, plus the tool/model short name for extracted rows (they made the picture). */
export function OriginTag({ origin, params }: { origin: PromptOrigin | null; params?: Record<string, unknown> | null }): React.ReactElement | null {
  const { t } = useTranslation();
  if (!origin) return null;
  const tool = origin === 'extracted' ? [params?.tool, params?.model].find((v) => typeof v === 'string' && v) : null;
  const label =
    origin === 'typed' ? t('prompts.origin.typed', 'Typed')
    : origin === 'extracted' ? t('prompts.origin.extracted', 'Extracted')
    : t('prompts.origin.captioned', 'Captioned');
  return (
    <span data-testid="prompt-origin-tag" className={`rounded-full bg-island-2 px-1.5 text-[9.5px] leading-4 text-content-3 ${origin === 'captioned' ? 'italic' : ''}`}>
      {tool ? `${label} · ${String(tool)}` : label}
    </span>
  );
}
```

```tsx
// frontend/components/prompts/TemplateForm.tsx
//
// The ONE form behind "Save as template", "Save current" and "New" on both
// surfaces. Controlled: the caller owns the value and the submit.
import React from 'react';
import { useTranslation } from 'react-i18next';
import { thumbSrc } from '../../services/promptsService';

export interface TemplateFormValue {
  title: string;
  group: string;
  positive: string;
  negative: string;
  exampleIds: string[];
}

const LABEL = 'block text-[9.5px] font-semibold uppercase tracking-[0.08em] text-content-3 mb-1';
const INPUT = 'w-full rounded-lg border border-line bg-card px-2 py-1.5 text-[12px] text-content outline-none focus:border-accent';

export function TemplateForm({
  value, onChange, examples, groups, disabled,
}: {
  value: TemplateFormValue;
  onChange: (v: TemplateFormValue) => void;
  examples: Array<{ id: string; url: string | null }>;
  groups: string[];
  disabled?: boolean;
}): React.ReactElement {
  const { t } = useTranslation();
  const set = (patch: Partial<TemplateFormValue>) => onChange({ ...value, ...patch });
  const toggle = (id: string) =>
    set({ exampleIds: value.exampleIds.includes(id) ? value.exampleIds.filter((x) => x !== id) : [...value.exampleIds, id] });
  return (
    <div className="flex flex-col gap-3" data-testid="template-form">
      <label className="block">
        <span className={LABEL}>{t('prompts.templateForm.title', 'Title')}</span>
        <input aria-label={t('prompts.templateForm.title', 'Title')} className={INPUT} value={value.title} disabled={disabled} onChange={(e) => set({ title: e.target.value })} />
      </label>
      <div>
        <span className={LABEL}>{t('prompts.templateForm.group', 'Group')}</span>
        <div className="flex flex-wrap items-center gap-1">
          {groups.map((g) => (
            <button key={g} type="button" disabled={disabled} onClick={() => set({ group: value.group === g ? '' : g })}
              className={`rounded-full border px-2 py-0.5 text-[11px] ${value.group === g ? 'border-accent bg-accent-soft text-accent' : 'border-line text-content-3'}`}>
              {g}
            </button>
          ))}
          <input aria-label={t('prompts.templateForm.groupNew', 'Or type a new group')} placeholder={t('prompts.templateForm.groupNew', 'Or type a new group')}
            className={`${INPUT} w-40`} value={groups.includes(value.group) ? '' : value.group} disabled={disabled} onChange={(e) => set({ group: e.target.value })} />
        </div>
      </div>
      {examples.length > 0 && (
        <div>
          <span className={LABEL}>{t('prompts.templateForm.examples', { count: value.exampleIds.length, total: examples.length, defaultValue: 'Examples · {{count}} of {{total}} ticked' })}</span>
          <div className="flex flex-wrap gap-1.5">
            {examples.map((ex) => {
              const on = value.exampleIds.includes(ex.id);
              return (
                <button key={ex.id} type="button" data-testid={`template-example-${ex.id}`} aria-pressed={on} disabled={disabled} onClick={() => toggle(ex.id)}
                  className={`h-14 w-14 overflow-hidden rounded-lg border ${on ? 'border-accent ring-2 ring-accent' : 'border-line opacity-50'}`}>
                  {ex.url ? <img src={thumbSrc(ex.url)} alt="" className="h-full w-full object-cover" /> : <span className="block h-full w-full bg-island-2" />}
                </button>
              );
            })}
          </div>
        </div>
      )}
      <label className="block">
        <span className={LABEL}>{t('prompts.templateForm.positive', 'Positive')}</span>
        <textarea aria-label={t('prompts.templateForm.positive', 'Positive')} className={`${INPUT} min-h-[72px] font-mono text-[11.5px]`} value={value.positive} disabled={disabled} onChange={(e) => set({ positive: e.target.value })} />
      </label>
      <label className="block">
        <span className={LABEL}>{t('prompts.templateForm.negative', 'Negative')}</span>
        <textarea aria-label={t('prompts.templateForm.negative', 'Negative')} className={`${INPUT} min-h-[40px] font-mono text-[11.5px]`} value={value.negative} disabled={disabled} onChange={(e) => set({ negative: e.target.value })} />
      </label>
    </div>
  );
}
```

- [ ] **Step 4: Add the `prompts` locale namespace**

Add a top-level `"prompts"` object to BOTH `frontend/public/locales/en.json` and `zh.json` (after `"assets"`). English values are the defaults above; Chinese:

```json
"prompts": {
  "form": { "template": "Template", "image": "Image", "album": "Album" },
  "origin": { "typed": "Typed", "extracted": "Extracted", "captioned": "Captioned" },
  "templateForm": {
    "title": "Title", "group": "Group", "groupNew": "Or type a new group",
    "examples": "Examples · {{count}} of {{total}} ticked", "positive": "Positive", "negative": "Negative"
  }
}
```
zh: `form: 模板 / 单图 / 图集`, `origin: 手写 / 提取 / AI 打标`, `templateForm: 标题 / 分组 / 或输入新分组 / 示例图 · 已选 {{count}} / {{total}} / 正向 / 负向`. Later tasks append more keys to this object; keep en/zh in lockstep — Task B4 adds a parity test over the whole namespace.

- [ ] **Step 5: Run tests**

Run: `cd frontend && npx vitest run components/prompts`
Expected: `5 passed`.

- [ ] **Step 6: Commit**

```bash
git add frontend/components/prompts frontend/public/locales/en.json frontend/public/locales/zh.json
git commit -m "feat(prompts): shared thumbs / form+origin tags / template form"
```

### Task 10 (B3): `PromptsShelf` — the resource library's Prompts page

**Files:**
- Create: `frontend/components/resources/prompts/promptFilters.ts`, `frontend/components/resources/prompts/PromptCard.tsx`, `frontend/components/resources/prompts/PromptAlbumCard.tsx`, `frontend/components/resources/prompts/PromptsShelf.tsx`
- Modify: `frontend/components/resources/assets/AssetsView.tsx` (branch), locales (`prompts.shelf.*`)
- Test: `frontend/components/resources/prompts/promptFilters.test.ts`, `frontend/components/resources/prompts/PromptsShelf.test.tsx`

**Interfaces:**
- Consumes: `fetchPrompts`, `promptText`, `paramChips`, `langAvailability` (B1); `PromptThumbs`, `FormTag`, `OriginTag` (B2); `useResourcesContext()` → `{ scopeId, resPath, refreshAssetCounts }`; `SendToCanvasModal` (`../SendToCanvasModal`, props `{ resource: Resource; positive: string; negative: string | null; onClose }`); `SaveAsTemplateDialog` (B4 — this task renders a stub button that B4 wires; keep the `onSaveAsTemplate(entry, slideNames?)` callback prop on the cards).
- Produces:
  ```ts
  // promptFilters.ts
  export interface PromptFilters { form: PromptForm | null; origin: PromptOrigin | null; projectId: string | null; sort: 'recent' | 'title'; q: string }
  export function parsePromptFilters(sp: URLSearchParams): PromptFilters
  export function serializePromptFilters(f: PromptFilters): URLSearchParams
  export function sortEntries(items: PromptEntry[], sort: PromptFilters['sort']): PromptEntry[]   // 'recent' keeps server order; 'title' localeCompare
  // PromptCard / PromptAlbumCard
  export interface PromptCardProps { entry: PromptEntry; lang: PromptLang; onSend: (entry: PromptEntry, slide?: PromptSlide) => void; onSaveAsTemplate: (entry: PromptEntry, slideNames?: string[]) => void; onOpen: (entry: PromptEntry) => void }
  // PromptsShelf
  export const PromptsShelf: React.FC
  ```

- [ ] **Step 1: Write the failing filter tests**

```ts
// frontend/components/resources/prompts/promptFilters.test.ts
import { describe, expect, it } from 'vitest';
import { parsePromptFilters, serializePromptFilters, sortEntries } from './promptFilters';

describe('promptFilters', () => {
  it('parses known values and drops unknown ones', () => {
    const f = parsePromptFilters(new URLSearchParams('form=album&origin=weird&project=55&sort=title&q=rain'));
    expect(f).toEqual({ form: 'album', origin: null, projectId: '55', sort: 'title', q: 'rain' });
  });
  it('round-trips and omits defaults', () => {
    const sp = serializePromptFilters({ form: null, origin: 'typed', projectId: null, sort: 'recent', q: '' });
    expect(sp.toString()).toBe('origin=typed');
  });
  it('sorts by title when asked and keeps server order otherwise', () => {
    const a = { title: 'b' } as never, b = { title: 'A' } as never;
    expect(sortEntries([a, b], 'title')).toEqual([b, a]);
    expect(sortEntries([a, b], 'recent')).toEqual([a, b]);
  });
});
```

- [ ] **Step 2: Write the failing shelf test**

```tsx
// frontend/components/resources/prompts/PromptsShelf.test.tsx
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (k: string, d?: unknown) => (typeof d === 'string' ? d : (d as { defaultValue?: string })?.defaultValue ?? k) }) }));
vi.mock('../../../utils/apiConfig', () => ({ getApiUrl: () => 'https://api.test' }));
vi.mock('../../../contexts/ResourcesContext', () => ({ useResourcesContext: () => ({ scopeId: '9000', resPath: (p: string) => p, refreshAssetCounts: vi.fn() }) }));
vi.mock('../SendToCanvasModal', () => ({ SendToCanvasModal: (p: { positive: string }) => <div data-testid="send-modal">{p.positive}</div> }));
vi.mock('../../prompts/SaveAsTemplateDialog', () => ({ SaveAsTemplateDialog: () => <div data-testid="save-dialog" /> }));
const fetchPrompts = vi.fn();
vi.mock('../../../services/promptsService', async (orig) => ({ ...(await orig<typeof import('../../../services/promptsService')>()), fetchPrompts: (...a: unknown[]) => fetchPrompts(...a) }));

import { PromptsShelf } from './PromptsShelf';

const image = { key: 'image:10', form: 'image', origin: 'extracted', title: 'Bicycle', tags: [], positive_en: 'cheerful woman, oranges', positive_zh: null, negative_en: 'flare', negative_zh: null, params: { width: 1920, height: 1080, steps: 28 }, thumbs: [{ url: '/api/v1/resources/10/cover', kind: 'image' }], slides: null, source: { store: 'uploads', id: '10' }, updated_at: '2026-09-03T00:00:00Z' };
const captioned = { ...image, key: 'image:11', origin: 'captioned', title: 'Courtyard', params: null, positive_en: 'A young woman', source: { store: 'uploads', id: '11' } };
const album = { ...image, key: 'album:7', form: 'album', title: 'Orange harvest', params: null, source: { store: 'uploads', id: '7' }, thumbs: [], slides: [
  { name: '002.jpg', url: '/api/v1/media/9/slides/002.jpg', positive_en: 'winking', positive_zh: null, negative_en: null, negative_zh: null },
  { name: '005.jpg', url: '/api/v1/media/9/slides/005.jpg', positive_en: null, positive_zh: null, negative_en: null, negative_zh: null },
] };
const page = { items: [image, captioned, album], total: 3, by_form: { template: 0, image: 2, album: 1 }, by_origin: { typed: 0, extracted: 1, captioned: 2 } };

function mount() {
  return render(<MemoryRouter initialEntries={['/resources/assets/prompt']}><PromptsShelf /></MemoryRouter>);
}

describe('PromptsShelf', () => {
  beforeEach(() => { fetchPrompts.mockReset(); fetchPrompts.mockResolvedValue(page); });

  it('renders text-first cards with form and origin counts', async () => {
    mount();
    await screen.findByText('cheerful woman, oranges');
    expect(screen.getByRole('button', { name: 'Images 2' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Captioned 2' })).toBeInTheDocument();
    expect(screen.getAllByTestId('prompt-card')).toHaveLength(3);
  });

  it('captioned card has no params row and is muted', async () => {
    mount();
    const card = (await screen.findByText('A young woman')).closest('[data-testid="prompt-card"]')!;
    expect(card).toHaveAttribute('data-origin', 'captioned');
    expect(card.querySelector('[data-testid="prompt-params"]')).toBeNull();
    expect(card.textContent).toContain('this text describes the picture');
  });

  it('album card expands to slides and disables Send on textless ones', async () => {
    mount();
    fireEvent.click(await screen.findByRole('button', { name: 'Expand' }));
    const rows = screen.getAllByTestId('prompt-slide-row');
    expect(rows).toHaveLength(2);
    expect(rows[1].querySelector('button')).toBeDisabled();
    fireEvent.click(rows[0].querySelector('button')!);
    expect(screen.getByTestId('send-modal')).toHaveTextContent('winking');
  });

  it('form chip refetches with the filter and writes the URL', async () => {
    mount();
    fireEvent.click(await screen.findByRole('button', { name: 'Albums 1' }));
    await waitFor(() => expect(fetchPrompts).toHaveBeenLastCalledWith('9000', expect.objectContaining({ form: 'album' })));
  });

  it('a failed load is an error row with Retry, not an empty page', async () => {
    fetchPrompts.mockRejectedValueOnce(new Error('boom'));
    mount();
    expect(await screen.findByRole('button', { name: 'Retry' })).toBeInTheDocument();
    expect(screen.queryByText('No prompts yet')).toBeNull();
  });

  it('true empty state names the three ways a prompt arrives', async () => {
    fetchPrompts.mockResolvedValueOnce({ items: [], total: 0, by_form: { template: 0, image: 0, album: 0 }, by_origin: { typed: 0, extracted: 0, captioned: 0 } });
    mount();
    expect(await screen.findByText(/No prompts yet/)).toBeInTheDocument();
  });
});
```

- [ ] **Step 3: Run to verify failure**

Run: `cd frontend && npx vitest run components/resources/prompts`
Expected: modules not found.

- [ ] **Step 4: Write `promptFilters.ts`**

```ts
// frontend/components/resources/prompts/promptFilters.ts
import { PROMPT_FORMS, PROMPT_ORIGINS, type PromptEntry, type PromptForm, type PromptOrigin } from '../../../services/promptsService';

export type PromptSort = 'recent' | 'title';
export interface PromptFilters {
  form: PromptForm | null;
  origin: PromptOrigin | null;
  projectId: string | null;
  sort: PromptSort;
  q: string;
}

export function parsePromptFilters(sp: URLSearchParams): PromptFilters {
  const form = sp.get('form');
  const origin = sp.get('origin');
  return {
    form: PROMPT_FORMS.includes(form as PromptForm) ? (form as PromptForm) : null,
    origin: PROMPT_ORIGINS.includes(origin as PromptOrigin) ? (origin as PromptOrigin) : null,
    projectId: sp.get('project') || null,
    sort: sp.get('sort') === 'title' ? 'title' : 'recent',
    q: sp.get('q') ?? '',
  };
}

export function serializePromptFilters(f: PromptFilters): URLSearchParams {
  const sp = new URLSearchParams();
  if (f.form) sp.set('form', f.form);
  if (f.origin) sp.set('origin', f.origin);
  if (f.projectId) sp.set('project', f.projectId);
  if (f.sort !== 'recent') sp.set('sort', f.sort);
  if (f.q.trim()) sp.set('q', f.q.trim());
  return sp;
}

export function sortEntries(items: PromptEntry[], sort: PromptSort): PromptEntry[] {
  if (sort !== 'title') return items;
  return [...items].sort((a, b) => a.title.localeCompare(b.title, undefined, { sensitivity: 'base' }));
}
```

- [ ] **Step 5: Write `PromptCard.tsx`**

```tsx
// frontend/components/resources/prompts/PromptCard.tsx
//
// Text first, picture beside (spec §3.3). The positive is the object; the
// thumbnail is evidence of what it produced.
import React from 'react';
import { useTranslation } from 'react-i18next';
import { FormTag, OriginTag } from '../../prompts/PromptTags';
import { PromptThumbs } from '../../prompts/PromptThumbs';
import { langAvailability, paramChips, promptText, type PromptEntry, type PromptLang, type PromptSlide } from '../../../services/promptsService';

export interface PromptCardProps {
  entry: PromptEntry;
  lang: PromptLang;
  onSend: (entry: PromptEntry, slide?: PromptSlide) => void;
  onSaveAsTemplate: (entry: PromptEntry, slideNames?: string[]) => void;
  onOpen: (entry: PromptEntry) => void;
}

export function LangNote({ entry }: { entry: { positive_en: string | null; positive_zh: string | null } }): React.ReactElement | null {
  const { t } = useTranslation();
  const a = langAvailability(entry);
  if (a === 'none') return null;
  const text = a === 'both' ? 'EN · 中' : a === 'en' ? t('prompts.shelf.enOnly', 'EN only') : t('prompts.shelf.zhOnly', '中 only');
  return <span className="text-[10.5px] text-content-3">{text}</span>;
}

export function PromptCard({ entry, lang, onSend, onSaveAsTemplate, onOpen }: PromptCardProps): React.ReactElement {
  const { t } = useTranslation();
  const text = promptText(entry, lang);
  const chips = entry.origin === 'captioned' ? [] : paramChips(entry.params);
  const muted = entry.origin === 'captioned';
  return (
    <div data-testid="prompt-card" data-origin={entry.origin ?? ''} data-form={entry.form}
      className="grid grid-cols-[1fr_64px] gap-x-3 gap-y-1.5 rounded-xl border border-line bg-card p-3">
      <div className="col-span-2 flex items-center gap-1.5">
        <span className="truncate text-[12.5px] font-semibold text-content">{entry.title}</span>
        <span className="flex-1" />
        <FormTag form={entry.form} />
        <OriginTag origin={entry.origin} params={entry.params} />
      </div>
      <pre className={`m-0 line-clamp-3 whitespace-pre-wrap font-mono text-[11.5px] leading-relaxed ${muted ? 'text-content-3' : 'text-content'}`}>{text.positive}</pre>
      <PromptThumbs thumbs={entry.thumbs} size={64} />
      {text.negative && <pre className="col-span-2 m-0 line-clamp-1 whitespace-pre-wrap font-mono text-[10.5px] text-content-3">− {text.negative}</pre>}
      <div className="col-span-2 flex flex-wrap items-center gap-1.5">
        {chips.length > 0 && (
          <div data-testid="prompt-params" className="flex flex-wrap gap-1">
            {chips.map((c) => <span key={c} className="rounded-md border border-line px-1.5 font-mono text-[10.5px] text-content">{c}</span>)}
          </div>
        )}
        {muted && <span className="text-[10.5px] text-content-3">{t('prompts.shelf.captionedNote', 'No parameters — this text describes the picture, it did not make it')}</span>}
        <span className="flex-1" />
        <LangNote entry={entry} />
      </div>
      <div className="col-span-2 flex items-center gap-1.5 border-t border-dashed border-line pt-2">
        <button type="button" className="rounded-md border border-line px-2 py-0.5 text-[10.5px]" onClick={() => onSend(entry)} disabled={!text.positive}>{t('prompts.shelf.sendToCanvas', 'Send to canvas')}</button>
        {entry.form !== 'template' && (
          <button type="button" className="rounded-md border border-line px-2 py-0.5 text-[10.5px]" onClick={() => onSaveAsTemplate(entry)}>{t('prompts.shelf.saveAsTemplate', 'Save as template')}</button>
        )}
        <button type="button" className="rounded-md px-2 py-0.5 text-[10.5px] text-content-3" onClick={() => onOpen(entry)}>{t('prompts.shelf.open', 'Open')}</button>
        <span className="flex-1" />
        <span className="whitespace-nowrap text-[10.5px] text-content-3">{entry.updated_at.slice(0, 10)}</span>
      </div>
    </div>
  );
}
```

- [ ] **Step 6: Write `PromptAlbumCard.tsx`**

```tsx
// frontend/components/resources/prompts/PromptAlbumCard.tsx
//
// One card that opens (spec §3.3): collapsed = stacked thumbs + count; open =
// one row per slide with its own Send. Textless slides stay visible and
// disabled so "5 of 6 have text" is a number the eye can check.
import React, { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { FormTag, OriginTag } from '../../prompts/PromptTags';
import { PromptThumbs } from '../../prompts/PromptThumbs';
import { promptText, thumbSrc, type PromptEntry, type PromptLang, type PromptSlide } from '../../../services/promptsService';
import type { PromptCardProps } from './PromptCard';

export function PromptAlbumCard({ entry, lang, onSend, onSaveAsTemplate, onOpen }: PromptCardProps): React.ReactElement {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const slides = entry.slides ?? [];
  const withText = useMemo(() => slides.filter((s) => promptText(s, lang).positive), [slides, lang]);
  return (
    <div data-testid="prompt-card" data-origin={entry.origin ?? ''} data-form="album" className="col-span-full rounded-xl border border-line bg-card p-3">
      <div className="flex items-center gap-1.5">
        {!open && <PromptThumbs thumbs={entry.thumbs} count={slides.length} size={40} />}
        <span className="truncate text-[12.5px] font-semibold text-content">{entry.title}</span>
        <span className="text-[10.5px] text-content-3">· {t('prompts.shelf.slidesWithText', { count: withText.length, total: slides.length, defaultValue: '{{count}} of {{total}} slides have text' })}</span>
        <span className="flex-1" />
        <FormTag form="album" />
        <OriginTag origin={entry.origin} params={entry.params} />
        <button type="button" className="rounded-md border border-line px-2 py-0.5 text-[10.5px]" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
          {open ? t('prompts.shelf.collapse', 'Collapse') : t('prompts.shelf.expand', 'Expand')}
        </button>
      </div>
      {open && (
        <div className="mt-2 grid grid-cols-[repeat(auto-fill,minmax(280px,1fr))] gap-1.5">
          {slides.map((s) => {
            const text = promptText(s, lang);
            const has = !!text.positive;
            return (
              <div key={s.name} data-testid="prompt-slide-row" className={`grid grid-cols-[40px_1fr_auto] items-start gap-2 rounded-lg bg-island-2 p-1.5 ${has ? '' : 'opacity-60'}`}>
                {s.url ? <img src={thumbSrc(s.url)} alt="" className="h-10 w-10 rounded-md object-cover" /> : <span className="block h-10 w-10 rounded-md bg-card" />}
                <div className="min-w-0">
                  <pre className={`m-0 line-clamp-2 whitespace-pre-wrap font-mono text-[10.5px] ${has ? 'text-content' : 'text-content-3'}`}>{has ? text.positive : t('prompts.shelf.noPromptOnSlide', 'No prompt on this slide')}</pre>
                  <span className="text-[9.5px] text-content-3">{s.name}{s.positive_en && s.positive_zh ? ' · EN · 中' : s.positive_zh ? ' · 中' : s.positive_en ? ' · EN' : ''}</span>
                </div>
                <button type="button" className="rounded-md border border-line px-2 py-0.5 text-[10.5px]" disabled={!has} onClick={() => onSend(entry, s)}>{t('prompts.shelf.send', 'Send')}</button>
              </div>
            );
          })}
        </div>
      )}
      <div className="mt-2 flex items-center gap-1.5 border-t border-dashed border-line pt-2">
        <button type="button" className="rounded-md border border-line px-2 py-0.5 text-[10.5px]" disabled={withText.length === 0} onClick={() => withText.forEach((s) => onSend(entry, s))}>{t('prompts.shelf.sendAllWithText', 'Send all with text to canvas')}</button>
        <button type="button" className="rounded-md border border-line px-2 py-0.5 text-[10.5px]" onClick={() => onSaveAsTemplate(entry, withText.map((s) => s.name))}>{t('prompts.shelf.saveAsTemplateEllipsis', 'Save as template…')}</button>
        <button type="button" className="rounded-md px-2 py-0.5 text-[10.5px] text-content-3" onClick={() => onOpen(entry)}>{t('prompts.shelf.openAlbum', 'Open album')}</button>
        <span className="flex-1" />
        <span className="whitespace-nowrap text-[10.5px] text-content-3">{entry.updated_at.slice(0, 10)}</span>
      </div>
    </div>
  );
}

export type { PromptSlide };
```

- [ ] **Step 7: Write `PromptsShelf.tsx`**

```tsx
// frontend/components/resources/prompts/PromptsShelf.tsx
//
// The asset library's Prompts tab (spec §3.3), fed by the unified catalog.
// Replaces the always-empty `AssetShelf` render for `assetType === 'prompt'`.
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Loader2 } from 'lucide-react';

import { useResourcesContext } from '../../../contexts/ResourcesContext';
import { fetchPrompts, promptText, type PromptEntry, type PromptForm, type PromptLang, type PromptOrigin, type PromptPage, type PromptSlide } from '../../../services/promptsService';
import type { Resource } from '../../../types';
import { SendToCanvasModal } from '../SendToCanvasModal';
import { SaveAsTemplateDialog } from '../../prompts/SaveAsTemplateDialog';
import { PromptAlbumCard } from './PromptAlbumCard';
import { PromptCard } from './PromptCard';
import { parsePromptFilters, serializePromptFilters, sortEntries, type PromptFilters } from './promptFilters';

const FORM_LABEL: Record<PromptForm, [string, string]> = {
  template: ['prompts.shelf.formTemplates', 'Templates'],
  image: ['prompts.shelf.formImages', 'Images'],
  album: ['prompts.shelf.formAlbums', 'Albums'],
};
const ORIGIN_LABEL: Record<PromptOrigin, [string, string]> = {
  typed: ['prompts.origin.typed', 'Typed'],
  extracted: ['prompts.origin.extracted', 'Extracted'],
  captioned: ['prompts.origin.captioned', 'Captioned'],
};

function Seg({ on, label, count, onClick }: { on: boolean; label: string; count?: number; onClick: () => void }) {
  const text = count === undefined ? label : `${label} ${count}`;
  return (
    <button type="button" aria-pressed={on} onClick={onClick}
      className={`border-r border-line px-2.5 py-0.5 text-[11px] last:border-r-0 ${on ? 'bg-accent-soft text-accent' : 'text-content-3'}`}>
      {text}
    </button>
  );
}

export const PromptsShelf: React.FC = () => {
  const { t } = useTranslation();
  const { scopeId, resPath, refreshAssetCounts } = useResourcesContext();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const filters = useMemo<PromptFilters>(() => parsePromptFilters(searchParams), [searchParams]);
  const setFilters = useCallback((patch: Partial<PromptFilters>) => setSearchParams(serializePromptFilters({ ...filters, ...patch }), { replace: true }), [filters, setSearchParams]);

  const [page, setPage] = useState<PromptPage | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [tick, setTick] = useState(0);
  const [lang] = useState<PromptLang>('en');
  const [send, setSend] = useState<{ entry: PromptEntry; slide?: PromptSlide } | null>(null);
  const [save, setSave] = useState<{ entry: PromptEntry; slideNames?: string[] } | null>(null);

  useEffect(() => {
    if (!scopeId) return;
    let cancelled = false;
    setLoading(true);
    setLoadError(false);
    fetchPrompts(scopeId, {
      segment: filters.projectId ? 'project' : 'mine',
      projectId: filters.projectId,
      form: filters.form,
      origin: filters.origin,
      q: filters.q,
      limit: 200,
    })
      .then((p) => { if (!cancelled) setPage(p); })
      .catch((err) => { console.error('[PromptsShelf] load failed:', err); if (!cancelled) setLoadError(true); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [scopeId, filters.projectId, filters.form, filters.origin, filters.q, tick]);

  const items = useMemo(() => sortEntries(page?.items ?? [], filters.sort), [page, filters.sort]);
  const filtered = !!(filters.form || filters.origin || filters.q.trim());

  const onOpen = (entry: PromptEntry) =>
    navigate(resPath(entry.source.store === 'assets' ? `/resources/assets/item/${entry.source.id}` : `/resources/file/${entry.source.id}`));

  const sendText = send ? promptText(send.slide ?? send.entry, lang) : null;

  return (
    <div className="flex h-full flex-col gap-2.5 p-4" data-testid="prompts-shelf">
      <div className="flex flex-wrap items-center gap-1.5">
        <div className="flex overflow-hidden rounded-lg border border-line">
          <Seg on={filters.form === null} label={t('prompts.shelf.all', 'All')} count={page?.total} onClick={() => setFilters({ form: null })} />
          {(Object.keys(FORM_LABEL) as PromptForm[]).map((f) => (
            <Seg key={f} on={filters.form === f} label={t(FORM_LABEL[f][0], FORM_LABEL[f][1])} count={page?.by_form[f]} onClick={() => setFilters({ form: filters.form === f ? null : f })} />
          ))}
        </div>
        <div className="flex overflow-hidden rounded-lg border border-line">
          <Seg on={filters.origin === null} label={t('prompts.shelf.anyOrigin', 'Any origin')} onClick={() => setFilters({ origin: null })} />
          {(Object.keys(ORIGIN_LABEL) as PromptOrigin[]).map((o) => (
            <Seg key={o} on={filters.origin === o} label={t(ORIGIN_LABEL[o][0], ORIGIN_LABEL[o][1])} count={page?.by_origin[o]} onClick={() => setFilters({ origin: filters.origin === o ? null : o })} />
          ))}
        </div>
        <input aria-label={t('prompts.shelf.search', 'Search prompts')} placeholder={t('prompts.shelf.search', 'Search prompts')} value={filters.q}
          onChange={(e) => setFilters({ q: e.target.value })} className="rounded-lg border border-line bg-card px-2 py-0.5 text-[11px] text-content outline-none" />
        <select aria-label={t('prompts.shelf.sort', 'Sort')} value={filters.sort} onChange={(e) => setFilters({ sort: e.target.value as PromptFilters['sort'] })} className="rounded-lg border border-line bg-card px-2 py-0.5 text-[11px] text-content">
          <option value="recent">{t('prompts.shelf.sortRecent', 'Recently updated')}</option>
          <option value="title">{t('prompts.shelf.sortTitle', 'Title')}</option>
        </select>
        <span className="flex-1" />
        <button type="button" className="rounded-lg border border-line px-2.5 py-0.5 text-[11px]" onClick={() => setSave({ entry: emptyEntry() })}>{t('prompts.shelf.newTemplate', 'New template')}</button>
      </div>

      {loading && !page ? (
        <div className="flex items-center gap-2 py-10 text-sm text-content-3"><Loader2 size={14} className="animate-spin" />{t('common.loading', 'Loading...')}</div>
      ) : loadError ? (
        <div className="flex items-center gap-2 py-10 text-sm text-danger">
          {t('prompts.shelf.loadFailed', 'Could not load prompts')}
          <button type="button" className="rounded-md border border-line px-2 py-0.5 text-[11px] text-content" onClick={() => setTick((v) => v + 1)}>{t('common.retry', 'Retry')}</button>
        </div>
      ) : items.length === 0 ? (
        <p className="py-10 text-sm text-content-3">
          {filtered
            ? t('prompts.shelf.emptyFiltered', 'No prompts match these filters')
            : t('prompts.shelf.emptyNone', 'No prompts yet — upload a picture that carries generation metadata, run a caption, or save one from a canvas.')}
        </p>
      ) : (
        <div className="grid grid-cols-[repeat(auto-fill,minmax(300px,1fr))] gap-2.5">
          {items.map((entry) =>
            entry.form === 'album'
              ? <PromptAlbumCard key={entry.key} entry={entry} lang={lang} onSend={(e, s) => setSend({ entry: e, slide: s })} onSaveAsTemplate={(e, names) => setSave({ entry: e, slideNames: names })} onOpen={onOpen} />
              : <PromptCard key={entry.key} entry={entry} lang={lang} onSend={(e) => setSend({ entry: e })} onSaveAsTemplate={(e) => setSave({ entry: e })} onOpen={onOpen} />,
          )}
        </div>
      )}

      {send && sendText && (
        <SendToCanvasModal
          resource={{ id: send.entry.source.id, filename: send.entry.title } as unknown as Resource}
          positive={sendText.positive}
          negative={sendText.negative}
          onClose={() => setSend(null)}
        />
      )}
      {save && (
        <SaveAsTemplateDialog
          scopeId={scopeId}
          entry={save.entry}
          slideNames={save.slideNames}
          lang={lang}
          onClose={() => setSave(null)}
          onSaved={() => { setSave(null); refreshAssetCounts(); setTick((v) => v + 1); }}
        />
      )}
    </div>
  );
};

/** "New template" starts from nothing; the dialog treats a template-form entry with no text as blank. */
function emptyEntry(): PromptEntry {
  return { key: 'template:new', form: 'template', origin: 'typed', title: '', tags: [], positive_en: null, positive_zh: null, negative_en: null, negative_zh: null, params: null, thumbs: [], slides: null, source: { store: 'assets', id: '' }, updated_at: '' };
}
```

- [ ] **Step 8: Branch `AssetsView`**

In `frontend/components/resources/assets/AssetsView.tsx` add `import { PromptsShelf } from '../prompts/PromptsShelf';` and change the last return to:

```tsx
  if (selectedAssetType === 'prompt') {
    return <PromptsShelf />;
  }
  return <AssetShelf assetType={selectedAssetType} />;
```

- [ ] **Step 9: Add locale keys**

Extend the `prompts` object in en.json / zh.json with `shelf`:

```json
"shelf": {
  "all": "All", "formTemplates": "Templates", "formImages": "Images", "formAlbums": "Albums", "anyOrigin": "Any origin",
  "search": "Search prompts", "sort": "Sort", "sortRecent": "Recently updated", "sortTitle": "Title", "newTemplate": "New template",
  "loadFailed": "Could not load prompts", "emptyFiltered": "No prompts match these filters",
  "emptyNone": "No prompts yet — upload a picture that carries generation metadata, run a caption, or save one from a canvas.",
  "enOnly": "EN only", "zhOnly": "中 only", "captionedNote": "No parameters — this text describes the picture, it did not make it",
  "sendToCanvas": "Send to canvas", "saveAsTemplate": "Save as template", "saveAsTemplateEllipsis": "Save as template…", "open": "Open",
  "slidesWithText": "{{count}} of {{total}} slides have text", "expand": "Expand", "collapse": "Collapse", "noPromptOnSlide": "No prompt on this slide",
  "send": "Send", "sendAllWithText": "Send all with text to canvas", "openAlbum": "Open album"
}
```
zh: 全部 / 模板 / 单图 / 图集 / 任意来源 / 搜索提示词 / 排序 / 最近更新 / 标题 / 新建模板 / 提示词加载失败 / 没有符合筛选的提示词 / 还没有提示词 — 上传带生成元数据的图片、跑一次打标，或从画布保存一条。 / 仅 EN / 仅中文 / 没有参数 — 这段文字描述了图片，不是生成它的提示词 / 送到画布 / 存为模板 / 存为模板… / 打开 / {{total}} 张中 {{count}} 张有文字 / 展开 / 收起 / 这张没有提示词 / 送出 / 把有文字的全部送到画布 / 打开图集. Confirm `common.loading` and `common.retry` exist in both files (`grep -n '"retry"' frontend/public/locales/en.json`); if `common.retry` is missing, add `"retry": "Retry"` / `"retry": "重试"` under `common`.

- [ ] **Step 10: Run tests + typecheck**

Run: `cd frontend && npx vitest run components/resources/prompts components/prompts && npx tsc --noEmit -p . 2>&1 | grep -E "resources/prompts|components/prompts|AssetsView" ; true`
Expected: all pass (the shelf test mocks `SaveAsTemplateDialog`, which B4 creates — until then add a one-line stub file `frontend/components/prompts/SaveAsTemplateDialog.tsx` exporting `export function SaveAsTemplateDialog(): null { return null; }` with the props type from B4, so `tsc` is green; B4 replaces it).

- [ ] **Step 11: Commit**

```bash
git add frontend/components/resources/prompts frontend/components/resources/assets/AssetsView.tsx frontend/components/prompts/SaveAsTemplateDialog.tsx frontend/public/locales/en.json frontend/public/locales/zh.json
git commit -m "feat(resources): Prompts shelf — unified prompts, text-first cards, album rows"
```

### Task 11 (B4): `SaveAsTemplateDialog`, sidebar/tab counts, locale parity guard

**Files:**
- Create (replace the B3 stub): `frontend/components/prompts/SaveAsTemplateDialog.tsx`
- Modify: `frontend/contexts/ResourcesContext.tsx:758-771` (override `assetCounts.prompt`)
- Test: `frontend/components/prompts/SaveAsTemplateDialog.test.tsx`, `frontend/components/prompts/promptsI18nParity.test.ts`

**Interfaces:**
- Consumes: `TemplateForm` (B2), `saveAsTemplate`, `promptText`, `fetchPromptCounts` (B1), `useOptionalToast` (`../Toast`, `toast?.addToast(message, 'success' | 'error' | 'info')`).
- Produces:
  ```tsx
  export interface SaveAsTemplateDialogProps { scopeId: string; entry: PromptEntry; slideNames?: string[]; lang: PromptLang; onClose: () => void; onSaved: (assetId: string) => void }
  export function SaveAsTemplateDialog(props: SaveAsTemplateDialogProps): JSX.Element
  export function initialTemplateValue(entry: PromptEntry, slideNames: string[] | undefined, lang: PromptLang): TemplateFormValue   // pure, exported for the panel's inline form
  ```

- [ ] **Step 1: Write the failing tests**

```tsx
// frontend/components/prompts/SaveAsTemplateDialog.test.tsx
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (k: string, d?: unknown) => (typeof d === 'string' ? d : (d as { defaultValue?: string })?.defaultValue ?? k) }) }));
vi.mock('../../utils/apiConfig', () => ({ getApiUrl: () => 'https://api.test' }));
const addToast = vi.fn();
vi.mock('../Toast', () => ({ useOptionalToast: () => ({ addToast }) }));
const saveAsTemplate = vi.fn(async () => ({ assetId: '900' }));
vi.mock('../../services/promptsService', async (orig) => ({ ...(await orig<typeof import('../../services/promptsService')>()), saveAsTemplate: (...a: unknown[]) => saveAsTemplate(...a) }));
import { SaveAsTemplateDialog, initialTemplateValue } from './SaveAsTemplateDialog';
import type { PromptEntry } from '../../services/promptsService';

const image: PromptEntry = { key: 'image:10', form: 'image', origin: 'extracted', title: 'Bicycle', tags: [], positive_en: 'p', positive_zh: null, negative_en: 'n', negative_zh: null, params: null, thumbs: [{ url: '/api/v1/resources/10/cover', kind: 'image' }], slides: null, source: { store: 'uploads', id: '10' }, updated_at: '' };
const album: PromptEntry = { ...image, key: 'album:7', form: 'album', title: 'Harvest', source: { store: 'uploads', id: '7' }, slides: [
  { name: '002.jpg', url: '/api/v1/media/9/slides/002.jpg', positive_en: 'winking', positive_zh: null, negative_en: null, negative_zh: null },
  { name: '003.jpg', url: '/api/v1/media/9/slides/003.jpg', positive_en: 'leaning', positive_zh: null, negative_en: null, negative_zh: null },
  { name: '005.jpg', url: null, positive_en: null, positive_zh: null, negative_en: null, negative_zh: null },
] };

describe('initialTemplateValue', () => {
  it('prefills from an image and ticks its one picture', () => {
    expect(initialTemplateValue(image, undefined, 'en')).toEqual({ title: 'Bicycle', group: '', positive: 'p', negative: 'n', exampleIds: ['10'] });
  });
  it('joins ticked slides as numbered lines; examples is the album resource', () => {
    expect(initialTemplateValue(album, ['002.jpg', '003.jpg'], 'en')).toEqual({ title: 'Harvest', group: '', positive: '1. winking\n2. leaning', negative: '', exampleIds: ['7'] });
  });
});

describe('SaveAsTemplateDialog', () => {
  it('saves and reports the new asset id', async () => {
    const onSaved = vi.fn();
    render(<SaveAsTemplateDialog scopeId="9000" entry={image} lang="en" onClose={() => {}} onSaved={onSaved} />);
    fireEvent.click(screen.getByRole('button', { name: 'Save to Mine' }));
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith('900'));
    expect(saveAsTemplate).toHaveBeenCalledWith('9000', expect.objectContaining({ title: 'Bicycle', positive: 'p', exampleResourceIds: ['10'] }));
    expect(addToast).toHaveBeenCalledWith('Saved to Mine', 'success');
  });
  it('refuses an empty title or positive without calling the API', () => {
    render(<SaveAsTemplateDialog scopeId="9000" entry={{ ...image, title: '', positive_en: null }} lang="en" onClose={() => {}} onSaved={() => {}} />);
    expect(screen.getByRole('button', { name: 'Save to Mine' })).toBeDisabled();
  });
  it('a failed save stays open and toasts the error code', async () => {
    saveAsTemplate.mockRejectedValueOnce(Object.assign(new Error('nope'), { code: 'duplicate_name' }));
    const onSaved = vi.fn();
    render(<SaveAsTemplateDialog scopeId="9000" entry={image} lang="en" onClose={() => {}} onSaved={onSaved} />);
    fireEvent.click(screen.getByRole('button', { name: 'Save to Mine' }));
    await waitFor(() => expect(addToast).toHaveBeenCalledWith('Could not save: duplicate_name', 'error'));
    expect(onSaved).not.toHaveBeenCalled();
    expect(screen.getByTestId('template-form')).toBeInTheDocument();
  });
});
```

```ts
// frontend/components/prompts/promptsI18nParity.test.ts
// en/zh parity for the `prompts.*` namespace, scanned from source like
// canvas-core's libraryI18n.test.ts — a key added in one locale file only is
// invisible to English machines and silently English for zh users.
import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';

const LOCALES = path.resolve(__dirname, '../../public/locales');
const ROOTS = [path.resolve(__dirname, '.'), path.resolve(__dirname, '../resources/prompts')];

function sources(dir: string): string[] {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((e) =>
    e.isDirectory() ? sources(path.join(dir, e.name)) : /\.tsx?$/.test(e.name) && !/\.test\.tsx?$/.test(e.name) ? [path.join(dir, e.name)] : []);
}
function at(tree: Record<string, unknown>, key: string): unknown {
  return key.split('.').reduce<unknown>((n, p) => (n && typeof n === 'object' ? (n as Record<string, unknown>)[p] : undefined), tree);
}

describe('prompts.* locale parity', () => {
  const keys = new Set<string>();
  for (const f of ROOTS.flatMap(sources)) {
    for (const m of fs.readFileSync(f, 'utf8').matchAll(/t\(\s*'(prompts\.[A-Za-z0-9_.]+)'/g)) keys.add(m[1]);
  }
  const en = JSON.parse(fs.readFileSync(path.join(LOCALES, 'en.json'), 'utf8'));
  const zh = JSON.parse(fs.readFileSync(path.join(LOCALES, 'zh.json'), 'utf8'));
  it('finds keys to check', () => expect(keys.size).toBeGreaterThan(10));
  it.each([...keys])('%s exists in en and zh', (key) => {
    expect(typeof at(en, key), `en ${key}`).toBe('string');
    expect(typeof at(zh, key), `zh ${key}`).toBe('string');
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `cd frontend && npx vitest run components/prompts`
Expected: the dialog tests fail (stub renders null); parity test may already pass — fine.

- [ ] **Step 3: Write the dialog**

```tsx
// frontend/components/prompts/SaveAsTemplateDialog.tsx
//
// Spec §3.5 — the promotion path. Same TemplateForm the panel embeds inline.
import React, { useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { useOptionalToast } from '../Toast';
import { promptText, saveAsTemplate, type PromptEntry, type PromptLang } from '../../services/promptsService';
import { TemplateForm, type TemplateFormValue } from './TemplateForm';

export interface SaveAsTemplateDialogProps {
  scopeId: string;
  entry: PromptEntry;
  slideNames?: string[];
  lang: PromptLang;
  onClose: () => void;
  onSaved: (assetId: string) => void;
}

/** Prefill: an image ticks itself; an album joins the ticked slides' text as
 *  numbered lines and ticks the album resource (slides are not resources). */
export function initialTemplateValue(entry: PromptEntry, slideNames: string[] | undefined, lang: PromptLang): TemplateFormValue {
  if (entry.form === 'album' && entry.slides) {
    const picked = entry.slides.filter((s) => (slideNames ? slideNames.includes(s.name) : !!promptText(s, lang).positive));
    const positive = picked.map((s, i) => `${i + 1}. ${promptText(s, lang).positive}`).filter((l) => !/^\d+\. $/.test(l)).join('\n');
    return { title: entry.title, group: '', positive, negative: '', exampleIds: entry.source.id ? [entry.source.id] : [] };
  }
  const text = promptText(entry, lang);
  return {
    title: entry.title,
    group: '',
    positive: text.positive,
    negative: text.negative ?? '',
    exampleIds: entry.form === 'image' && entry.source.id ? [entry.source.id] : [],
  };
}

export function SaveAsTemplateDialog({ scopeId, entry, slideNames, lang, onClose, onSaved }: SaveAsTemplateDialogProps): React.ReactElement {
  const { t } = useTranslation();
  const toast = useOptionalToast();
  const [value, setValue] = useState<TemplateFormValue>(() => initialTemplateValue(entry, slideNames, lang));
  const [busy, setBusy] = useState(false);
  const examples = useMemo(
    () => (entry.form === 'image' ? [{ id: entry.source.id, url: entry.thumbs[0]?.url ?? null }] : entry.form === 'album' ? [{ id: entry.source.id, url: entry.thumbs[0]?.url ?? null }] : []),
    [entry],
  );
  const canSave = value.title.trim().length > 0 && value.positive.trim().length > 0 && !busy;

  const submit = async () => {
    if (!canSave) return;
    setBusy(true);
    try {
      const { assetId } = await saveAsTemplate(scopeId, {
        title: value.title, group: value.group, positive: value.positive, negative: value.negative, exampleResourceIds: value.exampleIds,
      });
      toast?.addToast(t('prompts.save.saved', 'Saved to Mine'), 'success');
      onSaved(assetId);
    } catch (err) {
      console.error('[SaveAsTemplateDialog] save failed:', err);
      const code = (err as { code?: string })?.code ?? 'unknown';
      toast?.addToast(t('prompts.save.failed', { code, defaultValue: 'Could not save: {{code}}' }), 'error');
    } finally {
      setBusy(false);
    }
  };

  return createPortal(
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/50" onClick={(e) => { if (e.target === e.currentTarget) onClose(); }} data-testid="save-as-template-dialog">
      <div className="flex w-[520px] max-h-[80vh] flex-col rounded-xl border border-line bg-card shadow-xl">
        <div className="flex items-center border-b border-line px-4 py-2 text-[13px] font-semibold text-content">
          {entry.key === 'template:new' ? t('prompts.save.newTitle', 'New Template') : t('prompts.save.title', 'Save as Template')}
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
          <TemplateForm value={value} onChange={setValue} examples={examples} groups={[]} disabled={busy} />
        </div>
        <div className="flex items-center gap-2 border-t border-line px-4 py-2">
          <button type="button" className="rounded-lg bg-accent px-3 py-1 text-[12px] font-medium text-card disabled:opacity-40" disabled={!canSave} onClick={() => void submit()}>{t('prompts.save.submit', 'Save to Mine')}</button>
          <button type="button" className="rounded-lg border border-line px-3 py-1 text-[12px]" onClick={onClose}>{t('common.cancel', 'Cancel')}</button>
          <span className="flex-1" />
          <span className="text-[10.5px] text-content-3">{t('prompts.save.filesStay', 'Pictures stay where they are')}</span>
        </div>
      </div>
    </div>,
    document.body,
  );
}
```

Add to the `prompts` locale object: `"save": { "title": "Save as Template", "newTitle": "New Template", "submit": "Save to Mine", "saved": "Saved to Mine", "failed": "Could not save: {{code}}", "filesStay": "Pictures stay where they are" }` (zh: 存为模板 / 新建模板 / 保存到 Mine / 已保存到 Mine / 保存失败：{{code}} / 图片留在原处).

- [ ] **Step 4: Override the prompt count in `ResourcesContext`**

In `frontend/contexts/ResourcesContext.tsx`, import `fetchPromptCounts` from `../services/promptsService` and change the effect at ~line 758 so the `prompt` key comes from the unified catalog (sidebar and shelf tabs both read `assetCounts`, so they stay in agreement — spec §3.3):

```tsx
    fetchAssetCounts(scopeId)
      .then(async (counts) => {
        // The Prompts tab lists the unified catalog (templates + prompted
        // pictures), so its badge must count THAT — `/assets/counts` only
        // knows the template rows. A failed prompt count keeps the asset
        // number rather than blanking the badge.
        let prompt = counts.prompt;
        try {
          prompt = (await fetchPromptCounts(scopeId)).mine;
        } catch (err) {
          console.error('[ResourcesContext] prompt counts failed:', err);
        }
        if (!cancelled) setAssetCounts({ ...counts, prompt });
      })
```

- [ ] **Step 5: Run tests + typecheck**

Run: `cd frontend && npx vitest run components/prompts components/resources/prompts contexts && npx tsc --noEmit -p . 2>&1 | grep -E "prompts|ResourcesContext" ; true`
Expected: all pass, no type errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/components/prompts frontend/contexts/ResourcesContext.tsx frontend/public/locales/en.json frontend/public/locales/zh.json
git commit -m "feat(prompts): Save as Template dialog, unified prompt count in sidebar/tab, locale parity guard"
```

### Task 12 (B5): Phase B real-stack acceptance (no code)

- [ ] After deploy: `/resources/assets/prompt` shows the debug account's rows (not "Nothing here yet"); form/origin counts sum to `total`; expanding the album shows a disabled Send on the textless slide; a captioned card has no params row; `Save as template` on one image → new template row, sidebar Prompts count +1, `SELECT slot FROM asset_files WHERE asset_id=<new>` = `examples`. Then run `npm run e2e:prod`.

---

## Phase C — canvas Library panel → Prompts page

### Task 13 (C1): store state + `insertText` on the mention handle registry

**Files:**
- Modify: `frontend/features/canvas-core/library/libraryStore.ts` (Persisted + state + `setPage` width switch), `frontend/features/canvas-core/library/mentionLibraryItems.ts:44-47` (`MentionInserters`), `frontend/features/canvas-core/smart/nodes/PromptNodeView.tsx:334-348` (registration)
- Test: `frontend/features/canvas-core/library/libraryStore.test.ts` (extend), `frontend/features/canvas-core/library/mentionHandles.test.ts` (extend)

**Interfaces:**
- Produces (store): `promptLang: 'en' | 'zh'` (persisted, default `'en'`), `promptSegment: 'mine' | 'project' | 'system'` (not persisted, default `'mine'`), `promptForm: PromptForm | null` (not persisted), `setPromptLang`, `setPromptSegment`, `setPromptForm`; `setPage('prompts')` also sets `width` to `600` and `setPage('media')` to `340` (persisted with the page).
- Produces (registry): `MentionInserters.insertText: (text: string) => void` — PromptNodeView's wrapper throws `EditorGoneError` when the editor is unmounted, like the other two.

- [ ] **Step 1: Write the failing tests**

Append to `frontend/features/canvas-core/library/libraryStore.test.ts` (inside its top-level `describe`, reusing its existing `beforeEach` that resets the store — read the file's header first and match its reset helper):

```ts
  it('switches width with the page and persists the language', () => {
    useLibraryStore.getState().setPage('prompts');
    expect(useLibraryStore.getState().width).toBe(600);
    useLibraryStore.getState().setPage('media');
    expect(useLibraryStore.getState().width).toBe(340);
    useLibraryStore.getState().setPromptLang('zh');
    expect(JSON.parse(localStorage.getItem('canvas.library.v1')!).promptLang).toBe('zh');
  });

  it('prompt segment and form are session state with sane defaults', () => {
    expect(useLibraryStore.getState().promptSegment).toBe('mine');
    expect(useLibraryStore.getState().promptForm).toBeNull();
    useLibraryStore.getState().setPromptSegment('system');
    useLibraryStore.getState().setPromptForm('album');
    expect(useLibraryStore.getState().promptSegment).toBe('system');
    expect(JSON.parse(localStorage.getItem('canvas.library.v1')!)).not.toHaveProperty('promptSegment');
  });
```

Append to `mentionHandles.test.ts`:

```ts
  it('carries insertText alongside the two chip inserters', () => {
    const insertText = vi.fn();
    const undo = registerMentionHandle('n1', { insertImage: vi.fn(), insertAsset: vi.fn(), insertText });
    getMentionHandle('n1')!.insertText('hello');
    expect(insertText).toHaveBeenCalledWith('hello');
    undo();
  });
```

- [ ] **Step 2: Run to verify failure**

Run: `cd frontend && npx vitest run features/canvas-core/library/libraryStore.test.ts features/canvas-core/library/mentionHandles.test.ts`
Expected: the new cases fail (`setPromptLang is not a function`, type error on `insertText`).

- [ ] **Step 3: Implement the store changes**

In `libraryStore.ts`:
- `interface Persisted` gains `promptLang: 'en' | 'zh';`. `DEFAULTS` becomes `{ page: 'media', mediaStore: 'assets', width: 340, promptLang: 'en' }`. In `readPersisted`, add `promptLang: parsed.promptLang === 'zh' ? 'zh' : 'en'`. In `persist()`, destructure and write `promptLang` too.
- Add near `LibraryPage`: `export const PANEL_WIDTH: Record<LibraryPage, number> = { media: 340, prompts: 600 };` and import `type PromptForm, type PromptSegment` from `'../../../services/promptsService'`.
- `LibraryPanelState` gains `promptSegment: PromptSegment; promptForm: PromptForm | null; setPromptLang(lang: 'en' | 'zh'): void; setPromptSegment(s: PromptSegment): void; setPromptForm(f: PromptForm | null): void;`.
- Initial state: `promptSegment: 'mine', promptForm: null`.
- `setPage`:
  ```ts
    setPage(page) {
      // Spec §3.1/§3.4: the two pages have different natural widths and the
      // width is persisted, so switching pages re-asserts the page's width.
      set({ page, width: PANEL_WIDTH[page] });
      persist();
    },
  ```
- `openPanel`: when `opts.page` is given and differs from the current page, also set `width: PANEL_WIDTH[opts.page]` in the same `set`.
- New setters:
  ```ts
    setPromptLang(promptLang) { set({ promptLang }); persist(); },
    setPromptSegment(promptSegment) { set({ promptSegment, selection: [] }); },
    setPromptForm(promptForm) { set({ promptForm }); },
  ```

In `mentionLibraryItems.ts`, `MentionInserters` gains:

```ts
  /** Plain text at the caret. The Prompts page's Insert positive uses it; it
   *  never consumes a pending `@query` (the editor's insertText does not). */
  insertText: (text: string) => void;
```

In `PromptNodeView.tsx` registration (~line 336) add the third wrapper:

```ts
        insertText: (text) => {
          const editor = bodyEditorRef.current;
          if (!editor) throw new EditorGoneError();
          editor.insertText(text);
        },
```

Fix any other object literal typed as `MentionInserters` that `tsc` now flags (tests that build fakes) by adding `insertText: vi.fn()`.

- [ ] **Step 4: Run tests + typecheck**

Run: `cd frontend && npx vitest run features/canvas-core/library && npx tsc --noEmit -p . 2>&1 | grep -E "libraryStore|mentionHandles|mentionLibraryItems|PromptNodeView" ; true`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add frontend/features/canvas-core/library/libraryStore.ts frontend/features/canvas-core/library/libraryStore.test.ts frontend/features/canvas-core/library/mentionLibraryItems.ts frontend/features/canvas-core/library/mentionHandles.test.ts frontend/features/canvas-core/smart/nodes/PromptNodeView.tsx
git commit -m "feat(canvas): library store prompt state + width per page; insertText on mention handles"
```

### Task 14 (C2): pure prompt actions + catalog hook

**Files:**
- Create: `frontend/features/canvas-core/library/promptActions.ts`, `frontend/features/canvas-core/library/usePromptCatalog.ts`
- Test: `frontend/features/canvas-core/library/promptActions.test.ts`, `frontend/features/canvas-core/library/usePromptCatalog.test.tsx`

**Interfaces:**
- Consumes: `RATIO_LABELS` (`../smart/nodes/GenFooterControls`), `ratioFromParams`, `fetchPrompts`, `fetchPromptCounts` (`../../../services/promptsService`), `PromptNodeData`, `PromptGenSettings` (`../smart/types`).
- Produces:
  ```ts
  export function appendPositive(body: string, positive: string): string           // '' → positive; else body + '\n' + positive (one newline, trims trailing whitespace of body)
  export function buildApplyAllPatch(args: { positive: string; negative: string | null; params: Record<string, unknown> | null; node: PromptNodeData }): Partial<PromptNodeData>
    // body: positive; negative_body: negative ?? '' (keeps the box); gen: only when node.gen?.kind === 'image' and ratioFromParams(params) is a key of RATIO_LABELS → { ...node.gen, ratio }
  export function ratioPreset(params: Record<string, unknown> | null): string | null
  export function groupChips(items: PromptEntry[], max = 8): string[]                // tag frequency, most frequent first, ties by name
  export interface PromptCatalogState { page: PromptPage | null; counts: PromptCounts | null; loading: boolean; error: Error | null; reload: () => void }
  export function usePromptCatalog(args: { scopeId: string; segment: PromptSegment; projectId: string | null; form: PromptForm | null; query: string; enabled: boolean }): PromptCatalogState
    // 300ms debounce on query; refetches when segment/projectId/form change; counts fetched once per scopeId/projectId and on reload
  ```

- [ ] **Step 1: Write the failing tests**

```ts
// frontend/features/canvas-core/library/promptActions.test.ts
import { describe, expect, it } from 'vitest';
import { appendPositive, buildApplyAllPatch, groupChips, ratioPreset } from './promptActions';
import type { PromptNodeData } from '../smart/types';
import type { PromptEntry } from '../../../services/promptsService';

const text: PromptNodeData = { body: 'draft @', negative_body: undefined, gen: null } as unknown as PromptNodeData;
const image: PromptNodeData = { body: 'x', gen: { kind: 'image', model: '', ratio: '1:1', count: 1 } } as unknown as PromptNodeData;

describe('appendPositive', () => {
  it('starts an empty body, otherwise adds one newline', () => {
    expect(appendPositive('', 'p')).toBe('p');
    expect(appendPositive('draft @  ', 'p')).toBe('draft @\np');
  });
});

describe('buildApplyAllPatch', () => {
  it('replaces body and negative, keeps a cleared negative box', () => {
    expect(buildApplyAllPatch({ positive: 'p', negative: null, params: null, node: text })).toEqual({ body: 'p', negative_body: '' });
  });
  it('maps a preset ratio onto an image node only', () => {
    const p = buildApplyAllPatch({ positive: 'p', negative: 'n', params: { width: 1920, height: 1080 }, node: image });
    expect(p.gen).toEqual({ kind: 'image', model: '', ratio: '16:9', count: 1 });
    expect(buildApplyAllPatch({ positive: 'p', negative: 'n', params: { width: 1920, height: 1080 }, node: text }).gen).toBeUndefined();
  });
  it('leaves ratio alone when the picture size is not a preset', () => {
    expect(buildApplyAllPatch({ positive: 'p', negative: null, params: { width: 832, height: 1216 }, node: image }).gen).toBeUndefined();
    expect(ratioPreset({ width: 832, height: 1216 })).toBeNull();
    expect(ratioPreset({ width: 1024, height: 1024 })).toBe('1:1');
  });
});

describe('groupChips', () => {
  it('ranks tags by frequency then name and caps the list', () => {
    const mk = (tags: string[]) => ({ tags } as PromptEntry);
    expect(groupChips([mk(['b', 'a']), mk(['a']), mk(['c'])], 2)).toEqual(['a', 'b']);
  });
});
```

```tsx
// frontend/features/canvas-core/library/usePromptCatalog.test.tsx
import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
const fetchPrompts = vi.fn();
const fetchPromptCounts = vi.fn();
vi.mock('../../../services/promptsService', async (orig) => ({ ...(await orig<typeof import('../../../services/promptsService')>()), fetchPrompts: (...a: unknown[]) => fetchPrompts(...a), fetchPromptCounts: (...a: unknown[]) => fetchPromptCounts(...a) }));
import { usePromptCatalog } from './usePromptCatalog';

const page = { items: [], total: 0, by_form: { template: 0, image: 0, album: 0 }, by_origin: { typed: 0, extracted: 0, captioned: 0 } };

describe('usePromptCatalog', () => {
  beforeEach(() => { vi.useFakeTimers({ shouldAdvanceTime: true }); fetchPrompts.mockReset().mockResolvedValue(page); fetchPromptCounts.mockReset().mockResolvedValue({ mine: 1, project: null, system: 0 }); });
  afterEach(() => vi.useRealTimers());

  it('fetches page and counts, debouncing the query', async () => {
    const { result, rerender } = renderHook((p: { query: string }) => usePromptCatalog({ scopeId: '9000', segment: 'mine', projectId: null, form: null, query: p.query, enabled: true }), { initialProps: { query: '' } });
    await waitFor(() => expect(result.current.page).toEqual(page));
    expect(fetchPromptCounts).toHaveBeenCalledTimes(1);
    rerender({ query: 'r' }); rerender({ query: 'ra' });
    act(() => { vi.advanceTimersByTime(299); });
    expect(fetchPrompts).toHaveBeenCalledTimes(1);
    act(() => { vi.advanceTimersByTime(1); });
    await waitFor(() => expect(fetchPrompts).toHaveBeenLastCalledWith('9000', expect.objectContaining({ q: 'ra' })));
  });

  it('does nothing while disabled and surfaces errors', async () => {
    const { result, rerender } = renderHook((p: { enabled: boolean }) => usePromptCatalog({ scopeId: '9000', segment: 'mine', projectId: null, form: null, query: '', enabled: p.enabled }), { initialProps: { enabled: false } });
    expect(fetchPrompts).not.toHaveBeenCalled();
    fetchPrompts.mockRejectedValueOnce(new Error('boom'));
    rerender({ enabled: true });
    await waitFor(() => expect(result.current.error?.message).toBe('boom'));
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `cd frontend && npx vitest run features/canvas-core/library/promptActions.test.ts features/canvas-core/library/usePromptCatalog.test.tsx`
Expected: modules not found.

- [ ] **Step 3: Write `promptActions.ts`**

```ts
// features/canvas-core/library/promptActions.ts
//
// The pure half of the Prompts page's actions (spec §3.4 table). No store, no
// editor: LibraryPromptsPage applies these through patchNode/setNodes and the
// mention-handle registry, and the tests here never need React.
import { RATIO_LABELS } from '../smart/nodes/GenFooterControls';
import type { PromptNodeData } from '../smart/types';
import { ratioFromParams, type PromptEntry } from '../../../services/promptsService';

export function appendPositive(body: string, positive: string): string {
  const base = body.replace(/\s+$/, '');
  return base ? `${base}\n${positive}` : positive;
}

/** A picture's size as one of the node's ratio presets, or null. */
export function ratioPreset(params: Record<string, unknown> | null): string | null {
  const ratio = ratioFromParams(params);
  return ratio && Object.prototype.hasOwnProperty.call(RATIO_LABELS, ratio) ? ratio : null;
}

export function buildApplyAllPatch(args: {
  positive: string;
  negative: string | null;
  params: Record<string, unknown> | null;
  node: PromptNodeData;
}): Partial<PromptNodeData> {
  const patch: Partial<PromptNodeData> = { body: args.positive, negative_body: args.negative ?? '' };
  const ratio = ratioPreset(args.params);
  if (ratio && args.node.gen?.kind === 'image') {
    patch.gen = { ...args.node.gen, ratio };
  }
  return patch;
}

export function groupChips(items: PromptEntry[], max = 8): string[] {
  const freq = new Map<string, number>();
  for (const it of items) for (const tag of it.tags ?? []) freq.set(tag, (freq.get(tag) ?? 0) + 1);
  return [...freq.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .slice(0, max)
    .map(([tag]) => tag);
}
```

- [ ] **Step 4: Write `usePromptCatalog.ts`**

```ts
// features/canvas-core/library/usePromptCatalog.ts
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  fetchPromptCounts, fetchPrompts,
  type PromptCounts, type PromptForm, type PromptPage, type PromptSegment,
} from '../../../services/promptsService';

const DEBOUNCE_MS = 300;

export interface PromptCatalogState {
  page: PromptPage | null;
  counts: PromptCounts | null;
  loading: boolean;
  error: Error | null;
  reload: () => void;
}

export function usePromptCatalog(args: {
  scopeId: string;
  segment: PromptSegment;
  projectId: string | null;
  form: PromptForm | null;
  query: string;
  enabled: boolean;
}): PromptCatalogState {
  const { scopeId, segment, projectId, form, query, enabled } = args;
  const [page, setPage] = useState<PromptPage | null>(null);
  const [counts, setCounts] = useState<PromptCounts | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const [tick, setTick] = useState(0);
  const [debounced, setDebounced] = useState(query);
  const firstRef = useRef(true);

  useEffect(() => {
    if (firstRef.current) { firstRef.current = false; setDebounced(query); return; }
    const h = setTimeout(() => setDebounced(query), DEBOUNCE_MS);
    return () => clearTimeout(h);
  }, [query]);

  useEffect(() => {
    if (!enabled || !scopeId) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchPrompts(scopeId, { segment, projectId: segment === 'project' ? projectId : null, form, q: debounced, limit: 200 })
      .then((p) => { if (!cancelled) setPage(p); })
      .catch((err: unknown) => { console.error('[usePromptCatalog] load failed:', err); if (!cancelled) setError(err instanceof Error ? err : new Error(String(err))); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [enabled, scopeId, segment, projectId, form, debounced, tick]);

  useEffect(() => {
    if (!enabled || !scopeId) return;
    let cancelled = false;
    fetchPromptCounts(scopeId, projectId)
      .then((c) => { if (!cancelled) setCounts(c); })
      .catch((err: unknown) => console.error('[usePromptCatalog] counts failed:', err));
    return () => { cancelled = true; };
  }, [enabled, scopeId, projectId, tick]);

  const reload = useCallback(() => setTick((v) => v + 1), []);
  return { page, counts, loading, error, reload };
}
```

- [ ] **Step 5: Run tests**

Run: `cd frontend && npx vitest run features/canvas-core/library/promptActions.test.ts features/canvas-core/library/usePromptCatalog.test.tsx`
Expected: all pass (the debounce test relies on `shouldAdvanceTime: true` — see memory `reference-vitest-rtl-waitfor-needs-should-advance-time`).

- [ ] **Step 6: Commit**

```bash
git add frontend/features/canvas-core/library/promptActions.ts frontend/features/canvas-core/library/promptActions.test.ts frontend/features/canvas-core/library/usePromptCatalog.ts frontend/features/canvas-core/library/usePromptCatalog.test.tsx
git commit -m "feat(canvas): prompt actions (append/apply-all/ratio preset/group chips) + catalog hook"
```

### Task 15 (C3): `LibraryPromptList` + `LibraryPromptPreview`

**Files:**
- Create: `frontend/features/canvas-core/library/LibraryPromptList.tsx`, `frontend/features/canvas-core/library/LibraryPromptPreview.tsx`
- Test: `frontend/features/canvas-core/library/LibraryPromptList.test.tsx`, `frontend/features/canvas-core/library/LibraryPromptPreview.test.tsx`
- Modify: locales (`canvas.library.*` keys listed in Step 5)

**Interfaces:**
- Consumes: `PromptThumbs`, `FormTag`, `OriginTag` (`../../../components/prompts/*`), `promptText`, `paramChips`, `langAvailability`, `thumbSrc` (`../../../services/promptsService`), `chipClass` (`./libraryChrome`).
- Produces:
  ```tsx
  export interface LibraryPromptListProps { items: PromptEntry[]; activeKey: string | null; onActivate: (key: string) => void; lang: PromptLang; loading: boolean; error: Error | null; onRetry: () => void; emptyLabel: string }
  export function LibraryPromptList(props: LibraryPromptListProps): JSX.Element     // rows data-testid="library-prompt-row" data-key data-active; captioned rows get class text-canvas-muted
  export interface LibraryPromptPreviewProps {
    entry: PromptEntry | null; lang: PromptLang; onLangChange: (l: PromptLang) => void;
    slideName: string | null; onSlideChange: (name: string) => void;
    canAct: boolean; actHint: string;                        // false + hint when there is no target
    onInsert: () => void; onApplyAll: () => void; onSaveAsTemplate: () => void;
  }
  export function LibraryPromptPreview(props: LibraryPromptPreviewProps): JSX.Element
  export function activeSlide(entry: PromptEntry | null, slideName: string | null): PromptSlide | null   // first slide WITH text when name is null
  ```

- [ ] **Step 1: Write the failing tests**

```tsx
// frontend/features/canvas-core/library/LibraryPromptList.test.tsx
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (k: string, d?: unknown) => (typeof d === 'string' ? d : (d as { defaultValue?: string })?.defaultValue ?? k) }) }));
vi.mock('../../../utils/apiConfig', () => ({ getApiUrl: () => 'https://api.test' }));
import { LibraryPromptList } from './LibraryPromptList';
import type { PromptEntry } from '../../../services/promptsService';

const base = { tags: [], positive_zh: null, negative_en: null, negative_zh: null, params: null, slides: null, updated_at: '' };
const items: PromptEntry[] = [
  { ...base, key: 'template:1', form: 'template', origin: 'typed', title: 'Rain', positive_en: 'hero close-up', thumbs: [], source: { store: 'assets', id: '1' } },
  { ...base, key: 'album:7', form: 'album', origin: 'extracted', title: 'Harvest', positive_en: 'winking', thumbs: [{ url: '/api/v1/media/9/slides/002.jpg', kind: 'image' }], slides: [{ name: '002.jpg', url: null, positive_en: 'winking', positive_zh: null, negative_en: null, negative_zh: null }, { name: '003.jpg', url: null, positive_en: null, positive_zh: null, negative_en: null, negative_zh: null }], source: { store: 'uploads', id: '7' } },
  { ...base, key: 'image:11', form: 'image', origin: 'captioned', title: 'Courtyard', positive_en: 'a young woman', thumbs: [{ url: '/api/v1/resources/11/cover', kind: 'image' }], source: { store: 'uploads', id: '11' } },
];

describe('LibraryPromptList', () => {
  it('renders a row per entry with form tag, thumb state and muted captioned', () => {
    render(<LibraryPromptList items={items} activeKey="album:7" onActivate={() => {}} lang="en" loading={false} error={null} onRetry={() => {}} emptyLabel="Nothing Here Yet" />);
    const rows = screen.getAllByTestId('library-prompt-row');
    expect(rows).toHaveLength(3);
    expect(rows[0].querySelector('[data-empty="true"]')).not.toBeNull();       // template with no pictures
    expect(rows[1]).toHaveAttribute('data-active', 'true');
    expect(rows[1].textContent).toContain('1 of 2 slides with text');
    expect(rows[2].className).toContain('text-canvas-muted');
  });
  it('activates on click and shows error/empty states', () => {
    const onActivate = vi.fn();
    const { rerender } = render(<LibraryPromptList items={items} activeKey={null} onActivate={onActivate} lang="en" loading={false} error={null} onRetry={() => {}} emptyLabel="Nothing Here Yet" />);
    fireEvent.click(screen.getAllByTestId('library-prompt-row')[0]);
    expect(onActivate).toHaveBeenCalledWith('template:1');
    rerender(<LibraryPromptList items={[]} activeKey={null} onActivate={onActivate} lang="en" loading={false} error={new Error('x')} onRetry={() => {}} emptyLabel="Nothing Here Yet" />);
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument();
    rerender(<LibraryPromptList items={[]} activeKey={null} onActivate={onActivate} lang="en" loading={false} error={null} onRetry={() => {}} emptyLabel="Nothing Here Yet" />);
    expect(screen.getByText('Nothing Here Yet')).toBeInTheDocument();
  });
});
```

```tsx
// frontend/features/canvas-core/library/LibraryPromptPreview.test.tsx
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (k: string, d?: unknown) => (typeof d === 'string' ? d : (d as { defaultValue?: string })?.defaultValue ?? k) }) }));
vi.mock('../../../utils/apiConfig', () => ({ getApiUrl: () => 'https://api.test' }));
import { LibraryPromptPreview, activeSlide } from './LibraryPromptPreview';
import type { PromptEntry } from '../../../services/promptsService';

const image: PromptEntry = { key: 'image:10', form: 'image', origin: 'extracted', title: 'Bicycle', tags: [], positive_en: 'cheerful', positive_zh: null, negative_en: 'flare', negative_zh: null, params: { width: 1920, height: 1080, steps: 28 }, thumbs: [{ url: '/api/v1/resources/10/cover', kind: 'image' }], slides: null, source: { store: 'uploads', id: '10' }, updated_at: '' };
const album: PromptEntry = { ...image, key: 'album:7', form: 'album', title: 'Harvest', params: null, slides: [
  { name: '001.jpg', url: null, positive_en: null, positive_zh: null, negative_en: null, negative_zh: null },
  { name: '002.jpg', url: '/api/v1/media/9/slides/002.jpg', positive_en: 'winking', positive_zh: '眨眼', negative_en: 'blur', negative_zh: null },
  { name: '003.jpg', url: null, positive_en: 'leaning', positive_zh: null, negative_en: null, negative_zh: null },
] };
const noop = () => {};
const props = { lang: 'en' as const, onLangChange: noop, slideName: null, onSlideChange: noop, canAct: true, actHint: '', onInsert: noop, onApplyAll: noop, onSaveAsTemplate: noop };

describe('activeSlide', () => {
  it('defaults to the first slide that has text', () => {
    expect(activeSlide(album, null)?.name).toBe('002.jpg');
    expect(activeSlide(album, '003.jpg')?.name).toBe('003.jpg');
    expect(activeSlide(image, null)).toBeNull();
  });
});

describe('LibraryPromptPreview', () => {
  it('image: positive/negative/params blocks and the three actions', () => {
    const onInsert = vi.fn(), onApplyAll = vi.fn(), onSave = vi.fn();
    render(<LibraryPromptPreview {...props} entry={image} onInsert={onInsert} onApplyAll={onApplyAll} onSaveAsTemplate={onSave} />);
    expect(screen.getByTestId('library-prompt-positive')).toHaveTextContent('cheerful');
    expect(screen.getByTestId('library-prompt-negative')).toHaveTextContent('flare');
    expect(screen.getByTestId('library-prompt-params')).toHaveTextContent('16:9');
    fireEvent.click(screen.getByRole('button', { name: 'Insert positive' })); expect(onInsert).toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Apply all' })); expect(onApplyAll).toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Save as template…' })); expect(onSave).toHaveBeenCalled();
  });
  it('album: slide rows replace the positive block; textless slides disabled; selected slide names the actions', () => {
    const onSlideChange = vi.fn();
    render(<LibraryPromptPreview {...props} entry={album} slideName="002.jpg" onSlideChange={onSlideChange} />);
    const rows = screen.getAllByTestId('library-prompt-slide');
    expect(rows).toHaveLength(3);
    expect(rows[0].querySelector('button')).toBeDisabled();
    expect(rows[1]).toHaveAttribute('data-active', 'true');
    expect(screen.getByRole('button', { name: 'Insert slide 002.jpg' })).toBeInTheDocument();
    expect(screen.getByTestId('library-prompt-negative')).toHaveTextContent('blur');
    fireEvent.click(rows[2]);
    expect(onSlideChange).toHaveBeenCalledWith('003.jpg');
  });
  it('no target: actions disabled with the hint; template hides Save as template', () => {
    render(<LibraryPromptPreview {...props} entry={{ ...image, form: 'template', origin: 'typed' }} canAct={false} actHint="Pick a prompt node first" />);
    expect(screen.getByRole('button', { name: 'Insert positive' })).toBeDisabled();
    expect(screen.getByText('Pick a prompt node first')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Save as template…' })).toBeNull();
  });
  it('language toggle greys a missing side and shows the other with a note', () => {
    const onLangChange = vi.fn();
    render(<LibraryPromptPreview {...props} entry={image} lang="zh" onLangChange={onLangChange} />);
    expect(screen.getByTestId('library-prompt-positive')).toHaveTextContent('cheerful');
    expect(screen.getByText('EN only')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'EN' }));
    expect(onLangChange).toHaveBeenCalledWith('en');
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `cd frontend && npx vitest run features/canvas-core/library/LibraryPromptList.test.tsx features/canvas-core/library/LibraryPromptPreview.test.tsx`
Expected: modules not found.

- [ ] **Step 3: Write `LibraryPromptList.tsx`**

```tsx
// features/canvas-core/library/LibraryPromptList.tsx
//
// The left column of the Prompts page (spec §3.4): one row per PromptEntry.
// Not a LibraryGrid — a prompt is read, not glanced at, so the row is text
// with a 36px thumbnail, and there is no multi-select (insertion is one at a
// time by design; merging slides is the user's job).
import React, { useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { Loader2 } from 'lucide-react';
import { FormTag } from '../../../components/prompts/PromptTags';
import { PromptThumbs } from '../../../components/prompts/PromptThumbs';
import { promptText, type PromptEntry, type PromptLang } from '../../../services/promptsService';

export interface LibraryPromptListProps {
  items: PromptEntry[];
  activeKey: string | null;
  onActivate: (key: string) => void;
  lang: PromptLang;
  loading: boolean;
  error: Error | null;
  onRetry: () => void;
  emptyLabel: string;
}

export function LibraryPromptList({ items, activeKey, onActivate, lang, loading, error, onRetry, emptyLabel }: LibraryPromptListProps): React.ReactElement {
  const { t } = useTranslation();
  const rootRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    rootRef.current?.querySelector('[data-active="true"]')?.scrollIntoView?.({ block: 'nearest' });
  }, [activeKey]);

  if (error) {
    return (
      <div className="flex flex-col items-start gap-1 p-3 text-[11px] text-danger" data-testid="library-prompt-error">
        {t('canvas.library.promptsLoadFailed', 'Could not load prompts')}
        <button type="button" className="nodrag rounded border border-canvas-line px-2 py-0.5 text-canvas-text" onClick={onRetry}>{t('canvas.library.retry', 'Retry')}</button>
      </div>
    );
  }
  if (loading && items.length === 0) {
    return <div className="flex items-center gap-2 p-3 text-[11px] text-canvas-muted"><Loader2 size={12} className="animate-spin" />{t('canvas.library.loading', 'Loading…')}</div>;
  }
  if (items.length === 0) {
    return <p className="p-3 text-[11px] text-canvas-muted" data-testid="library-prompt-empty">{emptyLabel}</p>;
  }
  return (
    <div ref={rootRef} className="flex flex-col gap-px overflow-y-auto p-1" role="listbox" aria-label={t('canvas.library.promptsList', 'Prompts')}>
      {items.map((entry) => {
        const active = entry.key === activeKey;
        const muted = entry.origin === 'captioned';
        const first = promptText(entry, lang).positive;
        const withText = entry.slides?.filter((s) => promptText(s, lang).positive).length ?? 0;
        return (
          <div
            key={entry.key}
            role="option"
            aria-selected={active}
            data-testid="library-prompt-row"
            data-key={entry.key}
            data-active={active ? 'true' : 'false'}
            onClick={() => onActivate(entry.key)}
            className={`nodrag grid cursor-pointer grid-cols-[36px_1fr_auto] items-center gap-x-2 rounded-lg px-1.5 py-1 ${active ? 'bg-[var(--accent-soft)] ring-1 ring-inset ring-[var(--accent-border)]' : 'hover:bg-canvas-card'} ${muted ? 'text-canvas-muted' : 'text-canvas-text'}`}
          >
            <PromptThumbs thumbs={entry.thumbs} count={entry.form === 'album' ? entry.slides?.length : undefined} size={36} />
            <span className="truncate text-[12px] font-medium">{entry.title}</span>
            <FormTag form={entry.form} />
            <span className="col-start-2 col-end-4 truncate text-[10.5px] text-canvas-muted">
              {entry.form === 'album'
                ? t('canvas.library.slidesWithText', { count: withText, total: entry.slides?.length ?? 0, defaultValue: '{{count}} of {{total}} slides with text' })
                : first}
            </span>
          </div>
        );
      })}
    </div>
  );
}
```

- [ ] **Step 4: Write `LibraryPromptPreview.tsx`**

```tsx
// features/canvas-core/library/LibraryPromptPreview.tsx
//
// Right column (spec §3.4): what Apply all would write, labelled by what it
// is. An album previews as its slides; the selected slide's negative and
// params sit beneath. Actions are named after the thing they act on.
import React from 'react';
import { useTranslation } from 'react-i18next';
import { FormTag, OriginTag } from '../../../components/prompts/PromptTags';
import { langAvailability, paramChips, promptText, thumbSrc, type PromptEntry, type PromptLang, type PromptSlide } from '../../../services/promptsService';

export interface LibraryPromptPreviewProps {
  entry: PromptEntry | null;
  lang: PromptLang;
  onLangChange: (l: PromptLang) => void;
  slideName: string | null;
  onSlideChange: (name: string) => void;
  canAct: boolean;
  actHint: string;
  onInsert: () => void;
  onApplyAll: () => void;
  onSaveAsTemplate: () => void;
}

export function activeSlide(entry: PromptEntry | null, slideName: string | null): PromptSlide | null {
  const slides = entry?.slides;
  if (!slides || slides.length === 0) return null;
  if (slideName) return slides.find((s) => s.name === slideName) ?? null;
  return slides.find((s) => s.positive_en || s.positive_zh) ?? slides[0];
}

const BLOCK_LABEL = 'mb-0.5 flex items-baseline gap-1.5 text-[9.5px] font-semibold uppercase tracking-[0.08em] text-canvas-muted';
const BTN = 'nodrag rounded-lg border border-canvas-line px-2.5 py-1 text-[11px] font-medium text-canvas-text disabled:opacity-40';

export function LibraryPromptPreview(p: LibraryPromptPreviewProps): React.ReactElement {
  const { t } = useTranslation();
  const { entry, lang } = p;
  if (!entry) {
    return <div className="flex flex-1 items-center justify-center p-6 text-center text-[11px] text-canvas-muted">{t('canvas.library.promptPickOne', 'Pick a prompt to preview it')}</div>;
  }
  const slide = activeSlide(entry, p.slideName);
  const src = slide ?? entry;
  const text = promptText(src, lang);
  const avail = langAvailability(src);
  const chips = entry.origin === 'captioned' ? [] : paramChips(entry.params);
  const insertLabel = slide ? t('canvas.library.insertSlide', { name: slide.name, defaultValue: 'Insert slide {{name}}' }) : t('canvas.library.insertPositive', 'Insert positive');
  const applyLabel = slide ? t('canvas.library.applyAllFrom', { name: slide.name, defaultValue: 'Apply all from {{name}}' }) : t('canvas.library.applyAll', 'Apply all');
  const canInsert = p.canAct && !!text.positive;

  return (
    <div className="flex min-h-0 flex-1 flex-col" data-testid="library-prompt-preview">
      <div className="flex items-center gap-1.5 px-2.5 pt-2">
        <span className="truncate text-[13px] font-semibold text-canvas-text">{entry.title}</span>
        <FormTag form={entry.form} />
        <OriginTag origin={entry.origin} params={entry.params} />
        <span className="flex-1" />
        <div className="flex overflow-hidden rounded-full border border-canvas-line text-[10px]">
          {(['en', 'zh'] as PromptLang[]).map((l) => {
            const has = l === 'en' ? avail === 'both' || avail === 'en' : avail === 'both' || avail === 'zh';
            return (
              <button key={l} type="button" aria-pressed={lang === l} onClick={() => p.onLangChange(l)}
                className={`nodrag px-2 py-0.5 ${lang === l ? 'bg-[var(--accent-soft)] text-[var(--accent-text)]' : 'text-canvas-muted'} ${has ? '' : 'opacity-40'}`}>
                {l === 'en' ? 'EN' : '中'}
              </button>
            );
          })}
        </div>
      </div>
      <div className="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto px-2.5 py-2">
        {entry.thumbs.length > 0 && !entry.slides && (
          <div className="flex gap-1.5">{entry.thumbs.map((th) => <img key={th.url} src={thumbSrc(th.url)} alt="" className="h-14 w-14 rounded-lg object-cover" />)}</div>
        )}
        {entry.slides ? (
          <div>
            <div className={BLOCK_LABEL}>{t('canvas.library.slides', 'Slides')}<span className="font-normal normal-case tracking-normal">{t('canvas.library.slidesHint', 'click one to preview · each inserts on its own')}</span></div>
            <div className="flex flex-col gap-1">
              {entry.slides.map((s) => {
                const st = promptText(s, lang);
                const has = !!st.positive;
                const on = slide?.name === s.name;
                return (
                  <div key={s.name} data-testid="library-prompt-slide" data-active={on ? 'true' : 'false'} onClick={() => p.onSlideChange(s.name)}
                    className={`nodrag grid cursor-pointer grid-cols-[40px_1fr_auto] items-center gap-2 rounded-lg p-1.5 ${on ? 'bg-[var(--accent-soft)] ring-1 ring-inset ring-[var(--accent-border)]' : 'bg-canvas-card'} ${has ? '' : 'opacity-60'}`}>
                    {s.url ? <img src={thumbSrc(s.url)} alt="" className="h-10 w-10 rounded-md object-cover" /> : <span className="block h-10 w-10 rounded-md bg-canvas-page" />}
                    <div className="min-w-0">
                      <p className={`m-0 line-clamp-2 font-mono text-[10.5px] ${has ? 'text-canvas-text' : 'text-canvas-muted'}`}>{has ? st.positive : t('canvas.library.noPromptOnSlide', 'No prompt on this slide')}</p>
                      <small className="text-[9.5px] text-canvas-muted">{s.name}</small>
                    </div>
                    <button type="button" className={`${BTN} px-2 py-0.5 text-[10.5px]`} disabled={!has || !p.canAct} onClick={(e) => { e.stopPropagation(); p.onSlideChange(s.name); p.onInsert(); }}>{t('canvas.library.insert', 'Insert')}</button>
                  </div>
                );
              })}
            </div>
          </div>
        ) : (
          <div>
            <div className={BLOCK_LABEL}>{t('canvas.library.positive', 'Positive')}{text.shownLang && text.shownLang !== lang && <span className="font-normal normal-case tracking-normal">{text.shownLang === 'en' ? t('canvas.library.enOnly', 'EN only') : t('canvas.library.zhOnly', '中 only')}</span>}</div>
            <pre data-testid="library-prompt-positive" className="m-0 whitespace-pre-wrap font-mono text-[11.5px] leading-relaxed text-canvas-text">{text.positive || t('canvas.library.noPositive', 'No positive prompt')}</pre>
          </div>
        )}
        {text.negative && (
          <div>
            <div className={BLOCK_LABEL}>{t('canvas.library.negative', 'Negative')}{slide && <span className="font-normal normal-case tracking-normal">· {slide.name}</span>}</div>
            <pre data-testid="library-prompt-negative" className="m-0 whitespace-pre-wrap font-mono text-[11.5px] text-canvas-muted">{text.negative}</pre>
          </div>
        )}
        {chips.length > 0 && (
          <div>
            <div className={BLOCK_LABEL}>{t('canvas.library.params', 'Params')}<span className="font-normal normal-case tracking-normal">{t('canvas.library.paramsHint', 'applied only by Apply all')}</span></div>
            <div data-testid="library-prompt-params" className="flex flex-wrap gap-1">{chips.map((c) => <span key={c} className="rounded-md border border-canvas-line px-1.5 font-mono text-[10.5px] text-canvas-text">{c}</span>)}</div>
          </div>
        )}
        {entry.origin === 'captioned' && <p className="m-0 text-[10.5px] text-canvas-muted">{t('canvas.library.captionedNote', 'This text describes the picture, it did not make it')}</p>}
      </div>
      <div className="flex items-center gap-1.5 border-t border-canvas-line px-2.5 py-2">
        <button type="button" className={`${BTN} bg-[var(--accent-text)] text-white`} disabled={!canInsert} onClick={p.onInsert}>{insertLabel}</button>
        <button type="button" className={BTN} disabled={!canInsert} onClick={p.onApplyAll}>{applyLabel}</button>
        {entry.form !== 'template' && <button type="button" className={`${BTN} border-transparent`} onClick={p.onSaveAsTemplate}>{t('canvas.library.saveAsTemplate', 'Save as template…')}</button>}
        <span className="flex-1" />
        <span className="text-[10.5px] text-canvas-muted">{p.canAct ? '↵ · ⇧↵' : p.actHint}</span>
      </div>
    </div>
  );
}
```

- [ ] **Step 5: Add locale keys**

Under `canvas.library` in en.json / zh.json add: `promptsLoadFailed` "Could not load prompts" / 提示词加载失败; `retry` "Retry" / 重试; `loading` "Loading…" / 加载中…; `promptsList` "Prompts" / 提示词; `slidesWithText` "{{count}} of {{total}} slides with text" / {{total}} 张中 {{count}} 张有文字; `promptPickOne` "Pick a prompt to preview it" / 选一条提示词预览; `insertSlide` "Insert slide {{name}}" / 插入 {{name}}; `insertPositive` "Insert positive" / 插入正向; `applyAllFrom` "Apply all from {{name}}" / 从 {{name}} 全量应用; `applyAll` "Apply all" / 全量应用; `slides` "Slides" / 各张; `slidesHint` "click one to preview · each inserts on its own" / 点一张预览 · 各自插入; `noPromptOnSlide` "No prompt on this slide" / 这张没有提示词; `insert` "Insert" / 插入; `positive` "Positive" / 正向; `enOnly` "EN only" / 仅 EN; `zhOnly` "中 only" / 仅中文; `noPositive` "No positive prompt" / 没有正向提示词; `negative` "Negative" / 负向; `params` "Params" / 参数; `paramsHint` "applied only by Apply all" / 只有全量应用才会用; `captionedNote` "This text describes the picture, it did not make it" / 这段文字描述了图片，不是生成它的提示词; `saveAsTemplate` "Save as template…" / 存为模板…. (The `libraryI18n.test.ts` scanner will list any you miss.)

- [ ] **Step 6: Run tests and the two guards**

Run: `cd frontend && npx vitest run features/canvas-core/library/LibraryPromptList.test.tsx features/canvas-core/library/LibraryPromptPreview.test.tsx features/canvas-core/library/libraryI18n.test.ts features/canvas-core/library/libraryNaming.test.ts`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add frontend/features/canvas-core/library/LibraryPromptList.tsx frontend/features/canvas-core/library/LibraryPromptList.test.tsx frontend/features/canvas-core/library/LibraryPromptPreview.tsx frontend/features/canvas-core/library/LibraryPromptPreview.test.tsx frontend/public/locales/en.json frontend/public/locales/zh.json
git commit -m "feat(canvas): Prompts page list + preview (per-slide albums, language toggle)"
```

### Task 16 (C4): `LibraryPromptsPage` — wiring, four actions, confirm, forms, keyboard; mount in the panel

**Files:**
- Create: `frontend/features/canvas-core/library/LibraryPromptsPage.tsx`
- Modify: `frontend/features/canvas-core/library/LibraryPanel.tsx:186-193` (replace the stub), locales
- Test: `frontend/features/canvas-core/library/LibraryPromptsPage.test.tsx`, extend `frontend/features/canvas-core/library/LibraryPanel.test.tsx` (the "Prompts page renders the P3 line" case becomes "renders LibraryPromptsPage")

**Interfaces:**
- Consumes: everything from C1–C3; `useCanvasScope` (`../smart/canvasScope`), `useCanvasCoreStore` (`../store/canvasCoreStore`: `nodes`, `projectId`, `patchNode(id, { data })`, `setNodes(nodes)`), `useCanvasReadOnly`, `getMentionHandle`, `EditorGoneError` (`./mentionHandles`), `useOptionalToast`, `TemplateForm` + `initialTemplateValue` + `saveAsTemplate`, `createAsset` (for Save current / New — same `saveAsTemplate` with `exampleResourceIds: []`).
- Produces: `export function LibraryPromptsPage({ target, targetData }: { target: LibraryTarget | null; targetData: PromptNodeData | null }): JSX.Element`.

- [ ] **Step 1: Write the failing page test**

```tsx
// frontend/features/canvas-core/library/LibraryPromptsPage.test.tsx
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (k: string, d?: unknown) => (typeof d === 'string' ? d : (d as { defaultValue?: string })?.defaultValue ?? k) }) }));
vi.mock('../../../utils/apiConfig', () => ({ getApiUrl: () => 'https://api.test' }));
vi.mock('../smart/canvasScope', () => ({ useCanvasScope: () => ({ scopeId: '9000', resPath: (p: string) => p }) }));
const addToast = vi.fn();
vi.mock('../../../components/Toast', () => ({ useOptionalToast: () => ({ addToast }) }));
const fetchPrompts = vi.fn();
const fetchPromptCounts = vi.fn();
const saveAsTemplate = vi.fn(async () => ({ assetId: '900' }));
vi.mock('../../../services/promptsService', async (orig) => ({ ...(await orig<typeof import('../../../services/promptsService')>()), fetchPrompts: (...a: unknown[]) => fetchPrompts(...a), fetchPromptCounts: (...a: unknown[]) => fetchPromptCounts(...a), saveAsTemplate: (...a: unknown[]) => saveAsTemplate(...a) }));

import { useCanvasCoreStore } from '../store/canvasCoreStore';
import { useLibraryStore } from './libraryStore';
import { registerMentionHandle } from './mentionHandles';
import { LibraryPromptsPage } from './LibraryPromptsPage';
import type { PromptEntry } from '../../../services/promptsService';
import type { PromptNodeData } from '../smart/types';

const image: PromptEntry = { key: 'image:10', form: 'image', origin: 'extracted', title: 'Bicycle', tags: ['Lighting'], positive_en: 'cheerful', positive_zh: null, negative_en: 'flare', negative_zh: null, params: { width: 1920, height: 1080 }, thumbs: [], slides: null, source: { store: 'uploads', id: '10' }, updated_at: '' };
const page = { items: [image], total: 1, by_form: { template: 0, image: 1, album: 0 }, by_origin: { typed: 0, extracted: 1, captioned: 0 } };
const node = (data: Partial<PromptNodeData>) => ({ id: 'p1', type: 'prompt', position: { x: 0, y: 0 }, data: { body: '', gen: null, ...data } });
const target = { nodeId: 'p1', kind: 'prompt' as const, title: 'Prompt' };

function mount(data: Partial<PromptNodeData>, tgt = target) {
  useCanvasCoreStore.setState({ nodes: [node(data)] as never, projectId: null, readOnly: false } as never);
  return render(<LibraryPromptsPage target={tgt} targetData={useCanvasCoreStore.getState().nodes[0].data as PromptNodeData} />);
}

describe('LibraryPromptsPage', () => {
  beforeEach(() => {
    fetchPrompts.mockReset().mockResolvedValue(page);
    fetchPromptCounts.mockReset().mockResolvedValue({ mine: 1, project: null, system: 0 });
    saveAsTemplate.mockClear(); addToast.mockClear();
    useLibraryStore.setState({ open: true, page: 'prompts', query: '', promptSegment: 'mine', promptForm: null, promptLang: 'en' } as never);
  });

  it('hides the System segment when there are no presets and shows counts on Mine', async () => {
    mount({});
    await screen.findByTestId('library-prompt-row');
    expect(screen.getByRole('button', { name: 'Mine 1' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /System/ })).toBeNull();
  });

  it('Insert positive goes through the editor handle and never touches the body text', async () => {
    const insertText = vi.fn();
    const undo = registerMentionHandle('p1', { insertImage: vi.fn(), insertAsset: vi.fn(), insertText });
    mount({ body: 'draft @' });
    fireEvent.click(await screen.findByTestId('library-prompt-row'));
    fireEvent.click(screen.getByRole('button', { name: 'Insert positive' }));
    expect(insertText).toHaveBeenCalledWith('cheerful');
    expect((useCanvasCoreStore.getState().nodes[0] as { data: PromptNodeData }).data.body).toBe('draft @');
    undo();
  });

  it('Insert positive falls back to appending when the editor is not mounted', async () => {
    mount({ body: 'draft @' });
    fireEvent.click(await screen.findByTestId('library-prompt-row'));
    fireEvent.click(screen.getByRole('button', { name: 'Insert positive' }));
    expect((useCanvasCoreStore.getState().nodes[0] as { data: PromptNodeData }).data.body).toBe('draft @\ncheerful');
    expect(addToast).toHaveBeenCalledWith('Added to the end — the card was off screen', 'info');
  });

  it('Apply all onto a non-empty body asks first, then replaces body/negative/ratio in one write', async () => {
    mount({ body: 'old', gen: { kind: 'image', model: '', ratio: '1:1', count: 1 } });
    fireEvent.click(await screen.findByTestId('library-prompt-row'));
    fireEvent.click(screen.getByRole('button', { name: 'Apply all' }));
    expect(screen.getByTestId('library-prompt-confirm')).toHaveTextContent('Replace the body of');
    const before = useCanvasCoreStore.getState().historyPast.length;
    fireEvent.click(screen.getByRole('button', { name: 'Replace' }));
    const data = (useCanvasCoreStore.getState().nodes[0] as { data: PromptNodeData }).data;
    expect(data.body).toBe('cheerful'); expect(data.negative_body).toBe('flare'); expect(data.gen?.ratio).toBe('16:9');
    expect(useCanvasCoreStore.getState().historyPast.length).toBe(before + 1);
  });

  it('Apply all onto an empty body needs no confirmation', async () => {
    mount({ body: '' });
    fireEvent.click(await screen.findByTestId('library-prompt-row'));
    fireEvent.click(screen.getByRole('button', { name: 'Apply all' }));
    expect(screen.queryByTestId('library-prompt-confirm')).toBeNull();
    expect((useCanvasCoreStore.getState().nodes[0] as { data: PromptNodeData }).data.body).toBe('cheerful');
  });

  it('no target: actions disabled and the consequence line says so', async () => {
    mount({}, null as never);
    fireEvent.click(await screen.findByTestId('library-prompt-row'));
    expect(screen.getByRole('button', { name: 'Insert positive' })).toBeDisabled();
    expect(screen.getByTestId('library-consequence')).toHaveTextContent('Select a prompt node to insert or apply');
  });

  it('Save current prefills from the node and saves a template without pictures', async () => {
    mount({ body: 'my prompt', negative_body: 'ugly' });
    await screen.findByTestId('library-prompt-row');
    fireEvent.click(screen.getByRole('button', { name: 'Save current…' }));
    expect(screen.getByLabelText('Positive')).toHaveValue('my prompt');
    fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'Mine 1' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save to Mine' }));
    await waitFor(() => expect(saveAsTemplate).toHaveBeenCalledWith('9000', expect.objectContaining({ title: 'Mine 1', positive: 'my prompt', negative: 'ugly', exampleResourceIds: [] })));
    await waitFor(() => expect(fetchPrompts).toHaveBeenCalledTimes(2)); // reload after save
  });

  it('keyboard: ArrowDown moves, Enter inserts, Escape cancels an open form before closing', async () => {
    fetchPrompts.mockResolvedValue({ ...page, items: [image, { ...image, key: 'image:11', title: 'Second', positive_en: 'second' }], total: 2 });
    const insertText = vi.fn();
    const undo = registerMentionHandle('p1', { insertImage: vi.fn(), insertAsset: vi.fn(), insertText });
    mount({});
    const root = (await screen.findAllByTestId('library-prompt-row'))[0].closest('[data-testid="library-prompts-page"]')!;
    fireEvent.keyDown(root, { key: 'ArrowDown' });
    fireEvent.keyDown(root, { key: 'Enter' });
    expect(insertText).toHaveBeenCalledWith('second');
    fireEvent.click(screen.getByRole('button', { name: 'New…' }));
    fireEvent.keyDown(root, { key: 'Escape' });
    expect(screen.queryByTestId('template-form')).toBeNull();
    expect(useLibraryStore.getState().open).toBe(true);
    undo();
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `cd frontend && npx vitest run features/canvas-core/library/LibraryPromptsPage.test.tsx`
Expected: module not found.

- [ ] **Step 3: Write `LibraryPromptsPage.tsx`**

```tsx
// features/canvas-core/library/LibraryPromptsPage.tsx
//
// The panel's Prompts page (spec 2026-09-05 §3.4). Same shell as the Media
// page; the body is a list + preview over the unified catalog. Four actions:
//   Insert positive — editor handle insertText (fallback: append via patchNode)
//   Apply all       — inline confirm when the body is non-empty; ONE setNodes
//   Save current    — TemplateForm prefilled from the aimed node
//   New             — TemplateForm blank
// plus Save as template on any picture row (spec §3.5).
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useOptionalToast } from '../../../components/Toast';
import { TemplateForm, type TemplateFormValue } from '../../../components/prompts/TemplateForm';
import { initialTemplateValue } from '../../../components/prompts/SaveAsTemplateDialog';
import { promptText, saveAsTemplate, PROMPT_FORMS, type PromptEntry, type PromptForm, type PromptLang, type PromptSegment } from '../../../services/promptsService';
import { useCanvasScope } from '../smart/canvasScope';
import { useCanvasReadOnly } from '../smart/nodes/useCanvasReadOnly';
import type { PromptNodeData } from '../smart/types';
import { useCanvasCoreStore } from '../store/canvasCoreStore';
import { chipClass } from './libraryChrome';
import { LibraryPromptList } from './LibraryPromptList';
import { LibraryPromptPreview, activeSlide } from './LibraryPromptPreview';
import { useLibraryStore, type LibraryTarget } from './libraryStore';
import { EditorGoneError, getMentionHandle } from './mentionHandles';
import { appendPositive, buildApplyAllPatch, groupChips } from './promptActions';
import { usePromptCatalog } from './usePromptCatalog';

const FORM_LABEL: Record<PromptForm, readonly [string, string]> = {
  template: ['canvas.library.formTemplates', 'Templates'],
  image: ['canvas.library.formImages', 'Images'],
  album: ['canvas.library.formAlbums', 'Albums'],
};

type Sheet = { kind: 'confirm'; entry: PromptEntry } | { kind: 'form'; mode: 'current' | 'new' | 'promote'; entry: PromptEntry | null } | null;

export function LibraryPromptsPage({ target, targetData }: { target: LibraryTarget | null; targetData: PromptNodeData | null }): React.ReactElement {
  const { t } = useTranslation();
  const toast = useOptionalToast();
  const { scopeId } = useCanvasScope();
  const projectId = useCanvasCoreStore((s) => s.projectId);
  const readOnly = useCanvasReadOnly();
  const open = useLibraryStore((s) => s.open);
  const query = useLibraryStore((s) => s.query);
  const segment = useLibraryStore((s) => s.promptSegment);
  const form = useLibraryStore((s) => s.promptForm);
  const lang = useLibraryStore((s) => s.promptLang);
  const focusNonce = useLibraryStore((s) => s.focusNonce);

  const [activeKey, setActiveKey] = useState<string | null>(null);
  const [slideName, setSlideName] = useState<string | null>(null);
  const [group, setGroup] = useState<string | null>(null);
  const [sheet, setSheet] = useState<Sheet>(null);
  const [formValue, setFormValue] = useState<TemplateFormValue | null>(null);
  const [busy, setBusy] = useState(false);
  const searchRef = useRef<HTMLInputElement>(null);

  const catalog = usePromptCatalog({ scopeId, segment, projectId, form, query, enabled: open });
  const items = useMemo(() => {
    const all = catalog.page?.items ?? [];
    return group ? all.filter((e) => e.tags.includes(group)) : all;
  }, [catalog.page, group]);
  const groups = useMemo(() => groupChips(catalog.page?.items ?? []), [catalog.page]);
  const active = useMemo(() => items.find((e) => e.key === activeKey) ?? null, [items, activeKey]);
  useEffect(() => { setSlideName(null); }, [activeKey]);
  useEffect(() => { if (focusNonce) searchRef.current?.focus(); }, [focusNonce]);

  const live = !readOnly && target !== null && target.kind === 'prompt' && targetData !== null;
  const canAct = live;

  // ── the four actions ─────────────────────────────────────────────────────
  const currentText = useCallback(() => {
    if (!active) return null;
    return promptText(activeSlide(active, slideName) ?? active, lang);
  }, [active, slideName, lang]);

  const doInsert = useCallback(() => {
    const text = currentText();
    if (!live || !target || !text?.positive) return;
    const handle = getMentionHandle(target.nodeId);
    try {
      if (!handle) throw new EditorGoneError();
      handle.insertText(text.positive);
    } catch (err) {
      if (!(err instanceof EditorGoneError)) throw err;
      // The card is culled off-viewport: append instead, and say so.
      const body = (targetData?.body ?? '') as string;
      useCanvasCoreStore.getState().patchNode(target.nodeId, { data: { body: appendPositive(body, text.positive) } });
      toast?.addToast(t('canvas.library.insertedAtEnd', 'Added to the end — the card was off screen'), 'info');
    }
  }, [currentText, live, target, targetData, toast, t]);

  const applyNow = useCallback(() => {
    const text = currentText();
    if (!live || !target || !targetData || !text?.positive || !active) return;
    const patch = buildApplyAllPatch({ positive: text.positive, negative: text.negative, params: active.params, node: targetData });
    const { nodes, setNodes } = useCanvasCoreStore.getState();
    // ONE setNodes → one history entry → one ⌘Z brings body, negative and ratio back together.
    setNodes(nodes.map((n) => ((n as { id?: unknown }).id === target.nodeId
      ? { ...(n as Record<string, unknown>), data: { ...((n as { data?: Record<string, unknown> }).data ?? {}), ...patch } }
      : n)));
    setSheet(null);
  }, [currentText, live, target, targetData, active]);

  const doApplyAll = useCallback(() => {
    if (!active) return;
    const body = ((targetData?.body ?? '') as string).trim();
    if (body) setSheet({ kind: 'confirm', entry: active });
    else applyNow();
  }, [active, targetData, applyNow]);

  const openForm = useCallback((mode: 'current' | 'new' | 'promote', entry: PromptEntry | null) => {
    if (mode === 'promote' && entry) {
      setFormValue(initialTemplateValue(entry, undefined, lang));
    } else if (mode === 'current' && targetData) {
      setFormValue({ title: ((targetData.body ?? '') as string).split('\n')[0].slice(0, 40) || 'Prompt', group: '', positive: (targetData.body ?? '') as string, negative: (targetData.negative_body ?? '') as string, exampleIds: [] });
    } else {
      setFormValue({ title: '', group: '', positive: '', negative: '', exampleIds: [] });
    }
    setSheet({ kind: 'form', mode, entry });
  }, [lang, targetData]);

  const submitForm = useCallback(async () => {
    if (!formValue || !formValue.title.trim() || !formValue.positive.trim()) return;
    setBusy(true);
    try {
      const { assetId } = await saveAsTemplate(scopeId, { title: formValue.title, group: formValue.group, positive: formValue.positive, negative: formValue.negative, exampleResourceIds: formValue.exampleIds });
      toast?.addToast(t('canvas.library.savedToMine', 'Saved to Mine'), 'success');
      setSheet(null);
      useLibraryStore.getState().setPromptSegment('mine');
      setActiveKey(`template:${assetId}`);
      catalog.reload();
    } catch (err) {
      console.error('[LibraryPromptsPage] save failed:', err);
      toast?.addToast(t('canvas.library.saveFailed', { code: (err as { code?: string })?.code ?? 'unknown', defaultValue: 'Could not save: {{code}}' }), 'error');
    } finally {
      setBusy(false);
    }
  }, [formValue, scopeId, toast, t, catalog]);

  // ── keyboard (spec §3.4) ─────────────────────────────────────────────────
  const onKeyDown = (e: React.KeyboardEvent) => {
    const editing = (e.target as HTMLElement).closest('input, textarea');
    if (e.key === 'Escape') {
      if (sheet) { e.stopPropagation(); setSheet(null); return; }
      return; // LibraryPanel closes on Escape
    }
    if (editing && e.key !== 'Enter') return;
    if (e.key === '/' && !editing) { e.preventDefault(); searchRef.current?.focus(); return; }
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      const idx = items.findIndex((it) => it.key === activeKey);
      const next = items[Math.min(items.length - 1, Math.max(0, idx + (e.key === 'ArrowDown' ? 1 : -1)))];
      if (next) setActiveKey(next.key);
      return;
    }
    if ((e.key === 'ArrowRight' || e.key === 'ArrowLeft') && active?.slides) {
      e.preventDefault();
      const s = active.slides; const cur = activeSlide(active, slideName);
      const i = s.findIndex((x) => x.name === cur?.name);
      const n = s[Math.min(s.length - 1, Math.max(0, i + (e.key === 'ArrowRight' ? 1 : -1)))];
      if (n) setSlideName(n.name);
      return;
    }
    if (e.key === 'Tab' && !editing) {
      e.preventDefault();
      const order: PromptSegment[] = catalog.counts && catalog.counts.system > 0 ? ['mine', 'project', 'system'] : ['mine', 'project'];
      useLibraryStore.getState().setPromptSegment(order[(order.indexOf(segment) + 1) % order.length]);
      return;
    }
    if (e.key === 'Enter' && !editing) {
      e.preventDefault();
      if (sheet?.kind === 'confirm') applyNow();
      else if (e.shiftKey) doApplyAll();
      else doInsert();
    }
  };

  const consequence = !live
    ? t('canvas.library.promptsNoTarget', 'Select a prompt node to insert or apply')
    : t('canvas.library.promptsConsequence', 'Insert positive adds to your text · Apply all replaces body, negative and params');
  const showSystem = (catalog.counts?.system ?? 0) > 0;
  const fromPictures = (catalog.page?.by_form.image ?? 0) + (catalog.page?.by_form.album ?? 0);

  return (
    <div className="flex min-h-0 flex-1 flex-col" data-testid="library-prompts-page" onKeyDown={onKeyDown} tabIndex={-1}>
      <div className="flex items-center gap-1 px-2 pt-1.5">
        <button type="button" className={chipClass(segment === 'mine')} aria-pressed={segment === 'mine'} onClick={() => useLibraryStore.getState().setPromptSegment('mine')}>{t('canvas.library.segmentMine', 'Mine')}{catalog.counts ? ` ${catalog.counts.mine}` : ''}</button>
        <button type="button" className={chipClass(segment === 'project')} aria-pressed={segment === 'project'} disabled={!projectId} onClick={() => useLibraryStore.getState().setPromptSegment('project')}>{t('canvas.library.segmentProject', 'This project')}{catalog.counts?.project != null ? ` ${catalog.counts.project}` : ''}</button>
        {showSystem && <button type="button" className={chipClass(segment === 'system')} aria-pressed={segment === 'system'} onClick={() => useLibraryStore.getState().setPromptSegment('system')}>{t('canvas.library.segmentSystem', 'System')}</button>}
      </div>
      <div className="flex items-center gap-1.5 border-b border-canvas-line px-2 py-1.5">
        <input ref={searchRef} data-testid="library-search" aria-label={t('canvas.library.searchPrompts', 'Search prompts')} placeholder={t('canvas.library.searchPrompts', 'Search prompts')} value={query}
          onChange={(e) => useLibraryStore.getState().setQuery(e.target.value)} className="nodrag w-full bg-transparent text-[11px] text-canvas-text outline-none placeholder:text-canvas-muted" />
      </div>
      <div data-testid="library-consequence" className="border-b border-canvas-line px-2 py-1 text-[10.5px] text-canvas-muted">{consequence}</div>
      <div className="flex items-center gap-1 overflow-x-auto border-b border-canvas-line px-2 py-1.5">
        <button type="button" className={chipClass(form === null)} onClick={() => useLibraryStore.getState().setPromptForm(null)}>{t('canvas.library.formAll', 'All')}</button>
        {PROMPT_FORMS.map((f) => <button key={f} type="button" className={chipClass(form === f)} onClick={() => useLibraryStore.getState().setPromptForm(form === f ? null : f)}>{t(FORM_LABEL[f][0], FORM_LABEL[f][1])}</button>)}
        {groups.length > 0 && <span className="mx-1 h-3.5 w-px bg-canvas-line" />}
        {groups.map((g) => <button key={g} type="button" className={chipClass(group === g)} onClick={() => setGroup(group === g ? null : g)}>{g}</button>)}
      </div>

      <div className="grid min-h-0 flex-1 grid-cols-[250px_1fr]">
        <div className="min-w-0 border-r border-canvas-line">
          <LibraryPromptList items={items} activeKey={activeKey} onActivate={setActiveKey} lang={lang} loading={catalog.loading} error={catalog.error} onRetry={catalog.reload}
            emptyLabel={query.trim() || form || group ? t('canvas.library.promptsNoMatch', 'No prompts match') : t('canvas.library.promptsEmpty', 'No prompts yet — upload a picture that carries generation metadata, run a caption, or save the aimed node with Save current.')} />
        </div>
        <div className="flex min-w-0 flex-col">
          {sheet?.kind === 'form' && formValue ? (
            <div className="flex min-h-0 flex-1 flex-col">
              <div className="min-h-0 flex-1 overflow-y-auto px-2.5 py-2">
                <TemplateForm value={formValue} onChange={setFormValue} groups={groups}
                  examples={sheet.entry && sheet.entry.form !== 'template' ? [{ id: sheet.entry.source.id, url: sheet.entry.thumbs[0]?.url ?? null }] : []} disabled={busy} />
              </div>
              <div className="flex items-center gap-1.5 border-t border-canvas-line px-2.5 py-2">
                <button type="button" className="nodrag rounded-lg bg-[var(--accent-text)] px-2.5 py-1 text-[11px] font-medium text-white disabled:opacity-40" disabled={busy || !formValue.title.trim() || !formValue.positive.trim()} onClick={() => void submitForm()}>{t('canvas.library.saveToMine', 'Save to Mine')}</button>
                <button type="button" className="nodrag rounded-lg border border-canvas-line px-2.5 py-1 text-[11px]" onClick={() => setSheet(null)}>{t('canvas.library.cancel', 'Cancel')}</button>
                <span className="flex-1" />
                <span className="text-[10.5px] text-canvas-muted">{t('canvas.library.filesStay', 'Pictures stay where they are')}</span>
              </div>
            </div>
          ) : (
            <>
              <LibraryPromptPreview entry={active} lang={lang} onLangChange={(l: PromptLang) => useLibraryStore.getState().setPromptLang(l)} slideName={slideName} onSlideChange={setSlideName}
                canAct={canAct} actHint={t('canvas.library.pickPromptNodeFirst', 'Pick a prompt node first')} onInsert={doInsert} onApplyAll={doApplyAll} onSaveAsTemplate={() => active && openForm('promote', active)} />
              {sheet?.kind === 'confirm' && (
                <div data-testid="library-prompt-confirm" className="mx-2.5 mb-2 rounded-lg border border-warn bg-warn-soft px-2.5 py-2 text-[11px] text-canvas-text">
                  <b>{t('canvas.library.replaceBodyQ', { title: target?.title ?? '', defaultValue: 'Replace the body of 《{{title}}》?' })}</b><br />
                  {t('canvas.library.replaceBodyNote', 'Its text, negative and params are overwritten. Undo with ⌘Z.')}
                  <div className="mt-1.5 flex gap-1.5">
                    <button type="button" className="nodrag rounded-lg bg-danger px-2.5 py-1 text-[11px] font-medium text-white" onClick={applyNow}>{t('canvas.library.replace', 'Replace')}</button>
                    <button type="button" className="nodrag rounded-lg border border-canvas-line px-2.5 py-1 text-[11px]" onClick={() => setSheet(null)}>{t('canvas.library.cancel', 'Cancel')}</button>
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      </div>

      <div className="flex items-center gap-1.5 border-t border-canvas-line px-2 py-1.5 text-[10.5px] text-canvas-muted">
        <button type="button" className="nodrag rounded-lg border border-transparent px-2 py-0.5 text-[11px] text-canvas-text disabled:opacity-40" disabled={!live || !!sheet} onClick={() => openForm('current', null)}>{t('canvas.library.saveCurrent', 'Save current…')}</button>
        <button type="button" className="nodrag rounded-lg border border-transparent px-2 py-0.5 text-[11px] text-canvas-text disabled:opacity-40" disabled={readOnly || !!sheet} onClick={() => openForm('new', null)}>{t('canvas.library.newTemplate', 'New…')}</button>
        <span className="flex-1" />
        {catalog.page && t('canvas.library.promptsFooter', { count: catalog.page.total, pictures: fromPictures, defaultValue: '{{count}} prompts · {{pictures}} from pictures' })}
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Mount it in `LibraryPanel.tsx`**

Replace the stub block (lines 186–193) with:

```tsx
      {page === 'prompts' ? (
        <LibraryPromptsPage target={target} targetData={targetData} />
      ) : (
        <LibraryMediaPage target={target} targetData={targetData} />
      )}
```

and add `import { LibraryPromptsPage } from './LibraryPromptsPage';`. Delete the `canvas.library.promptsLater` key from both locale files. The panel's `onKeyDown` already lets `Escape` through to `close()` — the page stops propagation only while a sheet is open (Step 3), so Escape cancels a form/confirm first and closes the panel on the next press, exactly as spec §3.4 says.

- [ ] **Step 5: Add locale keys**

Under `canvas.library`: `formTemplates` Templates/模板, `formImages` Images/单图, `formAlbums` Albums/图集, `formAll` All/全部, `segmentMine` Mine/我的, `segmentProject` This project/本项目, `segmentSystem` System/预设, `searchPrompts` Search prompts/搜索提示词, `promptsNoTarget` Select a prompt node to insert or apply/先选一个提示词节点再插入或应用, `promptsConsequence` Insert positive adds to your text · Apply all replaces body, negative and params/插入正向是追加到正文 · 全量应用会替换正文、负向和参数, `promptsNoMatch` No prompts match/没有匹配的提示词, `promptsEmpty` No prompts yet — upload a picture that carries generation metadata, run a caption, or save the aimed node with Save current./还没有提示词 — 上传带生成元数据的图片、跑一次打标，或用「保存当前」存下瞄准的节点。, `insertedAtEnd` Added to the end — the card was off screen/已追加到末尾 — 卡片不在视口内, `savedToMine` Saved to Mine/已保存到 Mine, `saveFailed` Could not save: {{code}}/保存失败：{{code}}, `saveToMine` Save to Mine/保存到 Mine, `cancel` Cancel/取消, `filesStay` Pictures stay where they are/图片留在原处, `pickPromptNodeFirst` Pick a prompt node first/先选一个提示词节点, `replaceBodyQ` Replace the body of 《{{title}}》?/替换《{{title}}》的正文？, `replaceBodyNote` Its text, negative and params are overwritten. Undo with ⌘Z./它的正文、负向和参数会被覆盖。⌘Z 可撤销。, `replace` Replace/替换, `saveCurrent` Save current…/保存当前…, `newTemplate` New…/新建…, `promptsFooter` {{count}} prompts · {{pictures}} from pictures/{{count}} 条提示词 · {{pictures}} 条来自图片.

- [ ] **Step 6: Update `LibraryPanel.test.tsx`**

Change the case that asserts `library-prompts-stub` to mock `./LibraryPromptsPage` (`vi.mock('./LibraryPromptsPage', () => ({ LibraryPromptsPage: () => <div data-testid="library-prompts-page" /> }))`) and assert `screen.getByTestId('library-prompts-page')` after `openPanel({ page: 'prompts' })`. Add one case: `openPanel({ page: 'prompts' })` sets `useLibraryStore.getState().width` to `600` and the panel's inline style width follows.

- [ ] **Step 7: Run tests + guards + typecheck**

Run: `cd frontend && npx vitest run features/canvas-core/library && npx tsc --noEmit -p . 2>&1 | grep -E "canvas-core/library" ; true`
Expected: all pass; the naming guard stays green (no user-visible string here contains "library"; the segment labels do not).

- [ ] **Step 8: Commit**

```bash
git add frontend/features/canvas-core/library/LibraryPromptsPage.tsx frontend/features/canvas-core/library/LibraryPromptsPage.test.tsx frontend/features/canvas-core/library/LibraryPanel.tsx frontend/features/canvas-core/library/LibraryPanel.test.tsx frontend/public/locales/en.json frontend/public/locales/zh.json
git commit -m "feat(canvas): Library Prompts page — insert / apply all / save current / new, per-slide albums, keyboard"
```

### Task 17 (C5): entry points — node bookshelf, `⌘K`, attached composer

**Files:**
- Modify: `frontend/features/canvas-core/smart/nodes/PromptNodeView.tsx` (`libraryOpen` state :167, `:491`, bookshelf button :620-630, picker mount :937-939, imports :54-55, `handlePickAsset`), `frontend/features/canvas-core/palette/commands.ts:74+`, `frontend/features/canvas-core/smart/nodes/AttachedComposerPanel.tsx:18, 52, 236-252`, `frontend/features/canvas-core/library/libraryNaming.test.ts:22` (`ALLOWED` gains `palette/commands.ts`)
- Test: `frontend/features/canvas-core/smart/nodes/PromptNodeView.library.test.tsx` (remove the three `AssetPromptPicker` cases; add the bookshelf case), `frontend/features/canvas-core/palette/commands.test.ts` (extend or create), `frontend/features/canvas-core/smart/nodes/AttachedComposerPanel.test.tsx` (extend)

- [ ] **Step 1: Write the failing tests**

In `PromptNodeView.library.test.tsx`, delete the `describe` block holding `'opens AssetPromptPicker on click'`, `'applies the picked asset body/negative…'`, `'carries a video mediaKind…'`, `'falls back to the cover URL…'` and the `AssetPromptPicker`/`mediaImport` mocks they needed; add to the remaining describe:

```tsx
  it('the bookshelf button aims the panel at THIS node on the Prompts page', () => {
    setNode();
    const openPanel = vi.spyOn(useLibraryStore.getState(), 'openPanel');
    renderNode(BASE_DATA);
    fireEvent.click(screen.getByRole('button', { name: 'Prompt Templates' }));
    expect(openPanel).toHaveBeenCalledWith(expect.objectContaining({ page: 'prompts', focusSearch: true, target: { nodeId: 'p1', kind: 'prompt', title: 'pos' } }));
    expect(screen.queryByTestId('asset-prompt-picker')).toBeNull();
  });
```

Create/extend `palette/commands.test.ts`:

```ts
import { describe, expect, it, vi } from 'vitest';
import { buildCanvasCommands } from './commands';
import { useLibraryStore } from '../library/libraryStore';

describe('library-add command', () => {
  it('opens the panel on its remembered page and stays enabled read-only', () => {
    const cmd = buildCanvasCommands().find((c) => c.id === 'library-add')!;
    expect(cmd.title).toBe('Add from library…');
    const openPanel = vi.spyOn(useLibraryStore.getState(), 'openPanel');
    cmd.run();
    expect(openPanel).toHaveBeenCalledWith();
    expect(cmd.enabled?.() ?? true).toBe(true);
  });
});
```

Extend `AttachedComposerPanel.test.tsx` (match its existing render helper):

```tsx
  it('the bookshelf opens the Library panel on Prompts with no target', () => {
    const openPanel = vi.spyOn(useLibraryStore.getState(), 'openPanel');
    renderPanel();
    fireEvent.click(screen.getByTestId('composer-library'));
    expect(openPanel).toHaveBeenCalledWith({ page: 'prompts' });
    expect(screen.queryByTestId('asset-prompt-picker')).toBeNull();
  });
```

- [ ] **Step 2: Run to verify failure**

Run: `cd frontend && npx vitest run features/canvas-core/smart/nodes/PromptNodeView.library.test.tsx features/canvas-core/palette features/canvas-core/smart/nodes/AttachedComposerPanel.test.tsx`
Expected: new cases fail.

- [ ] **Step 3: Rewire `PromptNodeView.tsx`**

- Remove `import { AssetPromptPicker } from './AssetPromptPicker';` and `import { buildPromptAssetLoad } from '../loadPromptAsset';`.
- Remove `const [libraryOpen, setLibraryOpen] = useState(false);` (:167), the `setLibraryOpen(false)` at :491, the whole `handlePickAsset` callback (the block that calls `buildPromptAssetLoad`, ~:483-497) and the `{libraryOpen && (<AssetPromptPicker …/>)}` mount (:937-939).
- Change the bookshelf button's `onClick` (:626) to:
  ```tsx
            onClick={() => {
              const title = (body ?? '').split('\n')[0].slice(0, 40) || 'Prompt';
              useLibraryStore.getState().openPanel({ page: 'prompts', target: { nodeId: id, kind: 'prompt', title }, focusSearch: true });
            }}
  ```
  Keep its `aria-label={t('canvas.library.promptTemplates', 'Prompt Templates')}`.

- [ ] **Step 4: Add the `⌘K` command**

In `commands.ts`, import `useLibraryStore` from `'../library/libraryStore'` and append to the array `buildCanvasCommands` returns (after the store actions, before the return):

```ts
    {
      id: 'library-add',
      title: 'Add from library…',
      hint: 'L',
      run() {
        // The panel remembers its page; a read action, so it stays enabled in
        // a read-only session (the panel itself withholds every write there).
        useLibraryStore.getState().openPanel();
      },
    },
```

Add `path.join(ROOT, 'palette', 'commands.ts')` to `ALLOWED` in `libraryNaming.test.ts` with the comment `// ⌘K row naming the one Library the canvas has.`

- [ ] **Step 5: Rewire `AttachedComposerPanel.tsx`**

- Remove `import { AssetPromptPicker } from './AssetPromptPicker';` and `const [libraryOpen, setLibraryOpen] = useState(false);`.
- Replace the `{!libraryOpen ? (<button …/>) : (<AssetPromptPicker …/>)}` block (:236-252) with just the button, `onClick={() => useLibraryStore.getState().openPanel({ page: 'prompts' })}` (import `useLibraryStore` from `'../../library/libraryStore'`). Keep `data-testid="composer-library"` and the aria-label. Leave `MentionImageGrid` exactly as it is (spec §3.6 现状修正).

- [ ] **Step 6: Run tests + guards**

Run: `cd frontend && npx vitest run features/canvas-core/smart/nodes features/canvas-core/palette features/canvas-core/library/libraryNaming.test.ts`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add frontend/features/canvas-core/smart/nodes/PromptNodeView.tsx frontend/features/canvas-core/smart/nodes/PromptNodeView.library.test.tsx frontend/features/canvas-core/palette/commands.ts frontend/features/canvas-core/palette/commands.test.ts frontend/features/canvas-core/smart/nodes/AttachedComposerPanel.tsx frontend/features/canvas-core/smart/nodes/AttachedComposerPanel.test.tsx frontend/features/canvas-core/library/libraryNaming.test.ts
git commit -m "feat(canvas): bookshelf buttons and ⌘K open the Library Prompts page"
```

### Task 18 (C6): delete the old picker and its data path; extend the removals guard

**Files:**
- Delete: `frontend/features/canvas-core/smart/nodes/AssetPromptPicker.tsx`, `…/AssetPromptPicker.test.tsx`, `frontend/features/canvas-core/smart/loadPromptAsset.ts`, `…/loadPromptAsset.test.ts`, `frontend/services/resourceService.promptAssets.test.ts`
- Modify: `frontend/services/resourceService.ts:230-270` (remove `PromptAsset` + `fetchPromptAssets`), `frontend/features/canvas-core/library/libraryRemovals.test.ts` (`GONE` += the two stems), locales (delete `canvas.assetPromptPicker` object)
- Test: `frontend/features/canvas-core/library/libraryRemovals.test.ts`

- [ ] **Step 1: Extend the guard first (it must go red)**

In `libraryRemovals.test.ts`, replace the header comment's "`AssetPromptPicker` and `MentionImageGrid` are NOT here…" paragraph with: "`AssetPromptPicker` and `loadPromptAsset` joined the list in P3 (spec 2026-09-05 §3.7). `MentionImageGrid` stays mounted in the attached composer on purpose (spec §3.6)." and add to `GONE`:

```ts
  'smart/nodes/AssetPromptPicker.tsx',
  'smart/loadPromptAsset.ts',
```

Also add a second assertion in the same file:

```ts
it('nothing in frontend/ still calls the browser-side prompt query', () => {
  const svc = fs.readFileSync(path.resolve(ROOT, '../../services/resourceService.ts'), 'utf8');
  expect(svc).not.toMatch(/fetchPromptAssets|interface PromptAsset\b/);
});
```

Run: `cd frontend && npx vitest run features/canvas-core/library/libraryRemovals.test.ts` → Expected: FAIL (files exist, function exists).

- [ ] **Step 2: Delete and prune**

```bash
cd frontend
git rm features/canvas-core/smart/nodes/AssetPromptPicker.tsx features/canvas-core/smart/nodes/AssetPromptPicker.test.tsx features/canvas-core/smart/loadPromptAsset.ts features/canvas-core/smart/loadPromptAsset.test.ts services/resourceService.promptAssets.test.ts
```

In `resourceService.ts` remove `export interface PromptAsset {…}` and `export async function fetchPromptAssets(…) {…}` (lines ~230–270). Run `grep -rn "PromptAsset\b\|fetchPromptAssets\|promptTriggerTags" frontend --include='*.ts' --include='*.tsx' | grep -v node_modules` — `promptTriggerTags` has other importers (PromptBadge, ResourcePromptSection) and STAYS; anything else the grep lists must be fixed. Remove the `"assetPromptPicker": {…}` object under `canvas` in both locale files.

- [ ] **Step 3: Run the full frontend suite + build**

Run: `cd frontend && npx vitest run 2>&1 | tail -5 && npm run build 2>&1 | tail -3`
Expected: `Tests  N passed` (no failures) and a successful build. Per memory `reference-vitest-reporter-basic-runs-zero-tests`, only the `Tests N passed` line counts.

- [ ] **Step 4: Commit**

```bash
git add -u frontend/services/resourceService.ts frontend/features/canvas-core/library/libraryRemovals.test.ts frontend/public/locales/en.json frontend/public/locales/zh.json
git commit -m "chore(canvas): delete AssetPromptPicker / loadPromptAsset / fetchPromptAssets — the panel's Prompts page replaces them"
```

### Task 19 (C7): docs + real-stack acceptance

**Files:**
- Modify: `CLAUDE.md` (Asset Library section: one bullet), `docs/superpowers/plans/2026-09-03-canvas-library-panel-p1p2.md` (P3 pointer), memory is the controller's job

- [ ] **Step 1: Docs**

Add to `CLAUDE.md` under "Asset Library (P0 数据层)" after the P4 bullet:

```
- **统一提示词库**（P3, 2026-09）：提示词 = 模板资产 ∪ 带 `gen_prompt` 的图片 ∪ 带 `slide_prompts` 的图集，后端 `GET /api/v1/prompts` 归一成 PromptEntry（`app/services/prompts/`），资源库「提示词」tab 与画布面板 Prompts 页都只读它，浏览器不再直查提示词。`resources.prompt_origin`（mig 453）记录正向文字的最后写入方 typed / extracted / captioned，六个写入方都要 `stamp_origin`（`tests/services/prompts/test_origin_wiring.py` 钉住）。图集逐张插入；「存为模板」把图挂 `examples` 槽、文件不搬。spec：`docs/superpowers/specs/2026-09-05-unified-prompts-library-design.md`。
```

In the P1/P2 plan's header "Plan-time rulings" area add one line: "P3 shipped as `docs/superpowers/plans/2026-09-05-unified-prompts-p3.md` (unified prompts; Prompts page no longer a stub)."

- [ ] **Step 2: Acceptance on production (spec §4, items 5–12)** with the debug account after deploy; record results in the PR description:
  1. Aim a node whose body is `draft @`; Insert positive → body is `draft @` + text, `@` intact, `manual_refs` unchanged.
  2. Apply all on a non-empty body → confirm appears; Replace → body/negative replaced; `⌘Z` once restores both.
  3. Album row: select slide 002 → Insert inserts 002's text; `→` then Insert inserts 003's.
  4. Save as template from an image → new row in Mine (form template, that thumbnail); asset library Prompts count +1; `asset_files.slot='examples'` row exists.
  5. `grep -rn "AssetPromptPicker\|fetchPromptAssets\|loadPromptAsset" frontend --include='*.ts*'` lists only `libraryRemovals.test.ts`.
  6. With zero presets the System segment is absent; insert one `is_system_preset=true` prompt asset in staging DB → it appears.
  7. `npm run e2e:prod` passes.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md docs/superpowers/plans/2026-09-03-canvas-library-panel-p1p2.md
git commit -m "docs: unified prompts library (P3) — CLAUDE.md pointer, P1/P2 plan hand-off"
```

---

## Self-review notes (controller, 2026-09-05)

- **Spec coverage**: §3.1 read model → A4–A6; §3.2 origin → A1–A3; §3.3 shelf → B3–B4; §3.4 panel page → C1–C4; §3.5 promotion → B1 (`saveAsTemplate`), B4 (dialog), C4 (inline form); §3.6 entry points → C5; §3.7 deletions → C6; §4 acceptance → A7/B5/C7. Not covered on purpose (spec non-goals): storyboard/classic mount, per-slide origin, `MentionImageGrid` replacement.
- **Type consistency**: `PromptEntry` field names are identical in `schemas/prompts.py` and `promptsService.ts`; `MentionInserters.insertText` is added in C1 before C4 uses it; `initialTemplateValue` is exported from B4's dialog and imported by C4; `PANEL_WIDTH` lives in the store (C1) and the panel reads `width` unchanged.
- **Known soft spots for reviewers**: (1) `catalog_service.list` filters in Python after counting — acceptable at today's corpus size, flagged in its docstring; (2) `PromptsShelf` sends `segment=project` whenever a project filter is set, so "Mine ∩ project" is not a state — the segments are exclusive by design; (3) the panel's Prompts page test relies on `historyPast` being a public store field (it is, per `canvasCoreStore.ts:283`).
