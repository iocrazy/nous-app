"""Compile-level coverage for Phase B3 Task 1 (task_tracking 域 — 4 文件:
workflow_health_sweeper.py / scheduled_recovery.py / storage_router.py /
scheduled_cleanup.py).

Route C (CLAUDE.md「任务系统架构纪律」) makes this batch the strictest so far:
``task_tracking.phase/status/progress/started_at/completed_at/error_msg`` are
trigger-owned columns that business code must never PATCH — except a small,
already-approved set of "writer of last resort" reconciliation paths in the
sweeper/reaper that run when the DBOS lifecycle trigger will never fire (the
workflow's executor is dead/lost/orphaned). This file pins each of those
writes to the EXACT same columns/values the pre-ORM raw SQL wrote — proving
the rewrite didn't silently widen the exception — plus the batch's other
highest-risk rewrite points:

  - the NOT EXISTS-against-``dbos.workflow_status`` subquery
    (``reap_stuck_pending_tasks_step`` Pass B) — dbos.* has no ORM model
    (structural exception), so it stays a raw ``text()`` fragment embedded in
    an otherwise-ORM UPDATE, same allowed pattern as an INTERVAL literal.
  - the INTERVAL literal in ``_find_running_audit`` (same text()-fragment
    pattern, different call site).
  - ``_fetch_latest_audit``'s ``metadata_`` -> ``.label("metadata")``: the ORM
    attribute is renamed to dodge the reserved ``Base.metadata``, so without
    the explicit label the result-mapping KEY changes silently (from
    "metadata" to "metadata_") and downstream ``row.get("metadata")`` would
    always see ``None`` — a shape regression, not a crash.

Same technique as ``tests/test_orm_b2_task2_compile_coverage.py``: capture the
compiled statement(s) handed to the fake session and assert with MUTUALLY
EXCLUSIVE assertions that the right column/value/operator survived.
"""

from __future__ import annotations

import importlib
from contextlib import asynccontextmanager
from typing import Any

import pytest
from sqlalchemy.dialects import postgresql

import app.db.session as db_session
import app.workflows.scheduled_cleanup as scheduled_cleanup
import app.workflows.scheduled_recovery as scheduled_recovery
import app.workflows.workflow_health_sweeper as sweeper
from app.services.infra.unified_task_manager import ACTIVE_PHASES

storage_router = importlib.import_module("app.api.admin.storage_router")


def _compile(stmt: Any) -> tuple[str, dict[str, Any]]:
    compiled = stmt.compile(dialect=postgresql.dialect())
    return str(compiled), dict(compiled.params)


class _FakeResult:
    def __init__(self, rows: list[Any] | None = None, rowcount: int = 0) -> None:
        self._rows = rows if rows is not None else []
        self.rowcount = rowcount

    def mappings(self) -> "_FakeResult":
        return self

    def all(self) -> list[Any]:
        return self._rows

    def first(self) -> Any:
        return self._rows[0] if self._rows else None


class _RecordingSession:
    """Appends every compiled (sql, binds) pair to a SHARED ``calls`` list so
    a function opening the scope multiple times (once per row, or once for a
    read then once for a write) can be asserted on as one combined trace."""

    def __init__(self, calls: list[Any], results: list[_FakeResult] | None = None):
        self.calls = calls
        self._results = list(results or [])
        self._default = _FakeResult()

    async def execute(self, stmt: Any) -> _FakeResult:
        self.calls.append(_compile(stmt))
        return self._results.pop(0) if self._results else self._default


class _ScopeCM:
    def __init__(self, session: _RecordingSession) -> None:
        self._session = session

    async def __aenter__(self) -> _RecordingSession:
        return self._session

    async def __aexit__(self, *exc: Any) -> bool:
        return False


