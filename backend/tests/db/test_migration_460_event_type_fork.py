"""460 must admit 'fork', keep every type 459 admitted, and match the ORM
CheckConstraint literal EXACTLY (a stale ORM literal is silent drift: no
runtime check reads it)."""

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


def test_460_matches_the_orm_check_literal_exactly():
    assert _mig("460_transcript_event_type_fork.sql") == _literals(_orm_check_sql())
