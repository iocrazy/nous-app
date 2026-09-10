"""mig 459: the transcript event-type CHECK must admit the phase-2a question
events on BOTH sides -- the ORM literal and the migration file (what actually
reaches Postgres).

schema-drift (tests/db/test_schema_drift.py) compares tables / columns / FKs
only, never CHECK bodies, so this mirror test is the only thing binding the
two literals together. It parses the ARRAY[...] out of each side and demands
set equality, so a literal added or dropped on either side turns it red --
no third hand-copied list to drift.
"""

import re
from pathlib import Path

from sqlalchemy import CheckConstraint

from app.models.agents import AgentRunTranscriptEvents

PHASE_2A_EVENT_TYPES = frozenset({"question_asked", "question_answered"})
# Emitted by agent_runner since the capability gate landed, admitted only from 459.
LATENT_EVENT_TYPES = frozenset({"capability_denied"})
CONSTRAINT = "agent_run_transcript_events_event_type_check"
MIGRATION = (
    Path(__file__).resolve().parents[3]
    / "supabase"
    / "migrations"
    / "459_transcript_event_types_phase2a.sql"
)
# The allowlist is re-declared whole by each migration that touches it; the
# ORM literal must equal the LATEST one (461 admitted the phase-2b-2
# orchestration events).
LATEST_MIGRATION = MIGRATION.parent / "461_harness_p4_phase2b2_orchestration.sql"
_ARRAY = re.compile(r"ARRAY\[(.*?)\]", re.DOTALL)
_LITERAL = re.compile(r"'([a-z_]+)'::text")


def _literals(sql: str) -> frozenset[str]:
    arrays = _ARRAY.findall(sql)
    assert (
        len(arrays) == 1
    ), f"expected exactly one ARRAY[...] literal, got {len(arrays)}"
    found = _LITERAL.findall(arrays[0])
    assert len(found) == len(set(found)), "duplicate event type literal"
    return frozenset(found)


def _first_array(sql: str) -> frozenset[str]:
    """Literals of the FIRST ARRAY[...] only.

    ``_literals`` demands exactly one array, which is right for a migration
    that touches nothing but this allowlist. From 461 the allowlist migration
    also re-declares ``agent_run_inbox.kind``, so the event-type comparison
    slices the first array instead of loosening ``_literals`` for every caller.
    """
    arrays = _ARRAY.findall(sql)
    assert arrays, "expected at least one ARRAY[...] literal"
    found = _LITERAL.findall(arrays[0])
    assert len(found) == len(set(found)), "duplicate event type literal"
    return frozenset(found)


def _orm_check_sql() -> str:
    checks = [
        c
        for c in AgentRunTranscriptEvents.__table__.constraints
        if isinstance(c, CheckConstraint) and c.name == CONSTRAINT
    ]
    assert len(checks) == 1, "event-type CHECK must be declared exactly once"
    return str(checks[0].sqltext)


def _migration_body() -> str:
    assert MIGRATION.is_file(), MIGRATION
    return MIGRATION.read_text(encoding="utf-8")


def _statements(body: str) -> list[str]:
    return [
        line.strip()
        for line in body.splitlines()
        if line.strip() and not line.strip().startswith("--")
    ]


def test_orm_event_type_check_admits_phase2a_question_events():
    assert PHASE_2A_EVENT_TYPES <= _literals(_orm_check_sql())


def test_migration_459_admits_phase2a_question_events():
    body = _migration_body()
    assert CONSTRAINT in body
    assert PHASE_2A_EVENT_TYPES <= _literals(body)


def test_migration_459_admits_latent_capability_denied():
    assert LATENT_EVENT_TYPES <= _literals(_migration_body())
    assert LATENT_EVENT_TYPES <= _literals(_orm_check_sql())


def test_migration_and_orm_event_type_sets_are_identical():
    """Either side gaining or losing a literal is drift; nothing else checks it.
    Compares the LATEST allowlist migration (each one re-declares the whole
    CHECK), not 459."""
    assert _first_array(LATEST_MIGRATION.read_text(encoding="utf-8")) == _literals(
        _orm_check_sql()
    )


def test_migration_459_does_not_set_role():
    assert not any(
        re.match(r"(?i)^SET\s+ROLE\b", line) for line in _statements(_migration_body())
    ), "migrations must not SET ROLE (see CLAUDE.md)"
