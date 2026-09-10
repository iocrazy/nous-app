"""461 must keep every 460 literal, admit the three orchestration events and
subagent_result, free cron_expr behind a CHECK, and actually drop agent_tasks.
A DROP/ADD that forgets one literal silently rejects that family's inserts."""

import pathlib
import re

import pytest
from sqlalchemy import CheckConstraint

from app.models.agents import AgentRunInbox
from tests.models.test_transcript_event_types_phase2a import _literals

pytestmark = pytest.mark.unit
MIG = pathlib.Path(__file__).resolve().parents[3] / "supabase/migrations"
BODY = (MIG / "461_harness_p4_phase2b2_orchestration.sql").read_text(encoding="utf-8")
_ARRAYS = re.compile(r"ARRAY\[(.*?)\]", re.DOTALL)
_LITERAL = re.compile(r"'([a-z_]+)'::text")
INBOX_KIND_CONSTRAINT = "agent_run_inbox_kind_check"


def _array(index: int) -> frozenset[str]:
    found = _LITERAL.findall(_ARRAYS.findall(BODY)[index])
    assert len(found) == len(set(found)), "duplicate literal"
    return frozenset(found)


def _orm_inbox_kind_sql() -> str:
    checks = [
        c
        for c in AgentRunInbox.__table__.constraints
        if isinstance(c, CheckConstraint) and c.name == INBOX_KIND_CONSTRAINT
    ]
    assert len(checks) == 1, "inbox kind CHECK must be declared exactly once"
    return str(checks[0].sqltext)


def test_461_is_a_superset_of_460_plus_the_orchestration_events():
    prev = _literals((MIG / "460_transcript_event_type_fork.sql").read_text())
    cur = _array(0)
    assert {"subagent_spawned", "subagent_done", "schedule_set"} <= cur
    assert prev <= cur, prev - cur


def test_461_admits_subagent_result_and_keeps_the_453_inbox_kinds():
    kinds = _array(1)
    assert "subagent_result" in kinds
    assert {"steer", "answer", "pause", "resume", "budget_reply"} <= kinds


def test_461_frees_cron_disables_dead_task_types_and_drops_agent_tasks():
    for needle in (
        "ALTER COLUMN cron_expr DROP NOT NULL",
        "user_schedules_cron_or_once",
        "task_type = 'issue_wakeup'",
        # NULL-safe by construction: a missing `once` key makes the bare
        # comparison NULL, and Postgres reads a NULL CHECK as satisfied.
        "coalesce(payload->>'once', '') = 'true'",
        "pause_reason = 'task_type_unsupported'",
        "DROP TABLE IF EXISTS public.agent_tasks",
    ):
        assert needle in BODY, needle
    assert not any(
        re.match(r"(?i)^SET\s+ROLE\b", line.strip()) for line in BODY.splitlines()
    ), "migrations must not SET ROLE (see CLAUDE.md)"


def test_inbox_kind_orm_literal_matches_the_migration_exactly():
    """Nothing else binds these two lists: schema-drift compares columns, not
    CHECK bodies, and the transcript mirror test only guards the other table.
    Either side gaining or losing a kind is drift."""
    assert _literals(_orm_inbox_kind_sql()) == _array(1)
