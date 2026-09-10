"""issue_lifecycle steps must hit the SQLAlchemy ORM session with the right
statements (Phase B1 rewrite of the raw ``db_engine.fetch_*``/``execute_*``
calls). These don't hit a real database — they capture every statement handed
to the session, compile it against the postgresql dialect, and assert the
WHERE / SET / jsonb-operator semantics survived the rewrite.

``issue_lifecycle.py`` does local (function-scope) imports of ``read_scope``/
``write_scope`` from ``app.db.session`` — patching happens on the SOURCE
module (``app.db.session``), not on ``issue_lifecycle`` itself, since each
call re-imports the current attribute value (same idiom as
``test_input_gate_orm.py``)."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql.elements import TextClause

from app.db import session as db_session


def _compile(stmt: Any) -> tuple[str, dict[str, Any]]:
    compiled = stmt.compile(dialect=postgresql.dialect())
    return str(compiled), dict(compiled.params)


class _FakeResult:
    def __init__(self, *, rowcount: int = 0, scalar: Any = None, rows: Any = None):
        self.rowcount = rowcount
        self._scalar = scalar
        self._rows = rows if rows is not None else []

    def scalar_one_or_none(self) -> Any:
        return self._scalar

    def mappings(self) -> "_FakeMappings":
        return _FakeMappings(self._rows)


class _FakeMappings:
    def __init__(self, rows: Any) -> None:
        self._rows = rows

    def first(self) -> Any:
        return self._rows[0] if self._rows else None

    def all(self) -> Any:
        return self._rows


class _FakeSession:
    """Captures every (compiled_sql, binds) pair handed to execute()."""

    def __init__(self, results: list[_FakeResult] | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._results = list(results or [])
        self._default = _FakeResult()

    async def execute(self, stmt: Any) -> _FakeResult:
        self.calls.append(_compile(stmt))
        # "SET LOCAL ROLE service_role" is an auxiliary statement — its result
        # is never consumed by callers, so it must not eat a queued result
        # meant for the real SELECT/UPDATE that follows.
        if isinstance(stmt, TextClause):
            return self._default
        if self._results:
            return self._results.pop(0)
        return self._default


class _ScopeCM:
    def __init__(self, session: _FakeSession) -> None:
        self._session = session

    async def __aenter__(self) -> _FakeSession:
        return self._session

    async def __aexit__(self, *exc: Any) -> bool:
        return False


def _patch_scopes(monkeypatch: pytest.MonkeyPatch, session: _FakeSession) -> None:
    monkeypatch.setattr(db_session, "write_scope", lambda: _ScopeCM(session))
    monkeypatch.setattr(db_session, "read_scope", lambda: _ScopeCM(session))


def _all_sql(session: _FakeSession) -> str:
    return "\n".join(sql for sql, _ in session.calls)


def _last_update_call(session: _FakeSession) -> tuple[str, dict[str, Any]]:
    """The most recent UPDATE public.issues call — set_status's own write,
    not the stage-node projection's SELECT that may follow it."""
    updates = [c for c in session.calls if c[0].startswith("UPDATE public.issues")]
    return updates[-1]


async def test_atomic_checkout_updates_with_lock_guard(monkeypatch):
    import app.workflows.issue_lifecycle as il

    session = _FakeSession([_FakeResult(rowcount=1)])
    _patch_scopes(monkeypatch, session)

    locked = await il.atomic_checkout(42, "wf-1")

    assert locked is True
    sql = _all_sql(session)
    assert "SET LOCAL ROLE service_role" in sql
    assert "UPDATE public.issues SET" in sql
    assert "dbos_workflow_id=" in sql and "execution_locked_at=" in sql
    assert "public.issues.execution_locked_at IS NULL" in sql
    assert "public.issues.id = " in sql
    binds = session.calls[-1][1]
    assert binds["dbos_workflow_id"] == "wf-1"
    assert binds["id_1"] == 42


async def test_atomic_checkout_closes_the_dispatch_window_in_the_same_update(
    monkeypatch,
):
    """Phase 2b-2 §4.1: the ``dispatching`` marker must be REMOVED (jsonb ``-``,
    not merged to null) by the very UPDATE that takes the lock. A separate
    write could be interleaved by the fork this marker exists to stop, and a
    merged ``null`` would leave the key behind for every other reader."""
    import app.workflows.issue_lifecycle as il

    session = _FakeSession([_FakeResult(rowcount=1)])
    _patch_scopes(monkeypatch, session)

    assert await il.atomic_checkout(42, "wf-1") is True

    sql, binds = session.calls[-1]
    assert "execution_locked_at=" in sql and "execution_state=" in sql
    # The removal operand is a bound literal cast to text — the same explicit
    # form set_status uses so jsonb's overloaded ``-`` is not ambiguous.
    assert " - CAST(" in sql
    assert "dispatching" in [v for v in binds.values() if v == "dispatching"]
    # execution_state is NULLABLE and most rows have never been decorated.
    # `NULL - 'k'` is NULL in Postgres, which would WIPE the column on every
    # checkout; the coalesce is what keeps that from happening.
    assert "coalesce(public.issues.execution_state" in sql.lower()


