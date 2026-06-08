# Task Orchestration Enhancements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`). **Each Phase ships as its own PR**, in order C → A → B → D.

**Goal:** Four independent orchestration improvements: (C) mid-flight progress for AI workflows, (A) per-user concurrency cap for agent turns, (B) cooperative cancellation for in-flight downloads, (D) per-queue/per-user queue observability.

**Tech Stack:** Python 3.13, DBOS 2.19, FastAPI, Redis, pytest; frontend React/TS for D's admin surface.

---

## Phase C — Mid-flight progress for AI workflows (PR 1)

**Why:** `ai_summary` / `analyze_l1` / `ai_transcription` pre-create a task_tracking row then complete it, so the Task Center shows only elapsed time. Add coarse progress + subtitle at step boundaries via the sanctioned `manager.update_progress(wf_id, pct, subtitle=...)` (route-C safe: `progress`/`subtitle` are business-written by the manager; downloads already do this — `soda_download.py` calls `update_progress(20,...)`/`update_progress(80,...)`).

**Reality:** transcription is one opaque API call → coarse start/end only. summary (3 steps) + analyze_l1 (resolve→call) → coarse %.

**Files:** `backend/app/workflows/ai_summary.py`, `analyze_l1.py`, `ai_transcription.py`.

### Task C1: summary progress

**Files:** Modify `backend/app/workflows/ai_summary.py` (workflow body, ~lines 180–223)

- [ ] **Step 1: Locate the workflow body** — the `@DBOS.workflow` `summarize_workflow` (or similar) that calls `load_summary_inputs()` → `run_summarize_agent()` → `persist_summary()`. Confirm a `manager` / `get_task_manager()` and the `wf_id` (workflow id) are in scope (mirror how `soda_download.py` obtains `manager` + `wf_id`).

- [ ] **Step 2: Insert progress calls at the three boundaries**

After `load_summary_inputs(...)` returns:
```python
await manager.update_progress(wf_id, 25, subtitle="Preparing transcript...")
```
After `run_summarize_agent(...)` returns:
```python
await manager.update_progress(wf_id, 70, subtitle="Summary generated")
```
After `persist_summary(...)` returns:
```python
await manager.update_progress(wf_id, 100, subtitle="Summary saved")
```

> Use the SAME `manager` accessor + `wf_id` variable the workflow already has. If the workflow lacks a `manager` handle, add `from app.services.infra.unified_task_manager import get_task_manager` and `manager = get_task_manager()` (matching `soda_download.py`). Do NOT touch `phase`/`status` (trigger-managed).

- [ ] **Step 3: Verify import + the workflow still imports**

Run: `cd backend && uv run python -c "import app.workflows.ai_summary; print('ok')"`
Expected: `ok`

- [ ] **Step 4: Commit**

```bash
git add backend/app/workflows/ai_summary.py
git commit -m "feat(ai): coarse progress + subtitle for summary workflow"
```

### Task C2: analyze_l1 progress

**Files:** Modify `backend/app/workflows/analyze_l1.py` (workflow body, ~lines 121–158)

- [ ] **Step 1: Insert progress at the two step boundaries in the workflow body**

After `resolve_analyze_provider(...)`:
```python
await manager.update_progress(wf_id, 20, subtitle="Analyzing cover image...")
```
After `call_analyze_l1(...)` returns:
```python
await manager.update_progress(wf_id, 100, subtitle="Analysis complete")
```

> `call_analyze_l1` does the multimodal call + persist + embedding internally as one step; coarse 20→100 is the honest granularity without threading a callback into the step. Same `manager`/`wf_id` rule as C1.

- [ ] **Step 2: Verify import**

Run: `cd backend && uv run python -c "import app.workflows.analyze_l1; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add backend/app/workflows/analyze_l1.py
git commit -m "feat(ai): coarse progress + subtitle for analyze_l1 workflow"
```

### Task C3: transcription progress (coarse)

**Files:** Modify `backend/app/workflows/ai_transcription.py` (workflow body around the transcribe step, ~lines 278–331)

- [ ] **Step 1: Bracket the opaque transcription call**

Before the `transcribe_and_save(...)` step:
```python
await manager.update_progress(wf_id, 40, subtitle="Transcribing audio...")
```
After it returns:
```python
await manager.update_progress(wf_id, 100, subtitle="Transcription complete")
```

> Whisper/Volcengine return one result with no mid-stream events, so 40→100 is the honest granularity. Same `manager`/`wf_id` rule.

- [ ] **Step 2: Verify + commit**

Run: `cd backend && uv run python -c "import app.workflows.ai_transcription; print('ok')"`

