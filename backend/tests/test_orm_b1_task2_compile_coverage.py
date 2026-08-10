"""Compile-level coverage for Phase B1 Task 2 (issues 域 workflows 层): the
highest-risk operator-equivalence points in the 5-file batch — jsonb ``||``
merge, ``to_jsonb``/``CAST`` typing, the ``NOT EXISTS`` correlated subquery,
the ``->>`` text-extraction operator (vs the JSONB-typed ``->`` Task 1 had to
pin), and the ``INTERVAL`` literal fragment.

These don't hit a real database — they capture the compiled statement each
function hands to the session and assert the operator/WHERE clause survived
the raw-SQL → ORM rewrite, same technique as ``test_orm_b1_compile_coverage.py``
/ ``test_input_gate_orm.py``.

Two import styles are in play (see each module's own top): ``issue_lifecycle.py``
and ``stranded_issue_monitor.py`` do function-scope imports of
``read_scope``/``write_scope`` (patch the SOURCE module ``app.db.session``);
``backfill_issue_scope.py``, ``publish_issue_mirror.py`` and
``backfill_project_stage_issue_team_ids.py`` import them at module scope
(patch the attribute directly on each workflow module).
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.dialects import postgresql

import app.workflows.backfill_issue_scope as backfill_issue_scope
import app.workflows.publish_issue_mirror as publish_issue_mirror
import app.workflows.stranded_issue_monitor as stranded_issue_monitor
from app.db import session as db_session
from app.models import Issues, Projects, Teams


def _compile(stmt: Any) -> tuple[str, dict[str, Any]]:
    compiled = stmt.compile(dialect=postgresql.dialect())
    return str(compiled), dict(compiled.params)


class _FakeMappingsResult:
    def __init__(
        self,
        rows: list[Any] | None = None,
        scalar: Any = None,
        rowcount: int | None = None,
    ) -> None:
        self._rows = rows or []
        self._scalar = scalar
        self.rowcount = len(self._rows) if rowcount is None else rowcount

    def mappings(self) -> "_FakeMappingsResult":
        return self

    def first(self) -> Any:
        return self._rows[0] if self._rows else None

    def all(self) -> list[Any]:
        return self._rows

    def scalars(self) -> "_FakeScalars":
        return _FakeScalars(self._rows)

    def scalar_one_or_none(self) -> Any:
        return self._scalar


class _FakeScalars:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows


class _FakeSession:
    def __init__(self, result: _FakeMappingsResult | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._result = result or _FakeMappingsResult()

    async def execute(self, stmt: Any) -> _FakeMappingsResult:
        self.calls.append(_compile(stmt))
        return self._result


class _QueuedSession:
    """Returns one queued result per execute() call, in order — for
    functions (like ``_repair_rounded``) that issue several DIFFERENT
    statements in sequence (SELECT DISTINCT bad ids → SELECT reference-table
    ids → UPDATE) where a single fixed canned response isn't enough."""

    def __init__(self, results: list[_FakeMappingsResult]) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._queue = list(results)

    async def execute(self, stmt: Any) -> _FakeMappingsResult:
        self.calls.append(_compile(stmt))
        return self._queue.pop(0)


class _ScopeCM:
    def __init__(self, session: _FakeSession) -> None:
        self._session = session

    async def __aenter__(self) -> _FakeSession:
        return self._session

    async def __aexit__(self, *exc: Any) -> bool:
        return False


def _all_sql(session: _FakeSession) -> str:
    return "\n".join(sql for sql, _ in session.calls)


# ─── issue_lifecycle.mark_turn_progress ────────────────────────────────────


