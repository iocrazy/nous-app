# DBOS Pipeline Immediacy & Stall Fix — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the soda/parse task pipeline from stalling at "0 running, N queued" and make every enqueued stage get picked up in ~1s (flowing-line behavior), by removing the three evidence-proven causes: a workflow-killing crash, DBOS NOWAIT lock-contention backoff, and gateway-stranded internal-queue orphans.

**Architecture:** DBOS 2.19.0 queues are poll-only (no LISTEN/NOTIFY wakeup exists in the library — confirmed), so "push" means "keep the 1s poller from backing off to 120s." We (1) stop the `.single()` crash that fails soda workflows and orphans rows, (2) switch the per-user partitioned queues from global `concurrency` (which forces `FOR UPDATE NOWAIT` + REPEATABLE READ → `LockNotAvailable` → exponential backoff to 120s) to `worker_concurrency` (which uses `FOR UPDATE SKIP LOCKED` + READ COMMITTED → never raises → no backoff), and (3) make the gateway-stranded `_dbos_internal_queue` orphan cleanup periodic + precisely scoped to provably-dead rows (never age-guessing a live task).

**Tech Stack:** Python 3.13, DBOS 2.19.0, Supabase/PostgREST (async), pytest (asyncio_mode=auto), psycopg.

---

## Evidence (proven by code read — no speculation)

All paths under `backend/`. DBOS lib under `backend/.venv/lib/python3.13/site-packages/dbos/`.

**R1 — `.single()` crash kills soda workflows (and orphans rows).**
- `app/workflows/parse.py:286-298` `dispatch_soda_download_step` wraps `get_task_manager().create(...)` in `try/except Exception` that only `logger.warning`s, then **unconditionally** `soda_download_queue.enqueue(soda_download_workflow, …, wf_id)` at `parse.py:305-315` (outside the try).
- `app/workflows/soda_download.py:199` — the workflow's first line is `await manager.start(wf_id)`.
- `app/services/infra/unified_task_manager.py:302` `start()` → `_get_phase()` → `:171` uses `.single()`. PostgREST returns `PGRST116 "0 rows"` when the `task_tracking` row (keyed `dbos_workflow_id`) is missing. So: failed/absent pre-create → `start()` → `.single()` on 0 rows → PGRST116 → uncaught in async workflow → DBOS marks it `ERROR` (`dbos/_core.py:534`) and logs "Exception encountered in asynchronous workflow / Future exception was never retrieved".
- `.single()` callsites in `unified_task_manager.py`: 171 (`_get_phase`, the throwing one), 366/410/459 (metadata-patch branches, NOT hit by soda path), 525/724/851 (cancel/retry/notify, off-path).

**R2 — DBOS NOWAIT contention backoff → 120s → "0 running".**
- `dbos/_queue.py:39-48` `Queue.__init__(name, concurrency=None, limiter=None, *, worker_concurrency=None, priority_enabled=False, partition_queue=False, polling_interval_sec=1.0)`.
- `dbos/_sys_db.py:3010` `skip_locks = queue.concurrency is None`; `:3031` `.with_for_update(skip_locked=skip_locks, nowait=(not skip_locks))`. → **`concurrency` set ⇒ `FOR UPDATE NOWAIT`**; **`concurrency=None` ⇒ `FOR UPDATE SKIP LOCKED`**.
- `dbos/_sys_db.py:2944-2947` sets `REPEATABLE READ` when `concurrency is not None or limiter is not None`.
- `dbos/_queue.py:178-186` on `OperationalError` whose `.orig` is `SerializationFailure` or `LockNotAvailable`: `polling_interval = min(max_polling_interval, polling_interval*2.0)`; `:136` `max_polling_interval = max(polling_interval_sec, 120.0)`; `:202` scale-down `*0.9`/iter. **All literals hardcoded — no env/config knob.**
- `dbos/_sys_db.py:2980-3006` — `worker_concurrency` limits via in-memory `local_running_count` (no DB round-trip); `concurrency` limits via a cross-process DB `COUNT(PENDING)`. The **lock mode is decided solely by `concurrency`** (`:3010`), so a queue with `worker_concurrency` set but `concurrency=None` uses SKIP LOCKED + READ COMMITTED — the no-contention path.
- Our partitioned queues all set global `concurrency` (NOWAIT): `parse.py:47-51` `parse_user` (`MAX_PARSE_CONCURRENCY_PER_USER` default 3), `soda_download.py:45-49` `soda_download` (`SODA_DOWNLOAD_CONCURRENCY` default 3), `download.py:53-57` `download_user` (default 3), `agent_workforce.py:47-51` `agent_workforce` (default 8).
- `dbos/_queue.py:138-141` consumer waits `stop_event.wait(timeout=polling_interval*jitter)`; **no enqueue-side wakeup**. LISTEN/NOTIFY exists only for `dbos_notifications_channel`/`dbos_workflow_events_channel` (messaging/events), NOT the queue (`dbos/_migration.py:199,214`; enqueue INSERT has no `pg_notify`).