```bash
git add backend/app/workflows/ai_transcription.py
git commit -m "feat(ai): coarse progress + subtitle for transcription workflow"
```

### Phase C ship
- [ ] PR: "feat(ai): mid-flight progress for AI workflows". No frontend change (the Task Center already renders `progress` + `subtitle`). Deploy via backend Watchtower; verify a summarize/analyze task shows advancing % + subtitle in the Task Center.

---

## Phase A — Per-user concurrency cap for agent turns (PR 2)

**Why:** chat + issue agent turns are uncapped (a user can spawn many concurrent LLM calls). Both funnel through `AILibraryChatService.run_session_turn` (`ai_library_chat_service.py:360`) — chat via `.chat()`, issue via `run_issue_reply_step`. A single per-user gate there caps both with one mechanism.

**Mechanism:** in-process per-user `asyncio.Semaphore` (default 2, env `MAX_AGENT_CONCURRENCY_PER_USER`). Caps within each process (chat → gateway, issue → worker). Note for later: a Redis token would make it global across replicas; in-process is sufficient for the single-gateway deployment today.

**Files:** new `backend/app/services/ai/chat/agent_concurrency.py` + `ai_library_chat_service.py` (wrap `run_session_turn`).

### Task A1: per-user semaphore gate (pure-ish, tested)

**Files:** Create `backend/app/services/ai/chat/agent_concurrency.py` + test `backend/tests/test_agent_concurrency.py`

- [ ] **Step 1: Write the failing test**

```python
"""Per-user agent concurrency gate: at most N concurrent turns per user, others
wait; different users don't block each other; the limit is clamped + live."""

from __future__ import annotations

import asyncio

import pytest

from app.services.ai.chat import agent_concurrency as ac


@pytest.fixture(autouse=True)
def _reset():
    ac.reset_for_tests()
    ac.set_agent_concurrency(2)
    yield
    ac.reset_for_tests()


def test_limit_clamped():
    ac.set_agent_concurrency(0)
    assert ac.current_limit() == 1
    ac.set_agent_concurrency(999)
    assert ac.current_limit() == 20


@pytest.mark.asyncio
async def test_caps_per_user():
    ac.set_agent_concurrency(1)
    order: list[str] = []

    async def turn(tag: str):
        async with ac.user_slot("u1"):
            order.append(f"start:{tag}")
            await asyncio.sleep(0.05)
            order.append(f"end:{tag}")

    await asyncio.gather(turn("a"), turn("b"))
    # With limit 1, the two u1 turns are serialized (no interleave).
    assert order == ["start:a", "end:a", "start:b", "end:b"] or order == [
        "start:b", "end:b", "start:a", "end:a",
    ]


@pytest.mark.asyncio
async def test_different_users_run_concurrently():
    ac.set_agent_concurrency(1)
    started = asyncio.Event()

    async def hold():
        async with ac.user_slot("u1"):
            started.set()
            await asyncio.sleep(0.1)

    async def other():
        await asyncio.wait_for(started.wait(), 1.0)
        # u2 acquires immediately despite u1 holding its own slot
        async with ac.user_slot("u2"):
            return True

    _, ok = await asyncio.gather(hold(), other())
    assert ok is True
```

- [ ] **Step 2: Run test (RED)**

Run: `cd backend && uv run pytest tests/test_agent_concurrency.py -q`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement the gate**

```python
"""In-process per-user concurrency gate for agent turns (chat + issue).

A user gets at most `current_limit()` concurrent turns; the rest await a slot.
Per-process (chat runs on the gateway, issue on the worker) — sufficient for the
single-gateway deployment. A Redis token would make it global across replicas.
"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager

_DEFAULT = int(os.environ.get("MAX_AGENT_CONCURRENCY_PER_USER", "2"))
_limit = max(1, min(20, _DEFAULT))
_sems: dict[str, asyncio.Semaphore] = {}
_counts: dict[str, int] = {}


def current_limit() -> int:
    return _limit


def set_agent_concurrency(n: int) -> None:
    """Set the per-user agent cap live (clamped 1..20). New semaphores use the
    new limit; in-flight ones drain at their old limit (acceptable)."""
    global _limit, _sems
    _limit = max(1, min(20, int(n)))
    _sems = {}  # rebuild lazily at the new limit


def _sem_for(user_id: str) -> asyncio.Semaphore:
    sem = _sems.get(user_id)
    if sem is None:
        sem = asyncio.Semaphore(_limit)
        _sems[user_id] = sem
    return sem


@asynccontextmanager
async def user_slot(user_id: str):
    """Hold one of the user's agent-turn slots for the duration of the block."""
    sem = _sem_for(user_id)
    await sem.acquire()
    _counts[user_id] = _counts.get(user_id, 0) + 1
    try:
        yield
    finally:
        sem.release()
        _counts[user_id] = max(0, _counts.get(user_id, 0) - 1)


def reset_for_tests() -> None:
    global _sems, _counts
    _sems = {}
    _counts = {}
```

