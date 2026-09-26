"""492 re-declares both allowlists 461 last wrote: the transcript event types
gain ``media_job_done`` and the inbox kinds gain ``media_result`` (async
GenerateVideo). A DROP/ADD that forgets one existing literal silently rejects
that family's inserts, so each side must be a superset of 461 AND equal to
its ORM mirror (schema-drift compares columns, never CHECK bodies)."""

import pathlib
import re

import pytest
from sqlalchemy import CheckConstraint

from app.models.agents import AgentRunInbox, AgentRunTranscriptEvents

pytestmark = pytest.mark.unit
MIG = pathlib.Path(__file__).resolve().parents[3] / "supabase/migrations"
_NAME = "492_agent_video_async_inbox.sql"
_RAW = (MIG / _NAME).read_text(encoding="utf-8")
BODY = "\n".join(
    line for line in _RAW.splitlines() if not line.strip().startswith("--")
)
_ARRAYS = re.compile(r"ARRAY\[(.*?)\]", re.DOTALL)
_LITERAL = re.compile(r"'([a-z_]+)'::text")


def _arrays(sql: str) -> list[frozenset[str]]:
    out = []
    for arr in _ARRAYS.findall(sql):
        found = _LITERAL.findall(arr)
        assert len(found) == len(set(found)), "duplicate literal"
        out.append(frozenset(found))
    return out


def _orm(model, name: str) -> frozenset[str]:
    checks = [
        c
        for c in model.__table__.constraints
        if isinstance(c, CheckConstraint) and c.name == name
    ]
    assert len(checks) == 1, name
    (only,) = _arrays(str(checks[0].sqltext))
    return only


def test_492_is_the_only_migration_with_that_number():
    assert [p.name for p in MIG.glob("492_*.sql")] == [_NAME]


def test_492_keeps_every_461_literal_and_adds_the_two_new_ones():
    prev_events, prev_kinds = _arrays(
        (MIG / "461_harness_p4_phase2b2_orchestration.sql").read_text()
    )[:2]
    events, kinds = _arrays(BODY)
    assert prev_events <= events, prev_events - events
    assert prev_kinds <= kinds, prev_kinds - kinds
    assert events - prev_events == {"media_job_done"}
    assert kinds - prev_kinds == {"media_result"}


def test_orm_mirrors_equal_the_migration():
    """Direction only for the event types (509 re-declared the allowlist, so
    equality-to-the-head lives in test_transcript_event_types_phase2a via
    ``LATEST_MIGRATION``): nothing 492 admitted may fall out of the ORM. The
    inbox kinds are still 492's own list, so that one stays equality."""
    events, kinds = _arrays(BODY)
    orm_events = _orm(
        AgentRunTranscriptEvents, "agent_run_transcript_events_event_type_check"
    )
    assert events <= orm_events, events - orm_events
    assert _orm(AgentRunInbox, "agent_run_inbox_kind_check") == kinds


def test_492_is_idempotent_and_does_not_set_role():
    assert BODY.count("DROP CONSTRAINT IF EXISTS") == 2
    assert not any(
        re.match(r"(?i)^SET\s+ROLE\b", line.strip()) for line in BODY.splitlines()
    ), "migrations must not SET ROLE (see CLAUDE.md)"
