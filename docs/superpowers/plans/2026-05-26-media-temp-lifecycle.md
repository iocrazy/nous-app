# Media Temp Lifecycle (sub-plan 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use `- [ ]` checkboxes.

**Goal:** Give chat/issue temp resources a finite lifetime. Users see a per-scope TTL setting (Personal vs Team), a background sweeper soft-deletes expired temp resources daily, and a "Save" action promotes a temp resource out of the `temp` folder so it sticks around.

**Architecture:** Reuse the existing `resources` table — temp resources are just rows whose `folder_id` points at the scope's reserved `temp` folder (built in sub-plan 1). TTL is stored in `user_settings.settings_json.chat_temp_ttl_days` (personal) and `teams.settings_json.chat_temp_ttl_days` (team — new column). The DBOS sweeper is a `@DBOS.scheduled("0 4 * * *")` workflow that scans `temp` folders by scope, compares each resource's `created_at + ttl_days` against `NOW()`, and soft-deletes (`is_trashed=true`). Promote is a `folder_id` change via the existing `PATCH /resources/{id}` (one schema extension), with a Save split-button + FolderPickerModal in the UI.

**Tech Stack:** Postgres / Supabase (migration 225), FastAPI, DBOS workflows (`@DBOS.scheduled`), `ResourcesRepository` + `ResourcesService`, React/Vite frontend (`SettingsView`, `ResourcesViewInner`, `FolderPickerModal`), `aiLibraryService.ts` (extended).