def _patch_scopes(
    monkeypatch: pytest.MonkeyPatch, results: list[_FakeResult] | None = None
) -> _RecordingSession:
    calls: list[Any] = []
    session = _RecordingSession(calls, results)
    monkeypatch.setattr(db_session, "read_scope", lambda: _ScopeCM(session))
    monkeypatch.setattr(db_session, "write_scope", lambda: _ScopeCM(session))
    return session


# ── workflow_health_sweeper.py ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_classify_and_act_step_filters_queued_and_processing(monkeypatch):
    monkeypatch.setattr("app.db.engine.is_configured", lambda: True)
    session = _patch_scopes(monkeypatch, [_FakeResult(rows=[])])

    result = await sweeper.classify_and_act_step()

    assert result == sweeper._zero_counters()
    sql, binds = session.calls[0]
    assert "task_tracking" in sql
    # The sweeper must sweep every live phase, not a hand-picked pair — it used
    # to filter ("queued", "in_progress") and therefore classified no running
    # workflow at all. Compare against the one shared definition rather than
    # re-typing the words here (tests/test_task_phase_vocabulary.py owns the
    # reconciliation with the DB trigger).
    assert sorted(binds["phase_1"]) == sorted(ACTIVE_PHASES)


@pytest.mark.asyncio
async def test_refresh_policy_reads_workflow_timeout_policy_columns(monkeypatch):
    sweeper._POLICY_CACHE = {}
    sweeper._POLICY_CACHE_AT = 0.0
    monkeypatch.setattr("app.db.engine.is_configured", lambda: True)
    row = {
        "task_type": "download",
        "expected_duration_seconds": 1800,
        "hard_ceiling_seconds": 14400,
        "heartbeat_stale_seconds": 600,
    }
    session = _patch_scopes(monkeypatch, [_FakeResult(rows=[row])])

    cache = await sweeper._refresh_policy()

    assert cache["download"] == {
        "expected": 1800,
        "hard": 14400,
        "heartbeat_stale": 600,
    }
    sql, _binds = session.calls[0]
    assert "workflow_timeout_policy" in sql


@pytest.mark.asyncio
async def test_persist_classification_writes_only_health_status(monkeypatch):
    """health_status is a business-decoration column (NOT phase/status) —
    contrast with the writer-of-last-resort exceptions below."""
    session = _patch_scopes(monkeypatch)

    await sweeper._persist_classification(
        {"dbos_workflow_id": "wf-1", "health_status": None}, "STALLED"
    )

    sql, binds = session.calls[0]
    assert "task_tracking" in sql
    assert binds == {"health_status": "STALLED", "dbos_workflow_id_1": "wf-1"}


@pytest.mark.asyncio
async def test_mark_lost_writes_exactly_the_approved_columns(monkeypatch):
    session = _patch_scopes(monkeypatch)

    await sweeper._mark_lost(
        {"dbos_workflow_id": "wf-1", "task_type": "download", "title": "x"}
    )

    _sql, binds = session.calls[0]
    assert set(binds) == {
        "phase",
        "status",
        "error_code",
        "error_msg",
        "completed_at",
        "dbos_workflow_id_1",
    }
    assert binds["phase"] == "lost" and binds["status"] == "lost"
    assert binds["error_code"] == "worker_lost"


@pytest.mark.asyncio
async def test_cancel_orphan_writes_exactly_the_approved_columns(monkeypatch):
    session = _patch_scopes(monkeypatch)

    await sweeper._cancel_orphan({"dbos_workflow_id": "wf-2", "task_type": "parse"})

    _sql, binds = session.calls[0]
    assert binds["phase"] == "cancelled" and binds["status"] == "cancelled"
    assert binds["error_code"] == "executor_orphan"