async def test_atomic_checkout_returns_false_when_no_row(monkeypatch):
    import app.workflows.issue_lifecycle as il

    session = _FakeSession([_FakeResult(rowcount=0)])
    _patch_scopes(monkeypatch, session)

    assert await il.atomic_checkout(42, "wf-1") is False


async def test_set_status_in_progress_sets_started_at(monkeypatch):
    import app.workflows.issue_lifecycle as il

    session = _FakeSession()
    _patch_scopes(monkeypatch, session)

    await il.set_status(7, "in_progress")

    sql, binds = _last_update_call(session)
    assert "status=" in sql and "started_at=" in sql
    assert "completed_at" not in sql and "cancelled_at" not in sql
    assert binds["status"] == "in_progress"
    assert "started_at" in binds and binds["id_1"] == 7


def _state_payload(binds: dict[str, Any]) -> str:
    """The json.dumps(state) string handed to the jsonb merge operand."""
    return next(
        v
        for v in binds.values()
        if isinstance(v, str) and v.startswith("{") and v != "{}"
    )


async def test_set_status_blocked_writes_jsonb_error_state(monkeypatch):
    import app.workflows.issue_lifecycle as il

    session = _FakeSession()
    _patch_scopes(monkeypatch, session)

    await il.set_status(7, "blocked", error_code="x", error_message="boom")

    sql, binds = _last_update_call(session)
    assert "execution_state=" in sql and "CAST(" in sql
    assert '"error_code": "x"' in _state_payload(binds)
    # Writing an error must not also strip the keys it is writing.
    assert "error_code" not in [v for v in binds.values() if v == "error_code"]


# --------------------------------------------------------------------------
# execution_state ownership (2026-08-03 needs_input E2E follow-up)
#
# Two defects, one column: the write used to be a whole-column assignment
# that clobbered other writers' keys, and it skipped the column entirely on
# transitions that carried no error/outcome — so a resumed issue kept the
# error that blocked it. See set_status's docstring.
# --------------------------------------------------------------------------


async def test_set_status_resume_removes_stale_error_keys(monkeypatch):
    """blocked → in_progress must drop the previous run's error."""
    import app.workflows.issue_lifecycle as il

    session = _FakeSession()
    _patch_scopes(monkeypatch, session)

    await il.set_status(7, "in_progress")

    sql, binds = _last_update_call(session)
    assert "execution_state=" in sql, "column must be written on a bare resume"
    removed = [v for v in binds.values() if v in ("error_code", "error_message")]
    assert sorted(removed) == ["error_code", "error_message"]


async def test_set_status_resume_preserves_other_writers_keys(monkeypatch):
    """The removal is a subtraction on the existing column, not an
    assignment — `turn` / `awaiting_input` must survive a resume."""
    import app.workflows.issue_lifecycle as il

    session = _FakeSession()
    _patch_scopes(monkeypatch, session)

    await il.set_status(7, "in_progress")

    sql, _ = _last_update_call(session)
    assert "coalesce(public.issues.execution_state" in sql.lower()


async def test_set_status_outcome_merges_instead_of_overwriting(monkeypatch):
    """An outcome-carrying transition merges, so a concurrently-written
    awaiting_input marker is not wiped."""
    import app.workflows.issue_lifecycle as il

    session = _FakeSession()
    _patch_scopes(monkeypatch, session)

    await il.set_status(
        7, "needs_followup", agent_outcome="needs_input", outcome_reason="Mon or Wed?"
    )

    sql, binds = _last_update_call(session)
    assert "||" in sql, "must be a jsonb merge"
    assert "coalesce(public.issues.execution_state" in sql.lower()
    assert json.loads(_state_payload(binds)) == {
        "agent_outcome": "needs_input",
        "outcome_reason": "Mon or Wed?",
    }


