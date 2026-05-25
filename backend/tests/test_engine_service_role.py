"""execute_as_service_role must SET LOCAL ROLE service_role inside the txn,
before the write — the fix for the issues_update_allowlist trigger (mig 170)
that #340 broke by dropping the raw-psycopg `SET ROLE service_role`."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_execute_as_service_role_sets_role_before_sql(monkeypatch):
    import app.db.engine as e

    calls: list[str] = []

    class FakeResult:
        rowcount = 3

    class FakeConn:
        async def execute(self, stmt, params=None):
            calls.append(str(stmt))
            return FakeResult()

    class FakeBegin:
        async def __aenter__(self):
            return FakeConn()

        async def __aexit__(self, *a):
            return False

    class FakeEngine:
        def begin(self):
            return FakeBegin()

    monkeypatch.setattr(e, "get_engine", lambda: FakeEngine())

    n = await e.execute_as_service_role(
        "UPDATE public.issues SET execution_locked_at = now() WHERE id = :id",
        {"id": 7},
    )

    assert n == 3
    # SET LOCAL ROLE service_role must run FIRST, in the same txn, before the write.
    assert calls[0] == "SET LOCAL ROLE service_role"
    assert "UPDATE public.issues" in calls[1]