@pytest.mark.asyncio
async def test_mark_timed_out_writes_failed_status_not_cancelled(monkeypatch):
    """USER_TIMEOUT's terminal STATUS is 'failed' (phase is 'timed_out') —
    the one exception writer whose status != phase, easy to typo."""
    session = _patch_scopes(monkeypatch)

    await sweeper._mark_timed_out(
        {"dbos_workflow_id": "wf-3", "max_duration_minutes": 30}
    )

    _sql, binds = session.calls[0]
    assert binds["phase"] == "timed_out"
    assert binds["status"] == "failed"  # NOT "timed_out" — must match legacy SQL
    assert binds["error_code"] == "user_timeout"
    assert "max_duration_minutes=30" in binds["error_msg"]


@pytest.mark.asyncio
async def test_cancel_dbos_zombie_reconcile_guards_against_completed(monkeypatch):
    async def fake_engine_execute(sql, params=None):
        return 1  # the raw dbos.workflow_status CANCEL "succeeded"

    monkeypatch.setattr("app.db.engine.execute", fake_engine_execute, raising=False)
    session = _patch_scopes(monkeypatch)

    ok = await sweeper._cancel_dbos_zombie("wf-4")

    assert ok is True
    _sql, binds = session.calls[0]
    assert binds["status"] == "lost" and binds["phase"] == "lost"
    # The guard: WHERE ... AND status <> 'completed' — status_1 is the WHERE
    # bind (the guard value), NOT the SET value (which is under "status").
    assert binds["status_1"] == "completed"


@pytest.mark.asyncio
async def test_cancel_owner_dead_orphan_reconcile_guards_against_completed(
    monkeypatch,
):
    async def fake_engine_execute(sql, params=None):
        return 1

    monkeypatch.setattr("app.db.engine.execute", fake_engine_execute, raising=False)
    session = _patch_scopes(monkeypatch)

    ok = await sweeper._cancel_owner_dead_orphan("wf-5")

    assert ok is True
    _sql, binds = session.calls[0]
    assert binds["status"] == "lost" and binds["phase"] == "lost"
    assert binds["status_1"] == "completed"


# ── scheduled_recovery.py ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reap_pass_a_marks_dbos_error_rows_failed(monkeypatch):
    monkeypatch.setattr(
        "app.workflows.sweep_guard.within_boot_grace", lambda: False, raising=False
    )

    async def fake_fetch_all(sql, params=None):
        # Pass A's dbos.workflow_status JOIN read stays raw (structural
        # exception) — one errored row to drive the ORM write below.
        return [{"dbos_workflow_id": "wf-err", "error": None}]

    monkeypatch.setattr("app.db.engine.fetch_all", fake_fetch_all)
    # The resources-status loop (Phase C task 1: migrated off raw
    # db_engine.execute onto update(Resources) through write_scope()) now
    # flows through the SAME _patch_scopes session as every other write in
    # this test — no separate db_engine.execute mock needed.
    session = _patch_scopes(monkeypatch)

    result = await scheduled_recovery.reap_stuck_pending_tasks_step()

    assert result["errored_failed"] == 1
    error_writes = [
        (sql, binds)
        for sql, binds in session.calls
        if binds.get("error_code") == "DBOS_ERROR"
    ]
    assert len(error_writes) == 1
    sql, binds = error_writes[0]
    assert binds["status"] == "failed" and binds["phase"] == "failed"
    assert binds["dbos_workflow_id_1"] == "wf-err"


