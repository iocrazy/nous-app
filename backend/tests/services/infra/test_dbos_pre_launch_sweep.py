"""Verify _pre_launch_sweep_stale_scheduled cutoff default + env override."""

from __future__ import annotations

import os
from unittest.mock import patch

from app.services.infra import dbos_orchestrator


def test_default_cutoff_is_three_minutes(monkeypatch):
    """Default cutoff must be 3 minutes (was 30 — caused recovery storms)."""
    monkeypatch.delenv("DBOS_STALE_SCHED_CUTOFF_MINUTES", raising=False)
    monkeypatch.setenv("DBOS_DATABASE_URL", "")  # short-circuit: sweep returns
    # Capture the cutoff value used by the SQL by spying on psycopg.
    with patch("psycopg.connect") as conn_mock:
        dbos_orchestrator._pre_launch_sweep_stale_scheduled()
        # DBOS_DATABASE_URL is empty so psycopg.connect must NOT be called.
        assert conn_mock.call_count == 0


def test_env_override_respected(monkeypatch):
    """DBOS_STALE_SCHED_CUTOFF_MINUTES env var overrides default."""
    monkeypatch.setenv("DBOS_STALE_SCHED_CUTOFF_MINUTES", "7")
    monkeypatch.setenv("DBOS_DATABASE_URL", "postgresql://stub/none")
    captured: dict[str, object] = {}

    class _FakeCur:
        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        def execute(self, sql, params):
            captured["params"] = params

        def fetchall(self):
            return []

    class _FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        def cursor(self):
            return _FakeCur()

        def commit(self):
            pass

    with patch("psycopg.connect", return_value=_FakeConn()):
        dbos_orchestrator._pre_launch_sweep_stale_scheduled()

    assert captured["params"] == (7,)