async def test_mark_turn_progress_merges_jsonb_with_db_side_timestamp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The A1-2 progress decoration must MERGE (``||``) into execution_state,
    never overwrite it (that's ``set_status``'s job on terminal transitions),
    and the turn_started_at timestamp must stay DB-side (``now() AT TIME
    ZONE 'utc'`` via ``to_char``), not an app-clock Python value — the two can
    drift under DBOS step replay."""
    import app.workflows.issue_lifecycle as il

    session = _FakeSession()
    monkeypatch.setattr(db_session, "write_scope", lambda: _ScopeCM(session))

    await il.mark_turn_progress(7, 3)

    sql = _all_sql(session)
    assert "SET LOCAL ROLE service_role" in sql
    assert "coalesce(public.issues.execution_state" in sql
    assert " || jsonb_build_object(" in sql
    assert "CAST(" in sql and "AS INTEGER)" in sql
    assert "to_char(now() AT TIME ZONE " in sql
    assert "WHERE public.issues.id = " in sql
    update_call = next(c for c in session.calls if c[0].startswith("UPDATE"))
    binds = update_call[1]
    assert 3 in binds.values()  # the turn number, CAST to integer


# ─── stranded_issue_monitor._scan_stranded ─────────────────────────────────


async def test_scan_stranded_uses_not_exists_not_left_join(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pre-ORM SQL used a correlated ``NOT EXISTS`` (not a LEFT JOIN +
    IS NULL) — the rewrite must keep the same shape, or a live agent_run in
    ANY liveness state (not just running/silent/stuck) could wrongly count
    as "no live run" under a naive join rewrite."""
    monkeypatch.setattr(
        "app.workflows.workflow_health_sweeper._dbos_still_owns",
        lambda wf_id: False,
    )
    session = _FakeSession(_FakeMappingsResult(rows=[]))
    monkeypatch.setattr(db_session, "read_scope", lambda: _ScopeCM(session))

    await stranded_issue_monitor._scan_stranded()

    sql = _all_sql(session)
    assert "NOT (EXISTS (SELECT" in sql
    assert "public.agent_runs.issue_id = public.issues.id" in sql
    assert "public.agent_runs.status = " in sql
    assert "public.agent_runs.liveness_state IN " in sql
    assert "LEFT OUTER JOIN" not in sql
    assert "public.issues.status = " in sql
    assert "public.issues.assignee_agent_id IS NOT NULL" in sql


# ─── stranded_issue_monitor._prepare_redispatch ────────────────────────────


