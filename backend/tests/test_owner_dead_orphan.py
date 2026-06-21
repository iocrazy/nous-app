"""Owner-dead orphan reaper (Worker Foundation P2, HA enablement).

`_is_owner_dead_orphan` decides which non-terminal `dbos.workflow_status` rows
belong to a PROVABLY-dead worker (its `executor_id` is in the worker_registry
stale set) and so are safe to cancel + mark lost(retryable).

This is the flip side of P3a's per-replica recovery isolation: a dead replica's
in-flight rows are never reclaimed by a sibling (no double-exec), so they'd
linger forever — here we free them using the P1 registry heartbeat as EVIDENCE
of death, never a per-task timer (which would re-create #492 on coarse
mediahub workflows).

Pure helper → testable without a DB.
"""

import app.workflows.workflow_health_sweeper as sweeper

_FLOOR = 180.0
_STALE = frozenset({"worker-1", "worker-2"})


def _orphan(**kw):
    """Build args with sensible defaults; override per-case."""
    base = dict(
        status="RUNNING",
        queue_name="download_user",
        name="download_workflow",
        executor_id="worker-1",
        stale_executor_ids=_STALE,
        age_seconds=300.0,
        age_floor_seconds=_FLOOR,
    )
    base.update(kw)
    return sweeper._is_owner_dead_orphan(
        base["status"],
        base["queue_name"],
        base["name"],
        base["executor_id"],
        base["stale_executor_ids"],
        base["age_seconds"],
        base["age_floor_seconds"],
    )


# ── the precise signal: owner in the stale set ─────────────────────────
def test_running_on_dead_owner_is_orphan():
    assert _orphan(status="RUNNING", executor_id="worker-1") is True


def test_pending_on_dead_owner_is_orphan():
    assert _orphan(status="PENDING", executor_id="worker-2") is True


def test_enqueued_on_dead_owner_is_orphan():
    assert _orphan(status="ENQUEUED") is True


def test_case_insensitive_status():
    assert _orphan(status="running") is True


# ── live / unknown owner is never an orphan (bias to alive) ────────────
def test_live_owner_is_not_orphan():
    # owner not in the stale set → the worker is alive → healthy long task.
    assert _orphan(executor_id="worker-0") is False


def test_empty_owner_is_not_orphan():
    # No recorded owner → not yet claimed / unknown → never cancel.
    assert _orphan(executor_id=None) is False
    assert _orphan(executor_id="") is False


def test_empty_stale_set_never_orphan():
    assert _orphan(stale_executor_ids=frozenset()) is False


# ── age floor avoids racing freshly-claimed rows ───────────────────────
def test_too_young_is_not_orphan():
    assert _orphan(age_seconds=_FLOOR - 1) is False
    assert _orphan(age_seconds=_FLOOR + 1) is True


# ── terminal statuses are never orphans ────────────────────────────────
def test_terminal_status_is_never_orphan():
    for s in ("SUCCESS", "ERROR", "CANCELLED", "", None):
        assert _orphan(status=s) is False


# ── internal / scheduled workflows are out of scope ────────────────────
def test_internal_queue_excluded():
    assert _orphan(queue_name="_dbos_internal_queue") is False


def test_scheduled_workflow_excluded():
    assert _orphan(name="sched-workflow_health_sweeper-2026") is False


import pytest  # noqa: E402

# ── step-level safety: gating + never-reap-self ────────────────────────
import app.db.engine as _db_engine  # noqa: E402
import app.services.infra.worker_identity as _worker_identity  # noqa: E402
import app.workflows.sweep_guard as _sweep_guard  # noqa: E402


@pytest.mark.asyncio
async def test_step_noop_when_flag_off(monkeypatch):
    monkeypatch.setattr(_worker_identity, "multi_worker_enabled", lambda: False)
    result = await sweeper.reap_owner_dead_orphans_step()
    assert result == {"owner_dead_cancelled": 0, "skipped_disabled": 1}


def _arm_step(monkeypatch, *, stale, me, rows, cancelled_box):
    """Wire the step's collaborators so only the stale-set / self logic runs."""
    monkeypatch.setattr(_worker_identity, "multi_worker_enabled", lambda: True)
    monkeypatch.setattr(_sweep_guard, "within_boot_grace", lambda *a, **k: False)
    monkeypatch.setattr(_db_engine, "is_configured", lambda: True)

    async def _stale(_engine, _secs):
        return list(stale)

    monkeypatch.setattr(_worker_identity, "stale_executor_ids", _stale)
    monkeypatch.setattr(_worker_identity, "current_executor_id", lambda: me)

    async def _fetch_all(_sql, _params):
        return list(rows)

    monkeypatch.setattr(_db_engine, "fetch_all", _fetch_all)

    async def _cancel(wuid):
        cancelled_box.append(wuid)
        return True

    monkeypatch.setattr(sweeper, "_cancel_owner_dead_orphan", _cancel)


@pytest.mark.asyncio
async def test_step_never_reaps_self(monkeypatch):
    # The only "stale" id is THIS worker → filtered out → nothing queried/cancelled.
    box = []
    _arm_step(
        monkeypatch, stale=["worker-0"], me="worker-0", rows=[], cancelled_box=box
    )
    result = await sweeper.reap_owner_dead_orphans_step()
    assert result == {"owner_dead_cancelled": 0}
    assert box == []


@pytest.mark.asyncio
async def test_step_cancels_dead_sibling_orphan(monkeypatch):
    box = []
    rows = [
        {
            "workflow_uuid": "wf-dead",
            "executor_id": "worker-1",
            "status": "RUNNING",
            "queue_name": "download_user",
            "name": "download_workflow",
            "age_s": 600.0,
        }
    ]
    _arm_step(
        monkeypatch,
        stale=["worker-1", "worker-0"],  # worker-0 (self) must be ignored
        me="worker-0",
        rows=rows,
        cancelled_box=box,
    )
    result = await sweeper.reap_owner_dead_orphans_step()
    assert result == {"owner_dead_cancelled": 1}
    assert box == ["wf-dead"]


@pytest.mark.asyncio
async def test_step_skips_too_young_sibling_row(monkeypatch):
    # A row owned by a dead sibling but younger than the floor is left alone.
    box = []
    rows = [
        {
            "workflow_uuid": "wf-young",
            "executor_id": "worker-1",
            "status": "RUNNING",
            "queue_name": "download_user",
            "name": "download_workflow",
            "age_s": 10.0,
        }
    ]
    _arm_step(
        monkeypatch, stale=["worker-1"], me="worker-0", rows=rows, cancelled_box=box
    )
    result = await sweeper.reap_owner_dead_orphans_step()
    assert result == {"owner_dead_cancelled": 0}
    assert box == []
