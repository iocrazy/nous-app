# Asset Library P1 — Generated Inbox Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the "Project Assets" tree (Chat Uploads + Generations + per-canvas groups) with a flat **Generated** inbox — state tabs, source/project/type filters, `Save` / `Save as Asset…` / delete / batch / clean-up — backed by `/api/v1/generated` endpoints, a `SaveAsAssetDialog` that reaches the P0 asset library, and a migration that folds legacy chat-upload temp files into the same inbox.

**Architecture:** `generated_media` already receives every producer (canvas runs, agent tools, storyboard shots, cover studio, chat attachments) and P0 gave it `review_state` + `source_asset_id`. P1 adds (1) a read model over it — list by scope + state + origin with a computed `source` descriptor — and (2) two state transitions: `save` (= existing promote, now stamping `saved`) and `save-as-asset` (promote + `asset_files` attach in one `unit_of_work()`, stamping `in_assets`). Chat uploads that still land in the per-scope `temp` folder are registered as `chat_upload` rows (`saved`, `promoted_resource_id = self`) by a dry-run-first backfill plus a write-path hook, after which the temp folder is an ordinary folder. Frontend: a new `GeneratedView` + `SaveAsAssetDialog`; `ProjectAssetsTree`, `GenerationsGrid`, `TempResourceActions` and the temp state in `ResourcesContext` are deleted.

**Tech Stack:** FastAPI + SQLAlchemy 2 async (ORM only), DBOS backfill workflow, Pydantic v2; React 19 + Vite + vitest/RTL + Playwright; i18next; Tailwind semantic tokens.

**Spec:** `docs/superpowers/specs/2026-08-28-asset-library-loadout-design.md` — §2 decisions 6–8, §3.7 (`generated_media` ext + Chat Uploads), §5.2 (Generated API), §6.1 (resource library UI), §7 invariants, §9 P1 row. Mockup screens 1–2 and 5: https://claude.ai/code/artifact/3db4a9bf-907d-40c5-bd21-40694107efbb

## Global Constraints

- Python 3.13 via `cd backend && uv run …`; Node 22 via `cd frontend && npm …`.
- **ORM only** — no new `text()` SQL in app code. Sessions via `read_scope` / `write_scope`; multi-write operations inside `app.db.session.unit_of_work()` (`write_scope` joins the ambient UoW).
- **Scope = `?scope_id=` (a `teams.id`), gate = team membership** — same as `/assets` (P0 `assets_router._gate`). ⚠️ The existing `/generated-media` router resolves the caller's **personal** team only (`_scope(auth)`); the new `/generated` router must NOT inherit that — every route takes `scope_id`.
- Snowflake ids are **JSON strings** on the wire; request ids validated with `SnowflakeId` from `app/schemas/assets.py`; every failure is the `{success:false, error:{code, detail}}` envelope used by `assets_router` (`_ok` / `_err` / `AssetError`), never a bare 500.
- `generated_media` reads go through backend endpoints only (service-role RLS); the frontend never queries it via PostgREST.
- **Edge mocks use real wire shapes** (CLAUDE.md 2026-08-12): `generated_media.id`/`scope_id`/`canvas_id` are strings after `_normalize`, `resources` ids from `/resources/*` are strings, but any Playwright `page.route` fulfilling a *backend HTTP body* must copy the exact JSON the router returns (write the fixture from a real response captured in a test, not from memory).
- **UI text is English**; copy through i18n keys (`generated.*` namespace), en + zh both updated in the same task.
- Lint: backend `black` + `isort` + `flake8`; frontend `npm run lint`. Commit only files the task names; never `git add -A`.
- **Do not touch `_reject_execution_until_p3()`** in the P0 planner workflow; do not add UI for the assets library itself (P2).

---

## File Structure

| Path | Responsibility |
|---|---|
| `backend/app/schemas/generated.py` | Request/response models for `/generated` (`GeneratedItem`, `GeneratedSource`, `SaveAsAssetRequest`, `BatchRequest`, `CleanupRequest`, `CountsResponse`) |
| `backend/app/repositories/generated_media_repository.py` | + `list_inbox()` (state/origin/project/media_kind/model/since/cursor), `set_review_state()`, `count_by_state()`, `list_older_unreviewed()`; `mark_promoted()` stamps `saved` |
| `backend/app/services/library/generated_source.py` | Pure `describe_source(row, canvas_name_by_id) -> GeneratedSource` (label + deep_link) |
| `backend/app/services/library/generated_inbox_service.py` | `GeneratedInboxService`: list (+source enrichment), save, save_as_asset (UoW), batch, cleanup (dry-run) — typed `AssetError`s |
| `backend/app/api/generated_router.py` + `app/api/__init__.py` | `/generated` routes |
| `backend/app/services/library/chat_upload.py` | `save_chat_temp_upload` also registers a `chat_upload` generated_media row (`saved`, self-promoted) |
| `backend/app/workflows/backfill_generated_inbox.py` + `app/api/admin/backfill_router.py` | Backfill: temp-folder resources → `chat_upload` rows; promoted rows with `asset_files` → `in_assets` |
| `frontend/services/generatedService.ts` | Typed client for `/generated` |
| `frontend/components/resources/generated/GeneratedView.tsx` | Tabs + filters + grid + batch bar + cleanup dialog |
| `frontend/components/resources/generated/GeneratedCard.tsx` | One card (thumb, state label, title, source line, model·date, actions) |
| `frontend/components/resources/generated/CleanupDialog.tsx` | Dry-run then confirm |
| `frontend/components/assets/SaveAsAssetDialog.tsx` + `frontend/components/assets/assetSlots.ts` + `frontend/services/assetsService.ts` | The four-entry dialog (P1 wires the Generated entry only) |
| `frontend/components/ResourcesSidebar.tsx`, `contexts/ResourcesContext.tsx`, `components/ResourcesViewInner.tsx`, `router.tsx` | Sidebar item + route + view switch; temp/project-assets state removed |
| `frontend/public/locales/{en,zh}.json` | `generated.*`, `saveAsAsset.*` |
| Deleted: `components/resources/ProjectAssetsTree*.tsx`, `components/resources/GenerationsGrid*.tsx`, `components/TempResourceActions*.tsx`, `services/projectAssetsService.ts` (keep `fetchResourceCanvasRefs` → move to `resourceService.ts`) | |

