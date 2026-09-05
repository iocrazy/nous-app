"""The mirror binds Python values through the JSONB type — never a
pre-serialised string. Found on the real stack 2026-09-05: every phase-2
mirror row (todos / last_retry) and the first P4 view/cost rows were jsonb
STRINGS, so the frontend selectors read nothing. The mock boundary cannot see
this (a compiled param looks fine either way); this pins the bind's Python
type, and tests/integration/test_mirror_roundtrip.py pins the PG type."""

from __future__ import annotations

import pytest
from sqlalchemy.dialects import postgresql

from app.services.ai.runner.run_recorder import RunEventWriter

pytestmark = pytest.mark.unit


def test_mirror_binds_dicts_not_json_strings():
    w = RunEventWriter(42)
    w.views["view"]["step"] = {"done": 1, "total": 3, "label": "x"}
    c = w.mirror_stmt().compile(dialect=postgresql.dialect())
    params = dict(c.params)
    dict_binds = [v for v in params.values() if isinstance(v, dict)]
    assert any("phase" in d for d in dict_binds), "view must be bound as a dict"
    assert any("spent_cents" in d for d in dict_binds), "cost must be bound as a dict"
    assert not any(
        isinstance(v, str) and v.lstrip().startswith("{") and v != "{}"
        for v in params.values()
    ), "a JSON-looking string bind is the double-encoding bug"
    # path stays text[] (2026-08-27 lesson) and the update targets the run
    sql = str(c)
    assert "AS TEXT[]" in sql and "UPDATE public.agent_runs" in sql