async def test_prepare_redispatch_jsonb_merge_typed_via_to_jsonb(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``to_jsonb(:cnt::int)`` / ``to_jsonb(:ts::text)`` in the retired SQL
    forced explicit PG types before boxing into jsonb — the ORM rewrite must
    keep those CASTs (a bare Python int/str bound straight into
    jsonb_build_object risks a different jsonb scalar type on replay)."""
    session = _FakeSession()
    monkeypatch.setattr(db_session, "write_scope", lambda: _ScopeCM(session))

    wf_id = await stranded_issue_monitor._prepare_redispatch(42, 1)

    assert wf_id.startswith("issue-42-")
    sql = _all_sql(session)
    assert "SET LOCAL ROLE service_role" in sql
    assert "to_jsonb(CAST(" in sql
    assert "AS INTEGER))" in sql and "AS TEXT))" in sql
    assert " || jsonb_build_object(" in sql
    update_call = next(c for c in session.calls if c[0].startswith("UPDATE"))
    binds = update_call[1]
    assert binds.get("execution_locked_at") is None
    assert wf_id in binds.values()


# ─── publish_issue_mirror._unmirrored_stmt ─────────────────────────────────


def test_unmirrored_stmt_uses_text_extract_not_jsonb_arrow() -> None:
    """The retired SQL read ``t.metadata->>'publish_task_id'`` (text-
    extracting ``->>``, needed because the value feeds ``NULLIF(...,
    '')::bigint`` and a plain string comparison) — NOT the JSONB-typed ``->``
    Task 1 had to pin for ``_load_awaiting_marker``. Getting this backwards
    would break both the NULLIF/CAST chain and the join condition."""
    sql, binds = _compile(publish_issue_mirror._unmirrored_stmt())

    assert "metadata ->> " in sql
    assert "->" not in sql.replace("->>", "")  # only the text-extract form, no bare ->
    assert "nullif(public.task_tracking.metadata ->> " in sql
    assert "AS BIGINT)" in sql
    assert "interval '30 days'" in sql
    assert "LEFT OUTER JOIN public.publish_tasks" in sql
    assert "CAST(public.task_tracking.user_id AS TEXT)" in sql
    assert "ORDER BY public.task_tracking.created_at DESC" in sql
    # P1-2: the go-live instant must be selected, or the schedule gate silently
    # sees None for every row and closes scheduled batches early.
    assert "public.publish_tasks.scheduled_at" in sql


def test_mirrored_open_stmt_terminal_phase_and_open_status_filters() -> None:
    sql, binds = _compile(publish_issue_mirror._mirrored_open_stmt())

    assert "public.task_tracking.phase IN " in sql
    assert "public.issues.status NOT IN " in sql
    assert (
        "JOIN public.issues ON public.issues.id = public.task_tracking.issue_id" in sql
    )
    # The schedule gate's input, and the join must stay OUTER — a batch whose
    # publish_tasks row was deleted must still be able to close.
    assert "public.publish_tasks.scheduled_at" in sql
    assert "LEFT OUTER JOIN public.publish_tasks" in sql


# ─── backfill_issue_scope: null-scope repair COALESCE ──────────────────────


async def test_repair_null_scope_write_coalesces_project_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The retired SQL wrote ``project_id = COALESCE(project_id, :project_id)``
    — an existing non-NULL project_id on the issue must survive; only a NULL
    one gets backfilled from the canvas's project. A naive ``.values(
    project_id=...)`` without the DB-side COALESCE would clobber it."""
    scope_row = {"canvas_id": 5, "project_id": 100, "team_id": 200}
    issue_row = {"id": 1, "origin_id": "canvas:5"}

    calls = {"n": 0}

    class _SeqSession(_FakeSession):
        async def execute(self, stmt: Any):
            calls["n"] += 1
            self.calls.append(_compile(stmt))
            if calls["n"] == 1:
                return _FakeMappingsResult(rows=[issue_row])
            if calls["n"] == 2:
                return _FakeMappingsResult(rows=[scope_row])
            return _FakeMappingsResult(rowcount=1)  # the UPDATE itself

    session = _SeqSession()
    monkeypatch.setattr(backfill_issue_scope, "read_scope", lambda: _ScopeCM(session))
    monkeypatch.setattr(backfill_issue_scope, "write_scope", lambda: _ScopeCM(session))

    result: dict[str, Any] = {
        "null_scope_would_fix": 0,
        "null_scope_fixed": 0,
        "null_scope_malformed": 0,
        "null_scope_unresolvable": 0,
    }
    await backfill_issue_scope._repair_null_scope(False, 500, result)

    update_call = next(c for c in session.calls if c[0].startswith("UPDATE"))
    sql, binds = update_call
    assert "project_id=coalesce(public.issues.project_id, " in sql
    assert "team_id=" in sql
    assert result["null_scope_fixed"] == 1


# ─── backfill_issue_scope._repair_rounded (team + project) ────────────────
#
# Review round 1, Important: this is the one function in the batch whose
# internal signature moved from string SQL constants to column
# objects/models (reused for BOTH team and project repair via the same
# code path) — the highest-risk-of-copy-paste-drift spot with no compiled-
# statement assertion in the original delivery. Parametrized over both
# targets so a future swap of column/model between the two call sites (e.g.
# team repair accidentally binding to Issues.project_id, or vice versa)
# fails loudly here instead of silently corrupting the other domain's ids.


@pytest.mark.parametrize(
    "kind,issue_col,ref_model",
    [
        ("team", Issues.team_id, Teams),
        ("project", Issues.project_id, Projects),
    ],
)
async def test_repair_rounded_compiles_matching_column_for_each_target(
    kind: str,
    issue_col: Any,
    ref_model: type,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    table_name = ref_model.__tablename__
    session = _QueuedSession(
        [
            _FakeMappingsResult(rows=[42]),  # SELECT DISTINCT ... NOT IN (...)
            _FakeMappingsResult(rows=[42]),  # SELECT id FROM <ref_model>
            _FakeMappingsResult(rowcount=1),  # UPDATE ... SET <col>=:good
        ]
    )
    monkeypatch.setattr(backfill_issue_scope, "read_scope", lambda: _ScopeCM(session))
    monkeypatch.setattr(backfill_issue_scope, "write_scope", lambda: _ScopeCM(session))

    result: dict[str, Any] = {
        f"rounded_{kind}_distinct_bad": 0,
        f"rounded_{kind}_rows_fixed": 0,
        f"rounded_{kind}_would_fix": 0,
        f"rounded_{kind}_ambiguous_or_orphan": 0,
    }
    await backfill_issue_scope._repair_rounded(
        kind, issue_col, ref_model, False, result
    )

    assert len(session.calls) == 3

    bad_sql, _ = session.calls[0]
    assert f"SELECT DISTINCT public.issues.{issue_col.key}" in bad_sql
    assert f"public.issues.{issue_col.key} IS NOT NULL" in bad_sql
    assert (
        f"public.issues.{issue_col.key} NOT IN (SELECT public.{table_name}.id"
        in bad_sql
    )

    all_ids_sql, _ = session.calls[1]
    assert all_ids_sql.startswith(f"SELECT public.{table_name}.id")
    assert f"FROM public.{table_name}" in all_ids_sql

    update_sql, update_binds = session.calls[2]
    assert update_sql.startswith(f"UPDATE public.issues SET {issue_col.key}=")
    assert f"WHERE public.issues.{issue_col.key} = " in update_sql
    assert 42 in update_binds.values()

    assert result[f"rounded_{kind}_rows_fixed"] == 1
    assert result[f"rounded_{kind}_distinct_bad"] == 1
