"""Compile-level coverage for Phase B4 Task 1 (schedules/conversations 域 —
6 文件: scheduled_master.py / liveness_scanner.py / consolidate_agent_memory.py
/ agent_cost_anomaly.py / write_memory.py / services/liveness/reconcile.py).

Same technique as tests/test_orm_b3_task1_compile_coverage.py: capture the
compiled statement(s) handed to a fake session and assert with MUTUALLY
EXCLUSIVE assertions that the right column/value/operator survived. This
file pins the batch's highest-risk rewrite points:

  - agent_cost_anomaly's CTE chain (hourly/base/cur): the zscore division
    must stay float/float arithmetic — an explicit CAST(base.sd AS FLOAT) is
    present specifically to STOP SQLAlchemy's Numeric-comparator machinery
    from silently upgrading the divisor to NUMERIC (which would change the
    threshold comparison's precision, not just the rounded display value).
  - scheduled_master's dbos_workflow_id write: SET LOCAL ROLE service_role
    (a raw text() fragment — issues.dbos_workflow_id is service_role-only
    per the mig-170 column-allowlist trigger) followed by a plain ORM
    UPDATE, mirroring issue_lifecycle.py's established pattern.
  - consolidate_agent_memory's NULL-safe context matching: the personal-team
    CASE expression compared with IS NOT DISTINCT FROM (not a plain `=`,
    which would never match a NULL team_id context).
  - the body->>'text' JSONB operator (Messages.body["text"].astext) shared
    by write_memory.load_recent_messages_step and
    consolidate_agent_memory._recent_messages_stmt.
  - liveness_scanner's CAS updates: the WHERE-clause guard column must stay
    distinct from the SET-clause target column of the same name
    (liveness_state=<target> WHERE liveness_state=<expected>).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

import app.db.session as db_session
from app.workflows.agent_cost_anomaly import _findings_stmt
from app.workflows.consolidate_agent_memory import _recent_messages_stmt
from app.workflows.write_memory import load_recent_messages_step


def _compile(stmt: Any) -> tuple[str, dict[str, Any]]:
    compiled = stmt.compile(dialect=postgresql.dialect())
    return str(compiled), dict(compiled.params)


class _FakeResult:
    """Shared fake Result for the two ``read_scope``/``write_scope``-driven
    tests below — records nothing itself; call sites append to a list held
    by ``_RecordingSession`` instead."""

    def __init__(self, rows: list[Any] | None = None, scalar: Any = None) -> None:
        self._rows = rows if rows is not None else []
        self._scalar = scalar

    def mappings(self) -> "_FakeResult":
        return self

    def all(self) -> list[Any]:
        return self._rows

    def first(self) -> Any:
        return self._rows[0] if self._rows else None

    def scalar(self) -> Any:
        return self._scalar


class _RecordingSession:
    def __init__(self, results: list[_FakeResult] | None = None) -> None:
        self.calls: list[Any] = []
        self._results = list(results or [])
        self._default = _FakeResult()

    async def execute(self, stmt: Any) -> _FakeResult:
        self.calls.append(stmt)
        return self._results.pop(0) if self._results else self._default


def _patch_scopes(
    monkeypatch: pytest.MonkeyPatch, results: list[_FakeResult] | None = None
) -> _RecordingSession:
    session = _RecordingSession(results)

    @asynccontextmanager
    async def fake_scope():
        yield session

    monkeypatch.setattr(db_session, "read_scope", fake_scope)
    monkeypatch.setattr(db_session, "write_scope", fake_scope)
    return session


# ── agent_cost_anomaly.py — CTE chain + float-arithmetic guard ─────────────


def test_findings_stmt_zscore_division_stays_float_not_numeric():
    """The zscore divisor (base.sd) carries an explicit CAST(... AS FLOAT).
    Without it, SQLAlchemy's Numeric-comparator machinery inserts an implicit
    CAST(... AS NUMERIC) around the STDDEV_SAMP-derived column when it's
    divided — silently switching the WHERE clause's threshold comparison from
    float to NUMERIC arithmetic (not just the final rounded display value)."""
    sql, _binds = _compile(_findings_stmt(24, 3.0, 50.0))
    assert "WITH hourly AS" in sql
    assert "stddev_samp(hourly.cost)" in sql
    # The division appears 3x (zscore SELECT column, WHERE guard, ORDER BY);
    # each must show the FLOAT cast on the divisor, never NUMERIC. (A
    # legitimate CAST(base.sd AS NUMERIC) DOES appear once elsewhere — the
    # baseline_sd display column's rounding — so the assertion targets the
    # division expression specifically, not "NUMERIC" anywhere in the SQL.)
    assert sql.count("(cur.cost - base.mean) / CAST(base.sd AS FLOAT)") == 3
    assert "(cur.cost - base.mean) / CAST(base.sd AS NUMERIC)" not in sql


def test_findings_stmt_bind_params_carry_named_thresholds():
    _sql, binds = _compile(_findings_stmt(24, 3.0, 50.0))
    assert binds["min_hours"] == 24
    assert binds["z"] == 3.0
    assert binds["min_cost"] == 50.0


def test_findings_stmt_joins_ai_agents_for_slug_fallback():
    sql, _binds = _compile(_findings_stmt(24, 3.0, 50.0))
    assert "LEFT OUTER JOIN public.ai_agents" in sql
    assert "coalesce(public.ai_agents.slug, CAST(cur.agent_id AS TEXT))" in sql


# ── scheduled_master.py — SET LOCAL ROLE + service-role write ──────────────


@pytest.mark.asyncio
async def test_dbos_workflow_id_write_uses_set_local_role_then_plain_update(
    monkeypatch: pytest.MonkeyPatch,
):
    """dbos_workflow_id is guarded by the mig-170 column-allowlist trigger
    (service_role only). Drives the REAL production path
    (scheduled_master._fire_agent_routine) end-to-end — not a locally
    reconstructed statement — and captures what it actually executes: the
    write must be SET LOCAL ROLE service_role (a text() fragment — no ORM
    model represents a role-switch statement) followed by a plain ORM
    UPDATE, never a bare execute_as_service_role() raw-SQL call."""
    from app.workflows import scheduled_master as sm

    agent_id = str(uuid4())
    # [0] budget_team_id lookup (scalar None -> is_team_over_budget(None)==False)
    # [1] agent lookup
    session = _patch_scopes(
        monkeypatch,
        [
            _FakeResult(scalar=None),
            _FakeResult(rows=[{"id": agent_id, "name": "CEO"}]),
        ],
    )

    async def _atomic_create(body: dict) -> dict:
        return {"id": 42, **body}

    repo = MagicMock()
    repo.atomic_create = AsyncMock(side_effect=_atomic_create)
    tracker = MagicMock()
    tracker.create = AsyncMock(return_value="task-1")

    with (
        patch("app.repositories.issue_repository.issue_repository", repo),
        patch(
            "app.services.infra.unified_task_manager.get_task_manager",
            lambda: tracker,
        ),
    ):
        order = await sm._fire_agent_routine(
            {
                "id": "sched-1",
                "user_id": "11111111-1111-1111-1111-111111111111",
                "name": "Daily digest",
                "payload": {
                    "agent_slug": "ceo",
                    "prompt_md": "Summarize yesterday's downloads.",
                },
            }
        )

    compiled_calls = [_compile(c) for c in session.calls]
    role_calls = [
        (sql, binds) for sql, binds in compiled_calls if "SET LOCAL ROLE" in sql
    ]
    assert len(role_calls) == 1
    assert role_calls[0] == ("SET LOCAL ROLE service_role", {})

    issue_updates = [
        (sql, binds) for sql, binds in compiled_calls if "public.issues" in sql
    ]
    assert len(issue_updates) == 1
    update_sql, update_binds = issue_updates[0]
    assert update_sql == (
        "UPDATE public.issues SET dbos_workflow_id=%(dbos_workflow_id)s "
        "WHERE public.issues.id = %(id_1)s"
    )
    assert update_binds == {"dbos_workflow_id": order["workflow_id"], "id_1": 42}

    # Ordering: SET LOCAL ROLE must precede the UPDATE within the SAME
    # write_scope() transaction — a role switch issued afterwards wouldn't
    # protect the write it's meant to guard.
    role_idx = session.calls.index(
        next(
            c for c in session.calls if _compile(c)[0] == "SET LOCAL ROLE service_role"
        )
    )
    update_idx = session.calls.index(
        next(c for c in session.calls if "public.issues" in _compile(c)[0])
    )
    assert role_idx < update_idx


# ── consolidate_agent_memory.py — NULL-safe personal-team matching ─────────


def test_recent_messages_stmt_team_id_uses_is_not_distinct_from():
    """A personal-team context (team_id=None) must match via
    IS NOT DISTINCT FROM, not `=` — Postgres' `= NULL` never matches,
    which would silently make every personal-scope consolidation see zero
    recent messages."""
    sql, _binds = _compile(_recent_messages_stmt("u1", "a1", None, None))
    assert (
        "CASE WHEN (public.teams.kind = %(kind_1)s) THEN NULL ELSE "
        "public.conversations.scope_id END IS NOT DISTINCT FROM NULL" in sql
    )
    assert "conversations.scope_id = " not in sql  # never a plain equality


def test_recent_messages_stmt_team_scoped_context_binds_team_id():
    """A team-scoped context (team_id=10) binds the value into the IS NOT
    DISTINCT FROM comparison rather than rendering a literal NULL."""
    _sql, binds = _compile(_recent_messages_stmt("u1", "a1", 10, None))
    assert 10 in binds.values()


# ── shared JSONB ->> operator (write_memory + consolidate_agent_memory) ────


def test_load_recent_messages_step_uses_astext_for_body_text():
    """Messages.body["text"].astext compiles to the JSONB ->> operator, not
    the -> (object) operator — a ->  regression would hand back a JSON
    string literal (quoted) instead of the plain text content."""
    import asyncio

    async def _capture() -> tuple[str, dict[str, Any]]:
        from contextlib import asynccontextmanager

        import app.db.session as db_session

        captured: dict[str, Any] = {}

        class _FakeResult:
            def mappings(self):
                return self

            def all(self):
                return []

        class _Session:
            async def execute(self, stmt):
                captured["stmt"] = stmt
                return _FakeResult()

        @asynccontextmanager
        async def fake_read_scope():
            yield _Session()

        import pytest

        mp = pytest.MonkeyPatch()
        mp.setattr(db_session, "read_scope", fake_read_scope)
        try:
            await load_recent_messages_step("123")
        finally:
            mp.undo()
        return _compile(captured["stmt"])

    sql, binds = asyncio.run(_capture())
    assert "body ->> %(body_1)s" in sql
    assert "body -> %(body_1)s" not in sql.replace("body ->> ", "")
    assert binds["body_1"] == "text"
    assert binds["coalesce_1"] == ""


# ── liveness_scanner.py — CAS update WHERE-guard distinct from SET target ──


@pytest.mark.asyncio
async def test_recover_from_stuck_where_guard_distinct_from_set_target(
    monkeypatch: pytest.MonkeyPatch,
):
    """_recover_from_stuck's CAS UPDATE sets liveness_state='running' but
    GUARDS on liveness_state='stuck' — same column name on both sides, must
    bind under DIFFERENT keys or the guard would silently collide with the
    target value. Calls the REAL production function (not a locally
    reconstructed statement) and captures what it actually executes."""
    from app.workflows import liveness_scanner as ls

    session = _patch_scopes(monkeypatch)

    await ls._recover_from_stuck(42)

    assert len(session.calls) == 1
    _sql, binds = _compile(session.calls[0])
    assert binds["liveness_state"] == "running"  # SET target
    assert binds["liveness_state_1"] == "stuck"  # WHERE guard — distinct key
    assert binds["id_1"] == 42