- [ ] **Step 4: Run test (GREEN)**

Run: `cd backend && uv run pytest tests/test_agent_concurrency.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/ai/chat/agent_concurrency.py backend/tests/test_agent_concurrency.py
git commit -m "feat(agent): per-user concurrency gate (in-process semaphore)"
```

### Task A2: apply the gate in `run_session_turn`

**Files:** Modify `backend/app/services/ai/chat/ai_library_chat_service.py` (`run_session_turn`, ~line 360)

- [ ] **Step 1: Wrap the turn body with the gate**

At the top of `run_session_turn`, identify the `user_id` parameter. Wrap the existing body:

```python
from app.services.ai.chat.agent_concurrency import user_slot

async def run_session_turn(self, ..., user_id, ...):
    async with user_slot(str(user_id)):
        # ... existing body unchanged ...
```

> If `run_session_turn` is large, the minimal-diff form is to rename the existing method to `_run_session_turn_inner` and add a thin `run_session_turn` wrapper that does `async with user_slot(str(user_id)): return await self._run_session_turn_inner(...)`. Verify `user_id` is a parameter (the chat service receives it; the first agent confirmed `user_id=user_uuid` is passed).

- [ ] **Step 2: Wire the live setter to the existing concurrency bus event (optional but consistent)**

In `backend/app/startup/lifecycle_bus.py::_on_user_concurrency`, the handler already fans out to parse/soda/download. Agent cap is a SEPARATE knob (default 2, not the download value), so do NOT fold it into that handler. Leave `set_agent_concurrency` env-driven for now (no UI). (If a Settings control is wanted later, add a `config.agent_concurrency` bus event + emit from `user_settings_router`.)

- [ ] **Step 3: Verify import + run the chat service's existing tests**

Run: `cd backend && uv run python -c "import app.services.ai.chat.ai_library_chat_service; print('ok')"`
Run: `cd backend && uv run pytest tests/ -k "chat or session_turn or agent" -q`
Expected: import ok; existing tests still pass.

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/ai/chat/ai_library_chat_service.py
git commit -m "feat(agent): cap concurrent turns per user via run_session_turn gate"
```

### Phase A ship
- [ ] PR: "feat(agent): per-user concurrency cap for chat + issue turns". Backend-only. Verify: a user firing many chat turns runs at most N concurrently (rest queue server-side); different users unaffected.

---

## Phase B — Cooperative cancellation for in-flight downloads (PR 3)

**Why:** agent turns already cancel gracefully (poll `cancel_requested` + AbortController); queued tasks cancel via DBOS. The gap: a RUNNING download keeps streaming after "cancel" — the download loops don't check a cancel flag. Scope: make the download paths cooperatively abort.

**Mechanism:** cancel already flips `task_tracking.phase='cancelled'` + kills subprocesses (`unified_task_manager.py:549–592`). Add a lightweight **Redis cancel flag** set at cancel time, checked cooperatively by the download loops (cheap; reuses the existing Redis client that `UnifiedProgressTracker` uses).

**Files:** `backend/app/services/infra/unified_task_manager.py` (set flag on cancel) + a small `backend/app/tasks/cancel_flag.py` helper + the download loops (`soda_download.py` `download_and_decrypt` chunk loop; the yt-dlp path's `progress_hook`).

### Task B1: cancel-flag helper (tested)

**Files:** Create `backend/app/tasks/cancel_flag.py` + test `backend/tests/test_cancel_flag.py`

- [ ] **Step 1: Write the failing test** (fakeredis or a stub client)

```python
"""Cancel flag: set on cancel, checked cooperatively by download loops.
Keyed by workflow_id, short TTL so it self-cleans."""

from __future__ import annotations

import pytest

from app.tasks import cancel_flag


class _FakeRedis:
    def __init__(self):
        self.store = {}
    def set(self, k, v, ex=None):
        self.store[k] = v
    def get(self, k):
        return self.store.get(k)
    def delete(self, k):
        self.store.pop(k, None)


def test_set_and_check():
    r = _FakeRedis()
    assert cancel_flag.is_cancelled(r, "wf-1") is False
    cancel_flag.request_cancel(r, "wf-1")
    assert cancel_flag.is_cancelled(r, "wf-1") is True