async def test_set_status_outcome_without_error_also_clears_stale_error(monkeypatch):
    """A non-error outcome must not inherit a previous run's error — the old
    assignment dropped it as a side effect, the merge has to do it on
    purpose."""
    import app.workflows.issue_lifecycle as il

    session = _FakeSession()
    _patch_scopes(monkeypatch, session)

    await il.set_status(7, "in_review", agent_outcome="completed")

    sql, binds = _last_update_call(session)
    removed = [v for v in binds.values() if v in ("error_code", "error_message")]
    assert sorted(removed) == ["error_code", "error_message"]
    assert '"agent_outcome": "completed"' in _state_payload(binds)


async def test_set_status_error_write_builds_on_existing_column(monkeypatch):
    """The defect that hurt most: writing an error used to ASSIGN the whole
    column, taking awaiting_input / turn / stranded_* with it. Two structural
    guarantees are asserted here — the new value is built FROM the column
    itself, and the payload carries only the keys set_status owns, so there
    is nothing in the statement capable of dropping a foreign key.

    The semantic proof (keys actually present in the row afterwards) needs a
    real jsonb engine and lives in
    tests/migrations/test_405_execution_state_key_preservation.py."""
    import app.workflows.issue_lifecycle as il

    session = _FakeSession()
    _patch_scopes(monkeypatch, session)

    await il.set_status(7, "blocked", error_code="x", error_message="boom")

    sql, binds = _last_update_call(session)
    assert "coalesce(public.issues.execution_state" in sql.lower()
    assert "||" in sql
    assert set(json.loads(_state_payload(binds))) == {"error_code", "error_message"}


async def test_set_status_error_and_outcome_merge_together(monkeypatch):
    import app.workflows.issue_lifecycle as il

    session = _FakeSession()
    _patch_scopes(monkeypatch, session)

    await il.set_status(
        7,
        "blocked",
        error_code="TIMEOUT",
        error_message="took too long",
        agent_outcome="continue_capped",
    )

    sql, binds = _last_update_call(session)
    assert json.loads(_state_payload(binds)) == {
        "error_code": "TIMEOUT",
        "error_message": "took too long",
        "agent_outcome": "continue_capped",
    }
    assert "coalesce(public.issues.execution_state" in sql.lower()


async def test_set_status_done_sets_completed_at(monkeypatch):
    import app.workflows.issue_lifecycle as il

    session = _FakeSession()
    _patch_scopes(monkeypatch, session)

    await il.set_status(7, "done")

    sql, binds = _last_update_call(session)
    assert "completed_at=" in sql
    assert binds["status"] == "done"


async def test_set_status_cancelled_sets_cancelled_at(monkeypatch):
    import app.workflows.issue_lifecycle as il

    session = _FakeSession()
    _patch_scopes(monkeypatch, session)

    await il.set_status(7, "cancelled")

    sql, binds = _last_update_call(session)
    assert "cancelled_at=" in sql
    assert binds["status"] == "cancelled"


async def test_clear_lock_nullifies_execution_lock(monkeypatch):
    import app.workflows.issue_lifecycle as il

    session = _FakeSession()
    _patch_scopes(monkeypatch, session)

    await il.clear_lock(5)

    sql, binds = session.calls[-1]
    assert "SET LOCAL ROLE service_role" in _all_sql(session)
    assert "UPDATE public.issues SET execution_locked_at=" in sql
    assert binds["execution_locked_at"] is None
    assert binds["id_1"] == 5


async def test_load_issue_raises_when_missing(monkeypatch):
    import app.workflows.issue_lifecycle as il

    session = _FakeSession([_FakeResult(rows=[])])
    _patch_scopes(monkeypatch, session)

    with pytest.raises(RuntimeError, match="not found"):
        await il.load_issue(999)


async def test_run_issue_agent_step_invokes_executor(monkeypatch):
    import app.workflows.issue_lifecycle as il

    ran = {}

    async def fake_run_issue_agent(
        *, issue, agent_id, user_id, is_continuation=False, auto=False
    ):
        ran["issue_id"] = issue["id"]
        ran["agent_id"] = agent_id
        ran["user_id"] = user_id
        ran["is_continuation"] = is_continuation
        ran["auto"] = auto
        return {"content": "essay output", "outcome": "completed", "reason": "done"}

    monkeypatch.setattr(
        "app.services.issues.issue_agent_executor.run_issue_agent",
        fake_run_issue_agent,
    )
    out = await il.run_issue_agent_step(
        {"id": 409, "title": "t", "description": "d"}, "agent-uuid", "user-uuid"
    )
    assert ran == {
        "issue_id": 409,
        "agent_id": "agent-uuid",
        "user_id": "user-uuid",
        "is_continuation": False,
        # M4 Autopilot (task O2): default auto=False when the workflow-level
        # kwarg isn't threaded in (a plain manual-dispatch call).
        "auto": False,
    }
    assert out["content"] == "essay output"
    assert out["outcome"] == "completed"


