"""460 must admit 'fork', keep every type 459 admitted, and stay wholly
INSIDE the ORM CheckConstraint literal (a stale ORM literal is silent drift:
no runtime check reads it).

Until 461 this file asserted set EQUALITY with the ORM, which was the same
statement as "the ORM matches the newest allowlist migration" only while 460
WAS the newest. Each allowlist migration re-declares the whole CHECK, so
equality-to-the-head lives in exactly one place —
``test_transcript_event_types_phase2a.test_migration_and_orm_event_type_sets_are_identical``,
which reads ``LATEST_MIGRATION``. What this file still owes is direction:
nothing 460 admitted may quietly fall out of the ORM.
"""

import pathlib

import pytest

from tests.models.test_transcript_event_types_phase2a import _literals, _orm_check_sql

pytestmark = pytest.mark.unit
MIG = pathlib.Path(__file__).resolve().parents[3] / "supabase/migrations"


def _mig(name: str) -> frozenset[str]:
    return _literals((MIG / name).read_text())


def test_460_is_a_superset_of_459_plus_fork():
    prev = _mig("459_transcript_event_types_phase2a.sql")
    cur = _mig("460_transcript_event_type_fork.sql")
    assert "fork" in cur
    assert prev <= cur, prev - cur


def test_460_literals_all_survive_in_the_orm_check():
    prev = _mig("460_transcript_event_type_fork.sql")
    orm = _literals(_orm_check_sql())
    assert prev <= orm, prev - orm