**Spec:** `docs/superpowers/specs/2026-05-25-agent-media-context-and-assets-design.md`. Sub-plan 2 of 5. Builds on sub-plan 1 (`docs/superpowers/plans/2026-05-26-media-asset-foundation.md`, shipped as PR #351).

---

## Locked design decisions (from brainstorm with user, 2026-05-26)

1. **TTL scope = per-scope, separate**: Personal reads `user_settings.settings_json.chat_temp_ttl_days`; Team reads `teams.settings_json.chat_temp_ttl_days` (new column added in this plan).
2. **Default TTL = 30 days**. Special value `-1` (or absent) = "never" — sweeper skips that scope.
3. **Promote UX = Save split-button**: default click → `PATCH folder_id=null` (move to scope root). Small arrow / "Save to folder..." opens existing `FolderPickerModal` → `PATCH folder_id=<picked>`. Reuses existing endpoint after one schema extension.
4. **No new "Temp sidebar view"**: the `temp` folder is already a regular folder; it shows up in the existing `SidebarFolderTree`. We add visual affordances (TTL badge, Save button) on rows when the user is viewing the `temp` folder — not a separate route.
5. **Sweeper deletion = soft delete** (`is_trashed=true`, `trashed_at=NOW()`). The existing trash → eventual hard-delete pipeline handles file cleanup later; not in scope here.

## Reuse these existing utilities (don't re-roll)

- `app/services/library/chat_upload.py::TEMP_FOLDER_NAME` (= `"temp"`) — single source of truth for the reserved folder name.
- `app/repositories/resources_repository.py::ResourcesRepository.get_folders(scope_type, scope_id)` + `update_resource(resource_id, data)` — already used by sub-plan 1.
- `app/services/library/resources_service.py::ResourcesService` — wraps the repo with scope validation.
- `app/db/engine.py::fetch_one / fetch_all / execute` — canonical async DB-access pattern (used by `chat_upload._get_session_team_id`).
- `app/core/scope_guards.py::verify_scope_access(auth, scope_type, scope_id)` — 403 if caller doesn't own the personal/team scope.
- `app/workflows/scheduled_master.py` and `app/workflows/scheduled_memory_archival.py` — reference `@DBOS.scheduled` workflows, copy the import + decorator pattern.
- `frontend/components/FolderPickerModal.tsx` — reuse for "Save to folder..." action.
- `frontend/components/SettingsView.tsx` (`activeTab === 'general'` block) — host for the TTL section.

## File structure

- **Create** `backend/supabase/migrations/225_team_settings_json_and_temp_ttl.sql` — `ALTER TABLE teams ADD COLUMN settings_json JSONB DEFAULT '{}'::jsonb`; comment columns.
- **Create** `backend/app/services/library/temp_ttl_settings.py` — `get_chat_temp_ttl_days(scope_type, scope_id) -> int | None`; `set_chat_temp_ttl_days(scope_type, scope_id, ttl_days)`.
- **Create** `backend/tests/test_temp_ttl_settings.py`.
- **Create** `backend/app/api/temp_ttl_router.py` — `GET /api/v1/library/temp-ttl` + `PUT /api/v1/library/temp-ttl`.
- **Create** `backend/tests/test_temp_ttl_router.py`.
- **Modify** `backend/app/schemas/resources.py` (`ResourceUpdate`) — add `folder_id: Optional[str]`.
- **Modify** `backend/app/api/resources_crud_router.py::update_resource` — validate destination folder belongs to the same scope as the resource (or is `null` = root).
- **Create** `backend/tests/test_resource_promote.py`.
- **Create** `backend/app/workflows/temp_resource_sweeper.py` — `@DBOS.scheduled("0 4 * * *")` workflow.
- **Create** `backend/tests/test_temp_resource_sweeper.py`.
- **Modify** `backend/app/main.py` — register the new sweeper workflow + the new router.
- **Modify** `frontend/services/aiLibraryService.ts` (or create `frontend/services/tempTtlService.ts`) — add `getChatTempTtl` / `setChatTempTtl` and `promoteResource(resourceId, folderId)`.
- **Modify** `frontend/components/SettingsView.tsx` — add "Chat attachment TTL" section in the `general` tab.
- **Create** `frontend/components/ChatTempTtlPanel.tsx` — encapsulated section component used by SettingsView.
- **Modify** `frontend/components/ResourcesViewInner.tsx` — when row's folder_id matches the scope's `temp` folder, show TTL badge + Save split-button.
- **Create** `frontend/components/TempResourceActions.tsx` — Save split-button + FolderPickerModal integration.

---

## Task 1: DB migration — add `teams.settings_json`

**Files:**
- Create: `supabase/migrations/225_team_settings_json_and_temp_ttl.sql`

- [ ] **Step 1: Write the migration**

```sql
-- Migration: 225_team_settings_json_and_temp_ttl
-- Description: Add settings_json to teams so per-team settings (including
-- chat_temp_ttl_days) have a single home. Personal scope settings already
-- live in user_settings.settings_json (migration 008) — no change there.

ALTER TABLE public.teams
    ADD COLUMN IF NOT EXISTS settings_json JSONB NOT NULL DEFAULT '{}'::jsonb;

COMMENT ON COLUMN public.teams.settings_json IS
    'Per-team settings as JSON. Known keys: chat_temp_ttl_days (int days, -1 = never expire).';

COMMENT ON COLUMN public.user_settings.settings_json IS
    'Per-user settings as JSON. Known keys: chat_temp_ttl_days (int days, -1 = never expire).';
```

- [ ] **Step 2: Apply locally (or against the dev Supabase) to validate**

```bash
psql -h 127.0.0.1 -p 54322 -U postgres -d postgres -f supabase/migrations/225_team_settings_json_and_temp_ttl.sql
```

Expected output: `ALTER TABLE` + 2 `COMMENT` notices, no errors. If the column already exists from a partial earlier run, the `IF NOT EXISTS` makes it a no-op.

- [ ] **Step 3: Verify with information_schema**

```bash
psql -h 127.0.0.1 -p 54322 -U postgres -d postgres -c \
  "SELECT column_name, data_type, column_default FROM information_schema.columns WHERE table_name='teams' AND column_name='settings_json';"
```

Expected: one row, `data_type=jsonb`, `column_default='{}'::jsonb`.

- [ ] **Step 4: Commit**

```bash
git add supabase/migrations/225_team_settings_json_and_temp_ttl.sql
git commit -m "feat(db): teams.settings_json (+ comment user_settings.settings_json) for chat_temp_ttl_days"
```

---

## Task 2: TTL settings helper

**Files:**
- Create: `backend/app/services/library/temp_ttl_settings.py`
- Test: `backend/tests/test_temp_ttl_settings.py`

- [ ] **Step 1: Read the reuse surface**

Read `app/services/library/chat_upload.py` (the `_get_session_team_id` helper) to confirm the `from app.db import engine as db_engine` deferred-import pattern and the `fetch_one(sql, params)` / `execute(sql, params)` signatures used in this codebase. Also re-read `app/services/library/chat_upload.py::TEMP_FOLDER_NAME` so this module imports the same constant.

- [ ] **Step 2: Write the failing tests**

```python
# backend/tests/test_temp_ttl_settings.py
from unittest.mock import AsyncMock
import pytest
from app.services.library import temp_ttl_settings as m


@pytest.mark.asyncio
async def test_get_personal_ttl_reads_user_settings(monkeypatch):
    monkeypatch.setattr(
        m, "_fetch_settings_json",
        AsyncMock(return_value={"chat_temp_ttl_days": 7}),
    )
    assert await m.get_chat_temp_ttl_days("personal", "u1") == 7


@pytest.mark.asyncio
async def test_get_team_ttl_reads_teams_settings(monkeypatch):
    monkeypatch.setattr(
        m, "_fetch_settings_json",
        AsyncMock(return_value={"chat_temp_ttl_days": 14}),
    )
    assert await m.get_chat_temp_ttl_days("team", "42") == 14


@pytest.mark.asyncio
async def test_get_ttl_default_when_missing(monkeypatch):
    """No row OR no key → default 30 days."""
    monkeypatch.setattr(m, "_fetch_settings_json", AsyncMock(return_value=None))
    assert await m.get_chat_temp_ttl_days("personal", "u1") == m.DEFAULT_TTL_DAYS


@pytest.mark.asyncio
async def test_get_ttl_never_when_negative(monkeypatch):
    """-1 means 'never expire' → return None so sweeper skips."""
    monkeypatch.setattr(
        m, "_fetch_settings_json",
        AsyncMock(return_value={"chat_temp_ttl_days": -1}),
    )
    assert await m.get_chat_temp_ttl_days("personal", "u1") is None


@pytest.mark.asyncio
async def test_set_personal_ttl_upserts_user_settings(monkeypatch):
    fake_execute = AsyncMock()
    monkeypatch.setattr(m, "_upsert_settings_key", fake_execute)
    await m.set_chat_temp_ttl_days("personal", "u1", 14)
    fake_execute.assert_awaited_once_with("personal", "u1", "chat_temp_ttl_days", 14)


@pytest.mark.asyncio
async def test_set_ttl_invalid_value_raises():
    """Only allow positive ints or -1."""
    with pytest.raises(ValueError):
        await m.set_chat_temp_ttl_days("personal", "u1", 0)
    with pytest.raises(ValueError):
        await m.set_chat_temp_ttl_days("personal", "u1", -2)


def test_invalid_scope_type_raises():
    with pytest.raises(ValueError):
        # Any function entering with wrong scope must reject — picked one path
        import asyncio
        asyncio.run(m.get_chat_temp_ttl_days("project", "x"))
```

- [ ] **Step 3: Run → fail**

```bash
cd backend && uv run pytest tests/test_temp_ttl_settings.py -v
```

Expected: import error or all fail (module missing).

- [ ] **Step 4: Implement**

```python
# backend/app/services/library/temp_ttl_settings.py
"""Per-scope TTL settings for chat temp resources.

Personal scope reads/writes ``user_settings.settings_json.chat_temp_ttl_days``.
Team scope reads/writes ``teams.settings_json.chat_temp_ttl_days``.
Returns ``None`` when the value is ``-1`` (never expire) so the sweeper
can skip the scope cleanly.
"""
from __future__ import annotations

from typing import Any, Optional

from loguru import logger

# Default applied when no row / no key exists. Locked at 30 days per the
# sub-plan 2 design decision.
DEFAULT_TTL_DAYS = 30

# Sentinel stored in settings_json for "never expire".
_NEVER = -1

_VALID_SCOPES = ("personal", "team")


def _check_scope(scope_type: str) -> None:
    if scope_type not in _VALID_SCOPES:
        raise ValueError(
            f"unsupported scope_type {scope_type!r}; expected one of {_VALID_SCOPES}"
        )


async def _fetch_settings_json(scope_type: str, scope_id: str) -> Optional[dict[str, Any]]:
    """Return the row's ``settings_json`` value (a dict) or None if no row."""
    from app.db import engine as db_engine  # deferred — codebase convention

    if scope_type == "personal":
        sql = (
            "SELECT settings_json FROM public.user_settings "
            "WHERE user_id = :scope_id"
        )
    else:
        sql = (
            "SELECT settings_json FROM public.teams WHERE id = :scope_id"
        )
    row = await db_engine.fetch_one(sql, {"scope_id": scope_id})
    if row is None:
        return None
    raw = row.get("settings_json")
    # asyncpg returns jsonb as dict already; defensive parse for the str case.
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        import json

        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            logger.warning(
                f"[temp_ttl] {scope_type}/{scope_id} settings_json is not valid JSON: {raw!r}"
            )
            return None
    return None


async def get_chat_temp_ttl_days(scope_type: str, scope_id: str) -> Optional[int]:
    """Return TTL in days for the scope. None means 'never expire'."""
    _check_scope(scope_type)
    settings = await _fetch_settings_json(scope_type, str(scope_id))
    if settings is None:
        return DEFAULT_TTL_DAYS
    raw = settings.get("chat_temp_ttl_days")
    if raw is None:
        return DEFAULT_TTL_DAYS
    try:
        value = int(raw)
    except (TypeError, ValueError):
        logger.warning(
            f"[temp_ttl] {scope_type}/{scope_id} chat_temp_ttl_days not int: {raw!r}; "
            "falling back to default"
        )
        return DEFAULT_TTL_DAYS
    if value == _NEVER:
        return None
    if value <= 0:
        # Malformed (0 or negative-not-NEVER) → fall back to default.
        logger.warning(
            f"[temp_ttl] {scope_type}/{scope_id} chat_temp_ttl_days invalid ({value}); "
            "falling back to default"
        )
        return DEFAULT_TTL_DAYS
    return value


async def _upsert_settings_key(
    scope_type: str, scope_id: str, key: str, value: Any
) -> None:
    """Set a single key inside settings_json without clobbering other keys."""
    from app.db import engine as db_engine

    if scope_type == "personal":
        # user_settings has UNIQUE(user_id) and may not have a row yet — upsert.
        sql = (
            "INSERT INTO public.user_settings (user_id, settings_json) "
            "VALUES (:scope_id, jsonb_build_object(:key, to_jsonb(:value::int))) "
            "ON CONFLICT (user_id) DO UPDATE SET "
            "settings_json = COALESCE(public.user_settings.settings_json, '{}'::jsonb) "
            "|| jsonb_build_object(:key, to_jsonb(:value::int)), "
            "updated_at = NOW()"
        )
    else:
        # teams.settings_json was added in migration 225 with default '{}'::jsonb.
        sql = (
            "UPDATE public.teams SET settings_json = "
            "COALESCE(settings_json, '{}'::jsonb) "
            "|| jsonb_build_object(:key, to_jsonb(:value::int)) "
            "WHERE id = :scope_id"
        )
    await db_engine.execute(
        sql, {"scope_id": scope_id, "key": key, "value": value}
    )


async def set_chat_temp_ttl_days(
    scope_type: str, scope_id: str, ttl_days: int
) -> None:
    """Persist a new TTL. ``ttl_days`` must be a positive int or -1 ('never')."""
    _check_scope(scope_type)
    if ttl_days != _NEVER and ttl_days <= 0:
        raise ValueError(
            f"ttl_days must be a positive int or -1 (never); got {ttl_days}"
        )
    await _upsert_settings_key(
        scope_type, str(scope_id), "chat_temp_ttl_days", ttl_days
    )
```

- [ ] **Step 5: Run → pass + lint + commit**

```bash
cd backend && uv run pytest tests/test_temp_ttl_settings.py -v
uv run black app/services/library/temp_ttl_settings.py tests/test_temp_ttl_settings.py
uv run isort app/services/library/temp_ttl_settings.py tests/test_temp_ttl_settings.py
uv run flake8 app/services/library/temp_ttl_settings.py tests/test_temp_ttl_settings.py
git add -A && git commit -m "feat(library): per-scope chat_temp_ttl_days settings helper"
```

---

## Task 3: API — `GET/PUT /api/v1/library/temp-ttl`

**Files:**
- Create: `backend/app/api/temp_ttl_router.py`
- Modify: `backend/app/main.py` (register router)
- Test: `backend/tests/test_temp_ttl_router.py`

- [ ] **Step 1: Read the reuse surface**

Read `app/api/resources_upload_router.py` to copy the router/scope_type query-param + `verify_scope_access` pattern (line numbers will shift; grep for `scope_guards.verify_scope_access`). Read `app/main.py` to find where existing routers are registered (search for `include_router`); the new router will sit next to `resources_crud_router`.

- [ ] **Step 2: Write the failing test**

```python
# backend/tests/test_temp_ttl_router.py
from unittest.mock import AsyncMock
import pytest
from fastapi import HTTPException
from app.api import temp_ttl_router as r


class _Auth:
    def __init__(self, user_id="u1"):
        self.user_id = user_id


@pytest.mark.asyncio
async def test_get_temp_ttl_returns_default_when_unset(monkeypatch):
    monkeypatch.setattr(r, "verify_scope_access", AsyncMock())  # noop pass
    monkeypatch.setattr(
        r, "get_chat_temp_ttl_days", AsyncMock(return_value=30)
    )
    out = await r.get_temp_ttl(auth=_Auth(), scope_type="personal", scope_id="u1")
    assert out == {"ttl_days": 30}


@pytest.mark.asyncio
async def test_get_temp_ttl_returns_minus_one_for_never(monkeypatch):
    monkeypatch.setattr(r, "verify_scope_access", AsyncMock())
    monkeypatch.setattr(r, "get_chat_temp_ttl_days", AsyncMock(return_value=None))
    out = await r.get_temp_ttl(auth=_Auth(), scope_type="personal", scope_id="u1")
    assert out == {"ttl_days": -1}


@pytest.mark.asyncio
async def test_put_temp_ttl_writes(monkeypatch):
    monkeypatch.setattr(r, "verify_scope_access", AsyncMock())
    set_mock = AsyncMock()
    monkeypatch.setattr(r, "set_chat_temp_ttl_days", set_mock)
    out = await r.put_temp_ttl(
        auth=_Auth(), payload=r.TempTtlUpdate(scope_type="team", scope_id="42", ttl_days=7)
    )
    assert out == {"ttl_days": 7}
    set_mock.assert_awaited_once_with("team", "42", 7)


@pytest.mark.asyncio
async def test_put_temp_ttl_scope_access_denied(monkeypatch):
    async def _denied(*a, **kw):
        raise HTTPException(status_code=403, detail="forbidden")

    monkeypatch.setattr(r, "verify_scope_access", _denied)
    with pytest.raises(HTTPException) as e:
        await r.put_temp_ttl(
            auth=_Auth(),
            payload=r.TempTtlUpdate(scope_type="team", scope_id="42", ttl_days=7),
        )
    assert e.value.status_code == 403
```

- [ ] **Step 3: Run → fail.**

```bash
cd backend && uv run pytest tests/test_temp_ttl_router.py -v
```

- [ ] **Step 4: Implement**

```python
# backend/app/api/temp_ttl_router.py
"""GET/PUT /api/v1/library/temp-ttl — per-scope chat temp resource TTL."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.auth import AuthDep
from app.core.scope_guards import verify_scope_access
from app.services.library.temp_ttl_settings import (
    get_chat_temp_ttl_days,
    set_chat_temp_ttl_days,
)

router = APIRouter(prefix="/library", tags=["library"])

ScopeType = Literal["personal", "team"]


class TempTtlUpdate(BaseModel):
    scope_type: ScopeType
    scope_id: str = Field(..., min_length=1)
    # -1 means "never expire". Positive ints are days. Disallow 0 / -2 etc.
    ttl_days: int = Field(..., description="positive int, or -1 for never")


@router.get("/temp-ttl")
async def get_temp_ttl(
    auth: AuthDep,
    scope_type: ScopeType = Query(...),
    scope_id: str = Query(..., min_length=1),
) -> dict:
    """Read the current chat temp TTL for a scope. Returns ``-1`` for 'never'."""
    await verify_scope_access(auth, scope_type, scope_id)
    days = await get_chat_temp_ttl_days(scope_type, scope_id)
    return {"ttl_days": -1 if days is None else days}


@router.put("/temp-ttl")
async def put_temp_ttl(auth: AuthDep, payload: TempTtlUpdate) -> dict:
    """Set the chat temp TTL for a scope. Pass ``ttl_days=-1`` for 'never'."""
    await verify_scope_access(auth, payload.scope_type, payload.scope_id)
    if payload.ttl_days != -1 and payload.ttl_days <= 0:
        raise HTTPException(
            status_code=400,
            detail="ttl_days must be a positive int or -1 (never)",
        )
    await set_chat_temp_ttl_days(payload.scope_type, payload.scope_id, payload.ttl_days)
    return {"ttl_days": payload.ttl_days}
```

Then register in `app/main.py`: locate the line that includes `resources_crud_router.router` (`grep -n "resources_crud_router" app/main.py`) and add an identical line for `temp_ttl_router.router` right after it. **Read the surrounding `include_router` calls so the prefix style matches** (some routers are mounted via `app.include_router(x.router, prefix="/api/v1")` and others rely on the prefix declared in the router itself — match the convention you find).

- [ ] **Step 5: Run → pass + lint + commit**

```bash
cd backend && uv run pytest tests/test_temp_ttl_router.py -v
uv run black app/api/temp_ttl_router.py tests/test_temp_ttl_router.py app/main.py
uv run isort app/api/temp_ttl_router.py tests/test_temp_ttl_router.py app/main.py
uv run flake8 app/api/temp_ttl_router.py tests/test_temp_ttl_router.py
git add -A && git commit -m "feat(library): GET/PUT /api/v1/library/temp-ttl (per-scope)"
```

---

## Task 4: ~~Extend PATCH /resources/{id}~~ → **No-op: reuse existing `POST /resources/{id}/move`** (revised 2026-05-26)

**Revision reason (caught during Task 4 spec+quality review):** `folder_id` / `scope_type` / `scope_id` live on `resource_items` (the scope-membership join table), NOT on `resources`. Extending `update_resource` writes to the wrong table. AND `POST /api/v1/resources/{resource_id}/move` already exists at `app/api/resources_crud_router.py:867` with `ResourceMoveRequest{folder_id, scope_type, scope_id}` → `svc.move_resource → get_resource_item + update_resource_item`. The frontend Promote action calls THAT endpoint. Zero backend change for Task 4.

**Files:** (none — existing endpoint reused)

- [ ] **Step 1: Verify the existing /move endpoint covers our needs.** Read `app/api/resources_crud_router.py` lines ~867-885 and `app/services/library/resources_service.py::move_resource`. Confirm:
  - Endpoint: `POST /api/v1/resources/{resource_id}/move`
  - Body shape: `{ folder_id: str | null, scope_type: "personal"|"team", scope_id: str }`
  - Behavior: 404 if the (resource, scope) pair has no `resource_items` row (i.e., the caller is referring to a resource not in their scope — implicit scope guard via the join lookup); 500 on other errors.
  - `folder_id=null` clears to scope root.
  - `folder_id="<id>"` sets it (NO same-scope folder validation today — see "Known limitation" below).

- [ ] **Step 2: Verify implementer reverted any prior attempt.** If a previous Task 4 commit added `folder_id` to `ResourceUpdate` or modified `update_resource`, it must be reverted via `git revert <sha>` before Task 5 starts. Run `git log --oneline master..HEAD` and confirm the latest commit before Task 5 leaves the PATCH endpoint untouched.

- [ ] **Step 3: No code, no test, no commit.** Task 4's deliverable is the design decision recorded above. The next implementation work is Task 5 (sweeper).

**Known limitation (deferred, NOT in this plan):** `move_resource` does not currently validate that `folder_id` belongs to the same scope as the target `resource_item`. A caller in scope A could theoretically pass a `folder_id` from scope B, producing a `resource_item` whose `folder_id` references a folder in a different scope. The UI's `FolderPickerModal` is already scoped (only shows folders in the current scope), so this doesn't surface as a user-facing bug — but it's a defensive guard we should add in a follow-up. Tracked as a TODO for sub-plan 2 polish or as an independent backend hardening PR.

- [ ] **Step 1: Read the reuse surface**

Read `app/repositories/resources_repository.py` for `update_resource` (passes the data dict straight to Supabase update — verify `folder_id` will be respected) and `get_folder_by_id`. Confirm how a resource's `scope_type` / `scope_id` is available on the row (used for the same-scope guard below).

- [ ] **Step 2: Write the failing tests**

```python
# backend/tests/test_resource_promote.py
from unittest.mock import AsyncMock, MagicMock
import pytest
from fastapi import HTTPException

from app.api import resources_crud_router as r
from app.schemas.resources import ResourceUpdate


class _Auth:
    def __init__(self, user_id="u1"):
        self.user_id = user_id


def _patch_repo(monkeypatch, *, resource, folder=None, updated=None):
    repo = MagicMock()
    repo.get_resource_by_id = AsyncMock(return_value=resource)
    repo.get_folder_by_id = AsyncMock(return_value=folder)
    repo.update_resource = AsyncMock(return_value=updated or {**resource, **({"folder_id": "f-target"} if folder else {"folder_id": None})})
    monkeypatch.setattr(r, "ResourcesRepository", lambda: repo)
    return repo


@pytest.mark.asyncio
async def test_promote_to_root_clears_folder_id(monkeypatch):
    resource = {"id": "res-1", "scope_type": "personal", "scope_id": "u1", "folder_id": "f-temp"}
    repo = _patch_repo(monkeypatch, resource=resource)
    out = await r.update_resource(
        "res-1", ResourceUpdate(folder_id=None), _Auth()
    )
    assert out["success"] is True
    repo.update_resource.assert_awaited_once()
    args, kwargs = repo.update_resource.call_args
    assert args[0] == "res-1"
    assert args[1].get("folder_id") is None


@pytest.mark.asyncio
async def test_promote_to_same_scope_folder_succeeds(monkeypatch):
    resource = {"id": "res-1", "scope_type": "team", "scope_id": "42", "folder_id": "f-temp"}
    target_folder = {"id": "f-target", "scope_type": "team", "scope_id": "42"}
    _patch_repo(monkeypatch, resource=resource, folder=target_folder)
    out = await r.update_resource(
        "res-1", ResourceUpdate(folder_id="f-target"), _Auth()
    )
    assert out["success"] is True


@pytest.mark.asyncio
async def test_promote_to_other_scope_folder_rejected(monkeypatch):
    resource = {"id": "res-1", "scope_type": "personal", "scope_id": "u1", "folder_id": "f-temp"}
    other_scope_folder = {"id": "f-target", "scope_type": "team", "scope_id": "42"}
    _patch_repo(monkeypatch, resource=resource, folder=other_scope_folder)
    with pytest.raises(HTTPException) as e:
        await r.update_resource(
            "res-1", ResourceUpdate(folder_id="f-target"), _Auth()
        )
    assert e.value.status_code == 400
    assert "scope" in str(e.value.detail).lower()


@pytest.mark.asyncio
async def test_promote_to_missing_folder_rejected(monkeypatch):
    resource = {"id": "res-1", "scope_type": "personal", "scope_id": "u1", "folder_id": "f-temp"}
    _patch_repo(monkeypatch, resource=resource, folder=None)  # folder lookup miss
    with pytest.raises(HTTPException) as e:
        await r.update_resource(
            "res-1", ResourceUpdate(folder_id="f-ghost"), _Auth()
        )
    assert e.value.status_code == 404
```

- [ ] **Step 3: Run → fail.**

- [ ] **Step 4: Implement**

In `backend/app/schemas/resources.py`, add to `ResourceUpdate`:

```python
    # folder_id=null → move to scope root (used by the "Save" Promote action).
    # Non-null → move into that folder. The router validates same-scope.
    folder_id: Optional[str] = Field(None)
```

In `backend/app/api/resources_crud_router.py::update_resource`, after `update_data = data.model_dump(exclude_none=True)` and the `is_trashed`/`trashed_at` pop block, but **before** `if not update_data`, insert the folder validation. The full new function body:

```python
@router.patch("/{resource_id}")
async def update_resource(resource_id: str, data: ResourceUpdate, auth: AuthDep):
    """Update resource metadata. Use DELETE endpoint for trashing.

    ``folder_id`` may be set to move the resource between folders (used by the
    Promote action on chat temp resources). The destination folder must belong
    to the same scope as the resource; ``folder_id=None`` clears the folder
    (move to scope root).
    """
    try:
        repo = ResourcesRepository()
        resource = await repo.get_resource_by_id(resource_id)
        if not resource:
            raise HTTPException(status_code=404, detail="Resource not found")

        # Detect folder_id presence BEFORE exclude_none drops nulls, because
        # ``folder_id=None`` is meaningful (clear the folder).
        sent_folder_change = "folder_id" in data.model_fields_set
        update_data = data.model_dump(exclude_none=True)
        if sent_folder_change and "folder_id" not in update_data:
            # User sent folder_id=null explicitly → preserve as clear-to-root.
            update_data["folder_id"] = None

        # Prevent direct is_trashed manipulation via PATCH.
        update_data.pop("is_trashed", None)
        update_data.pop("trashed_at", None)

        # Validate folder move stays in the same scope.
        if sent_folder_change and update_data.get("folder_id") is not None:
            target = await repo.get_folder_by_id(update_data["folder_id"])
            if not target:
                raise HTTPException(
                    status_code=404, detail="Destination folder not found"
                )
            if (
                target.get("scope_type") != resource.get("scope_type")
                or str(target.get("scope_id")) != str(resource.get("scope_id"))
            ):
                raise HTTPException(
                    status_code=400,
                    detail="Destination folder is in a different scope",
                )

        if not update_data and not sent_folder_change:
            return {"success": True, "data": resource}

        result = await repo.update_resource(resource_id, update_data)
        return {"success": True, "data": result}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update resource {resource_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to update resource")
```

- [ ] **Step 5: Run → pass + lint + commit**

```bash
cd backend && uv run pytest tests/test_resource_promote.py -v
uv run black app/schemas/resources.py app/api/resources_crud_router.py tests/test_resource_promote.py
uv run isort app/schemas/resources.py app/api/resources_crud_router.py tests/test_resource_promote.py
uv run flake8 app/schemas/resources.py app/api/resources_crud_router.py tests/test_resource_promote.py
git add -A && git commit -m "feat(resources): PATCH supports folder_id for Promote (same-scope guard)"
```

---

## Task 5: DBOS sweeper — `temp_resource_sweeper`

**Files:**
- Create: `backend/app/workflows/temp_resource_sweeper.py`
- Modify: `backend/app/main.py` (register workflow import for DBOS pickup)
- Test: `backend/tests/test_temp_resource_sweeper.py`

- [ ] **Step 1: Read the reuse surface**

Read `app/workflows/scheduled_memory_archival.py` and `app/workflows/scheduled_master.py` for the canonical `@DBOS.workflow` + `@DBOS.scheduled` patterns. Note how they import DBOS (`from dbos import DBOS`), how scheduled args get passed (`scheduled_time, actual_time`), and how `@DBOS.step` is used for the DB-touching units. Read `app/repositories/resources_repository.py` for `get_folders` + a method to list resources inside a folder (`list_resources_in_folder` / `find_by_folder` — grep for it). If no such method exists, plan to add a small repo method `list_resources_in_folder(folder_id, include_trashed=False)`.

- [ ] **Step 2: Write the failing tests**

```python
# backend/tests/test_temp_resource_sweeper.py
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock
import pytest

from app.workflows import temp_resource_sweeper as m


def _resource(id_: str, days_old: int) -> dict:
    return {
        "id": id_,
        "created_at": (datetime.now(timezone.utc) - timedelta(days=days_old)).isoformat(),
    }


@pytest.mark.asyncio
async def test_sweep_scope_soft_deletes_expired(monkeypatch):
    """Resources older than TTL get is_trashed=true; younger ones untouched."""
    fake_repo = MagicMock()
    fake_repo.get_folders = AsyncMock(
        return_value=[{"id": "f-temp", "name": "temp"}]
    )
    fake_repo.list_resources_in_folder = AsyncMock(
        return_value=[
            _resource("r-old", days_old=45),
            _resource("r-young", days_old=5),
        ]
    )
    fake_repo.soft_delete_resource = AsyncMock()
    monkeypatch.setattr(m, "_build_repo", lambda: fake_repo)
    monkeypatch.setattr(
        m, "get_chat_temp_ttl_days", AsyncMock(return_value=30)
    )

    deleted = await m._sweep_scope("personal", "u1")

    assert deleted == 1
    fake_repo.soft_delete_resource.assert_awaited_once_with("r-old")


@pytest.mark.asyncio
async def test_sweep_scope_with_never_ttl_skips(monkeypatch):
    fake_repo = MagicMock()
    fake_repo.get_folders = AsyncMock(return_value=[{"id": "f-temp", "name": "temp"}])
    fake_repo.list_resources_in_folder = AsyncMock()
    fake_repo.soft_delete_resource = AsyncMock()
    monkeypatch.setattr(m, "_build_repo", lambda: fake_repo)
    monkeypatch.setattr(m, "get_chat_temp_ttl_days", AsyncMock(return_value=None))

    deleted = await m._sweep_scope("team", "42")

    assert deleted == 0
    fake_repo.list_resources_in_folder.assert_not_called()
    fake_repo.soft_delete_resource.assert_not_called()


@pytest.mark.asyncio
async def test_sweep_scope_no_temp_folder_skips(monkeypatch):
    fake_repo = MagicMock()
    fake_repo.get_folders = AsyncMock(return_value=[{"id": "f-other", "name": "Other"}])
    fake_repo.soft_delete_resource = AsyncMock()
    monkeypatch.setattr(m, "_build_repo", lambda: fake_repo)
    monkeypatch.setattr(m, "get_chat_temp_ttl_days", AsyncMock(return_value=30))

    assert await m._sweep_scope("personal", "u1") == 0
    fake_repo.soft_delete_resource.assert_not_called()


@pytest.mark.asyncio
async def test_iter_scopes_yields_all_personal_and_team(monkeypatch):
    """The sweeper iterates every user with a temp folder + every team."""
    fake_fetch = AsyncMock(side_effect=[
        [{"user_id": "u1"}, {"user_id": "u2"}],   # personal scopes
        [{"id": "42"}, {"id": "99"}],             # team scopes
    ])
    monkeypatch.setattr(m, "_fetch_scopes_with_temp", fake_fetch)
    scopes = [s async for s in m._iter_scopes()]
    assert set(scopes) == {("personal", "u1"), ("personal", "u2"), ("team", "42"), ("team", "99")}
```

- [ ] **Step 3: Run → fail.**

- [ ] **Step 4: Implement**

If `ResourcesRepository.list_resources_in_folder` does not exist yet, add the minimal implementation in `app/repositories/resources_repository.py`:

```python
async def list_resources_in_folder(
    self, folder_id: str, *, include_trashed: bool = False
) -> list[dict]:
    """Return all resources whose folder_id matches. Used by the temp sweeper."""
    client = await self._client()
    query = client.table("resources").select("id, created_at, is_trashed").eq(
        "folder_id", folder_id
    )
    if not include_trashed:
        query = query.eq("is_trashed", False)
    result = await query.execute()
    return result.data or []


async def soft_delete_resource(self, resource_id: str) -> None:
    """Mark a resource as trashed without removing the file on disk.

    The existing trash → eventual hard-delete pipeline owns the file cleanup;
    this only flips the boolean + timestamp.
    """
    client = await self._client()
    await client.table("resources").update(
        {"is_trashed": True, "trashed_at": "NOW()"}
    ).eq("id", resource_id).execute()
```

(Grep the repo first for any existing equivalent — `grep -n "soft_delete\|is_trashed.*True\|trashed_at" backend/app/repositories/resources_repository.py`. If a comparable method exists, reuse it instead of adding a duplicate.)

Then the sweeper:

```python
# backend/app/workflows/temp_resource_sweeper.py
"""DBOS scheduled workflow: soft-delete expired chat temp resources.

Runs daily at 04:00 UTC. For every scope (personal + team) that owns a
``temp`` folder, reads the scope's ``chat_temp_ttl_days`` setting and
soft-deletes resources whose ``created_at + ttl_days < now``.

Soft delete only — file cleanup is handled by the existing trash pipeline,
not by this workflow.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import AsyncIterator, Tuple

from dbos import DBOS
from loguru import logger

from app.services.library.chat_upload import TEMP_FOLDER_NAME
from app.services.library.temp_ttl_settings import get_chat_temp_ttl_days


def _build_repo():
    """Indirection so tests can monkeypatch the repo factory."""
    from app.repositories.resources_repository import ResourcesRepository

    return ResourcesRepository()


async def _fetch_scopes_with_temp(scope_type: str) -> list[dict]:
    """Return scope rows that own at least one ``temp`` folder.

    Uses a single SQL hit instead of scanning every user/team, so the sweeper
    scales with active users rather than total accounts.
    """
    from app.db import engine as db_engine

    if scope_type == "personal":
        sql = (
            "SELECT DISTINCT scope_id::text AS user_id FROM public.folders "
            "WHERE scope_type = 'personal' AND name = :name AND is_trashed = false"
        )
    else:
        sql = (
            "SELECT DISTINCT scope_id::text AS id FROM public.folders "
            "WHERE scope_type = 'team' AND name = :name AND is_trashed = false"
        )
    rows = await db_engine.fetch_all(sql, {"name": TEMP_FOLDER_NAME})
    return rows or []


async def _iter_scopes() -> AsyncIterator[Tuple[str, str]]:
    """Yield (scope_type, scope_id) for every scope that has a temp folder."""
    personal = await _fetch_scopes_with_temp("personal")
    for row in personal:
        yield ("personal", str(row["user_id"]))
    team = await _fetch_scopes_with_temp("team")
    for row in team:
        yield ("team", str(row["id"]))


def _is_expired(created_at_str: str, ttl_days: int, now: datetime) -> bool:
    """Parse the row's created_at (ISO string from PostgREST) and compare."""
    try:
        created = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
    except ValueError:
        # Malformed timestamps shouldn't happen, but never delete on parse error.
        logger.warning(f"[temp_sweeper] unparseable created_at {created_at_str!r}; skip")
        return False
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return (now - created) > timedelta(days=ttl_days)


async def _sweep_scope(scope_type: str, scope_id: str) -> int:
    """Soft-delete expired temp resources for one scope. Returns count deleted."""
    ttl_days = await get_chat_temp_ttl_days(scope_type, scope_id)
    if ttl_days is None:  # "never"
        return 0

    repo = _build_repo()
    folders = await repo.get_folders(scope_type, scope_id)
    temp_folder = next(
        (f for f in folders if f.get("name") == TEMP_FOLDER_NAME), None
    )
    if temp_folder is None:
        return 0

    resources = await repo.list_resources_in_folder(temp_folder["id"])
    now = datetime.now(timezone.utc)
    deleted = 0
    for res in resources:
        created_at = res.get("created_at")
        if created_at and _is_expired(created_at, ttl_days, now):
            await repo.soft_delete_resource(str(res["id"]))
            deleted += 1
    if deleted:
        logger.info(
            f"[temp_sweeper] {scope_type}/{scope_id}: soft-deleted {deleted} expired"
        )
    return deleted


@DBOS.workflow()
async def sweep_temp_resources() -> dict:
    """Sweep every scope's temp folder for expired resources."""
    started = datetime.now(timezone.utc)
    total_deleted = 0
    scopes_swept = 0
    async for scope_type, scope_id in _iter_scopes():
        try:
            total_deleted += await _sweep_scope(scope_type, scope_id)
            scopes_swept += 1
        except Exception as exc:
            # Failure in one scope must not block the rest — log and continue.
            logger.exception(
                f"[temp_sweeper] {scope_type}/{scope_id} sweep failed: {exc}"
            )
    duration_s = (datetime.now(timezone.utc) - started).total_seconds()
    logger.info(
        f"[temp_sweeper] done: scopes={scopes_swept} deleted={total_deleted} "
        f"duration_s={duration_s:.1f}"
    )
    return {
        "scopes_swept": scopes_swept,
        "total_deleted": total_deleted,
        "duration_s": duration_s,
    }


@DBOS.scheduled("0 4 * * *")  # daily at 04:00 UTC
@DBOS.workflow()
async def temp_resource_sweeper_scheduled(scheduled_time, actual_time):
    """Entry point — wraps sweep_temp_resources for DBOS scheduler."""
    return await sweep_temp_resources()
```

Then ensure `app/main.py` imports this module so DBOS sees the `@DBOS.scheduled` registration at startup (DBOS only registers workflows from modules that get imported during process boot). Locate where existing scheduled workflows are imported (`grep -n "scheduled_memory_archival\|scheduled_master\|workforce_dispatch" app/main.py`) and add an identical line for `app.workflows.temp_resource_sweeper`.

- [ ] **Step 5: Run → pass + lint + commit**

```bash
cd backend && uv run pytest tests/test_temp_resource_sweeper.py -v
uv run black app/workflows/temp_resource_sweeper.py app/repositories/resources_repository.py tests/test_temp_resource_sweeper.py app/main.py
uv run isort app/workflows/temp_resource_sweeper.py tests/test_temp_resource_sweeper.py app/main.py
uv run flake8 app/workflows/temp_resource_sweeper.py tests/test_temp_resource_sweeper.py
git add -A && git commit -m "feat(workflows): DBOS scheduled temp_resource_sweeper (daily 04:00 UTC)"
```

---

## Task 6: Frontend service — TTL get/set + promote

**Files:**
- Modify: `frontend/services/aiLibraryService.ts` OR Create: `frontend/services/tempTtlService.ts`
- Test: `frontend/services/tempTtlService.test.ts` (or extend existing test)

Per existing convention (one service per cohesive domain), CREATE a new file rather than overloading `aiLibraryService`.

- [ ] **Step 1: Read the reuse surface**

Read `frontend/services/aiLibraryService.ts` lines around `uploadChatAttachment` for the auth + fetch helper pattern (`getAuthHeaders`, the `base()` function, error envelope). Use the same `handle<T>` helper if it's exported; otherwise inline a tiny equivalent.

- [ ] **Step 2: Write the failing test**

```ts
// frontend/services/tempTtlService.test.ts
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { tempTtlService } from './tempTtlService';

vi.mock('./parserService', () => ({
  getAuthHeaders: async () => ({ Authorization: 'Bearer t' }),
}));

vi.mock('../utils/apiConfig', () => ({
  getApiUrl: () => 'https://api.test',
}));

describe('tempTtlService', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('getChatTempTtl reads /library/temp-ttl', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ ttl_days: 30 }), { status: 200 }),
      );
    const result = await tempTtlService.getChatTempTtl('personal', 'u1');
    expect(result).toEqual({ ttl_days: 30 });
    expect(fetchMock.mock.calls[0][0]).toContain(
      '/library/temp-ttl?scope_type=personal&scope_id=u1',
    );
  });

  it('setChatTempTtl PUTs the body', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ ttl_days: 7 }), { status: 200 }),
      );
    await tempTtlService.setChatTempTtl('team', '42', 7);
    const [, init] = fetchMock.mock.calls[0];
    expect((init as RequestInit).method).toBe('PUT');
    expect(JSON.parse((init as RequestInit).body as string)).toEqual({
      scope_type: 'team',
      scope_id: '42',
      ttl_days: 7,
    });
  });

  it('promoteResource POSTs to /move with scope context', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ success: true, data: {} }), { status: 200 }),
      );
    await tempTtlService.promoteResource('res-1', {
      folderId: null,
      scopeType: 'personal',
      scopeId: 'u1',
    });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain('/resources/res-1/move');
    expect((init as RequestInit).method).toBe('POST');
    expect(JSON.parse((init as RequestInit).body as string)).toEqual({
      folder_id: null,
      scope_type: 'personal',
      scope_id: 'u1',
    });
  });

  it('promoteResource forwards a non-null folder_id', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ success: true, data: {} }), { status: 200 }),
      );
    await tempTtlService.promoteResource('res-1', {
      folderId: 'f-target',
      scopeType: 'team',
      scopeId: '42',
    });
    const [, init] = fetchMock.mock.calls[0];
    expect(JSON.parse((init as RequestInit).body as string)).toEqual({
      folder_id: 'f-target',
      scope_type: 'team',
      scope_id: '42',
    });
  });
});
```

- [ ] **Step 3: Run → fail.**

```bash
cd frontend && npx vitest run services/tempTtlService.test.ts --reporter=default
```

- [ ] **Step 4: Implement**

```ts
// frontend/services/tempTtlService.ts
/**
 * Per-scope chat temp resource TTL settings + the Promote action.
 *
 * The "Save" / "Save to folder..." actions in the resource library reuse
 * PATCH /resources/{id} (the existing endpoint) — promote is just a
 * folder_id change. TTL get/set hit the new /library/temp-ttl endpoints.
 */

import { getAuthHeaders } from './parserService';
import { getApiUrl } from '../utils/apiConfig';

const apiBase = (): string => `${getApiUrl()}/api/v1`;

async function handle<T>(resp: Response): Promise<T> {
  if (!resp.ok) {
    const text = await resp.text().catch(() => '');
    throw new Error(`${resp.status}: ${text}`);
  }
  return resp.json() as Promise<T>;
}

export type ScopeType = 'personal' | 'team';

export interface TempTtlResponse {
  ttl_days: number; // positive int = days; -1 = never expire
}

export const tempTtlService = {
  async getChatTempTtl(
    scope_type: ScopeType,
    scope_id: string,
  ): Promise<TempTtlResponse> {
    const qs = new URLSearchParams({ scope_type, scope_id });
    const resp = await fetch(
      `${apiBase()}/library/temp-ttl?${qs.toString()}`,
      { headers: await getAuthHeaders() },
    );
    return handle<TempTtlResponse>(resp);
  },

  async setChatTempTtl(
    scope_type: ScopeType,
    scope_id: string,
    ttl_days: number,
  ): Promise<TempTtlResponse> {
    const resp = await fetch(`${apiBase()}/library/temp-ttl`, {
      method: 'PUT',
      headers: {
        ...(await getAuthHeaders()),
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ scope_type, scope_id, ttl_days }),
    });
    return handle<TempTtlResponse>(resp);
  },

  /**
   * Promote a temp resource — move it out of the `temp` folder.
   *
   * Wraps the existing `POST /api/v1/resources/{id}/move` endpoint.
   * Scope context is required because a resource may be present in multiple
   * scopes (the `resource_items` join carries scope membership). The caller
   * supplies the scope they want to move WITHIN (typically the currently
   * viewed scope).
   *
   * @param resourceId target resource id
   * @param dest       folder + scope context. `folderId=null` clears to scope root.
   */
  async promoteResource(
    resourceId: string,
    dest: { folderId: string | null; scopeType: ScopeType; scopeId: string },
  ): Promise<{ success: boolean; data: unknown }> {
    const resp = await fetch(
      `${apiBase()}/resources/${encodeURIComponent(resourceId)}/move`,
      {
        method: 'POST',
        headers: {
          ...(await getAuthHeaders()),
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          folder_id: dest.folderId,
          scope_type: dest.scopeType,
          scope_id: dest.scopeId,
        }),
      },
    );
    return handle<{ success: boolean; data: unknown }>(resp);
  },
};
```

- [ ] **Step 5: Run → pass + commit**

```bash
cd frontend && npx vitest run services/tempTtlService.test.ts --reporter=default
git add services/tempTtlService.ts services/tempTtlService.test.ts
git commit -m "feat(library): tempTtlService — TTL get/set + promoteResource"
```

---

## Task 7: Settings UI — TTL section in the general tab

**Files:**
- Create: `frontend/components/ChatTempTtlPanel.tsx`
- Modify: `frontend/components/SettingsView.tsx` (general tab block)
- Test: `frontend/components/ChatTempTtlPanel.test.tsx`

- [ ] **Step 1: Read the reuse surface**

Read `frontend/components/SettingsView.tsx` from line 308 (`{activeTab === 'general' && (`) for ~80 lines so the new section visually matches existing controls (Tailwind classes, label sizing, save-button styling). Read how the user / team list is sourced — search for `useTeams`, `useCurrentUser`, or similar hooks. Read `frontend/contexts/ResourcesContext.tsx` to find the canonical scope picker pattern if it's reusable.

- [ ] **Step 2: Write the failing test**

```tsx
// frontend/components/ChatTempTtlPanel.test.tsx
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { ChatTempTtlPanel } from './ChatTempTtlPanel';
import { tempTtlService } from '../services/tempTtlService';

vi.mock('../services/tempTtlService', () => ({
  tempTtlService: {
    getChatTempTtl: vi.fn(),
    setChatTempTtl: vi.fn(),
  },
}));

describe('ChatTempTtlPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('loads and displays the current TTL on mount', async () => {
    (tempTtlService.getChatTempTtl as ReturnType<typeof vi.fn>).mockResolvedValue({
      ttl_days: 14,
    });
    render(<ChatTempTtlPanel scopeType="personal" scopeId="u1" />);
    await waitFor(() => {
      expect(screen.getByLabelText(/Chat attachment TTL/i)).toHaveValue('14');
    });
  });

  it('persists a new selection via setChatTempTtl', async () => {
    (tempTtlService.getChatTempTtl as ReturnType<typeof vi.fn>).mockResolvedValue({
      ttl_days: 30,
    });
    (tempTtlService.setChatTempTtl as ReturnType<typeof vi.fn>).mockResolvedValue({
      ttl_days: 7,
    });
    render(<ChatTempTtlPanel scopeType="team" scopeId="42" />);
    const select = await screen.findByLabelText(/Chat attachment TTL/i);
    fireEvent.change(select, { target: { value: '7' } });
    await waitFor(() => {
      expect(tempTtlService.setChatTempTtl).toHaveBeenCalledWith('team', '42', 7);
    });
  });

  it('renders "Never" for ttl_days=-1', async () => {
    (tempTtlService.getChatTempTtl as ReturnType<typeof vi.fn>).mockResolvedValue({
      ttl_days: -1,
    });
    render(<ChatTempTtlPanel scopeType="personal" scopeId="u1" />);
    const select = await screen.findByLabelText(/Chat attachment TTL/i);
    await waitFor(() => {
      expect(select).toHaveValue('-1');
    });
  });
});
```

- [ ] **Step 3: Run → fail.**

- [ ] **Step 4: Implement**

```tsx
// frontend/components/ChatTempTtlPanel.tsx
/**
 * Settings panel section: choose the TTL for chat attachment temp resources.
 *
 * The dropdown maps to days (positive ints) or `-1` (never expire). The
 * sweeper reads this value daily and soft-deletes expired temp resources.
 */

import { useEffect, useState } from 'react';
import { tempTtlService, ScopeType } from '../services/tempTtlService';

interface Props {
  scopeType: ScopeType;
  scopeId: string;
}

const OPTIONS: ReadonlyArray<{ value: number; label: string }> = [
  { value: 7, label: '7 days' },
  { value: 14, label: '14 days' },
  { value: 30, label: '30 days' },
  { value: 90, label: '90 days' },
  { value: -1, label: 'Never' },
];

export function ChatTempTtlPanel({ scopeType, scopeId }: Props) {
  const [ttl, setTtl] = useState<number | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    setTtl(null);
    tempTtlService
      .getChatTempTtl(scopeType, scopeId)
      .then((r) => {
        if (!cancelled) setTtl(r.ttl_days);
      })
      .catch((err) => {
        if (!cancelled) setError(String(err));
      });
    return () => {
      cancelled = true;
    };
  }, [scopeType, scopeId]);

  async function onChange(next: number) {
    setSaving(true);
    setError(null);
    try {
      const r = await tempTtlService.setChatTempTtl(scopeType, scopeId, next);
      setTtl(r.ttl_days);
    } catch (err) {
      setError(String(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="flex flex-col gap-2 py-3">
      <label
        htmlFor={`ttl-${scopeType}-${scopeId}`}
        className="text-sm font-medium text-gray-700 dark:text-gray-200"
      >
        Chat attachment TTL ({scopeType})
      </label>
      <select
        id={`ttl-${scopeType}-${scopeId}`}
        className="rounded border px-2 py-1 text-sm dark:bg-gray-800 dark:border-gray-600"
        value={ttl === null ? '' : String(ttl)}
        disabled={ttl === null || saving}
        onChange={(e) => onChange(Number(e.target.value))}
      >
        {ttl === null && <option value="">Loading…</option>}
        {OPTIONS.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
      {error && (
        <span className="text-xs text-red-600 dark:text-red-400">{error}</span>
      )}
      <p className="text-xs text-gray-500 dark:text-gray-400">
        Temp uploads in this scope are soft-deleted after this many days.
        Promoted resources (Saved out of the temp folder) are not affected.
      </p>
    </div>
  );
}
```

In `SettingsView.tsx`, inside the `{activeTab === 'general' && (` block, add (positioned wherever feels coherent with existing sections — typically after the download path settings):

```tsx
import { ChatTempTtlPanel } from './ChatTempTtlPanel';
// ... in the general tab block:
<section className="border-t pt-4 mt-4 dark:border-gray-700">
  <h3 className="text-base font-semibold mb-2">Chat attachment TTL</h3>
  {/* Personal scope is always present. */}
  <ChatTempTtlPanel scopeType="personal" scopeId={currentUserId} />
  {/* One panel per team the user belongs to. */}
  {userTeams.map((t) => (
    <ChatTempTtlPanel key={t.id} scopeType="team" scopeId={String(t.id)} />
  ))}
</section>
```

Wire `currentUserId` and `userTeams` from the props or hooks the existing SettingsView already uses (read Step 1's findings). If the existing component doesn't pass them yet, add them as props from the page-level caller (`SettingsPage.tsx`).

- [ ] **Step 5: Run → pass + commit**

```bash
cd frontend && npx vitest run components/ChatTempTtlPanel.test.tsx --reporter=default
git add components/ChatTempTtlPanel.tsx components/ChatTempTtlPanel.test.tsx components/SettingsView.tsx
git commit -m "feat(settings): Chat attachment TTL panel (per personal + team scope)"
```

---

## Task 8: Resources view — Save split-button + TTL badge on temp rows

**Files:**
- Create: `frontend/components/TempResourceActions.tsx`
- Modify: `frontend/components/ResourcesViewInner.tsx`
- Test: `frontend/components/TempResourceActions.test.tsx`

- [ ] **Step 1: Read the reuse surface**

Read `frontend/components/ResourcesViewInner.tsx` to find where individual resource rows are rendered (search for the row component or `.map(` over resources). Read `frontend/components/FolderPickerModal.tsx` for its props (callback, default-selected, scope). Read `frontend/contexts/ResourcesContext.tsx` to find the current scope (`scopeType`, `scopeId`) + the current folder being viewed (its `id` and `name`) — the Save action needs both.

- [ ] **Step 2: Write the failing test**

```tsx
// frontend/components/TempResourceActions.test.tsx
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { TempResourceActions } from './TempResourceActions';
import { tempTtlService } from '../services/tempTtlService';

vi.mock('../services/tempTtlService', () => ({
  tempTtlService: { promoteResource: vi.fn() },
}));

vi.mock('./FolderPickerModal', () => ({
  FolderPickerModal: ({ onPick, onClose }: { onPick: (id: string) => void; onClose: () => void }) => (
    <div data-testid="folder-picker">
      <button onClick={() => { onPick('f-target'); onClose(); }}>pick</button>
    </div>
  ),
}));

describe('TempResourceActions', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('Save button promotes to root (folder_id=null)', async () => {
    (tempTtlService.promoteResource as ReturnType<typeof vi.fn>).mockResolvedValue({ success: true });
    const onDone = vi.fn();
    render(
      <TempResourceActions
        resourceId="res-1"
        scopeType="personal"
        scopeId="u1"
        onDone={onDone}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /^Save$/ }));
    await waitFor(() => {
      expect(tempTtlService.promoteResource).toHaveBeenCalledWith('res-1', null);
      expect(onDone).toHaveBeenCalled();
    });
  });

  it('arrow + picker promotes to the picked folder', async () => {
    (tempTtlService.promoteResource as ReturnType<typeof vi.fn>).mockResolvedValue({ success: true });
    const onDone = vi.fn();
    render(
      <TempResourceActions
        resourceId="res-1"
        scopeType="personal"
        scopeId="u1"
        onDone={onDone}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /Save to folder/i }));
    fireEvent.click(await screen.findByText('pick'));
    await waitFor(() => {
      expect(tempTtlService.promoteResource).toHaveBeenCalledWith('res-1', 'f-target');
      expect(onDone).toHaveBeenCalled();
    });
  });
});
```

- [ ] **Step 3: Run → fail.**

- [ ] **Step 4: Implement**

```tsx
// frontend/components/TempResourceActions.tsx
/**
 * Save / Save to folder split-button shown on temp-folder rows.
 *
 * - "Save"            → promoteResource(id, null)        (move to scope root)
 * - "Save to folder…" → opens FolderPickerModal then     promoteResource(id, picked)
 *
 * Both code paths call back via `onDone` so the caller can refresh the row.
 */

import { useState } from 'react';
import { tempTtlService, ScopeType } from '../services/tempTtlService';
import { FolderPickerModal } from './FolderPickerModal';

interface Props {
  resourceId: string;
  scopeType: ScopeType;
  scopeId: string;
  onDone: () => void;
}

export function TempResourceActions({ resourceId, scopeType, scopeId, onDone }: Props) {
  const [busy, setBusy] = useState(false);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function promote(folderId: string | null) {
    setBusy(true);
    setError(null);
    try {
      await tempTtlService.promoteResource(resourceId, folderId);
      onDone();
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="inline-flex items-center gap-1">
      <button
        type="button"
        disabled={busy}
        className="rounded bg-blue-600 px-2 py-1 text-xs text-white hover:bg-blue-700 disabled:opacity-50"
        onClick={() => promote(null)}
      >
        Save
      </button>
      <button
        type="button"
        disabled={busy}
        aria-label="Save to folder"
        className="rounded bg-blue-600 px-1 py-1 text-xs text-white hover:bg-blue-700 disabled:opacity-50"
        onClick={() => setPickerOpen(true)}
      >
        ▾
      </button>
      {pickerOpen && (
        <FolderPickerModal
          scopeType={scopeType}
          scopeId={scopeId}
          onClose={() => setPickerOpen(false)}
          onPick={(id) => promote(id)}
        />
      )}
      {error && <span className="text-xs text-red-600">{error}</span>}
    </div>
  );
}
```

In `ResourcesViewInner.tsx`, detect "we're viewing the temp folder" — the simplest way is `currentFolder?.name === 'temp'` (read Step 1's notes for the actual prop name). When true, render the actions component + a TTL badge per row. Example row decoration:

```tsx
// Inside the row render, near other row actions:
{currentFolder?.name === 'temp' && (
  <>
    <span className="text-xs text-amber-700 dark:text-amber-300">
      {ttlBadgeText(resource.created_at, scopeTtl)}
    </span>
    <TempResourceActions
      resourceId={resource.id}
      scopeType={scopeType}
      scopeId={scopeId}
      onDone={refreshResources}
    />
  </>
)}
```

And the helper (place above the component or in a small util file `frontend/utils/tempTtl.ts`):

```ts
// frontend/utils/tempTtl.ts
export function ttlBadgeText(createdAt: string, ttlDays: number | null): string {
  if (ttlDays === null) return 'never expires';
  const created = new Date(createdAt).getTime();
  if (Number.isNaN(created)) return '';
  const expiresAt = created + ttlDays * 24 * 60 * 60 * 1000;
  const remainingMs = expiresAt - Date.now();
  if (remainingMs <= 0) return 'expired';
  const remainingDays = Math.ceil(remainingMs / (24 * 60 * 60 * 1000));
  if (remainingDays === 1) return 'expires today';
  return `expires in ${remainingDays} days`;
}
```

`scopeTtl` is loaded once per scope via `tempTtlService.getChatTempTtl(scopeType, scopeId)` and cached in component state (or in `ResourcesContext`). If `ResourcesContext` already loads scope-level settings, extend it there to keep the row render synchronous.

- [ ] **Step 5: Run → pass + commit**

```bash
cd frontend && npx vitest run components/TempResourceActions.test.tsx --reporter=default
git add components/TempResourceActions.tsx components/TempResourceActions.test.tsx utils/tempTtl.ts components/ResourcesViewInner.tsx
git commit -m "feat(resources): Save split-button + TTL badge on temp-folder rows"
```

---

## Task 9: Final verification

- [ ] Backend full suite passes:

```bash
cd backend && uv run pytest tests/ -q
```

- [ ] Frontend full suite passes:

```bash
cd frontend && npx vitest run --reporter=default
```

- [ ] Import chain OK:

```bash
cd backend && uv run python -c "import app.api.temp_ttl_router, app.workflows.temp_resource_sweeper, app.services.library.temp_ttl_settings; print('OK')"
```

- [ ] Lint clean across all touched backend files (black/isort/flake8) and frontend (vitest already implies tsc transform).

- [ ] **Manual sanity (against local dev stack)**:
  - Open Settings → general tab → confirm "Chat attachment TTL" section shows Personal + each team you belong to.
  - Change a value, refresh, confirm it persists (the API rounds-trips correctly).
  - Upload a chat attachment in any session, navigate to the resource library → temp folder, confirm the row shows "expires in N days" + Save button.
  - Click Save → row moves out of temp folder (now at root). The "expires" badge disappears since the row is no longer in `temp`.
  - Click ▾ → FolderPickerModal opens; pick a folder; confirm row moves there.

- [ ] **Deploy note for PR body**: includes a new migration (225), a new DBOS scheduled workflow (runs daily 04:00 UTC), one new router (`/api/v1/library/temp-ttl`), one extension to PATCH `/resources/{id}` (folder_id), and two frontend additions (Settings TTL panel, Save split-button + TTL badge). No backfill needed — defaults handle missing settings rows. After deploy, run the sweeper manually once via DBOS admin or just wait for the first 04:00 UTC tick.

## Self-review checklist

- **Spec coverage**: Sub-plan 2 spec calls for Settings TTL (✓ Task 3 + 7), DBOS sweeper (✓ Task 5), promote action (✓ Tasks 4 + 6 + 8), Temp sidebar visibility (✓ Task 8 — TTL badge + Save inline; no new sidebar route per locked decision #4).
- **Reuse vs new**: extended existing `PATCH /resources/{id}` (one schema field + same-scope guard) instead of new `/promote` endpoint. Reused `ResourcesService.upload_resource` indirectly through PATCH path. Reused `FolderPickerModal` for "Save to folder…".
- **Open for implementer to confirm by reading**: (a) the exact `app/main.py` `include_router` and workflow-import sites (Task 3 Step 4 + Task 5 Step 4) — codebase convention varies, match it. (b) whether `ResourcesRepository` already has a soft-delete helper (Task 5 Step 4 — grep first, only add if absent). (c) the row render entry point in `ResourcesViewInner.tsx` (Task 8 Step 1 — search for the resource-list `.map(`). (d) how `currentUserId` / `userTeams` flow into `SettingsView` (Task 7 Step 4 — match the existing hook/prop pattern).
- **Known limitation**: a malformed `created_at` row (won't happen for any row created by `ResourcesService.upload_resource`, but defensive) gets a warning + skipped — never deleted on parse error. Documented in `_is_expired`.
- **Not in scope**: hard delete of trashed files (lives in the existing trash pipeline). The hard-delete cycle of trashed `temp` resources is whatever that pipeline already does — no special handling needed here.
