"""W3c budget breaker in the autopilot scheduler.

Over budget → _fire_agent_routine skips the fire (returns None), bumps
skipped_count, and never creates/dispatches an issue. DB + budget lookup faked.
"""

from __future__ import annotations

import pytest

from app.workflows import scheduled_master as sm


def _row():
    return {
        "id": 900555,
        "user_id": "11111111-1111-1111-1111-111111111111",
        "name": "Daily digest",
        "payload": {
            "agent_slug": "script_ai",
            "prompt_md": "Summarize today",
            "delivery_policy": "skip_if_active",
        },
    }


@pytest.mark.asyncio
async def test_over_budget_skips_and_bumps_skipped_count(monkeypatch):
    executed: list = []

    async def fake_fetch_val(sql, params=None):
        # the owner→personal-team resolution
        return 900123  # resolved personal team

    async def fake_execute(sql, params=None):
        executed.append((sql, params))
        return 1

    async def over_budget(team_id):
        assert team_id == 900123
        return True

    # atomic_create must NOT be reached when over budget.
    called = {"created": False}

    class _IssueRepo:
        async def atomic_create(self, body):
            called["created"] = True
            return {"id": 1}

    monkeypatch.setattr("app.db.engine.fetch_val", fake_fetch_val)
    monkeypatch.setattr("app.db.engine.execute", fake_execute)
    monkeypatch.setattr("app.services.ai_usage.is_team_over_budget", over_budget)
    import app.repositories.issue_repository as ir

    monkeypatch.setattr(ir, "issue_repository", _IssueRepo())

    result = await sm._fire_agent_routine(_row())

    assert result is None  # skipped, no dispatch order
    assert called["created"] is False  # no paid issue created
    assert len(executed) == 1
    assert "skipped_count = skipped_count + 1" in executed[0][0]
    assert executed[0][1]["id"] == 900555


@pytest.mark.asyncio
async def test_under_budget_passes_gate(monkeypatch):
    """Not over budget → the gate is a no-op; execution proceeds past it (proven
    by reaching the agent lookup, which we stub to a not-found RuntimeError)."""

    async def fake_fetch_val(sql, params=None):
        return 900123

    async def under_budget(team_id):
        return False

    # First fetch_one after the gate is the delivery-gate check; then the agent
    # lookup. Return None agent → RuntimeError proves we passed the budget gate.
    calls = {"n": 0}

    async def fake_fetch_one(sql, params=None):
        calls["n"] += 1
        return None

    monkeypatch.setattr("app.db.engine.fetch_val", fake_fetch_val)
    monkeypatch.setattr("app.db.engine.fetch_one", fake_fetch_one)
    monkeypatch.setattr("app.services.ai_usage.is_team_over_budget", under_budget)

    with pytest.raises(RuntimeError, match="not found"):
        await sm._fire_agent_routine(_row())
    assert calls["n"] >= 1  # advanced past the budget gate