**R3 — gateway strands `_dbos_internal_queue` scheduled ticks (the 487 orphans).**
- `dbos/_queue.py:222-226` — even with `DBOS.listen_queues([])`, DBOS **always** re-adds `INTERNAL_QUEUE_NAME` to the consumed set. So the gateway runs an internal-queue consumer thread.
- Worker enqueues scheduled ticks (`@DBOS.scheduled` → `dbos/_scheduler_decorator.py:50` `get_internal_queue().enqueue(...)`) as `ENQUEUED / queue_name=_dbos_internal_queue / executor_id=NULL`.
- Gateway's internal-queue thread dequeues one, `_sys_db.py:3065-3077` flips `status→PENDING`, stamps `executor_id='gateway'`, **does not clear `queue_name`**. Gateway can't execute it (it does NOT import `_scheduled_bundle` — `app/workflows/__init__.py:19-20` gates it on `role != GATEWAY`), raises `DBOSWorkflowFunctionNotFoundError`, logged + dropped (`_queue.py:174-177`).
- Recovery is executor-scoped: `_sys_db.py:1607-1620` `get_pending_workflows` filters `executor_id == <self>`. The worker (`executor_id='worker'`) never reclaims a `'gateway'`-stamped row → **stranded indefinitely**.
- Only app startup sweeps clear them: `dbos_orchestrator.py:213` `_pre_launch_sweep_stale_scheduled` (sched-* only, 3 min) and `app/startup/bootstrap.py:128-165` `_bg_reap_internal_queue` (`queue_name='_dbos_internal_queue'` PENDING/ENQUEUED age>5min). **Both run only at startup** → orphans accumulate during uptime (447→487→…), bloating `dbos.workflow_status` and amplifying R2's NOWAIT conflicts.
- `executor_id='gateway'` source: `app/startup/dbos_init.py:56` `init_dbos(executor_id=role.value)` → `dbos/_dbos.py:438-439` `GlobalParams.executor_id='gateway'`.
- `start_workflow_routed` (`dbos_orchestrator.py:444-448`) = `DBOS.start_workflow` (no queue) ⇒ `queue_name=NULL`, NOT `_dbos_internal_queue` — so it is **NOT** an orphan source (signature mismatch). The 16 gateway HTTP `start_workflow_routed` callsites execute on the gateway's own pool; out of scope here.

---

## File Structure

| File | Responsibility | Change |
|------|----------------|--------|
| `backend/app/services/infra/unified_task_manager.py` | task_tracking CRUD + phase reads | `_get_phase` → `.maybe_single()` + missing-row → QUEUED; `start()` self-heals a missing row (idempotent create) |
| `backend/tests/test_unified_task_manager_missing_row.py` | regression for R1 | Create |
| `backend/app/workflows/parse.py` | dispatch steps | stop silently swallowing `create()` failure in `dispatch_soda_download_step` (log at ERROR, still enqueue — workflow self-heals) — small wording/level change only |
| `backend/app/workflows/parse.py`, `soda_download.py`, `download.py`, `agent_workforce.py` | queue definitions | `concurrency=N` → `worker_concurrency=N` (keep `partition_queue=True`); update the live `set_*_concurrency` mutators to set `worker_concurrency` |
| `backend/tests/test_queue_lock_mode.py` | proves queues use SKIP-LOCKED (no NOWAIT) | Create |
| `backend/app/startup/bootstrap.py` | `_bg_reap_internal_queue` | make periodic (asyncio loop) + add executor-scoped precise cancel of provably-dead gateway internal-queue rows |
| `backend/tests/test_internal_queue_reaper_filter.py` | proves reaper only targets dead rows | Create |