---

### Task 1: Repository — inbox list, state transitions, counts

**Files:**
- Modify: `backend/app/repositories/generated_media_repository.py`
- Test: `backend/tests/repositories/test_generated_media_inbox_repo.py`

**Interfaces:**
- Produces on `GeneratedMediaRepository`:
  - `async def list_inbox(self, scope_id: int, *, state: str | None, origin_kinds: list[str] | None, project_id: int | None, media_kind: str | None, model: str | None, since: datetime | None, cursor: str | None, limit: int) -> dict` — `{"items": [row…], "next_cursor"}`; `state=None` means all except `deleted`; `project_id` filters through `canvases.project_id` (rows with `canvas_id`) — rows without a canvas never match a project filter (documented).
  - `async def set_review_state(self, gen_id: int, scope_id: int, state: str) -> dict | None`
  - `async def count_by_state(self, scope_id: int) -> dict[str, int]` — always returns all four keys.
  - `async def list_older_unreviewed(self, scope_id: int, older_than: datetime, limit: int) -> list[dict]`
  - `mark_promoted()` now also sets `review_state='saved'` **only if** the current state is `unreviewed` (never downgrade `in_assets`).
- Pure helpers exported for tests: `_inbox_filters(...)` returning the list of SQLAlchemy criteria (so filters can be asserted without a DB).

- [ ] **Step 1: Write failing tests (pure — compiled SQL inspection)**

```python
# backend/tests/repositories/test_generated_media_inbox_repo.py
import datetime as dt

from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from app.models import GeneratedMedia
from app.repositories.generated_media_repository import (
    GeneratedMediaRepository,
    _inbox_filters,
)


def _sql(criteria):
    stmt = select(GeneratedMedia.id).where(*criteria)
    return str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


def test_default_state_excludes_deleted_only():
    sql = _sql(_inbox_filters(scope_id=7, state=None, origin_kinds=None, project_id=None,
                              media_kind=None, model=None, since=None))
    assert "review_state != 'deleted'" in sql and "scope_id = 7" in sql


def test_state_and_origin_filters():
    sql = _sql(_inbox_filters(scope_id=7, state="unreviewed", origin_kinds=["canvas_run", "shot_generate"],
                              project_id=None, media_kind="image", model="seedream-4", since=None))
    assert "review_state = 'unreviewed'" in sql
    assert "origin_kind IN ('canvas_run', 'shot_generate')" in sql
    assert "media_kind = 'image'" in sql and "model = 'seedream-4'" in sql


def test_project_filter_goes_through_canvases():
    sql = _sql(_inbox_filters(scope_id=7, state=None, origin_kinds=None, project_id=55,
                              media_kind=None, model=None, since=None))
    assert "canvases" in sql and "project_id = 55" in sql


def test_since_filter():
    since = dt.datetime(2026, 8, 1, tzinfo=dt.timezone.utc)
    sql = _sql(_inbox_filters(scope_id=7, state=None, origin_kinds=None, project_id=None,
                              media_kind=None, model=None, since=since))
    assert "created_at >= '2026-08-01" in sql


def test_repo_exposes_new_methods():
    repo = GeneratedMediaRepository()
    for name in ("list_inbox", "set_review_state", "count_by_state", "list_older_unreviewed"):
        assert callable(getattr(repo, name))
```

- [ ] **Step 2: Run to verify fail**

Run: `cd backend && uv run pytest tests/repositories/test_generated_media_inbox_repo.py -v`
Expected: FAIL `ImportError: cannot import name '_inbox_filters'`.

- [ ] **Step 3: Implement**

Add to `generated_media_repository.py` (imports: `from app.models import Canvases`, `from sqlalchemy import and_, case, exists, or_`):

