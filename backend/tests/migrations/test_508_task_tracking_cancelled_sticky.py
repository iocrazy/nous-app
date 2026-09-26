"""Guard for migration 508 — 'cancelled' is sticky in the lifecycle mirror.

508 is a full CREATE OR REPLACE of ``mirror_dbos_lifecycle_to_tracking``
copied from the baseline. A copy is where unrelated edits sneak in, so this
pins that the ONLY differences from the baseline body are the two guarded
CASE blocks (status / phase). Behaviour on real Postgres is in
``tests/db/test_508_cancelled_sticky_integration.py``.
"""

from __future__ import annotations

import re
from pathlib import Path

_SUPABASE = Path(__file__).resolve().parents[3] / "supabase"
_MIGRATION = _SUPABASE / "migrations" / "508_task_tracking_cancelled_sticky.sql"


def _function_body(sql: str) -> str:
    start = sql.index("FUNCTION public.mirror_dbos_lifecycle_to_tracking()")
    body = sql[start : sql.index("$$;", start)]
    body = re.sub(r"--[^\n]*", "", body)  # comments are free to differ
    return re.sub(r"\s+", " ", body).strip()


def _without_guards(body: str) -> str:
    return re.sub(r"(status|phase) = CASE .*? END,", r"\1 = <guard>,", body)


def test_migration_is_a_replace_without_role_switch():
    sql = _MIGRATION.read_text(encoding="utf-8")
    assert (
        "CREATE OR REPLACE FUNCTION public.mirror_dbos_lifecycle_to_tracking()" in sql
    )
    assert "SET ROLE" not in sql.upper()
    assert "SECURITY DEFINER" not in sql.upper()


def test_cancelled_is_sticky_for_status_and_phase():
    body = _function_body(_MIGRATION.read_text(encoding="utf-8"))
    assert "WHEN status = 'cancelled' THEN status" in body
    assert "WHEN phase = 'cancelled' THEN phase" in body
    # the pre-existing guard for 'failed' against a regressing SUCCESS stays
    assert "WHEN status = 'failed' AND mapped_status = 'completed' THEN status" in body
    assert "WHEN phase = 'failed' AND mapped_phase = 'completed' THEN phase" in body


def test_only_the_two_guards_differ_from_the_baseline():
    base = _function_body(
        (_SUPABASE / "schema_baseline.sql").read_text(encoding="utf-8")
    )
    new = _function_body(_MIGRATION.read_text(encoding="utf-8"))
    assert _without_guards(new) == _without_guards(base)
