"""Effective AI status = terminal column, else in-flight derived from tasks.

``resources.transcript_status`` / ``summary_status`` only ever receive
TERMINAL values in production (the three workflow write points, verified
against the prod DB: 0 rows in pending/processing/skipped out of 1421).
So every "is it being processed right now" branch keyed on those columns
was dead code. This module derives the in-flight half from
``task_tracking`` instead of teaching the columns to hold a state nobody
clears.

These tests pin the three rules the read paths depend on:
  1. a terminal column value wins outright (the column is authoritative
     once work has finished);
  2. otherwise an active ``task_tracking`` row makes it pending/processing;
  3. otherwise the column value passes through untouched.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return _Rows(self._rows)


def _patch_task_query(monkeypatch, task_rows):
    """Serve ``task_rows`` to the module's task_tracking SELECT and hand the
    compiled SQL back so tests can assert on the predicate."""
    import app.db.session as session_module

    captured: dict = {"sql": None, "calls": 0}

    class _Session:
        async def execute(self, stmt):
            from sqlalchemy.dialects import postgresql

            captured["calls"] += 1
            captured["sql"] = str(
                stmt.compile(
                    dialect=postgresql.dialect(),
                    compile_kwargs={"literal_binds": True},
                )
            )
            return _Result(task_rows)

    @asynccontextmanager
    async def _read_scope():
        yield _Session()

    monkeypatch.setattr(session_module, "read_scope", _read_scope)
    return captured


def _task(
    resource_id="1", task_type="ai_transcription", status="pending", phase="queued"
):
    return {
        "resource_id": resource_id,
        "task_type": task_type,
        "status": status,
        "phase": phase,
    }


async def _effective(monkeypatch, rows, task_rows):
    from app.services.ai.resource_ai_status import effective_ai_statuses

    captured = _patch_task_query(monkeypatch, task_rows)
    return await effective_ai_statuses(rows), captured


# ── rule 2: an active task makes an idle column read as in-flight ────


@pytest.mark.parametrize(
    "status,phase,expected",
    [
        ("pending", "queued", "pending"),
        ("processing", "in_progress", "processing"),
        # phase alone carries the signal (status lagging behind the mirror)
        ("pending", "in_progress", "processing"),
        # status alone carries it (phase never written / legacy row)
        ("running", None, "processing"),
    ],
)
async def test_active_transcription_task_lights_up_the_status(
    monkeypatch, status, phase, expected
):
    out, _ = await _effective(
        monkeypatch,
        {"1": {"transcript_status": "none", "summary_status": "none"}},
        [_task(status=status, phase=phase)],
    )
    assert out["1"]["transcript_status"] == expected
    # A transcription run says nothing about the summary.
    assert out["1"]["summary_status"] == "none"


async def test_extract_audio_counts_as_transcription_in_flight(monkeypatch):
    """The transcribe endpoint dispatches ``extract_audio`` (which chains
    transcription internally) whenever the media has no audio track yet —
    the majority path for downloaded video. Ignoring that task_type would
    leave exactly those resources reading 'none' while work is running."""
    out, _ = await _effective(
        monkeypatch,
        {"1": {"transcript_status": "none", "summary_status": "none"}},
        [_task(task_type="extract_audio", status="processing", phase="in_progress")],
    )
    assert out["1"]["transcript_status"] == "processing"


async def test_summary_task_only_moves_the_summary_status(monkeypatch):
    out, _ = await _effective(
        monkeypatch,
        {"1": {"transcript_status": "completed", "summary_status": "none"}},
        [_task(task_type="ai_summary", status="processing", phase="in_progress")],
    )
    assert out["1"]["summary_status"] == "processing"
    assert out["1"]["transcript_status"] == "completed"


async def test_processing_outranks_pending_when_both_tasks_are_active(monkeypatch):
    """extract_audio running + transcription already queued behind it: the
    resource is being processed, not merely waiting."""
    out, _ = await _effective(
        monkeypatch,
        {"1": {"transcript_status": "none", "summary_status": "none"}},
        [
            _task(task_type="ai_transcription", status="pending", phase="queued"),
            _task(task_type="extract_audio", status="processing", phase="in_progress"),
        ],
    )
    assert out["1"]["transcript_status"] == "processing"


# ── rule 1: a terminal column wins over any task row ─────────────────


@pytest.mark.parametrize("terminal", ["completed", "failed", "skipped"])
async def test_terminal_column_wins_over_an_active_task(monkeypatch, terminal):
    """A finished transcript stays finished even if a re-run is queued —
    the content is there and the agent can use it right now."""
    out, _ = await _effective(
        monkeypatch,
        {"1": {"transcript_status": terminal, "summary_status": "none"}},
        [_task(status="processing", phase="in_progress")],
    )
    assert out["1"]["transcript_status"] == terminal


async def test_a_row_with_both_columns_terminal_is_not_queried_at_all(monkeypatch):
    out, captured = await _effective(
        monkeypatch,
        {"1": {"transcript_status": "completed", "summary_status": "failed"}},
        [],
    )
    assert out["1"] == {
        "transcript_status": "completed",
        "summary_status": "failed",
    }
    assert captured["calls"] == 0


# ── rule 3: nothing in flight → the column passes through ────────────


async def test_no_active_task_leaves_the_column_alone(monkeypatch):
    out, _ = await _effective(
        monkeypatch,
        {"1": {"transcript_status": "none", "summary_status": "none"}},
        [],
    )
    assert out["1"] == {"transcript_status": "none", "summary_status": "none"}


async def test_terminal_task_rows_are_not_treated_as_in_flight(monkeypatch):
    """Defensive: the SELECT filters these out, but if a completed row ever
    reaches the reducer it must not read as in-flight."""
    out, _ = await _effective(
        monkeypatch,
        {"1": {"transcript_status": "none", "summary_status": "none"}},
        [_task(status="completed", phase="completed")],
    )
    assert out["1"]["transcript_status"] == "none"


async def test_a_missing_column_stays_none_rather_than_crashing(monkeypatch):
    """Row shapes without the columns (test doubles, older cached paths)
    must degrade, not blow up the whole @-mention turn."""
    out, _ = await _effective(monkeypatch, {"1": {}}, [])
    assert out["1"] == {"transcript_status": None, "summary_status": None}


async def test_enum_members_are_coerced_to_their_wire_value(monkeypatch):
    """Real ORM rows carry ``AiTaskStatus`` members; ``str()`` on that
    ``(str, Enum)`` mixin yields ``'AiTaskStatus.COMPLETED'``."""
    from app.models._enums import AiTaskStatus

    out, _ = await _effective(
        monkeypatch,
        {"1": {"transcript_status": AiTaskStatus.COMPLETED, "summary_status": None}},
        [],
    )
    assert out["1"]["transcript_status"] == "completed"
    assert type(out["1"]["transcript_status"]) is str


async def test_empty_input_short_circuits(monkeypatch):
    out, captured = await _effective(monkeypatch, {}, [])
    assert out == {}
    assert captured["calls"] == 0


# ── batching + query shape ───────────────────────────────────────────


async def test_many_resources_are_resolved_in_one_query(monkeypatch):
    """The picker renders 20 rows per page; one query per row would make
    the search endpoint N+1."""
    rows = {
        str(i): {"transcript_status": "none", "summary_status": "none"}
        for i in range(1, 21)
    }
    out, captured = await _effective(
        monkeypatch, rows, [_task(resource_id="7", status="processing")]
    )
    assert captured["calls"] == 1
    assert out["7"]["transcript_status"] == "processing"
    assert out["3"]["transcript_status"] == "none"


async def test_query_covers_both_transcription_task_types_and_both_columns(monkeypatch):
    _, captured = await _effective(
        monkeypatch,
        {"1": {"transcript_status": "none", "summary_status": "none"}},
        [],
    )
    sql = captured["sql"] or ""
    for expected in (
        "ai_transcription",
        "extract_audio",
        "ai_summary",
        "in_progress",
        "task_tracking",
    ):
        assert expected in sql, f"{expected!r} missing from the task query"


# ── the dedup conflict recogniser (migration 121) ────────────────────


async def test_unique_index_violation_is_recognised_as_already_in_progress():
    from app.services.ai.resource_ai_status import is_active_task_conflict

    exc = Exception(
        "duplicate key value violates unique constraint "
        '"idx_task_tracking_active_per_resource_type"'
    )
    assert is_active_task_conflict(exc) is True


async def test_an_unrelated_failure_is_not_mistaken_for_a_conflict():
    from app.services.ai.resource_ai_status import is_active_task_conflict

    assert is_active_task_conflict(RuntimeError("DBOS is not launched")) is False
    assert (
        is_active_task_conflict(
            Exception('violates unique constraint "task_tracking_pkey"')
        )
        is False
    )


async def test_conflict_is_recognised_via_the_driver_constraint_name():
    """asyncpg exposes the index name as ``constraint_name``; matching that
    first keeps the check working if the message text is ever localised."""
    from app.services.ai.resource_ai_status import is_active_task_conflict

    class _Orig:
        constraint_name = "idx_task_tracking_active_per_resource_type"

    class _Wrapped(Exception):
        orig = _Orig()

    assert is_active_task_conflict(_Wrapped("opaque")) is True
