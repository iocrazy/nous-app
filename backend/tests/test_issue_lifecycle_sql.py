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

    with patch("app.db.engine.execute_as_service_role", fake_execute):
        locked = await il.atomic_checkout(42, "wf-1")

    assert locked is True
    assert "UPDATE public.issues" in captured["sql"]
    assert "execution_locked_at IS NULL" in captured["sql"]
    assert captured["params"] == {"wid": "wf-1", "id": 42}


async def test_atomic_checkout_returns_false_when_no_row():
    import app.workflows.issue_lifecycle as il

    async def fake_execute(sql, params=None):
        return 0

    with patch("app.db.engine.execute_as_service_role", fake_execute):
        assert await il.atomic_checkout(42, "wf-1") is False


async def test_set_status_in_progress_sets_started_at():
    import app.workflows.issue_lifecycle as il

    captured = {}

    async def fake_execute(sql, params=None):
        captured["sql"] = sql
        captured["params"] = params
        return 1

    with patch("app.db.engine.execute_as_service_role", fake_execute):
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

    with patch("app.db.engine.execute_as_service_role", fake_execute):
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

    with patch("app.db.engine.execute_as_service_role", fake_execute):
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

    with patch("app.db.engine.execute_as_service_role", fake_execute):
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

    with patch("app.db.engine.execute_as_service_role", fake_execute):
        await il.clear_lock(5)

    assert "execution_locked_at = NULL" in captured["sql"]
    assert captured["params"] == {"id": 5}


async def test_run_issue_agent_step_invokes_executor(monkeypatch):
    import app.workflows.issue_lifecycle as il

    ran = {}

    async def fake_run_issue_agent(
        *, issue, agent_id, user_id, is_continuation=False, auto=False
    ):
        ran["issue_id"] = issue["id"]
        ran["agent_id"] = agent_id
        ran["user_id"] = user_id
        ran["is_continuation"] = is_continuation
        ran["auto"] = auto
        return {"content": "essay output", "outcome": "completed", "reason": "done"}

    monkeypatch.setattr(
        "app.services.issues.issue_agent_executor.run_issue_agent",
        fake_run_issue_agent,
    )
    out = await il.run_issue_agent_step(
        {"id": 409, "title": "t", "description": "d"}, "agent-uuid", "user-uuid"
    )
    assert ran == {
        "issue_id": 409,
        "agent_id": "agent-uuid",
        "user_id": "user-uuid",
        "is_continuation": False,
        # M4 Autopilot (task O2): default auto=False when the workflow-level
        # kwarg isn't threaded in (a plain manual-dispatch call).
        "auto": False,
    }
    assert out["content"] == "essay output"
    assert out["outcome"] == "completed"


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


# ── set_status → project_stage node projection (probe follow-up) ─────────────


async def test_set_status_in_review_projects_onto_stage_node():
    """Regression (live E2E probe, 2026-07-31): ``set_status`` writes
    ``public.issues`` with raw SQL, bypassing ``transition_status`` where the
    issue→node projection lives. An agent self-completing to ``in_review``
    left its stage node on ``in_progress``, so the Stage Board showed work
    still running that was actually awaiting a manager's review."""
    import app.workflows.issue_lifecycle as il

    fired = {}

    async def fake_execute(sql, params=None):
        return 1

    async def fake_fetch_one(sql, params=None):
        assert "origin_kind" in sql
        return {"id": 7, "origin_kind": "project_stage", "origin_id": "ps:1:2"}

    async def fake_sync(issue, new_status, *, enqueue_autopilot=True):
        fired["issue"] = issue
        fired["status"] = new_status
        fired["enqueue_autopilot"] = enqueue_autopilot

    with (
        patch("app.db.engine.execute_as_service_role", fake_execute),
        patch("app.db.engine.fetch_one", fake_fetch_one),
        patch("app.repositories.issue_repository.fire_stage_node_sync", fake_sync),
    ):
        await il.set_status(7, "in_review")

    assert fired["status"] == "in_review"
    assert fired["issue"]["origin_kind"] == "project_stage"
    # Suppressed on purpose: set_status runs inside a @DBOS.step, where
    # starting a workflow raises a bare AssertionError.
    assert fired["enqueue_autopilot"] is False


async def test_set_status_skips_projection_for_unmapped_status():
    """``blocked`` has no node counterpart — don't even fetch the row."""
    import app.workflows.issue_lifecycle as il

    calls = []

    async def fake_execute(sql, params=None):
        return 1

    async def fake_fetch_one(sql, params=None):
        calls.append(sql)
        return None

    with (
        patch("app.db.engine.execute_as_service_role", fake_execute),
        patch("app.db.engine.fetch_one", fake_fetch_one),
    ):
        await il.set_status(7, "blocked", error_code="x", error_message="boom")

    assert calls == []


async def test_set_status_survives_a_failing_stage_node_projection():
    """The projection is enrichment — a failure must never abort the status
    write the workflow depends on."""
    import app.workflows.issue_lifecycle as il

    async def fake_execute(sql, params=None):
        return 1

    async def fake_fetch_one(sql, params=None):
        return {"id": 7, "origin_kind": "project_stage", "origin_id": "ps:1:2"}

    async def boom(issue, new_status, *, enqueue_autopilot=True):
        raise RuntimeError("node repo down")

    with (
        patch("app.db.engine.execute_as_service_role", fake_execute),
        patch("app.db.engine.fetch_one", fake_fetch_one),
        patch("app.repositories.issue_repository.fire_stage_node_sync", boom),
    ):
        await il.set_status(7, "in_review")  # must not raise
