"""Spec-2 hygiene: wire the dead liveness continuation_attempt scaffolding.

_recover_from_stuck must atomically flip stuck→running AND bump
continuation_attempt, so a run that keeps flapping stuck→running burns through
MAX_CONTINUATIONS and is finally judged dead (closes the flap-forever hole).
"""

from __future__ import annotations

import pytest

from app.workflows import liveness_scanner as ls


@pytest.mark.asyncio
async def test_recover_from_stuck_bumps_continuation_and_cas_guards(monkeypatch):
    captured = {}

    class _FakeEngine:
        async def execute(self, sql, params):
            captured["sql"] = sql
            captured["params"] = params
            return 1

    monkeypatch.setattr("app.db.engine", _FakeEngine(), raising=False)
    # _recover_from_stuck imports `from app.db import engine as db_engine`
    import app.db as _appdb

    monkeypatch.setattr(_appdb, "engine", _FakeEngine(), raising=False)

    await ls._recover_from_stuck(42)

    sql = captured["sql"]
    # increments the counter
    assert "continuation_attempt = continuation_attempt + 1" in sql
    # flips to running
    assert "liveness_state = 'running'" in sql
    # CAS-guards on the stuck precondition (idempotent under concurrent scans)
    assert "liveness_state = 'stuck'" in sql
    assert captured["params"] == {"id": 42}


def test_should_auto_continue_is_gone():
    # Dead helper removed; importing it must fail (no lingering dead export).
    with pytest.raises(ImportError):
        from app.agent_framework.output_budget import (  # noqa: F401
            should_auto_continue,
        )