---

## Task 0: Verify `worker_concurrency` partition semantics (DECISION GATE — read-only, no commit)

**Why:** Task 3 swaps `concurrency`→`worker_concurrency` to escape NOWAIT. This is only correct if `worker_concurrency` is enforced **per partition** (per user). If it is flat per-process, multi-user anti-ban semantics change (cap becomes total-across-users on one worker, not per-user).

- [ ] **Step 1: Read the worker-concurrency counting path**

Read `backend/.venv/lib/python3.13/site-packages/dbos/_sys_db.py:2977-3006` and the `count_for_queue` implementation it calls (grep `def count_for_queue` and `_active_workflows_set` in the dbos package). Determine: does `local_running_count` (the value compared against `worker_concurrency`) count **per queue** or **per (queue, partition_key)**?

- [ ] **Step 2: Record the verdict in this plan**

Write one line under this task: `worker_concurrency is PER-PARTITION` or `worker_concurrency is PER-QUEUE (flat)`.

- [ ] **Step 3: Branch the approach**

  - If **per-partition**: proceed with Task 3 as written (swap to `worker_concurrency`).
  - If **per-queue (flat)**: STOP and surface to the user — Task 3 changes per-user→per-worker cap. Options to present: (a) accept flat cap (single active user today, simplest), or (b) keep `concurrency` and rely on Task 1 + Task 4 (lean table) to keep NOWAIT conflicts rare instead of eliminating them. Do not silently change multi-user semantics.

> There is currently ONE worker process, so per-process == global for the cap *value* today; the per-partition question only affects multi-user fairness. Capture it, don't guess.

---

## Task 1: `_get_phase` tolerates a missing task_tracking row (kills the PGRST116 crash)

**Files:**
- Modify: `backend/app/services/infra/unified_task_manager.py` (`_get_phase` ~164-178; `start` ~296-310)
- Test: `backend/tests/test_unified_task_manager_missing_row.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_unified_task_manager_missing_row.py
"""R1 regression: a missing task_tracking row must NOT crash the workflow.
Before the fix, _get_phase used .single() → PGRST116 on 0 rows, killing
soda_download_workflow on its first manager.start() call."""
from __future__ import annotations

import pytest

from app.services.infra.unified_task_manager import TaskPhase, UnifiedTaskManager


class _Resp:
    def __init__(self, data):
        self.data = data


class _Query:
    """Minimal async PostgREST stub: maybe_single().execute() → data=None."""
    def table(self, *_a, **_k): return self
    def select(self, *_a, **_k): return self
    def eq(self, *_a, **_k): return self
    def maybe_single(self): return self
    def single(self):  # if the code still calls .single(), make the test loud
        raise AssertionError("_get_phase must use maybe_single(), not single()")
    async def execute(self): return _Resp(None)  # 0 rows
    def insert(self, *_a, **_k): return self


@pytest.fixture
def mgr(monkeypatch):
    m = UnifiedTaskManager()
    async def _client():
        return _Query()
    monkeypatch.setattr(m, "_get_client", _client)
    return m


async def test_get_phase_missing_row_returns_queued(mgr):
    phase = await mgr._get_phase("wf-does-not-exist")
    assert phase == TaskPhase.QUEUED


async def test_start_missing_row_does_not_raise(mgr):
    # start() must not raise PGRST116 when the row is absent.
    await mgr.start("wf-does-not-exist")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_unified_task_manager_missing_row.py -q`
