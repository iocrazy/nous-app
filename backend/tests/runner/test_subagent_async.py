"""``Skill(skill="task", await=false)`` — the background sub-agent.

Three typed rejections, and the shape of the queued task row a successful
background spawn leaves for the workforce tick to pick up.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.services.ai.runner.subagent_task_service import SubAgentTaskService

pytestmark = pytest.mark.unit


def _svc(**kw):
    base = dict(caller_agent_id=uuid4(), caller_user_id=uuid4(), parent_run_id="900")
    return SubAgentTaskService(**{**base, **kw})


async def _ok(value):
    return value


class _Rec:
    """Parent recorder stand-in: an issue-bound run, so a reply target exists."""

    run_id = 900
    issue_id = 7

    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    async def record_event(self, event_type, payload, **kw):
        self.events.append((event_type, payload))


async def test_background_spawn_without_a_reply_target_is_refused(monkeypatch):
    """A sub-agent's own sub-agent, or a probe run, has nowhere to deliver the
    result. Queueing it anyway would run a billed turn nobody ever reads."""
    svc = _svc()
    out = await svc.spawn(
        {"subagent_type": "librarian", "prompt": "go", "await": False}
    )
    assert out["status"] == "failed" and out["error"] == "no_reply_target"


async def test_a_sub_agent_cannot_spawn_a_background_sub_agent(monkeypatch):
    svc = _svc(agent_depth=1, parent_recorder=_Rec())
    out = await svc.spawn(
        {"subagent_type": "librarian", "prompt": "go", "await": False}
    )
    assert (
        out["status"] == "failed" and out["error"] == "async_not_allowed_for_subagent"
    )


async def test_continue_inside_a_fan_out_is_refused(monkeypatch):
    """``tasks`` starts N fresh children; ``child_run_id`` names exactly one
    existing child. Together they have no meaning — say so instead of
    silently continuing the same run N times."""
    svc = _svc(parent_recorder=_Rec())
    out = await svc.spawn(
        {"tasks": [{"subagent_type": "a", "prompt": "b"}], "child_run_id": "5"}
    )
    assert (
        out["status"] == "failed" and out["error"] == "continue_not_allowed_in_fanout"
    )


async def test_async_creates_a_queued_task_row_and_emits_spawned(monkeypatch):
    created: dict = {}

    class _Repo:
        @staticmethod
        async def create_task(*, agent_id, user_id, payload, title=None, **kw):
            created.update(payload=payload, title=title, agent_id=agent_id)
            return {"id": "task-1"}

    import app.repositories.agent_workforce_repository as wf_mod

    monkeypatch.setattr(wf_mod, "get_agent_workforce_repository", lambda: _Repo())

    rec = _Rec()
    svc = _svc(parent_recorder=rec)
    target_id = uuid4()
    monkeypatch.setattr(svc, "_resolve_agent_id", lambda slug: _ok(target_id))

    out = await svc.spawn(
        {
            "subagent_type": "librarian",
            "prompt": "dig",
            "description": "d",
            "await": False,
        }
    )
    assert out["status"] == "queued"
    assert out["task_id"] == "task-1" and out["sub_run_id"] is None
    p = created["payload"]
    assert p["kind"] == "subagent"
    assert p["reply_to"] == {"target_kind": "issue", "target_id": 7}
    assert p["parent_run_id"] == "900"
    assert p["child_run_id"] is None and p["agent_depth"] == 0
    assert p["caller_agent_id"] == str(svc.caller_agent_id)
    assert rec.events[0][0] == "subagent_spawned"
    assert rec.events[0][1]["mode"] == "async"
    assert rec.events[0][1]["child_run_id"] is None
    assert rec.events[0][1]["task_id"] == "task-1"


async def test_unknown_slug_is_refused_before_the_task_row(monkeypatch):
    import app.repositories.agent_workforce_repository as wf_mod

    class _Repo:
        @staticmethod
        async def create_task(**kw):  # pragma: no cover — must not be reached
            raise AssertionError("a row was created for an unknown slug")

    monkeypatch.setattr(wf_mod, "get_agent_workforce_repository", lambda: _Repo())
    svc = _svc(parent_recorder=_Rec())
    monkeypatch.setattr(svc, "_resolve_agent_id", lambda slug: _ok(None))
    out = await svc.spawn({"subagent_type": "nope", "prompt": "x", "await": False})
    assert out["status"] == "failed" and "unknown agent slug" in out["error"]


async def test_a_failed_task_insert_is_typed_not_silent(monkeypatch):
    import app.repositories.agent_workforce_repository as wf_mod

    class _Repo:
        @staticmethod
        async def create_task(**kw):
            return None

    monkeypatch.setattr(wf_mod, "get_agent_workforce_repository", lambda: _Repo())
    svc = _svc(parent_recorder=_Rec())
    monkeypatch.setattr(svc, "_resolve_agent_id", lambda slug: _ok(uuid4()))
    out = await svc.spawn({"subagent_type": "librarian", "prompt": "x", "await": False})
    assert out["status"] == "failed" and out["error"] == "task_create_failed"


async def test_conversation_run_falls_back_to_the_conversation_target(monkeypatch):
    """No issue, but a session: the result still has somewhere to land."""

    class _ConvRec:
        run_id = 900
        issue_id = None
        conversation_id = 42

        async def record_event(self, *a, **kw):
            return None

    created: dict = {}

    class _Repo:
        @staticmethod
        async def create_task(*, agent_id, user_id, payload, title=None, **kw):
            created.update(payload=payload)
            return {"id": "task-2"}

    import app.repositories.agent_workforce_repository as wf_mod

    monkeypatch.setattr(wf_mod, "get_agent_workforce_repository", lambda: _Repo())
    svc = _svc(parent_recorder=_ConvRec())
    monkeypatch.setattr(svc, "_resolve_agent_id", lambda slug: _ok(uuid4()))
    out = await svc.spawn({"subagent_type": "librarian", "prompt": "x", "await": False})
    assert out["status"] == "queued"
    assert created["payload"]["reply_to"] == {
        "target_kind": "conversation",
        "target_id": 42,
    }
