"""issue_lifecycle steps must hit the SQLAlchemy engine with the right SQL/params
(no more raw psycopg). We patch the engine helpers + capture calls."""

from __future__ import annotations

from unittest.mock import patch

import pytest


async def test_atomic_checkout_updates_with_lock_guard():
    import app.workflows.issue_lifecycle as il

    captured = {}

    async def fake_execute(sql, params=None):
        captured["sql"] = sql
        captured["params"] = params
        return 1

    with patch("app.db.engine.execute", fake_execute):
        locked = await il.atomic_checkout(42, "wf-1")

    assert locked is True
    assert "UPDATE public.issues" in captured["sql"]
    assert "execution_locked_at IS NULL" in captured["sql"]
    assert captured["params"] == {"wid": "wf-1", "id": 42}


async def test_atomic_checkout_returns_false_when_no_row():
    import app.workflows.issue_lifecycle as il

    async def fake_execute(sql, params=None):
        return 0

    with patch("app.db.engine.execute", fake_execute):
        assert await il.atomic_checkout(42, "wf-1") is False


async def test_set_status_in_progress_sets_started_at():
    import app.workflows.issue_lifecycle as il

    captured = {}

    async def fake_execute(sql, params=None):
        captured["sql"] = sql
        captured["params"] = params
        return 1

    with patch("app.db.engine.execute", fake_execute):
        await il.set_status(7, "in_progress")

    assert "status = :status" in captured["sql"]
    assert "started_at = :ts" in captured["sql"]
    assert captured["params"]["status"] == "in_progress"
    assert "ts" in captured["params"] and captured["params"]["id"] == 7


async def test_set_status_blocked_writes_jsonb_error_state():
    import app.workflows.issue_lifecycle as il

    captured = {}

    async def fake_execute(sql, params=None):
        captured["sql"] = sql
        captured["params"] = params
        return 1

    with patch("app.db.engine.execute", fake_execute):
        await il.set_status(7, "blocked", error_code="x", error_message="boom")

    assert "execution_state = CAST(:state AS jsonb)" in captured["sql"]
    assert '"error_code": "x"' in captured["params"]["state"]


async def test_load_issue_raises_when_missing():
    import app.workflows.issue_lifecycle as il

    async def fake_fetch_one(sql, params=None):
        return None

    with patch("app.db.engine.fetch_one", fake_fetch_one):
        with pytest.raises(RuntimeError, match="not found"):
            await il.load_issue(999)


async def test_set_status_done_sets_completed_at():
    import app.workflows.issue_lifecycle as il

    captured = {}

    async def fake_execute(sql, params=None):
        captured["sql"] = sql
        captured["params"] = params
        return 1

    with patch("app.db.engine.execute", fake_execute):
        await il.set_status(7, "done")

    assert "completed_at = :ts" in captured["sql"]
    assert captured["params"]["status"] == "done"


async def test_set_status_cancelled_sets_cancelled_at():
    import app.workflows.issue_lifecycle as il

    captured = {}

    async def fake_execute(sql, params=None):
        captured["sql"] = sql
        captured["params"] = params
        return 1

    with patch("app.db.engine.execute", fake_execute):
        await il.set_status(7, "cancelled")

    assert "cancelled_at = :ts" in captured["sql"]
    assert captured["params"]["status"] == "cancelled"


async def test_clear_lock_nullifies_execution_lock():
    import app.workflows.issue_lifecycle as il

    captured = {}

    async def fake_execute(sql, params=None):
        captured["sql"] = sql
        captured["params"] = params
        return 1

    with patch("app.db.engine.execute", fake_execute):
        await il.clear_lock(5)

    assert "execution_locked_at = NULL" in captured["sql"]
    assert captured["params"] == {"id": 5}


async def test_run_issue_agent_step_invokes_executor(monkeypatch):
    import app.workflows.issue_lifecycle as il

    ran = {}

    async def fake_run_issue_agent(*, issue, agent_id, user_id):
        ran["issue_id"] = issue["id"]
        ran["agent_id"] = agent_id
        ran["user_id"] = user_id
        return "essay output"

    monkeypatch.setattr(
        "app.services.issues.issue_agent_executor.run_issue_agent",
        fake_run_issue_agent,
    )
    out = await il.run_issue_agent_step(
        {"id": 409, "title": "t", "description": "d"}, "agent-uuid", "user-uuid"
    )
    assert ran == {"issue_id": 409, "agent_id": "agent-uuid", "user_id": "user-uuid"}
    assert out == "essay output"


async def test_load_issue_normalizes_datetime_and_uuid():
    import datetime as dt
    import uuid

    import app.workflows.issue_lifecycle as il

    owner = uuid.uuid4()

    async def fake_fetch_one(sql, params=None):
        return {
            "id": 7,
            "created_at": dt.datetime(2026, 5, 24, tzinfo=dt.timezone.utc),
            "assignee_user_id": owner,
            "title": "x",
        }

    with patch("app.db.engine.fetch_one", fake_fetch_one):
        out = await il.load_issue(7)

    assert out["id"] == 7  # bigint passes through
    assert isinstance(out["created_at"], str)  # datetime → isoformat
    assert out["assignee_user_id"] == str(owner)  # UUID → str
    assert out["title"] == "x"
