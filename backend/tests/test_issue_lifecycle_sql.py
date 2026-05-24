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