async def test_load_issue_normalizes_datetime_and_uuid(monkeypatch):
    import datetime as dt
    import uuid

    import app.workflows.issue_lifecycle as il

    owner = uuid.uuid4()
    row = {
        "id": 7,
        "created_at": dt.datetime(2026, 5, 24, tzinfo=dt.timezone.utc),
        "assignee_user_id": owner,
        "title": "x",
    }
    session = _FakeSession([_FakeResult(rows=[row])])
    _patch_scopes(monkeypatch, session)

    out = await il.load_issue(7)

    assert out["id"] == 7  # bigint passes through
    assert isinstance(out["created_at"], str)  # datetime → isoformat
    assert out["assignee_user_id"] == str(owner)  # UUID → str
    assert out["title"] == "x"


# ── set_status → project_stage node projection (probe follow-up) ─────────────


async def test_set_status_in_review_projects_onto_stage_node(monkeypatch):
    """Regression (live E2E probe, 2026-07-31): ``set_status`` writes
    ``public.issues`` with an UPDATE, bypassing ``transition_status`` where the
    issue→node projection lives. An agent self-completing to ``in_review``
    left its stage node on ``in_progress``, so the Stage Board showed work
    still running that was actually awaiting a manager's review."""
    import app.workflows.issue_lifecycle as il

    fired = {}
    row = {"id": 7, "origin_kind": "project_stage", "origin_id": "ps:1:2"}
    session = _FakeSession([_FakeResult(), _FakeResult(rows=[row])])
    _patch_scopes(monkeypatch, session)

    async def fake_sync(issue, new_status, *, enqueue_autopilot=True):
        fired["issue"] = issue
        fired["status"] = new_status
        fired["enqueue_autopilot"] = enqueue_autopilot

    with patch("app.repositories.issue_repository.fire_stage_node_sync", fake_sync):
        await il.set_status(7, "in_review")

    assert fired["status"] == "in_review"
    assert fired["issue"]["origin_kind"] == "project_stage"
    # Suppressed on purpose: set_status runs inside a @DBOS.step, where
    # starting a workflow raises a bare AssertionError.
    assert fired["enqueue_autopilot"] is False
    # second execute() call is the SELECT id, origin_kind, origin_id read
    read_sql = session.calls[-1][0]
    assert "origin_kind" in read_sql


async def test_set_status_skips_projection_for_unmapped_status(monkeypatch):
    """``blocked`` has no node counterpart — don't even issue the SELECT."""
    import app.workflows.issue_lifecycle as il

    session = _FakeSession()
    _patch_scopes(monkeypatch, session)

    with patch("app.repositories.issue_repository.fire_stage_node_sync") as mock_sync:
        await il.set_status(7, "blocked", error_code="x", error_message="boom")
        mock_sync.assert_not_called()

    # Only SET LOCAL ROLE + the UPDATE ran — no SELECT for the stage-node
    # projection (blocked has no node counterpart).
    assert len(session.calls) == 2
    assert not any(sql.startswith("SELECT") for sql, _ in session.calls)


async def test_set_status_survives_a_failing_stage_node_projection(monkeypatch):
    """The projection is enrichment — a failure must never abort the status
    write the workflow depends on."""
    import app.workflows.issue_lifecycle as il

    row = {"id": 7, "origin_kind": "project_stage", "origin_id": "ps:1:2"}
    session = _FakeSession([_FakeResult(), _FakeResult(rows=[row])])
    _patch_scopes(monkeypatch, session)

    async def boom(issue, new_status, *, enqueue_autopilot=True):
        raise RuntimeError("node repo down")

    with patch("app.repositories.issue_repository.fire_stage_node_sync", boom):
        await il.set_status(7, "in_review")  # must not raise


@pytest.mark.parametrize("terminal", ["done", "cancelled", "closed"])
async def test_set_status_terminal_clears_paused_at(monkeypatch, terminal):
    """Phase 2a: the agent's own terminal landing ends a target-level pause
    (the rollup reads paused_at first — a done issue must not show paused)."""
    import app.workflows.issue_lifecycle as il

    session = _FakeSession()
    _patch_scopes(monkeypatch, session)
    await il.set_status(7, terminal)
    sql, binds = _last_update_call(session)
    assert "paused_at=" in sql
    assert binds["paused_at"] is None


async def test_set_status_non_terminal_leaves_paused_at_alone(monkeypatch):
    import app.workflows.issue_lifecycle as il

    session = _FakeSession()
    _patch_scopes(monkeypatch, session)
    await il.set_status(7, "in_progress")
    sql, _ = _last_update_call(session)
    assert "paused_at" not in sql