def test_missing_client_is_safe():
    # No redis → never cancelled (don't crash downloads on Redis outage).
    assert cancel_flag.is_cancelled(None, "wf-1") is False
    cancel_flag.request_cancel(None, "wf-1")  # no-op, no raise
```

- [ ] **Step 2: Run (RED)** — `cd backend && uv run pytest tests/test_cancel_flag.py -q` → FAIL (module missing).

- [ ] **Step 3: Implement**

```python
"""Redis-backed cooperative cancel flag for in-flight downloads. Cheap to set
(on cancel) + check (in the download loop). Self-cleans via TTL."""

from __future__ import annotations

from typing import Any, Optional

_PREFIX = "cancel_flag:"
_TTL_SECONDS = 3600


def _key(workflow_id: str) -> str:
    return f"{_PREFIX}{workflow_id}"


def request_cancel(redis_client: Optional[Any], workflow_id: str) -> None:
    if redis_client is None:
        return
    try:
        redis_client.set(_key(workflow_id), "1", ex=_TTL_SECONDS)
    except Exception:
        pass  # cancel is best-effort; never raise into the cancel path


def is_cancelled(redis_client: Optional[Any], workflow_id: str) -> bool:
    if redis_client is None:
        return False
    try:
        return redis_client.get(_key(workflow_id)) is not None
    except Exception:
        return False
```

- [ ] **Step 4: Run (GREEN)** — `cd backend && uv run pytest tests/test_cancel_flag.py -q` → PASS.

- [ ] **Step 5: Commit** — `git add backend/app/tasks/cancel_flag.py backend/tests/test_cancel_flag.py && git commit -m "feat(cancel): redis cancel-flag helper for cooperative download abort"`

### Task B2: set the flag on cancel

**Files:** Modify `backend/app/services/infra/unified_task_manager.py` (`cancel`, ~lines 514–592)

- [ ] **Step 1: After flipping phase=cancelled + killing subprocesses, set the cancel flag**

In `cancel(self, task_id, user_id)`, after the existing status flip / subprocess kill, add:
```python
try:
    from app.tasks.cancel_flag import request_cancel
    from app.db.redis_client import get_redis_client  # verify the real accessor
    request_cancel(get_redis_client(), task_id)
except Exception as e:
    logger.warning(f"[cancel] set cancel flag failed: {e}")
```

> Verify the Redis accessor name used elsewhere (grep how `UnifiedProgressTracker` gets `redis_client` — `download_progress.py` takes one in its ctor; find the app's redis getter, e.g. `get_redis_client` / `get_redis`). `task_id` here is the dbos_workflow_id, which is what the download workflow knows as its `wf_id`.

- [ ] **Step 2: Commit** — `git add ... && git commit -m "feat(cancel): set redis cancel flag when a task is cancelled"`

### Task B3: check the flag in the download loops

**Files:** Modify `backend/app/workflows/soda_download.py` (`download_and_decrypt` chunk loop) + the yt-dlp download path's progress hook.

- [ ] **Step 1: soda chunk loop** — locate the `async for chunk` / chunked write in `download_and_decrypt` (the soda direct-MP4/audio download). Inside the loop, every ~1MB or every N chunks:
```python
from app.tasks.cancel_flag import is_cancelled
# ... inside the chunk loop, throttled (e.g. every 64 chunks) ...
if is_cancelled(redis_client, wf_id):
    raise RuntimeError("Download cancelled by user")
```
Raising aborts the workflow (DBOS marks it failed/cancelled; the partial file cleanup is the existing half-write guard).

> Locate the exact loop + how `wf_id`/`redis_client` are in scope in that function. If `wf_id` isn't passed in, thread it from the workflow. Throttle the check (don't hit Redis every chunk).

- [ ] **Step 2: yt-dlp path** — locate the yt-dlp invocation (the generic `download_workflow` strategy). yt-dlp accepts `progress_hooks`; add a hook that checks the flag and raises `yt_dlp.utils.DownloadError` (or a cooperative exception) to abort:
```python
def _cancel_hook(d):
    if is_cancelled(redis_client, wf_id):
        raise Exception("Download cancelled by user")
