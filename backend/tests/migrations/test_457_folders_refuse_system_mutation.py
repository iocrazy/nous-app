"""Guard for migration 457 — the system-folder guard moves into the database.

mig 441 put it in the API layer (``_refuse_if_system`` in the folders router,
409 ``system_folder`` on rename / move / trash / delete). The frontend's folder
move does not go through that router: ``moveFolder`` in
``frontend/services/resourceService.ts`` writes ``public.folders`` through
PostgREST directly, so batch Move could relocate Chat Uploads or the
cover-template library with nothing refusing it anywhere.

A trigger is the only placement every writer passes. These assertions pin the
three properties that make it one — it fires BEFORE UPDATE on the right table,
it keys on ``OLD.is_system``, and it raises with the HINT the frontend reads
back (``frontend/hooks/moveBatch.ts::describeMoveFailure``) — plus the
idempotence the migration runner requires.

Text assertions, no database: the migration is applied by the CI runner, and
the behaviour it produces belongs to ``tests/db`` integration coverage.
"""

from __future__ import annotations

from pathlib import Path

_MIGRATION = (
    Path(__file__).resolve().parents[3]
    / "supabase"
    / "migrations"
    / "457_folders_refuse_system_mutation.sql"
)


def _sql() -> str:
    assert _MIGRATION.exists(), f"missing migration: {_MIGRATION}"
    return _MIGRATION.read_text(encoding="utf-8")


def test_trigger_fires_before_update_on_folders():
    assert "BEFORE UPDATE ON public.folders" in _sql()


def test_guard_keys_on_the_old_rows_is_system_flag():
    """OLD, not NEW — otherwise clearing is_system in the same UPDATE escapes."""
    assert "OLD.is_system" in _sql()


def test_refusal_carries_the_hint_the_frontend_matches_on():
    assert "HINT = 'system_folder'" in _sql()


def test_the_four_protected_columns_are_all_named():
    sql = _sql()
    for column in ("parent_id", "library_id", "name", "is_trashed"):
        assert f"NEW.{column} IS DISTINCT FROM OLD.{column}" in sql


def test_migration_is_idempotent():
    """The runner may replay it; neither statement may fail on a second pass."""
    sql = _sql()
    assert "CREATE OR REPLACE FUNCTION public.folders_refuse_system_mutation" in sql
    assert (
        "DROP TRIGGER IF EXISTS trg_folders_refuse_system_mutation ON public.folders"
        in sql
    )


def test_no_set_role_and_no_concurrent_index():
    """Both are repo-wide bans for migrations (CLAUDE.md); cheap to re-pin here."""
    sql = _sql().upper()
    assert "SET ROLE" not in sql
    assert "CONCURRENTLY" not in sql