@pytest.mark.asyncio
async def test_reap_pass_b_not_exists_dbos_subquery_is_raw_text_fragment(monkeypatch):
    """HIGHEST RISK rewrite in this batch: dbos.workflow_status has no ORM
    model, so the NOT EXISTS guard must survive as a literal text() fragment
    (not silently dropped, not turned into a bind param that could get
    mis-escaped)."""
    monkeypatch.setattr(
        "app.workflows.sweep_guard.within_boot_grace", lambda: False, raising=False
    )

    async def fake_fetch_all(sql, params=None):
        return []  # no errored rows — only Pass B's write matters here

    monkeypatch.setattr("app.db.engine.fetch_all", fake_fetch_all)
    # The resources-status loop (Phase C task 1) now runs update(Resources)
    # through the SAME _patch_scopes session — no separate db_engine.execute
    # mock needed (see the sibling Pass A test above).
    session = _patch_scopes(monkeypatch, [_FakeResult(rowcount=3)])

    result = await scheduled_recovery.reap_stuck_pending_tasks_step()

    assert result["tasks_reaped"] == 3
    lost_writes = [
        (sql, binds) for sql, binds in session.calls if binds.get("status") == "lost"
    ]
    assert len(lost_writes) == 1
    sql, binds = lost_writes[0]
    assert "NOT EXISTS" in sql and "dbos.workflow_status" in sql
    assert "'PENDING', 'ENQUEUED', 'ERROR', 'SUCCESS'" in sql
    assert binds["phase"] == "lost"
    assert binds["error_code"] == "WORKER_LOST"
    # WHERE guards (started_at IS NULL / status='pending' / phase='queued')
    # bind under their own names, distinct from the SET values above.
    assert binds["status_1"] == "pending"
    assert binds["phase_1"] == "queued"


@pytest.mark.asyncio
async def test_recover_stale_locks_reads_broad_and_narrow_cutoffs(monkeypatch):
    monkeypatch.setattr(
        "app.workflows.sweep_guard.within_boot_grace", lambda: False, raising=False
    )
    session = _patch_scopes(monkeypatch, [_FakeResult(rows=[]), _FakeResult(rows=[])])

    result = await scheduled_recovery.recover_stale_orchestrator_locks_step()

    assert result == {"status": "success", "recovered": 0}
    assert len(session.calls) == 2
    for sql, _binds in session.calls:
        assert "task_tracking" in sql
        assert "phase = %(phase_1)s" in sql
        assert "started_at <" in sql


# ── storage_router.py ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_latest_audit_preserves_metadata_key(monkeypatch):
    """metadata_ -> .label("metadata"): without this the result-mapping key
    would silently become "metadata_" and row.get("metadata") downstream
    would always see None (a shape regression the brief's rule 3 forbids)."""
    row = {
        "phase": "completed",
        "metadata": {"scanned": 5, "errors": 0},
        "completed_at": None,
    }
    session = _patch_scopes(monkeypatch, [_FakeResult(rows=[row])])

    out = await storage_router._fetch_latest_audit()

    assert out["status"] == "completed"
    assert out["scanned"] == 5
    sql, _binds = session.calls[0]
    assert "task_tracking" in sql
    assert "AS metadata" in sql  # the label survived compilation


@pytest.mark.asyncio
async def test_find_running_audit_interval_is_raw_text_fragment(monkeypatch):
    session = _patch_scopes(monkeypatch, [_FakeResult(rows=[])])

    out = await storage_router._find_running_audit()

    assert out is None
    sql, binds = session.calls[0]
    assert "interval '2 hours'" in sql
    assert "created_at >" in sql
    # phase IN (...) is a POSTCOMPILE expanding param — its values live in
    # binds, not the compiled SQL text. The set is ACTIVE_PHASES: the old
    # ("queued", "in_progress") pair matched no running audit at all, so this
    # dedup was a no-op and an admin double-click really started two scans.
    assert sorted(binds["phase_1"]) == sorted(ACTIVE_PHASES)


# ── scheduled_cleanup.py ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cleanup_old_task_tracking_deletes_terminal_states_only(monkeypatch):
    session = _patch_scopes(monkeypatch, [_FakeResult(rowcount=7)])

    result = await scheduled_cleanup.cleanup_old_task_tracking_step()

    assert result == {"status": "success", "deleted": 7}
    sql, binds = session.calls[0]
    assert sql.startswith("DELETE FROM public.task_tracking")
    assert "updated_at <" in sql
    # status IN (...) is a POSTCOMPILE expanding param — check the values.
    assert binds["status_1"] == ["completed", "failed", "cancelled"]