# pass progress_hooks=[_cancel_hook] into the YoutubeDL opts
```

> Locate the yt-dlp opts construction (grep `YoutubeDL`, `progress_hooks`, `ydl_opts`). If downloads don't use yt-dlp (e.g. a custom httpx streamer), insert the check in that chunk loop instead. Find the actual streaming site before writing.

- [ ] **Step 3: Verify imports + commit**

Run: `cd backend && uv run python -c "import app.workflows.soda_download; print('ok')"`
```bash
git add backend/app/workflows/soda_download.py backend/app/workflows/download.py
git commit -m "feat(cancel): cooperative cancel check in download loops"
```

### Phase B ship
- [ ] PR: "feat(cancel): cooperative cancellation for in-flight downloads". Backend-only. Verify: start a large download, cancel it → the transfer stops promptly (not after completion); the task shows cancelled and the queue slot frees.

> **Note in PR:** agent-turn + queued-task cancellation were already graceful; this PR only closes the in-flight-download gap.

---

## Phase D — Queue observability (PR 4, ops/admin)

**Why:** `/system/status` returns only total running/pending. Add a per-task_type breakdown + oldest-queued age for ops. **This is an admin/ops surface** (SystemMonitorPanel), not the end-user Task Center — modest end-user value, so keep it backend-light.

**Files:** `backend/app/services/infra/system_monitor_service.py` (+ `system_router.py` response) + `frontend/components/SystemMonitorPanel.tsx` (admin) + `frontend/services/systemService.ts`.

### Task D1: backend per-type queue breakdown

**Files:** Modify `system_monitor_service.py` + `system_router.py`

- [ ] **Step 1: Add a `get_queue_breakdown()` reading task_tracking (route-C: NOT dbos.workflow_status)**

One grouped read over `task_tracking` for `phase IN ('queued','processing')`:
```python
async def get_queue_breakdown(self) -> list[dict]:
    """Per task_type: running / queued counts + oldest queued age (seconds).
    Reads task_tracking only (route-C). Cached 5s like get_queue_status."""
    # SELECT task_type, phase, count(*), min(created_at) ... GROUP BY task_type, phase
    # then pivot to: [{task_type, running, pending, oldest_queued_age_sec}, ...]
```
Use the same asyncpg/SQLAlchemy path `get_queue_status` already uses (avoids the httpcore leak, Issue #199). Pivot rows client-side into one entry per task_type.

- [ ] **Step 2: Expose it on `/api/v1/system/queue-breakdown`** (or add a `breakdown` field to the existing `/system/status` response). Add the Pydantic response model in `system_router.py` mirroring `QueueStatus`.

- [ ] **Step 3: Test** — add `backend/tests/test_queue_breakdown.py` calling `get_queue_breakdown` with a stubbed DB client returning sample rows; assert the pivot (running/pending/oldest age per type).

- [ ] **Step 4: Commit** — `git commit -m "feat(system): per-task_type queue breakdown + oldest-queued age (task_tracking)"`

### Task D2: admin surface

**Files:** Modify `frontend/services/systemService.ts` (+ type) + `frontend/components/SystemMonitorPanel.tsx`

- [ ] **Step 1:** Add `getQueueBreakdown()` to `systemService.ts` + a `QueueBreakdownРow` type.
- [ ] **Step 2:** In `SystemMonitorPanel.tsx` (admin), render a small table: task_type · running · pending · oldest-queued age. Poll on the panel's existing interval.
- [ ] **Step 3:** tsc + build; commit `feat(system): admin queue-breakdown table`.

### Phase D ship
- [ ] PR: "feat(system): queue observability (per-type depth + oldest age)". Note in PR it's an **ops/admin** surface (SystemMonitorPanel), not the user Task Center.

---

## Self-Review

**Spec coverage:** C (3 workflows) / A (gate + apply) / B (flag + set + check) / D (backend + admin) — all four present, each its own PR in order. ✓

**Placeholder scan:** code shown for every tested/critical piece (A1 gate, B1 flag, the progress calls, the queue query shape). The download-internals steps (B3) + Redis accessor (B2) + run_session_turn shape (A2) carry explicit "locate/verify the real symbol before writing" notes rather than guessed code, because those internals weren't read line-by-line — pointed checks, not vague TODOs. ✓

**Type/name consistency:** `set_agent_concurrency`/`current_limit`/`user_slot`/`reset_for_tests` used identically in test + impl + A2. `request_cancel`/`is_cancelled(redis, wf_id)` identical in B1 test/impl + B2/B3 call sites. `manager.update_progress(wf_id, pct, subtitle=)` identical across C1–C3 (matches the proven `soda_download.py` usage). ✓

**Honest scope flags:** transcription progress is coarse (opaque API); B only closes the in-flight-download gap (agent/queued cancel already work); D is ops/admin (modest end-user value). All stated in-line so they aren't mistaken for gaps.
