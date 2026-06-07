# Unify Workflow DB Access Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the three inconsistent DB-access patterns left in `app/workflows/` so backend data access is uniform — workflows reach the asyncpg engine either directly or through the repo factories, never by bypassing the factory, never via raw `psycopg`.

**Architecture:** Three independent phases, each shippable on its own. Phase 1 routes 9 direct repo constructions through the existing `get_*_repository()` factories (so the `USE_ASYNCPG_*` flags actually apply). Phase 2 ports `issue_lifecycle.py` off raw `psycopg` (a third DB driver) onto the SQLAlchemy engine. Phase 3 ports `MemoryWriter` (the service behind `write_memory`'s extract step) off supabase-py REST onto the engine, reusing the pgvector text-cast pattern already proven in `scheduled_memory_consolidation.py`.

**Tech Stack:** Python 3.12, SQLAlchemy 2.0 async engine over asyncpg (`app/db/engine.py`), DBOS workflows/steps, pytest, pgvector. Lint: black + isort + flake8 (CI scopes to changed files).

**Scope decision (why this set, not more):** the httpcore leak is already fixed for *all* supabase-py by `keepalive=0` (#328), so migration is uniformity, not a fix. We do NOT convert the ~85 non-workflow direct repo constructions in `api/` and `tasks/` (mass-flipping them onto the asyncpg variants is broad behaviour change for zero leak benefit). We do NOT migrate the service-delegating workflows (`ai_summary`, `storyboard`, etc.) or `parse.py`'s `TagsRepository` (no asyncpg variant exists — would mean building one for a cold path). Frontend / auth / storage / realtime stay REST forever.

---

## File Structure

| File | Responsibility | Phase |
|------|----------------|-------|
| `backend/app/workflows/download.py` | swap 4× `MediaRepository()` + 1× `ResourcesRepository()` → factory | 1 |
| `backend/app/workflows/transcode.py` | swap 2× `ResourcesRepository()` → factory | 1 |
| `backend/app/workflows/extract_audio.py` | swap 1× `MediaRepository()` → factory | 1 |
| `backend/app/workflows/scheduled_recovery.py` | swap 1× `MediaRepository()` → factory (retry step) | 1 |
| `backend/app/workflows/issue_lifecycle.py` | rewrite 5 steps psycopg → engine; steps + workflow become async | 2 |
| `backend/tests/test_issue_lifecycle_sql.py` | NEW — assert ported SQL/params per step | 2 |
| `backend/app/db/engine.py` | add `execute_returning_one` helper | 3 |
| `backend/tests/test_engine_helpers.py` | add test for `execute_returning_one` | 3 |
| `backend/app/services/ai/memory/writer.py` | port all 5 supabase calls → engine; add `_parse_embedding` | 3 |
| `backend/app/workflows/write_memory.py` | drop `get_async_supabase_admin()`; construct `MemoryWriter` without a client | 3 |
| `backend/tests/test_memory_writer.py` | update mocks REST → engine | 3 |

---

# Phase 1 — Workflow repo constructions → factory

**Why:** `download/transcode/extract_audio/scheduled_recovery` build `MediaRepository()` / `ResourcesRepository()` directly, which always uses the REST base class — even though prod has `USE_ASYNCPG_MEDIA=USE_ASYNCPG_RESOURCES=true`. The factory `get_media_repository()` / `get_resources_repository()` returns the asyncpg subclass when the flag + Supavisor are set, falling back to REST otherwise. The subclass inherits every non-overridden method from the REST base, so swapping is safe: covered methods go asyncpg, the rest behave exactly as today.

### Task 1.1: download.py — route both repos through the factory

**Files:**
- Modify: `backend/app/workflows/download.py` (4 sites: ~65/80, ~188-196, ~638-641, ~694-697)

- [ ] **Step 1: Apply the edits**

Site A (function around line 65-80):
```python
# OLD
    from app.repositories.media_repository import MediaRepository
    ...
    media_repo = MediaRepository()
# NEW
    from app.repositories.media_repository import get_media_repository
    ...
    media_repo = get_media_repository()
```

Site B (function around line 188-196):
```python
# OLD
    from app.repositories.media_repository import MediaRepository
    from app.repositories.resources_repository import ResourcesRepository
    ...
    media_repo = MediaRepository()
    ...
        res_repo = ResourcesRepository()
# NEW
    from app.repositories.media_repository import get_media_repository
    from app.repositories.resources_repository import get_resources_repository
    ...
    media_repo = get_media_repository()
    ...
        res_repo = get_resources_repository()
```

Site C (line ~638-641) and Site D (line ~694-697), both identical shape:
```python
# OLD
        from app.repositories.media_repository import MediaRepository
            repo = MediaRepository()
# NEW
        from app.repositories.media_repository import get_media_repository
            repo = get_media_repository()
```

- [ ] **Step 2: Verify no direct constructions remain in this file**

Run: `cd backend && grep -nE "MediaRepository\(\)|ResourcesRepository\(\)" app/workflows/download.py`
Expected: no output (all replaced with `get_*_repository()`).

- [ ] **Step 3: Import smoke**

Run: `cd backend && uv run python -c "import app.workflows.download; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Run download workflow tests**

Run: `cd backend && uv run pytest tests/ -k "download" -q`
Expected: PASS (same set that passed before the edit).

- [ ] **Step 5: Commit**

```bash
git add backend/app/workflows/download.py
git commit -m "refactor(workflows): download → repo factories (#199)"
```

### Task 1.2: transcode.py + extract_audio.py + scheduled_recovery.py

**Files:**
- Modify: `backend/app/workflows/transcode.py` (lines 39/41 and 120/155)
- Modify: `backend/app/workflows/extract_audio.py` (lines 45/48)
- Modify: `backend/app/workflows/scheduled_recovery.py` (lines 37/41)

- [ ] **Step 1: Apply the edits**

`transcode.py` (both occurrences, replace_all):
```python
# OLD
    from app.repositories.resources_repository import ResourcesRepository
    repo = ResourcesRepository()
# NEW
    from app.repositories.resources_repository import get_resources_repository
    repo = get_resources_repository()
```

`extract_audio.py`:
```python
# OLD
    from app.repositories.media_repository import MediaRepository
        await MediaRepository().update(platform_id, {"extract_audio_status": status})
# NEW
    from app.repositories.media_repository import get_media_repository
        await get_media_repository().update(platform_id, {"extract_audio_status": status})
```

`scheduled_recovery.py` (inside `retry_failed_downloads_step`):
```python
# OLD
    from app.repositories.media_repository import MediaRepository
    repo = MediaRepository()
# NEW
    from app.repositories.media_repository import get_media_repository
    repo = get_media_repository()
```

- [ ] **Step 2: Verify no direct constructions remain**

Run: `cd backend && grep -rnE "MediaRepository\(\)|ResourcesRepository\(\)" app/workflows/transcode.py app/workflows/extract_audio.py app/workflows/scheduled_recovery.py`
Expected: no output.

- [ ] **Step 3: Import smoke + tests**

Run: `cd backend && uv run python -c "import app.workflows.transcode, app.workflows.extract_audio, app.workflows.scheduled_recovery; print('OK')"`
Expected: `OK`
Run: `cd backend && uv run pytest tests/ -k "transcode or extract_audio or recovery" -q`
Expected: PASS.

- [ ] **Step 4: Lint changed files**

Run: `cd backend && uv run black --check app/workflows/transcode.py app/workflows/extract_audio.py app/workflows/scheduled_recovery.py app/workflows/download.py && uv run isort --check-only app/workflows/transcode.py app/workflows/extract_audio.py app/workflows/scheduled_recovery.py app/workflows/download.py`
Expected: all clean.

- [ ] **Step 5: Commit**

```bash
git add backend/app/workflows/transcode.py backend/app/workflows/extract_audio.py backend/app/workflows/scheduled_recovery.py
git commit -m "refactor(workflows): transcode/extract_audio/recovery → repo factories (#199)"
```

---

# Phase 2 — issue_lifecycle: raw psycopg → SQLAlchemy engine

**Why:** `issue_lifecycle.py` is the only backend file using `psycopg` (a third DB driver, on `DBOS_DATABASE_URL` with explicit `SET ROLE service_role`). The engine connects to the same Postgres via Supavisor with sufficient privilege (every other migrated workflow writes `public.*` without `SET ROLE`), so the role switch is dropped. The 5 steps are currently **sync**; the engine helpers are async, so the steps and the `execute_issue` workflow become `async def` (matches the async workflows already in the repo, e.g. `scheduled_recovery`).

**Pre-req check (do first):**

- [ ] **Step 0: Confirm how execute_issue + its steps are invoked**

Run: `cd backend && grep -rnE "execute_issue|atomic_checkout|set_status\(|clear_lock|create_agent_run_for_issue|load_issue" app/ --include=*.py | grep -v "app/workflows/issue_lifecycle.py"`
Expected: callers go through `DBOS.start_workflow` / enqueue (which accept async workflows). If any caller invokes these **synchronously** (e.g. `execute_issue(id)` and uses the return without await), note it — those call sites must be awaited in Step 4. Record the list before editing.

### Task 2.1: Write the failing SQL-shape tests

**Files:**
- Create: `backend/tests/test_issue_lifecycle_sql.py`

- [ ] **Step 1: Write the failing test**

```python
"""issue_lifecycle steps must hit the SQLAlchemy engine with the right SQL/params
(no more raw psycopg). We patch the engine helpers + capture calls."""

from __future__ import annotations

from unittest.mock import patch

import pytest


async def test_atomic_checkout_updates_with_lock_guard():
    import app.workflows.issue_lifecycle as il

    captured = {}

    async def fake_execute(sql, params=None):
        captured["sql"] = sql
        captured["params"] = params
        return 1  # one row locked

    with patch("app.db.engine.execute", fake_execute):
        locked = await il.atomic_checkout(42, "wf-1")

    assert locked is True
    assert "UPDATE public.issues" in captured["sql"]
    assert "execution_locked_at IS NULL" in captured["sql"]
    assert captured["params"] == {"wid": "wf-1", "id": 42}


async def test_atomic_checkout_returns_false_when_no_row():
    import app.workflows.issue_lifecycle as il

    async def fake_execute(sql, params=None):
        return 0

    with patch("app.db.engine.execute", fake_execute):
        assert await il.atomic_checkout(42, "wf-1") is False


async def test_set_status_in_progress_sets_started_at():
    import app.workflows.issue_lifecycle as il

    captured = {}

    async def fake_execute(sql, params=None):
        captured["sql"] = sql
        captured["params"] = params
        return 1

    with patch("app.db.engine.execute", fake_execute):
        await il.set_status(7, "in_progress")

    assert "status = :status" in captured["sql"]
    assert "started_at = :ts" in captured["sql"]
    assert captured["params"]["status"] == "in_progress"
    assert "ts" in captured["params"] and captured["params"]["id"] == 7


async def test_set_status_blocked_writes_jsonb_error_state():
    import app.workflows.issue_lifecycle as il

    captured = {}

    async def fake_execute(sql, params=None):
        captured["sql"] = sql
        captured["params"] = params
        return 1

    with patch("app.db.engine.execute", fake_execute):
        await il.set_status(7, "blocked", error_code="x", error_message="boom")

    assert "execution_state = CAST(:state AS jsonb)" in captured["sql"]
    assert '"error_code": "x"' in captured["params"]["state"]


async def test_load_issue_raises_when_missing():
    import app.workflows.issue_lifecycle as il

    async def fake_fetch_one(sql, params=None):
        return None

    with patch("app.db.engine.fetch_one", fake_fetch_one):
        with pytest.raises(RuntimeError, match="not found"):
            await il.load_issue(999)
```

- [ ] **Step 2: Run — verify it FAILS**

Run: `cd backend && uv run pytest tests/test_issue_lifecycle_sql.py -q`
Expected: FAIL — current steps are sync + use psycopg, so `await il.atomic_checkout(...)` raises `TypeError: object bool can't be used in 'await' expression` (or the patch target `app.db.engine.execute` is never called).

### Task 2.2: Rewrite issue_lifecycle.py on the engine

**Files:**
- Modify: `backend/app/workflows/issue_lifecycle.py` (whole file)

- [ ] **Step 1: Replace the module body**

Replace the header import block (drop `os`, `psycopg`, `psycopg.rows`, `_dsn`) and rewrite the 5 steps + workflow as async:

```python
"""execute_issue parent workflow — design doc Protocol 2.

Lifecycle: issue(todo) → atomic_checkout → set_status(in_progress) →
[agent/leaf runs] → set_status(done|in_review|blocked) → clear_lock.

DB access goes through the SQLAlchemy engine (app.db.engine) over asyncpg
→ Supavisor — same privileged connection the other migrated workflows use,
so the legacy `SET ROLE service_role` from the raw-psycopg version is gone.
Steps + workflow are async because the engine helpers are async.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

from dbos import DBOS
from loguru import logger


@DBOS.step()
async def atomic_checkout(issue_id: int, dbos_workflow_id: str) -> bool:
    """Atomically claim an issue. False if someone else already holds the lock."""
    from app.db import engine as db_engine

    locked = await db_engine.execute(
        "UPDATE public.issues SET execution_locked_at = now(), "
        "dbos_workflow_id = :wid "
        "WHERE id = :id AND execution_locked_at IS NULL",
        {"wid": dbos_workflow_id, "id": issue_id},
    )
    return locked > 0


@DBOS.step()
async def set_status(
    issue_id: int,
    status: str,
    *,
    error_code: Optional[str] = None,
    error_message: Optional[str] = None,
) -> None:
    """Transition issue.status with side-effect timestamps (design Protocol 5)."""
    from app.db import engine as db_engine

    now_dt = datetime.now(timezone.utc)
    cols = ["status = :status"]
    params: dict[str, Any] = {"status": status}
    if status == "in_progress":
        cols.append("started_at = :ts")
        params["ts"] = now_dt
    elif status == "done":
        cols.append("completed_at = :ts")
        params["ts"] = now_dt
    elif status == "cancelled":
        cols.append("cancelled_at = :ts")
        params["ts"] = now_dt
    if error_code or error_message:
        cols.append("execution_state = CAST(:state AS jsonb)")
        params["state"] = json.dumps(
            {"error_code": error_code, "error_message": error_message}
        )
    params["id"] = issue_id
    await db_engine.execute(
        f"UPDATE public.issues SET {', '.join(cols)} WHERE id = :id", params
    )


@DBOS.step()
async def clear_lock(issue_id: int) -> None:
    """Release the execution lock so the issue can be retried later."""
    from app.db import engine as db_engine

    await db_engine.execute(
        "UPDATE public.issues SET execution_locked_at = NULL WHERE id = :id",
        {"id": issue_id},
    )


@DBOS.step()
async def create_agent_run_for_issue(
    issue_id: int, agent_id: str, user_id: str, dbos_workflow_id: str
) -> Optional[str]:
    """Create an agent_runs row linked to the issue. Returns run id or None
    (never blocks the workflow on this)."""
    import uuid

    from app.db import engine as db_engine

    run_id = str(uuid.uuid4())
    now_dt = datetime.now(timezone.utc)
    try:
        await db_engine.execute(
            "INSERT INTO public.agent_runs (id, agent_id, user_id, issue_id, "
            "status, trigger, started_at, heartbeat_at, last_useful_action_at) "
            "VALUES (:id, :agent_id, :user_id, :issue_id, 'running', "
            "'issue_dispatch', :now, :now, :now)",
            {
                "id": run_id,
                "agent_id": agent_id,
                "user_id": user_id,
                "issue_id": issue_id,
                "now": now_dt,
            },
        )
        logger.info(
            f"[execute_issue] created agent_run {run_id} for issue {issue_id} "
            f"(agent={agent_id}, dbos_wf={dbos_workflow_id})"
        )
        return run_id
    except Exception as exc:
        logger.warning(
            f"[execute_issue] agent_run insert failed for issue {issue_id}: {exc}"
        )
        return None


@DBOS.step()
async def load_issue(issue_id: int) -> dict[str, Any]:
    """Read issue row as a plain dict (serializes through DBOS step memo).

    The engine returns datetime/UUID as objects (normalized to str below) and
    jsonb as a string; no current consumer reads a jsonb column off this dict."""
    from app.db import engine as db_engine

    row = await db_engine.fetch_one(
        "SELECT * FROM public.issues WHERE id = :id", {"id": issue_id}
    )
    if not row:
        raise RuntimeError(f"issue id={issue_id} not found")
    out: dict[str, Any] = {}
    for k, v in row.items():
        if hasattr(v, "isoformat"):
            out[k] = v.isoformat()
        elif hasattr(v, "hex"):  # UUID has .hex
            out[k] = str(v)
        else:
            out[k] = v
    return out


@DBOS.workflow()
async def execute_issue(issue_id: int) -> dict[str, Any]:
    """Parent workflow — owns the issue lifecycle."""
    await load_issue(issue_id)

    workflow_id = DBOS.workflow_id
    locked = await atomic_checkout(issue_id, workflow_id)
    if not locked:
        logger.info(f"[execute_issue] issue {issue_id} already locked, skipping")
        return {"skipped": True, "issue_id": issue_id, "reason": "already_locked"}

    await set_status(issue_id, "in_progress")

    try:
        issue_row = await load_issue(issue_id)
        agent_id = issue_row.get("assignee_agent_id")
        user_id = issue_row.get("created_by_user_id") or issue_row.get(
            "assignee_user_id"
        )
        agent_run_id: Optional[str] = None
        if agent_id and user_id:
            agent_run_id = await create_agent_run_for_issue(
                issue_id, agent_id, user_id, workflow_id
            )

        result: dict[str, Any] = {"issue_id": issue_id, "agent_run_id": agent_run_id}
        if agent_run_id is None:
            await set_status(issue_id, "done")
        return result

    except Exception as exc:  # noqa: BLE001
        await set_status(
            issue_id,
            "blocked",
            error_code="execute_issue_failed",
            error_message=str(exc)[:500],
        )
        raise
    finally:
        await clear_lock(issue_id)
```

- [ ] **Step 2: Run the SQL-shape tests — verify they PASS**

Run: `cd backend && uv run pytest tests/test_issue_lifecycle_sql.py -q`
Expected: PASS (all 5).

- [ ] **Step 3: Confirm psycopg is gone + import smoke**

Run: `cd backend && grep -n "psycopg\|_dsn\|SET ROLE\|DBOS_DATABASE_URL" app/workflows/issue_lifecycle.py || echo CLEAN`
Expected: `CLEAN`
Run: `cd backend && uv run python -c "import app.workflows.issue_lifecycle; print('OK')"`
Expected: `OK`

### Task 2.3: Adapt callers + verify

- [ ] **Step 1: Await any sync callers found in Task 2.1 Step 0**

For each caller invoking `execute_issue` / the steps synchronously, change to `await` (and make the caller async if needed). If all callers go through `DBOS.start_workflow`, no change — note that in the commit body.

- [ ] **Step 2: Full issue + workflow test sweep**

Run: `cd backend && uv run pytest tests/ -k "issue or workflow" -q`
Expected: PASS.

- [ ] **Step 3: Lint**

Run: `cd backend && uv run black --check app/workflows/issue_lifecycle.py tests/test_issue_lifecycle_sql.py && uv run isort --check-only app/workflows/issue_lifecycle.py tests/test_issue_lifecycle_sql.py && uv run flake8 app/workflows/issue_lifecycle.py tests/test_issue_lifecycle_sql.py`
Expected: clean.

- [ ] **Step 4: Commit**

```bash
git add backend/app/workflows/issue_lifecycle.py backend/tests/test_issue_lifecycle_sql.py
git commit -m "refactor(workflows): issue_lifecycle psycopg → SQLAlchemy engine (#199)"
```

---

# Phase 3 — write_memory / MemoryWriter: supabase-py → engine

**Why:** `write_memory`'s extract step is the last workflow path still on REST. It delegates to `MemoryWriter` (used only here), which does a batch insert into `agent_memories` (pgvector), a recent-thread read, and a Python-cosine contradiction check. We reuse the exact pgvector text-cast pattern from `scheduled_memory_consolidation.py`: read `CAST(embedding AS text)` + `json.loads`, write `CAST(:emb AS vector)`.

### Task 3.1: Add `execute_returning_one` to the engine

**Files:**
- Modify: `backend/app/db/engine.py`
- Test: `backend/tests/test_engine_helpers.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_engine_helpers.py` (the `_Engine`/`_Result` fakes already exist there; `_Result` needs a `mappings().first()` path — extend the fake):

```python
async def test_execute_returning_one_returns_row_dict(monkeypatch):
    # INSERT ... RETURNING * must run on begin() (commit) and return the row.
    class _R(_Result):
        def first(self):
            return {"id": "new-id", "embedding": "[0.1,0.2]"}

    monkeypatch.setattr(db_engine, "get_engine", lambda: _Engine(_R()))
    row = await db_engine.execute_returning_one(
        "INSERT INTO t (x) VALUES (:x) RETURNING *", {"x": 1}
    )
    assert row == {"id": "new-id", "embedding": "[0.1,0.2]"}
```

If `_Result.mappings()` returns `self` and lacks `.first()`, add `def first(self): return self._rows[0] if self._rows else None` to the base `_Result` and have the fake pass `rows=[{...}]` instead of subclassing.

- [ ] **Step 2: Run — verify FAIL**

Run: `cd backend && uv run pytest tests/test_engine_helpers.py::test_execute_returning_one_returns_row_dict -q`
Expected: FAIL — `AttributeError: module 'app.db.engine' has no attribute 'execute_returning_one'`.

- [ ] **Step 3: Implement the helper**

In `app/db/engine.py`, after `execute_returning_val`, add:

```python
async def execute_returning_one(
    sql: str, params: Optional[dict] = None
) -> Optional[dict]:
    """INSERT / UPDATE ... RETURNING * inside an auto-committing transaction →
    the first RETURNING row as a plain dict, or None. Use for writes that need
    the inserted row back (fetch_one would run on connect() and never commit)."""
    from sqlalchemy import text

    eng = get_engine()
    async with eng.begin() as conn:
        result = await conn.execute(text(sql), params or {})
        row = result.mappings().first()
        return dict(row) if row else None
```

Add `"execute_returning_one"` to `__all__`.

- [ ] **Step 4: Run — verify PASS**

Run: `cd backend && uv run pytest tests/test_engine_helpers.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/db/engine.py backend/tests/test_engine_helpers.py
git commit -m "feat(db): add engine.execute_returning_one (INSERT…RETURNING * with commit) (#199)"
```

### Task 3.2: Port MemoryWriter to the engine

**Files:**
- Modify: `backend/app/services/ai/memory/writer.py`

- [ ] **Step 1: Add the embedding parser + drop the `supabase_client` field**

At module level (near `_cosine`), add:

```python
def _parse_embedding(raw: Any) -> list[float] | None:
    """pgvector comes back from the engine as the text literal "[0.1,...]"
    (we SELECT CAST(embedding AS text)). That literal is valid JSON."""
    if not raw:
        return None
    try:
        seq = json.loads(raw) if isinstance(raw, str) else raw
        return [float(x) for x in seq]
    except (ValueError, TypeError):
        return None
```

Add `import json` at the top if absent. Remove the `supabase_client: Any` dataclass field (and its docstring line). Keep `embedding_service` and `contradiction_classifier`.

- [ ] **Step 2: Port `write()`'s batch insert + thread fetch**

Replace the `_fetch_recent` closure body:

```python
                async def _fetch_recent(aid, uid, sid, limit):
                    from app.db import engine as db_engine

                    return await db_engine.fetch_all(
                        "SELECT created_at, thread_id FROM public.agent_memories "
                        "WHERE agent_id = :aid AND user_id = :uid "
                        "AND session_id = :sid AND status = 'active' "
                        "ORDER BY created_at DESC LIMIT :lim",
                        {"aid": str(aid), "uid": str(uid), "sid": str(sid), "lim": limit},
                    )
```

Replace the batch insert block (the `self.supabase_client.table("agent_memories").insert(rows)` try/except) with a per-row insert through the engine (row count is small — a few facts per harvest):

```python
        from app.db import engine as db_engine

        inserted_rows: list[dict] = []
        for row in rows:
            cols = list(row.keys())
            placeholders = []
            params: dict[str, Any] = {}
            for c in cols:
                if c == "embedding":
                    params[c] = "[" + ",".join(repr(x) for x in row[c]) + "]"
                    placeholders.append("CAST(:embedding AS vector)")
                elif c == "metadata_json":
                    params[c] = json.dumps(row[c])
                    placeholders.append("CAST(:metadata_json AS jsonb)")
                else:
                    params[c] = row[c]
                    placeholders.append(f":{c}")
            sql = (
                "INSERT INTO public.agent_memories (" + ", ".join(cols) + ") "
                "VALUES (" + ", ".join(placeholders) + ") RETURNING *"
            )
            try:
                got = await db_engine.execute_returning_one(sql, params)
                if got:
                    inserted_rows.append(got)
            except Exception:  # noqa: BLE001
                logger.exception(
                    "[memory.writer] insert failed; dropped 1 candidate fact"
                )
        if not inserted_rows:
            return 0
```

Then update the contradiction block to use the local `inserted_rows` (drop the `insert_result.data` reference):

```python
        if self.contradiction_classifier is not None:
            try:
                await self._supersede_contradicting(
                    inserted_rows,
                    agent_id=agent_id,
                    user_id=user_id,
                    scope=scope,
                )
            except Exception:
                logger.exception(
                    "[memory.writer] contradiction post-pass failed (non-fatal)"
                )

        return len(inserted_rows)
```

- [ ] **Step 3: Port `_nearest_existing` + the supersede UPDATE**

In `_nearest_existing`, replace the supabase select with:

```python
        from app.db import engine as db_engine

        try:
            rows = await db_engine.fetch_all(
                "SELECT id, summary, CAST(embedding AS text) AS embedding "
                "FROM public.agent_memories "
                "WHERE agent_id = :aid AND user_id = :uid AND scope = :scope "
                "AND status = 'active' AND id <> :exclude LIMIT 50",
                {
                    "aid": str(agent_id),
                    "uid": str(user_id),
                    "scope": scope.value,
                    "exclude": str(exclude_id),
                },
            )
        except Exception:
            return []
```

In the scoring loop, parse the embedding text before cosine:

```python
        scored: list[tuple[float, dict]] = []
        for row in rows:
            emb = _parse_embedding(row.get("embedding"))
            if not emb:
                continue
            sim = _cosine(embedding, emb)
            if sim >= threshold:
                scored.append((sim, row))
```

In `_supersede_contradicting`, the `new_embedding = new_row.get("embedding")` now comes from `RETURNING *` as a text literal — parse it before passing to `_nearest_existing`:

```python
            new_embedding = _parse_embedding(new_row.get("embedding"))
```

And replace the supersede UPDATE:

```python
            from app.db import engine as db_engine

            for old_id in target_ids:
                try:
                    await db_engine.execute(
                        "UPDATE public.agent_memories SET status = 'superseded', "
                        "superseded_by = :new WHERE id = :old",
                        {"new": str(new_id), "old": str(old_id)},
                    )
                except Exception:
                    logger.exception(
                        "[memory.writer] failed to supersede %s by %s",
                        old_id,
                        new_id,
                    )
```

- [ ] **Step 4: Compile**

Run: `cd backend && uv run python -m py_compile app/services/ai/memory/writer.py && echo OK`
Expected: `OK`

### Task 3.3: Update write_memory.py to drop the supabase client

**Files:**
- Modify: `backend/app/workflows/write_memory.py` (`extract_and_persist_memories_step`, ~lines 61-79)

- [ ] **Step 1: Apply the edit**

```python
# OLD
    from app.db import get_async_supabase_admin
    from app.services.ai.memory.extractor import (
        AssistantMemoryExtractor,
        UserMemoryExtractor,
    )
    from app.services.ai.memory.writer import MemoryWriter
    from app.services.ai.providers.embedding_service import EmbeddingService

    client = await get_async_supabase_admin()
    llm_call = await _build_cheap_llm_call()
    user_extractor = UserMemoryExtractor(llm_call=llm_call)
    asst_extractor = AssistantMemoryExtractor(llm_call=llm_call)

    writer = MemoryWriter(
        user_extractor=user_extractor,
        assistant_extractor=asst_extractor,
        embedding_service=EmbeddingService(),
        supabase_client=client,
    )
# NEW
    from app.services.ai.memory.extractor import (
        AssistantMemoryExtractor,
        UserMemoryExtractor,
    )
    from app.services.ai.memory.writer import MemoryWriter
    from app.services.ai.providers.embedding_service import EmbeddingService

    llm_call = await _build_cheap_llm_call()
    user_extractor = UserMemoryExtractor(llm_call=llm_call)
    asst_extractor = AssistantMemoryExtractor(llm_call=llm_call)

    writer = MemoryWriter(
        user_extractor=user_extractor,
        assistant_extractor=asst_extractor,
        embedding_service=EmbeddingService(),
    )
```

- [ ] **Step 2: Confirm no supabase left in the workflow**

Run: `cd backend && grep -nE "get_async_supabase|\.table\(|supabase_client" app/workflows/write_memory.py || echo CLEAN`
Expected: `CLEAN`

### Task 3.4: Update MemoryWriter tests + verify

**Files:**
- Modify: `backend/tests/test_memory_writer.py`

- [ ] **Step 1: Repoint the test doubles from the REST client to the engine helpers**

Find the test's supabase-client mock (it builds `MemoryWriter(supabase_client=<fake>)`). Replace with constructing `MemoryWriter(...)` *without* `supabase_client`, and patch the engine helpers it now calls:

```python
from unittest.mock import patch


async def _run_with_fake_engine(writer, *, inserted, **write_kwargs):
    async def fake_execute_returning_one(sql, params=None):
        # echo back an agent_memories row shaped like RETURNING *
        return {
            "id": "mem-1",
            "summary": params.get("summary", ""),
            "embedding": params.get("embedding", "[0.0]"),
        }

    async def fake_fetch_all(sql, params=None):
        return []

    async def fake_execute(sql, params=None):
        return 1

    with (
        patch("app.db.engine.execute_returning_one", fake_execute_returning_one),
        patch("app.db.engine.fetch_all", fake_fetch_all),
        patch("app.db.engine.execute", fake_execute),
    ):
        return await writer.write(**write_kwargs)
```

Update each existing test to use this harness (or inline the three patches). Assert `write()` returns the inserted count as before. Keep the existing extractor/embedding-service fakes unchanged.

- [ ] **Step 2: Run the writer tests — verify PASS**

Run: `cd backend && uv run pytest tests/test_memory_writer.py -q`
Expected: PASS.

- [ ] **Step 3: Broader memory sweep + lint**

Run: `cd backend && uv run pytest tests/ -k "memory or write_memory" -q`
Expected: PASS.
Run: `cd backend && uv run black --check app/services/ai/memory/writer.py app/workflows/write_memory.py tests/test_memory_writer.py && uv run isort --check-only app/services/ai/memory/writer.py app/workflows/write_memory.py tests/test_memory_writer.py && uv run flake8 app/services/ai/memory/writer.py app/workflows/write_memory.py`
Expected: clean.

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/ai/memory/writer.py backend/app/workflows/write_memory.py backend/tests/test_memory_writer.py
git commit -m "refactor(memory): MemoryWriter + write_memory → SQLAlchemy engine (#199)"
```

---

## Final verification (all phases)

- [ ] **No workflow file uses supabase-py REST or psycopg anymore**

Run: `cd backend && grep -rnE "\.table\(|get_async_supabase|_get_client\(|psycopg" app/workflows/ || echo CLEAN`
Expected: `CLEAN`

- [ ] **Full backend test suite**

Run: `cd backend && uv run pytest tests/ -q`
Expected: all pass (no new failures vs master baseline).

- [ ] **Open PR** (one PR for all three phases, or one per phase if preferred)

```bash
git push -u origin refactor/unify-workflow-db-access
gh pr create --base master --title "refactor: unify workflow DB access — factories + drop psycopg + MemoryWriter→engine (#199)" --body "Phases: (1) workflow repo constructions → factories, (2) issue_lifecycle psycopg→engine, (3) MemoryWriter/write_memory→engine. No behaviour change; uniformity only. Leak already fixed by #328."
```

---

## Self-Review

**Spec coverage:** scope was (1) workflow repo-direct → factory, (2) issue_lifecycle psycopg → engine, (3) write_memory extract → engine. Phase 1 = (1) across download/transcode/extract_audio/scheduled_recovery (9 sites). Phase 2 = (2). Phase 3 = (3), which required migrating MemoryWriter (the service behind it) + a new `execute_returning_one` helper. All three covered.

**Placeholder scan:** every code step shows full code; no TBD/TODO. The only deliberately-open item is Phase 2 Step 0 (enumerate callers) → Task 2.3 Step 1 (await them) — this is investigation-then-edit, not a placeholder, because the caller set must be read from the live tree.

**Type consistency:** `get_media_repository()` / `get_resources_repository()` (Phase 1) match the factory names verified in `media_repository.py:661` / `resources_repository.py:1461`. `execute_returning_one` (added 3.1) is used in 3.2. `_parse_embedding` (added 3.2 Step 1) is used in 3.2 Steps 2-3. Engine helpers `execute` (rowcount int), `fetch_one` (dict|None), `fetch_all` (list[dict]) match their signatures in `engine.py`. pgvector text-cast pattern matches `scheduled_memory_consolidation.py`.

**Risk notes:** Phase 1 is near-zero risk (factory falls back to REST when flag off; asyncpg variants already in prod). Phase 2 changes sync→async — the one real risk is a caller invoking the steps synchronously (guarded by Task 2.1 Step 0). Phase 3 is the heaviest (pgvector round-trips on the memory-write hot path); the `_parse_embedding` + CAST pattern is already proven in consolidation, and every DB call keeps its existing best-effort try/except so a harvest failure degrades the next chat, not the current one.