```python
def _inbox_filters(
    *,
    scope_id: int,
    state: Optional[str],
    origin_kinds: Optional[list[str]],
    project_id: Optional[int],
    media_kind: Optional[str],
    model: Optional[str],
    since: Optional[datetime.datetime],
) -> list:
    """Criteria for the Generated inbox list. Pure so tests can compile them.

    state=None → every state except 'deleted'. project_id is resolved through
    canvases.project_id; rows without a canvas_id (chat uploads, agent runs)
    never match a project filter — the inbox says so in its empty state.
    """
    crit = [GeneratedMedia.scope_id == int(scope_id)]
    if state:
        crit.append(GeneratedMedia.review_state == state)
    else:
        crit.append(GeneratedMedia.review_state != "deleted")
    if origin_kinds:
        crit.append(GeneratedMedia.origin_kind.in_(list(origin_kinds)))
    if project_id is not None:
        crit.append(
            GeneratedMedia.canvas_id.in_(
                select(Canvases.id).where(Canvases.project_id == int(project_id))
            )
        )
    if media_kind:
        crit.append(GeneratedMedia.media_kind == media_kind)
    if model:
        crit.append(GeneratedMedia.model == model)
    if since is not None:
        crit.append(GeneratedMedia.created_at >= since)
    return crit
```

and the methods on the class:

```python
    async def list_inbox(
        self,
        scope_id: int,
        *,
        state: Optional[str] = None,
        origin_kinds: Optional[list[str]] = None,
        project_id: Optional[int] = None,
        media_kind: Optional[str] = None,
        model: Optional[str] = None,
        since: Optional[datetime.datetime] = None,
        cursor: Optional[str] = None,
        limit: int = 60,
    ) -> dict:
        limit = max(1, min(int(limit), 200))
        stmt = select(*_GM_COLS).where(
            *_inbox_filters(
                scope_id=scope_id, state=state, origin_kinds=origin_kinds,
                project_id=project_id, media_kind=media_kind, model=model, since=since,
            )
        )
        decoded = _decode_cursor(cursor)
        if decoded:
            c_ts, c_id = decoded
            stmt = stmt.where(
                tuple_(GeneratedMedia.created_at, GeneratedMedia.id)
                < tuple_(_cursor_ts(c_ts), c_id)
            )
        stmt = stmt.order_by(
            GeneratedMedia.created_at.desc(), GeneratedMedia.id.desc()
        ).limit(limit + 1)
        async with read_scope() as session:
            rows = [dict(m) for m in (await session.execute(stmt)).mappings().all()]
        next_cursor = None
        if len(rows) > limit:
            last = rows[limit - 1]
            next_cursor = _encode_cursor(str(last["created_at"]), int(last["id"]))
            rows = rows[:limit]
        return {"items": [_normalize(r) for r in rows], "next_cursor": next_cursor}

    async def set_review_state(
        self, gen_id: int, scope_id: int, state: str
    ) -> Optional[dict]:
        async with write_scope() as session:
            row = (
                await session.execute(
                    sa_update(GeneratedMedia)
                    .where(GeneratedMedia.id == int(gen_id))
                    .where(GeneratedMedia.scope_id == int(scope_id))
                    .values(review_state=state)
                    .returning(*_GM_COLS)
                )
            ).mappings().first()
        return _normalize(dict(row)) if row else None

    async def count_by_state(self, scope_id: int) -> dict[str, int]:
        stmt = (
            select(GeneratedMedia.review_state, func.count())
            .where(GeneratedMedia.scope_id == int(scope_id))
            .group_by(GeneratedMedia.review_state)
        )
        out = {"unreviewed": 0, "saved": 0, "in_assets": 0, "deleted": 0}
        async with read_scope() as session:
            for state, n in (await session.execute(stmt)).all():
                out[state] = int(n)
        return out

    async def list_older_unreviewed(
        self, scope_id: int, older_than: datetime.datetime, limit: int = 500
    ) -> list[dict]:
        stmt = (
            select(*_GM_COLS)
            .where(GeneratedMedia.scope_id == int(scope_id))
            .where(GeneratedMedia.review_state == "unreviewed")
            .where(GeneratedMedia.created_at < older_than)
            .order_by(GeneratedMedia.created_at.asc())
            .limit(max(1, min(int(limit), 2000)))
        )
        async with read_scope() as session:
            rows = [dict(m) for m in (await session.execute(stmt)).mappings().all()]
        return [_normalize(r) for r in rows]
```

In `mark_promoted`, change `.values(promoted_resource_id=resource_id)` to:

```python
                .values(
                    promoted_resource_id=resource_id,
                    review_state=case(
                        (GeneratedMedia.review_state == "unreviewed", "saved"),
                        else_=GeneratedMedia.review_state,
                    ),
                )
```

- [ ] **Step 4: Run tests**

Run: `cd backend && uv run pytest tests/repositories/test_generated_media_inbox_repo.py tests/test_generated_media_router.py tests/test_generated_media_promote_route.py -v`
Expected: all PASS (existing promote tests still green — they mock the repo or assert `promoted_resource_id` only).

- [ ] **Step 5: Commit**

```bash
cd backend && uv run black app/repositories/generated_media_repository.py tests/repositories/test_generated_media_inbox_repo.py && uv run isort app/repositories/generated_media_repository.py tests/repositories/test_generated_media_inbox_repo.py
git add backend/app/repositories/generated_media_repository.py backend/tests/repositories/test_generated_media_inbox_repo.py
git commit -m "feat(generated): inbox list/state/count queries; promote stamps saved"
```

---

### Task 2: Source descriptor (pure)

**Files:**
- Create: `backend/app/services/library/generated_source.py`
- Test: `backend/tests/services/library/test_generated_source.py`

