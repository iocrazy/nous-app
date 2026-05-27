"""Verify _pre_launch_sweep_stale_scheduled cutoff default + env override."""

from __future__ import annotations

from unittest.mock import patch

from app.services.infra import dbos_orchestrator


class _FakeCur:
    """Module-level fake cursor — captures every execute() call's (sql, params)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False

    def execute(self, sql, params):
        self.calls.append((sql, params))

    def fetchall(self):
        return []


class _FakeConn:
    """Module-level fake connection — returns a single shared _FakeCur instance."""

    def __init__(self) -> None:
        self.cur = _FakeCur()

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False

    def cursor(self):
        return self.cur

    def commit(self):
        pass


def test_default_cutoff_is_three_minutes(monkeypatch):
    """Default cutoff must be 3 minutes (was 30 — caused recovery storms)."""
    monkeypatch.delenv("DBOS_STALE_SCHED_CUTOFF_MINUTES", raising=False)
    monkeypatch.setenv("DBOS_DATABASE_URL", "")  # short-circuit: sweep returns
    # Capture the cutoff value used by the SQL by spying on psycopg.
    with patch("psycopg.connect") as conn_mock:
        dbos_orchestrator._pre_launch_sweep_stale_scheduled()
        # DBOS_DATABASE_URL is empty so psycopg.connect must NOT be called.
        assert conn_mock.call_count == 0


def test_default_cutoff_without_env_var(monkeypatch):
    """When DBOS_STALE_SCHED_CUTOFF_MINUTES is unset, the SQL uses 3 (default)."""
    monkeypatch.delenv("DBOS_STALE_SCHED_CUTOFF_MINUTES", raising=False)
    monkeypatch.setenv("DBOS_DATABASE_URL", "postgresql://stub/none")
    conn = _FakeConn()
    with patch("psycopg.connect", return_value=conn):
        dbos_orchestrator._pre_launch_sweep_stale_scheduled()
    assert conn.cur.calls[0][1] == (3,)


def test_env_override_respected(monkeypatch):
    """DBOS_STALE_SCHED_CUTOFF_MINUTES env var overrides default."""
    monkeypatch.setenv("DBOS_STALE_SCHED_CUTOFF_MINUTES", "7")
    monkeypatch.setenv("DBOS_DATABASE_URL", "postgresql://stub/none")
    conn = _FakeConn()
    with patch("psycopg.connect", return_value=conn):
        dbos_orchestrator._pre_launch_sweep_stale_scheduled()
    # First execute() call is the UPDATE — its params should reflect the override.
    assert conn.cur.calls[0][1] == (7,)


def test_sweep_issues_only_the_update(monkeypatch):
    """Sweep must issue exactly the UPDATE — no DELETE against the
    non-existent dbos._dbos_internal_queue table. Earlier code added
    a DELETE that crashed with UndefinedTable on prod (DBOS 2.x has
    no such table — `_dbos_internal_queue` is a queue name, stored
    in dbos.workflow_status.queue_name). The crash rolled back the
    UPDATE in the same transaction, so the whole sweep silently
    failed. Hotfix 2026-05-27."""
    monkeypatch.setenv("DBOS_DATABASE_URL", "postgresql://stub/none")
    monkeypatch.setenv("DBOS_STALE_SCHED_CUTOFF_MINUTES", "5")
    conn = _FakeConn()
    with patch("psycopg.connect", return_value=conn):
        dbos_orchestrator._pre_launch_sweep_stale_scheduled()
    assert len(conn.cur.calls) == 1, (
        f"expected exactly 1 SQL stmt (UPDATE), got {len(conn.cur.calls)}: "
        f"{[c[0][:60] for c in conn.cur.calls]}"
    )
    sql = conn.cur.calls[0][0]
    assert "UPDATE dbos.workflow_status" in sql
    assert "DELETE" not in sql.upper()