Expected: FAIL — current `_get_phase` calls `.single()` (the stub's `single()` raises AssertionError), and/or `start()` propagates.

- [ ] **Step 3: Implement — `.maybe_single()` + missing → QUEUED**

In `unified_task_manager.py` `_get_phase` (the `.single()` at ~line 171). Replace the `.single()` query with `.maybe_single()` and treat `data is None` as a not-yet-created task (phase QUEUED):

```python
async def _get_phase(self, task_id: str) -> TaskPhase:
    """Fetch the current phase of a task. A missing row (not yet created,
    or a swallowed pre-create failure) is treated as QUEUED rather than
    raising PGRST116 — a missing tracking row must never kill the workflow."""
    client = await self._get_client()
    result = await (
        client.table("task_tracking")
        .select("phase")
        .eq("dbos_workflow_id", task_id)
        .maybe_single()
        .execute()
    )
    if not result or result.data is None:
        return TaskPhase.QUEUED
    raw = result.data.get("phase", "queued")
    # (keep the existing raw→TaskPhase coercion that followed the .single() call)
    ...
```

Keep the existing phase-coercion logic that converted `raw` to a `TaskPhase` — only the query + the None-guard are new.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_unified_task_manager_missing_row.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Run the existing task-manager suite for regressions**

Run: `cd backend && uv run pytest tests/ -k "task_manager or task_tracking" -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/infra/unified_task_manager.py backend/tests/test_unified_task_manager_missing_row.py
git commit -m "fix(tasks): _get_phase tolerates missing task_tracking row (kills PGRST116 workflow crash)"
```

---

## Task 2: `start()` self-heals a missing row + dispatch logs create failure loudly

**Files:**
- Modify: `backend/app/services/infra/unified_task_manager.py` (`start` ~296-310)
- Modify: `backend/app/workflows/parse.py` (`dispatch_soda_download_step` ~286-298)
- Test: extend `backend/tests/test_unified_task_manager_missing_row.py`

- [ ] **Step 1: Write the failing test (self-heal creates the row)**

Append to `tests/test_unified_task_manager_missing_row.py`:

```python
async def test_start_missing_row_selfheals_create(monkeypatch):
    """When start() finds no row, it creates a minimal one so downstream
    update_progress/complete have a row to update (the trigger mirrors
    lifecycle into it)."""
    created = {}

    class _Q2(_Query):
        async def execute(self):
            return _Resp(None)  # phase lookup: 0 rows

    m = UnifiedTaskManager()
    async def _client(): return _Q2()
    monkeypatch.setattr(m, "_get_client", _client)

    async def _fake_create(**kwargs):
        created.update(kwargs)
        return kwargs.get("dbos_workflow_id")
    monkeypatch.setattr(m, "create", _fake_create)

    await m.start("wf-missing")
    assert created.get("dbos_workflow_id") == "wf-missing"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_unified_task_manager_missing_row.py::test_start_missing_row_selfheals_create -q`
Expected: FAIL — `start()` does not currently call `create()` on a missing row.

- [ ] **Step 3: Implement self-heal in `start()`**

In `start()` (~line 296): when `_get_phase` returns QUEUED *and* the row is absent, create a minimal tracking row before proceeding. Use a presence check to avoid a duplicate insert when the row exists:

```python
async def start(self, task_id: str) -> None:
    current = await self._get_phase(task_id)
    if current == TaskPhase.PROCESSING:
        return
    # Self-heal: a swallowed pre-create (see parse.py dispatch) can leave no
    # row. Create a minimal one so the lifecycle trigger + downstream
    # update/complete have a row to mirror into. Idempotent: only when absent.
    if not await self._row_exists(task_id):
        try:
            await self.create(dbos_workflow_id=task_id, task_type="download",
                              title="(recovered)")
        except Exception as e:  # never let self-heal crash the workflow
            logger.warning(f"[task] start() self-heal create failed for {task_id}: {e}")
    ...  # existing start() body (the phase→processing transition)
```

Add a tiny `_row_exists` helper using `.maybe_single()` (returns bool). Match the actual `create()` signature in this file (pass the kwargs `create` requires; `task_type`/`title` shown are illustrative — use the real required args).

> Implementer note: read the real `create()` signature (~`unified_task_manager.py:198`) and pass exactly its required kwargs. Do NOT invent kwargs.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_unified_task_manager_missing_row.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Stop the silent swallow in dispatch (log at ERROR with workflow id)**

In `parse.py:286-298`, change the `except Exception as e: logger.warning(...)` to log at ERROR with the wf_id so a real create failure is visible (the enqueue stays — the workflow now self-heals):

```python
    except Exception as e:
        logger.error(
            f"[parse] pre-create soda download task_tracking FAILED "
            f"(wf={wf_id}, will self-heal in workflow.start): {e!r}"
        )
```

- [ ] **Step 6: Run parse tests**

Run: `cd backend && uv run pytest tests/ -k "parse or soda" -q`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/infra/unified_task_manager.py backend/app/workflows/parse.py backend/tests/test_unified_task_manager_missing_row.py
git commit -m "fix(tasks): start() self-heals missing row; dispatch logs create failure at ERROR"
```

---

## Task 3: Partitioned queues use `worker_concurrency` (SKIP LOCKED) instead of `concurrency` (NOWAIT)

**Precondition:** Task 0 verdict = per-partition (or user accepted flat-cap). Otherwise follow Task 0 Step 3 branch.

**Files:**
- Modify: `backend/app/workflows/parse.py:47-66` (`parse_user_queue` + `set_parse_concurrency`)
- Modify: `backend/app/workflows/soda_download.py:45-60` (`soda_download_queue` + `set_soda_concurrency`)
- Modify: `backend/app/workflows/download.py:53-73` (`download_user_queue` + `set_download_concurrency`)
- Modify: `backend/app/workflows/agent_workforce.py:47-51` (`agent_workforce_queue`)
- Test: `backend/tests/test_queue_lock_mode.py` (create)

- [ ] **Step 1: Write the failing test (queues must NOT set global concurrency)**

```python
# backend/tests/test_queue_lock_mode.py
"""R2: partitioned user queues must use worker_concurrency (→ SKIP LOCKED),
NOT global concurrency (→ FOR UPDATE NOWAIT → contention backoff to 120s).
dbos/_sys_db.py:3010 `skip_locks = queue.concurrency is None` is the hinge."""
from __future__ import annotations

import pytest

from app.workflows.parse import parse_user_queue
from app.workflows.soda_download import soda_download_queue
from app.workflows.download import download_user_queue


@pytest.mark.parametrize("q", [parse_user_queue, soda_download_queue, download_user_queue])
def test_queue_uses_skip_locked_not_nowait(q):
    # concurrency is None ⇒ DBOS dequeue uses SKIP LOCKED (no NOWAIT backoff).
    assert q.concurrency is None, (
        f"{q.name}: global concurrency set ⇒ NOWAIT ⇒ contention backoff. "
        "Use worker_concurrency instead."
    )
    assert q.worker_concurrency is not None and q.worker_concurrency >= 1
    assert q.partition_queue is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_queue_lock_mode.py -q`
Expected: FAIL — queues currently set `concurrency=…`, so `q.concurrency is None` is False.

- [ ] **Step 3: Implement — swap `concurrency=` → `worker_concurrency=`**

`parse.py:47-51`:

```python
parse_user_queue = Queue(
    "parse_user",
    worker_concurrency=MAX_PARSE_CONCURRENCY_DEFAULT,  # was: concurrency=...
    partition_queue=True,
)
```

`parse.py:54-66` mutator — set `worker_concurrency`:

```python
def set_parse_concurrency(n: int) -> None:
    parse_user_queue.worker_concurrency = max(1, int(n))  # was: .concurrency
```

Apply the identical change to `soda_download.py` (`soda_download_queue` + `set_soda_concurrency`), `download.py` (`download_user_queue` + `set_download_concurrency`), and `agent_workforce.py` (`agent_workforce_queue` — `worker_concurrency=_DEFAULT_CONCURRENCY`).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_queue_lock_mode.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Run the lifecycle-bus concurrency test + workflow imports**

Run: `cd backend && uv run pytest tests/ -k "concurrency or lifecycle or dispatch_bundle" -q`
Expected: all pass. If a test asserts `.concurrency`, update it to `.worker_concurrency` (same intent).

- [ ] **Step 6: Commit**

```bash
git add backend/app/workflows/parse.py backend/app/workflows/soda_download.py backend/app/workflows/download.py backend/app/workflows/agent_workforce.py backend/tests/test_queue_lock_mode.py
git commit -m "perf(dbos): partitioned queues use worker_concurrency (SKIP LOCKED) to kill NOWAIT contention backoff"
```

---

## Task 4: Periodic + executor-scoped internal-queue orphan reaper (no age-guessing of live tasks)

**Files:**
- Modify: `backend/app/startup/bootstrap.py:128-175` (`_bg_reap_internal_queue` + its scheduling)
- Test: `backend/tests/test_internal_queue_reaper_filter.py` (create)

**Design (addresses the user's "what if a task runs for days" concern):** Split the cancel into two precise predicates and run on a periodic loop (default every 120s, env `DBOS_REAP_INTERVAL_SECONDS`):
1. **Provably-dead (any age):** `queue_name='_dbos_internal_queue' AND status='PENDING' AND executor_id='gateway'`. The gateway cannot execute any dequeued workflow (it does not import `_scheduled_bundle`; recovery is executor-scoped), so such a row is dead regardless of age. A real long-running worker task has `executor_id <> 'gateway'` (it runs on the worker) or `queue_name=NULL` (start_workflow_routed) — never matched.
2. **Stale ENQUEUED scheduled ticks (age-gated, sched-* only):** unchanged from today — `queue_name='_dbos_internal_queue' AND status='ENQUEUED' AND name LIKE 'sched-%' AND age>5min`. Age is only used for the stateless `sched-*` ticks (superseded by newer ticks), never for arbitrary user workflows.

> We deliberately do NOT cancel arbitrary `_dbos_internal_queue` PENDING rows by age. That is the dangerous predicate the user flagged.

- [ ] **Step 1: Write the failing test (filter builder)**

Extract the cancel predicate into a pure SQL-builder so it is unit-testable:

```python
# backend/tests/test_internal_queue_reaper_filter.py
"""R3: the internal-queue reaper must cancel ONLY provably-dead rows
(gateway-stranded internal-queue PENDING) + stale sched-* ENQUEUED ticks.
It must NEVER target a worker-running workflow regardless of age."""
from app.startup.bootstrap import build_reap_predicates


def test_dead_gateway_rows_any_age():
    sql = build_reap_predicates()["dead_gateway"]
    assert "queue_name = '_dbos_internal_queue'" in sql
    assert "status = 'PENDING'" in sql
    assert "executor_id = 'gateway'" in sql
    assert "INTERVAL" not in sql  # NO age gate on the dead-gateway predicate


def test_stale_sched_is_age_gated_and_sched_only():
    sql = build_reap_predicates()["stale_sched"]
    assert "name LIKE 'sched-%'" in sql
    assert "status = 'ENQUEUED'" in sql
    assert "INTERVAL '5 minutes'" in sql


def test_no_blanket_age_cancel_of_pending():
    # Guard: there must be no predicate that cancels generic PENDING by age.
    preds = build_reap_predicates()
    for key, sql in preds.items():
        if "status = 'PENDING'" in sql and "INTERVAL" in sql:
            raise AssertionError(f"predicate {key} age-cancels PENDING — unsafe")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_internal_queue_reaper_filter.py -q`
Expected: FAIL — `build_reap_predicates` does not exist yet.

- [ ] **Step 3: Implement `build_reap_predicates` + use it in the reaper**

In `bootstrap.py`, add:

```python
def build_reap_predicates() -> dict[str, str]:
    """SQL WHERE clauses for the internal-queue orphan reaper. Kept pure so the
    safety properties (no blanket age-cancel of PENDING) are unit-testable."""
    return {
        # Provably dead: gateway can never execute a dequeued workflow. Any age.
        "dead_gateway": (
            "queue_name = '_dbos_internal_queue' "
            "AND status = 'PENDING' "
            "AND executor_id = 'gateway'"
        ),
        # Stale stateless scheduled ticks — superseded by newer ticks. Age-gated,
        # sched-* only. Never matches arbitrary user workflows.
        "stale_sched": (
            "queue_name = '_dbos_internal_queue' "
            "AND status = 'ENQUEUED' "
            "AND name LIKE 'sched-%' "
            "AND created_at < (EXTRACT(EPOCH FROM NOW() - INTERVAL '5 minutes') * 1000)::bigint"
        ),
    }
```

Rewrite the reaper body to `UPDATE dbos.workflow_status SET status='CANCELLED' WHERE (<dead_gateway>) OR (<stale_sched>) RETURNING workflow_uuid;` and log the count. Then schedule it periodically (replace the one-shot startup call): an asyncio task that sleeps `int(os.environ.get("DBOS_REAP_INTERVAL_SECONDS", "120"))` and re-runs, started in the same place the current `_bg_reap_internal_queue` is started (`bootstrap.py:175`). Keep it non-fatal (wrap in try/except, log warnings) — identical robustness to the current implementation.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_internal_queue_reaper_filter.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/app/startup/bootstrap.py backend/tests/test_internal_queue_reaper_filter.py
git commit -m "fix(dbos): periodic + executor-scoped internal-queue reaper (cancel only provably-dead gateway orphans, never age-guess a live task)"
```

---

## Task 5: Full-suite verification + deploy-readiness

- [ ] **Step 1: Backend suite**

Run: `cd backend && uv run pytest tests/ -q`
Expected: green (note any pre-existing flaky per [[bug_flaky_run_async_drain_test]] — rerun, don't chase).

- [ ] **Step 2: Soda + queue + task subset**

Run: `cd backend && uv run pytest tests/soda tests/test_queue_lock_mode.py tests/test_unified_task_manager_missing_row.py tests/test_internal_queue_reaper_filter.py -q`
Expected: green.

- [ ] **Step 3: Post-deploy live check (after merge + Watchtower)**

On NAS, after deploy, push a large soda playlist and confirm via psql that `parse_user` ENQUEUED drops steadily with `worker_concurrency` items PENDING (running), `Contention detected` no longer escalates in worker logs, and `_dbos_internal_queue` PENDING stays near-zero (periodic reaper). Command template:

```bash
ssh -i ~/.ssh/nas_deploy_key -p 1122 192.168.50.9 \
  "sudo /usr/local/bin/docker exec mediahub-sb-prod-db psql -U postgres -d postgres -t -c \
   \"SELECT queue_name,status,count(*) FROM dbos.workflow_status WHERE status IN ('ENQUEUED','PENDING') GROUP BY 1,2 ORDER BY 3 DESC;\""
```

---

## Decisions to confirm (surface to user before/at execution)

1. **Task 0 verdict drives Task 3.** If `worker_concurrency` is per-queue (flat), swapping changes per-user→per-worker cap. Single active user today makes it moot, but confirm we accept flat multi-user semantics, else keep `concurrency` + lean-table mitigation.
2. **Scope:** Tasks 1+2 (crash) and 4 (orphan reaper) are independently safe and high-value. Task 3 (lock-mode swap) is the biggest behavior change and the real "immediacy" win — but is the one touching the concurrency model. Confirm whether to ship all together or land 1+2+4 first, then 3.

---

## Self-Review

- **Spec coverage:** R1→Tasks 1,2. R2→Task 3 (+Task 0 gate). R3→Task 4. Push-vs-poll question→answered in Architecture (DBOS is poll-only; the fix is removing backoff so the 1s poll = immediacy). ✓
- **Placeholders:** none — every code step has concrete code; the two "read the real signature" notes are explicit implementer instructions, not hand-waving. ✓
- **Type consistency:** `worker_concurrency`/`concurrency`/`partition_queue` match the verified `Queue.__init__` signature (`_queue.py:39-48`); `TaskPhase.QUEUED`, `_get_phase`, `start`, `create`, `build_reap_predicates` are used consistently. ✓