**Interfaces:**
- Produces `describe_source(row: dict, *, canvas_names: dict[str, str], team_id: str) -> dict` returning `{"kind": <origin_kind>, "label": str, "canvas_id": str|None, "node_id": str|None, "shot_id": str|None, "conversation_id": str|None, "deep_link": str|None}`.
- Label rules (English, Title Case where product-ish): `canvas_run` → `"{canvas name or 'Untitled canvas'} · Canvas"`; `canvas_upload` → `"{canvas name} · Upload"`; `shot_generate` → `"Storyboard · Shot {node_id}"`; `shot_video` → `"Storyboard · Shot {node_id} · Video"`; `agent_run` → `"Chat generation"`; `chat_upload` → `"Chat upload"`; unknown → the raw kind.
- `deep_link`: canvas kinds → `/team/{team_id}/canvas/{canvas_id}` (+ `?node={node_id}` when present); every other kind → `None` (P1 has no storyboard/chat deep route; do not invent one).

- [ ] **Step 1: Tests**

```python
# backend/tests/services/library/test_generated_source.py
from app.services.library.generated_source import describe_source

NAMES = {"501": "EP1 · Storyboard"}


def test_canvas_run_with_name_and_node():
    s = describe_source(
        {"origin_kind": "canvas_run", "canvas_id": "501", "node_id": "n9"},
        canvas_names=NAMES, team_id="42",
    )
    assert s["label"] == "EP1 · Storyboard · Canvas"
    assert s["deep_link"] == "/team/42/canvas/501?node=n9"
    assert s["canvas_id"] == "501" and s["node_id"] == "n9"


def test_canvas_run_unknown_canvas_name():
    s = describe_source({"origin_kind": "canvas_run", "canvas_id": "999", "node_id": None},
                        canvas_names=NAMES, team_id="42")
    assert s["label"] == "Untitled canvas · Canvas"
    assert s["deep_link"] == "/team/42/canvas/999"


def test_shot_kinds_have_label_but_no_deep_link():
    s = describe_source({"origin_kind": "shot_generate", "canvas_id": None, "node_id": "7012"},
                        canvas_names={}, team_id="42")
    assert s["label"] == "Storyboard · Shot 7012" and s["shot_id"] == "7012" and s["deep_link"] is None
    v = describe_source({"origin_kind": "shot_video", "node_id": "7012"}, canvas_names={}, team_id="42")
    assert v["label"].endswith("· Video")


def test_chat_and_agent_kinds():
    assert describe_source({"origin_kind": "chat_upload", "conversation_id": "88"}, canvas_names={}, team_id="1")["label"] == "Chat upload"
    assert describe_source({"origin_kind": "agent_run"}, canvas_names={}, team_id="1")["label"] == "Chat generation"


def test_unknown_kind_is_passed_through_not_crashed():
    s = describe_source({"origin_kind": "future_thing"}, canvas_names={}, team_id="1")
    assert s["kind"] == "future_thing" and s["label"] == "future_thing" and s["deep_link"] is None
```

- [ ] **Step 2: Run → FAIL (module missing)**

- [ ] **Step 3: Implement**

```python
# backend/app/services/library/generated_source.py
"""Source descriptor for Generated-inbox cards (spec §6.1 screen 2, note 3).

Pure. The label is what the card prints under the title; deep_link is where
"open source" goes. Only canvas kinds have a route today — shot/chat kinds get
a label and None, never an invented URL.
"""

from __future__ import annotations

from typing import Any, Optional

_UNTITLED = "Untitled canvas"


def describe_source(
    row: dict[str, Any], *, canvas_names: dict[str, str], team_id: str
) -> dict[str, Any]:
    kind = str(row.get("origin_kind") or "")
    canvas_id = str(row["canvas_id"]) if row.get("canvas_id") is not None else None
    node_id = str(row["node_id"]) if row.get("node_id") is not None else None
    conv_id = str(row["conversation_id"]) if row.get("conversation_id") is not None else None
    label: str
    deep_link: Optional[str] = None
    shot_id: Optional[str] = None

    if kind in ("canvas_run", "canvas_upload"):
        name = canvas_names.get(canvas_id or "", _UNTITLED) if canvas_id else _UNTITLED
        label = f"{name} · {'Canvas' if kind == 'canvas_run' else 'Upload'}"
        if canvas_id:
            deep_link = f"/team/{team_id}/canvas/{canvas_id}"
            if node_id:
                deep_link += f"?node={node_id}"
    elif kind in ("shot_generate", "shot_video"):
        shot_id = node_id
        label = f"Storyboard · Shot {node_id or '?'}"
        if kind == "shot_video":
            label += " · Video"
    elif kind == "agent_run":
        label = "Chat generation"
    elif kind == "chat_upload":
        label = "Chat upload"
    else:
        label = kind

    return {
        "kind": kind,
        "label": label,
        "canvas_id": canvas_id,
        "node_id": node_id,
        "shot_id": shot_id,
        "conversation_id": conv_id,
        "deep_link": deep_link,
    }
```

- [ ] **Step 4: Run → PASS.** Commit: `git add backend/app/services/library/generated_source.py backend/tests/services/library/test_generated_source.py && git commit -m "feat(generated): pure source descriptor (label + deep link)"`

---

### Task 3: Schemas for `/generated`

**Files:**
- Create: `backend/app/schemas/generated.py`
- Test: `backend/tests/services/library/test_generated_schemas.py`

