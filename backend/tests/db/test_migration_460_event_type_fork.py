"""460 must admit 'fork' and keep every type 459 admitted (a DROP/ADD that
forgets one silently rejects that family's best-effort inserts)."""

import pathlib
import re

import pytest

pytestmark = pytest.mark.unit
MIG = pathlib.Path(__file__).resolve().parents[3] / "supabase/migrations"


def _types(path: pathlib.Path) -> set[str]:
    return set(re.findall(r"'([a-z_]+)'::text", path.read_text()))


def test_460_is_a_superset_of_459_plus_fork():
    prev = _types(MIG / "459_transcript_event_types_phase2a.sql")
    cur = _types(MIG / "460_transcript_event_type_fork.sql")
    assert "fork" in cur
    assert prev <= cur, prev - cur