**Interfaces:**
- `ReviewState = Literal["unreviewed","saved","in_assets","deleted"]`
- `GeneratedSource(kind, label, canvas_id?, node_id?, shot_id?, conversation_id?, deep_link?)`
- `GeneratedItem`: `id, scope_id, media_kind, mime?, prompt?, model?, provider?, origin_kind, canvas_id?, node_id?, created_at, promoted_resource_id?, review_state, source_asset_id?, source: GeneratedSource, title: str` (title = first sentence of prompt ≤ 80 chars, else `"{media_kind} · {model or provider or origin_kind}"`)
- `GeneratedPage(items, next_cursor)`; `CountsResponse(unreviewed, saved, in_assets)` (deleted omitted on the wire)
- `SaveAsAssetRequest`: `asset_id: SnowflakeId | None`, `new_asset: NewAssetSpec | None` (`asset_type: AssetType, name`), `slot: str = "unsorted"` (min_length 1), `loadout_id: SnowflakeId | None`; validator: exactly one of `asset_id` / `new_asset`.
- `BatchRequest(ids: list[SnowflakeId] (1..200), action: Literal["save","save_as_asset","delete"], save_as_asset: SaveAsAssetRequest | None)` — validator: `save_as_asset` required iff `action == "save_as_asset"`.
- `CleanupRequest(older_than_days: int = Field(ge=1, le=3650), dry_run: bool = True)`; `CleanupResponse(dry_run, count, sample: list[GeneratedItem], deleted: int)`.
- `model_config = ConfigDict(extra="forbid")` on all request models.

- [ ] **Step 1: Tests** — `SaveAsAssetRequest` rejects both-none and both-set; `BatchRequest` rejects `save_as_asset` action without payload and unknown action; `CleanupRequest` default `dry_run=True`; `GeneratedItem.title` helper `derive_title(prompt, media_kind, model, provider, origin_kind)` (module-level pure function) — `"A multi-camera angle reference sheet in 3×3 grid layout, showing…"` → `"A multi-camera angle reference sheet in 3×3 grid layout, showing…"` truncated at the first `.`/`。`/newline or 80 chars; empty prompt → `"image · seedream-4"`.
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement** the models exactly as listed (reuse `SnowflakeId`, `AssetType` from `app.schemas.assets`).
- [ ] **Step 4: Run → PASS.** Commit: `feat(generated): request/response schemas`.

---

### Task 4: `GeneratedInboxService`

**Files:**
- Create: `backend/app/services/library/generated_inbox_service.py`
- Modify: `backend/app/services/library/promote_generated_media_service.py` (no behavior change; `promote` is reused as-is)
- Test: `backend/tests/services/library/test_generated_inbox_service.py`

**Interfaces:**
- `class GeneratedInboxService(gen_repo=None, promote=None, assets=None, canvases=None)`:
  - `async def list(self, scope_id, team_id: str, **filters) -> dict` — calls `list_inbox`, collects canvas ids, resolves names in one query (`CanvasRepository` — use the existing `get_by_id` in a loop only if no batch method exists; prefer adding `names_by_ids(ids) -> dict` to `canvas_repository.py` in this task), attaches `source` + `title`.
  - `async def counts(self, scope_id) -> dict`
  - `async def save(self, gen_id, scope_id, user_id) -> dict` — `PromoteGeneratedMediaService.promote(gen_id=, user_id=, target_scope_id=scope_id)`; `PermissionError` → `AssetError(403, "not_authorised")`, `ValueError("generation not found")` → 404 `generation_not_found`, `ValueError("generation file missing")` → 409 `file_missing`; returns the refreshed row (state now `saved`).
  - `async def save_as_asset(self, gen_id, scope_id, user_id, req: SaveAsAssetRequest) -> dict` — inside `unit_of_work()`: promote → (create asset if `new_asset`, via `AssetsService.create_asset`; 409 propagates as-is) → `AssetsService.attach_file(asset_id, scope_id, AttachFileRequest(resource_id=promoted_id, slot, loadout_id), user_id)` → `set_review_state(gen_id, scope_id, "in_assets")`; returns `{"generation": row, "asset_id", "resource_id"}`. Any `AssetError` rolls the whole thing back (the UoW re-raises).
  - `async def delete(self, gen_id, scope_id) -> None` — existing repo `delete` (hard delete + file removal) → 404 if False.
  - `async def batch(self, req, scope_id, user_id) -> dict` — per-item results `{"ok": [...ids], "failed": [{"id", "code", "detail"}]}`; **not** atomic across items (each item is its own operation; the spec's atomic guarantee is per `save_as_asset`), and stops nowhere — every id is attempted; documented.
  - `async def cleanup(self, req, scope_id) -> dict` — `older_than = now - days`; `dry_run` → `{"dry_run": True, "count", "sample": first 12 items, "deleted": 0}`; else delete each and count.
- Precedent for `AssetError` and fakes: `app/services/assets/assets_service.py` + its tests.

- [ ] **Step 1: Tests with fakes** — cover: list attaches `source.label` and `title`; save maps the three error kinds; save_as_asset with `asset_id` attaches with the promoted resource id and sets `in_assets`; with `new_asset` creates then attaches; an `AssetError` from `attach_file` (e.g. invalid slot) leaves state untouched (fake UoW records rollback — model it as: the service must not call `set_review_state` if attach raised); batch mixes ok/failed and attempts every id; cleanup dry-run deletes nothing and returns count/sample; cleanup live deletes and returns `deleted`.
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement** per the interface (wrap `promote` calls' exceptions exactly as listed; use `async with unit_of_work():` from `app.db.session`).
- [ ] **Step 4: Run → PASS** (`tests/services/library/test_generated_inbox_service.py`, plus `tests/services/assets -q` to be safe). Commit: `feat(generated): inbox service — save / save-as-asset (UoW) / batch / cleanup`.

---

### Task 5: `/generated` router

**Files:**
- Create: `backend/app/api/generated_router.py`
- Modify: `backend/app/api/__init__.py` (register after `assets_router`, using the same alias pattern `assets_router` used — read the P0 registration line and mirror it)
- Test: `backend/tests/api/test_generated_router.py`

**Interfaces (all `AuthDep`, `scope_id` query gated by team membership — reuse `assets_router._gate` by importing it; envelopes via `assets_router._ok/_err`):**
- `GET /generated?scope_id&state&origin_kind(repeatable)&project_id&media_kind&model&since&cursor&limit` → `{success, data: GeneratedPage}`; `state` defaults to `unreviewed`; `state=all` → `None`.
- `GET /generated/counts?scope_id` → `CountsResponse`
- `POST /generated/{id}/save?scope_id` → 200 item
- `POST /generated/{id}/save-as-asset?scope_id` body `SaveAsAssetRequest` → 201 `{generation, asset_id, resource_id}`
- `DELETE /generated/{id}?scope_id` → `{deleted: true}`
- `POST /generated/batch?scope_id` body `BatchRequest` → 200 `{ok, failed}`
- `POST /generated/cleanup?scope_id` body `CleanupRequest` → 200 `CleanupResponse`
- Query id params use the same int64-bounded pattern/validator as `assets_router` (import `_SNOWFLAKE`/`within_int64` from there rather than duplicating).

- [ ] **Step 1: Tests** (pattern: `tests/api/test_assets_router.py` — `dependency_overrides[get_auth]`, monkeypatch `_is_member` and `_service`): default state is `unreviewed`; `state=all` passes `None`; `origin_kind` repeatable becomes a list; non-member 403 envelope; `save-as-asset` 201 and 409 envelope pass-through; `cleanup` body default dry-run; `batch` validation 422 when `save_as_asset` missing.
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement** + register.
- [ ] **Step 4: Run** `tests/api/test_generated_router.py tests/api/test_module_gates_mounted.py -v` → PASS. Commit: `feat(generated): /generated router`.

---

### Task 6: Chat uploads join the inbox (write path + backfill)

**Files:**
- Modify: `backend/app/services/library/chat_upload.py` (`save_chat_temp_upload` registers a `chat_upload` generated_media row after the resource is created: `origin_kind='chat_upload'`, `conversation_id` when known, `promoted_resource_id = resource id`, `review_state='saved'`, `file_path` = the resource's stored path, `scope_id` = the resolved team). Use a new repo method `insert_registered_resource(...)` on `GeneratedMediaRepository` (ORM insert; no blob copy — the bytes already live under the resource).
- Create: `backend/app/workflows/backfill_generated_inbox.py` (DBOS, `dry_run=True` default, `run_user_id`, Task Center lifecycle like `backfill_resource_gen_params.py`): (a) every resource in a folder named `temp` (per scope, `chat_upload.TEMP_FOLDER_NAME`) that has no `generated_media` row with `promoted_resource_id = resource.id` → insert one as above; (b) every `generated_media` row with `promoted_resource_id` that has an `asset_files` row → `review_state='in_assets'`; (c) counts + per-scope sample in the result; idempotent.
- Modify: `backend/app/api/admin/backfill_router.py` → register `"generated_inbox"`.
- Test: `backend/tests/workflows/test_backfill_generated_inbox.py` (pure planning function `plan_inbox_backfill(temp_resources, existing_gen_by_resource, promoted_with_asset_files) -> {"to_register": [...], "to_mark_in_assets": [...], "counts"}`), `backend/tests/services/library/test_chat_upload_registers_generation.py` (monkeypatch the repo; assert the insert kwargs).

- [ ] **Step 1: Tests** as named. **Step 2: FAIL. Step 3: Implement. Step 4: PASS** (`tests/workflows tests/services/library tests/test_backfill_issue_scope.py -q`). Commit: `feat(generated): chat uploads register into the inbox; dry-run backfill for legacy temp files + in_assets`.

---

### Task 7: Frontend service + slot table

**Files:**
- Create: `frontend/services/generatedService.ts` (`fetchGenerated(scopeId, {state, originKinds, projectId, mediaKind, model, since, cursor})`, `fetchGeneratedCounts`, `saveGeneration`, `saveGenerationAsAsset`, `deleteGeneration`, `batchGenerated`, `cleanupGenerated`; types mirror Task 3 **with string ids**; error → throws `GeneratedApiError {status, code, detail}` parsed from the envelope)
- Create: `frontend/services/assetsService.ts` (`searchAssets(scopeId, {type, q})`, `createAsset`, `fetchAsset(id, scopeId)` — only what the dialog needs)
- Create: `frontend/components/assets/assetSlots.ts` (`PRIMARY_SLOT`, `SLOTS` — a verbatim mirror of `backend/app/services/assets/slots.py`, with a comment naming the source and a unit test that pins the six types)
- Test: `frontend/services/generatedService.test.ts` (fetch stub: query string building incl. repeatable `origin_kind`, envelope error parsing with a **real** error body captured from the router test in Task 5), `frontend/components/assets/assetSlots.test.ts`

- [ ] Steps: tests → fail → implement → `cd frontend && npx vitest run services/generatedService.test.ts components/assets/assetSlots.test.ts` → pass → commit `feat(generated): frontend clients + slot table mirror`.

---

### Task 8: Sidebar, route, context — Generated replaces Project Assets

**Files:**
- Modify: `frontend/router.tsx` — `resources/temp` and `resources/project-assets` both `Navigate` to `../generated`; `generated` is served by the existing `resources/:section` route (no new element).
- Modify: `frontend/contexts/ResourcesContext.tsx` — `SidebarView` gains `'generated'`, loses `'temp'` and `'project-assets'`; delete `isTempView`, `tempFolderId`, `tempResources`, `reloadTemp`, `tempRefreshTick` and their effects; add `generatedUnreviewedCount: number | null` loaded via `fetchGeneratedCounts` on scope change and exposed with `refreshGeneratedCounts()`.
- Modify: `frontend/components/ResourcesSidebar.tsx` — the Project Assets button becomes **Generated** (`Sparkles` icon from lucide) with a warn-toned pill showing `generatedUnreviewedCount` when > 0; i18n key `resources.generated`.
- Modify: `frontend/components/ResourcesViewInner.tsx` — remove the `isTempView` / `isProjectAssetsView` branches and the `ProjectAssetsTree`/`GenerationsGrid` imports; add `isGeneratedView ? <GeneratedView /> : …` (component from Task 9; for this task render a placeholder `<div data-testid="generated-view" />` to keep the commit green, replaced in Task 9).
- Delete: `frontend/components/resources/ProjectAssetsTree.tsx` + test, `frontend/components/resources/GenerationsGrid.tsx` + test, `frontend/components/TempResourceActions.tsx` + test; move `fetchResourceCanvasRefs` from `services/projectAssetsService.ts` into `services/resourceService.ts` (keep `ResourceDetailPage`'s "Appears in N canvases" working) and delete `projectAssetsService.ts`; update `ResourceGrid*.test.tsx` fixtures that referenced temp props.
- i18n: `resources.generated` = "Generated"; remove `resources.projectAssets`, `resources.temp`, the `projectAssets.*` block except `appearsInCanvases_*` (still used) — move those two keys under `resources.`.
- Test: `frontend/components/ResourcesSidebar.test.tsx` (new or extended): renders Generated with pill `12` when count is 12, no pill at 0; `ResourcesShell.test.tsx` still green.

- [ ] Steps: tests → fail → implement → `npx vitest run components/ResourcesSidebar.test.tsx components/ResourcesShell.test.tsx components/ResourceGrid.*.test.tsx pages/ResourcesPage.test.tsx` → pass → `npm run lint` → commit `feat(generated): sidebar/route/context — Generated replaces Project Assets; temp view retired`.

---

### Task 9: `GeneratedView` + `GeneratedCard` + `CleanupDialog`

**Files:**
- Create: `frontend/components/resources/generated/GeneratedView.tsx`, `GeneratedCard.tsx`, `CleanupDialog.tsx`, `generatedFilters.ts` (pure: URL search-param ↔ filter state; source option list `[canvas_run, canvas_upload, shot_generate, shot_video, agent_run, chat_upload]` with labels)
- Test: `GeneratedView.test.tsx`, `GeneratedCard.test.tsx`, `CleanupDialog.test.tsx`, `generatedFilters.test.ts`

**Behavior (mockup screen 2):**
- Header `Generated` + warn chip `{n} unreviewed`; tabs `Unreviewed / Saved / In Assets / All` with counts (from `fetchGeneratedCounts`); the active tab is in the URL (`?state=`), default `unreviewed`.
- Filter chips: Source (multi), Project (single; options from `projectService.listProjects` for the scope), Type (image/video), Model (options = distinct `model` in the loaded page), Date (`since` presets: 24h / 7d / 30d). Chips are plain buttons + popovers (do **not** reuse `FilterBar` — its chip set is resource-specific); state in URL.
- Grid of `GeneratedCard`: thumb via `generatedMediaCoverUrl(id)` (video: `generatedMediaStreamUrl` poster), state label (`New` warn / `Saved` neutral / `Asset` ok), title, source line (button → `navigate(deep_link)` when present, else plain text with `title=` tooltip), `model · date`. Actions by state: `unreviewed` → `Save`, `As Asset…`, delete icon; `saved` → `In My Uploads` (disabled ghost), `As Asset…`; `in_assets` → `Open asset` (navigates to `/team/{teamId}/resources/assets/{asset_id}` — the P2 route; until P2 lands it 404s in-app: acceptable, note in the report).
- Multi-select (checkbox top-left on hover) → batch bar: `Save`, `As Asset…` (opens the dialog in batch mode), `Delete`, `Clear`.
- `Clean up…` (toolbar, ghost) → `CleanupDialog`: days input (default 30) → **Preview** (dry-run: shows count + 12 thumbs) → **Delete N** confirm; never sends `dry_run=false` without a completed preview in the same dialog instance.
- Empty states: unreviewed → "Nothing to review"; project filter with no canvas-origin items → "Only canvas generations can be filtered by project".
- Every failure surfaces via toast with the envelope's `code` mapped through i18n (`generated.err.<code>` with a generic fallback) — no swallowed errors.

- [ ] Steps: tests (RTL with `vi.mock('../../../services/generatedService')`, fixtures copied from real wire shapes — string ids) → fail → implement → `npx vitest run components/resources/generated` → pass → lint → commit `feat(generated): inbox view, cards, batch bar, clean-up dialog`.

---

### Task 10: `SaveAsAssetDialog` (Generated entry)

**Files:**
- Create: `frontend/components/assets/SaveAsAssetDialog.tsx` (+ `SaveAsAssetDialog.test.tsx`)
- Modify: `GeneratedView.tsx` / `GeneratedCard.tsx` to open it (single + batch)

**Behavior (mockup screen 5):**
- Props: `{ open, scopeId, items: GeneratedItem[] (1..n), onClose, onDone(result) }`.
- Left: preview of the first item + its title/model/date/source label; in batch mode a `+{n-1} more` strip.
- Type seg (six types, from `assetSlots.ts`); default = type of the suggested asset if any, else `character`.
- Candidates: `searchAssets(scopeId, {type, q})`, debounced; if `items[0].source_asset_id` is set, fetch that asset and pin it first with a `suggested · from canvas` tag; last row `+ Create new {type} "{title}"` → `createAsset` on submit.
- Slot seg from `SLOTS[type]` + `Unsorted`; default `Unsorted`. Loadout seg only for `character` with a chosen existing asset (from `fetchAsset(id).loadouts`, default = the default loadout).
- Footer: fixed sentence `File stays where it is · attaching never moves or copies`; primary button `Attach to {asset name}` (or `Create & attach`). Submit → single: `saveGenerationAsAsset`; batch: `batchGenerated({action:'save_as_asset', …})`, then toast `Attached to {name} · {slot}` / batch summary `{ok} attached, {failed} failed` listing failed codes.
- 409 `asset_exists` on create-new → show `An asset with this name exists — attach to it instead?` with a one-click switch to that `existing_asset_id`.
- Uses `UiModal` (`components/ui/primitives.tsx`), lucide icons only, no emoji.

- [ ] Steps: tests (suggested-first, create-new path, 409 switch, batch summary) → fail → implement → `npx vitest run components/assets` → pass → lint → commit `feat(generated): Save as Asset dialog wired to the inbox`.

---

### Task 11: i18n, Playwright e2e, docs, suite

**Files:**
- Modify: `frontend/public/locales/en.json`, `zh.json` — `generated.*`, `saveAsAsset.*`, `resources.generated` (zh values Chinese, keys camelCase).
- Create: `frontend/e2e/generated-inbox.spec.ts` — route-mocked flow with **captured real bodies**: open `/team/{id}/resources/generated` → tabs show counts → card `As Asset…` → dialog → suggested asset pinned → attach → card moves to `In Assets` tab; cleanup preview then delete. (Add the fixture file under `frontend/e2e/fixtures/generated/` with a header comment naming the router test that produced each body.)
- Modify: `frontend/e2e-prod/walkthrough.spec.ts` — add one visible-assertion step: sidebar `Generated` item visible → page heading visible (no data assumptions).
- Modify: `CLAUDE.md` — in the 项目结构/资源库 area: one line "Generated inbox = `generated_media` + `review_state`; `/api/v1/generated`; Project Assets/Temp views retired (P1, 2026-08-29)".
- [ ] Run: `cd frontend && npx vitest run && npm run lint && npx playwright test e2e/generated-inbox.spec.ts`; `cd backend && uv run pytest -q -x --ignore=tests/integration`; lint trio on changed backend files.
- [ ] Commit: `test(generated): e2e with real wire fixtures; i18n; docs`.

---

### Task 12: PR

- [ ] `/ship` is unavailable in agent sessions — the controller pushes and opens the PR. PR body must state: (1) the backfill `generated_inbox` must be dispatched **dry-run first** from the admin backfill page after deploy, reviewed, then run live — it is the step that makes existing chat uploads appear; (2) `in_assets` will be empty until users attach; (3) P2 owns the assets pages, so `Open asset` links 404 until then.

---

## Self-review

- **Spec coverage (P1 row of §9):** GeneratedView + sidebar (T8/T9), Chat Uploads registration (T6), save-as-asset endpoint (T4/T5), SaveAsAssetDialog Generated entry (T10). §5.2 endpoints all present (T5). §3.7 "temp folder becomes plain folder / tempResources retired" (T8). Decision 7 "batch cleanup manual, dry-run first" (T4/T9). Decision 8 two buttons (T9).
- **Not in P1 (by spec):** My Uploads right-click / canvas Output node / chat attachment entries of the dialog (P6/P4/P5); assets pages (P2); `deleted` review_state is reserved — P1 hard-deletes as before (ruled: keeps existing file-removal semantics; revisit if a trash is wanted).
- **Type consistency:** `SaveAsAssetRequest` (T3) is what T4 consumes and T7 mirrors; `describe_source` output keys (T2) == `GeneratedSource` fields (T3) == card props (T9); `AssetError` codes reused verbatim from P0.
- **Placeholders:** none — T3/T4/T6–T10 describe tests by behavior rather than pasting every fixture (the implementer writes them from the named behaviors); every named function/route/key is defined in this document.
