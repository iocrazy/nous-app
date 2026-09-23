"""GenerateVideo is submit-only: it files a task, starts ``agent_video_workflow``
and returns at once. The result reaches the model later as an
``agent_run_inbox`` item of kind ``media_result`` (see
``app/workflows/agent_video.py``).

Why: a local-daemon video job may take ~27 min
(``local_dispatch.DREAMINA_DISPATCH_TIMEOUT_S``) while a tool call has a 600s
budget, so waiting in the turn abandoned a job the user's machine was still
running. The tool now never waits on the generation.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.services.media.parsers.video_providers.db_registry import (
    LocalVideoRoute,
    ServerVideoRoute,
)

_USER = "11111111-1111-1111-1111-111111111111"
_ROUTE_TARGET = (
    "app.services.media.parsers.video_providers.db_registry.resolve_video_route"
)
_LOCAL = LocalVideoRoute(
    engine="dreamina", engine_model="seedance2.0", row_name="jimeng-local-video"
)
_SERVER = ServerVideoRoute(
    provider=object(), actual_model="seedance", row_name="jimeng-cli-seedance"
)


def _ctx(**over):
    base = {
        "run_id": 4242,
        "user_id": _USER,
        "team_id": 7,
        "agent_id": "ag-1",
        "turn": 1,
        "step": 3,
        "conversation_id": 9001,
        "issue_id": None,
    }
    base.update(over)
    return base


_ARGS = {"prompt": "pan left", "source_image_url": "/api/v1/generated-media/5/cover"}


class _Mgr:
    def __init__(self, active=None):
        self.created: list[dict] = []
        self.active = active

    async def create(self, **kwargs):
        self.created.append(kwargs)
        return kwargs["dbos_workflow_id"]

    async def find_active_by_dedup_key(self, user_id, task_type, dedup_key):
        self.lookup = (user_id, task_type, dedup_key)
        return self.active


@pytest.fixture
def wired(monkeypatch):
    """Route + task manager + dispatch seams, all observable."""
    import app.services.ai.tools.generate_media_tools as gmt

    state: dict = {"route": _LOCAL, "online": True, "deferred": False}
    mgr = _Mgr()
    state["mgr"] = mgr
    dispatches: list[dict] = []
    state["dispatches"] = dispatches

    async def _route(name, *, user_id=None):
        state["route_call"] = (name, user_id)
        r = state["route"]
        if isinstance(r, Exception):
            raise r
        return r

    async def _start(task_type, **kwargs):
        dispatches.append({"task_type": task_type, **kwargs})
        out = {"mode": "dbos", "dbos_workflow_id": kwargs["workflow_id"]}
        if state["deferred"]:
            out["deferred"] = True
        return out

    async def _online(user_id):
        return state["online"]

    monkeypatch.setattr(
        "app.services.media.parsers.video_providers.db_registry.resolve_video_route",
        _route,
    )
    monkeypatch.setattr(
        "app.services.infra.dbos_orchestrator.start_workflow_routed", _start
    )
    monkeypatch.setattr(
        "app.services.infra.unified_task_manager.get_task_manager", lambda: mgr
    )
    monkeypatch.setattr(gmt, "_daemon_online", _online)
    monkeypatch.setenv("GENMEDIA_DEFAULT_VIDEO_PROVIDER", "jimeng-local-video")
    monkeypatch.setenv("GENMEDIA_DEFAULT_VIDEO_MODEL", "")
    return state


@pytest.mark.asyncio
async def test_chat_turn_submits_and_returns_at_once(wired):
    from app.services.ai.tools.generate_media_tools import GenerateMediaTools
    from app.workflows.agent_video import agent_video_workflow

    out = await GenerateMediaTools().generate_video(dict(_ARGS), _ctx())

    assert out["ok"] is True
    assert out["status"] == "submitted"
    task_id = out["task_id"]
    assert "inbox message" in out["note"]
    assert "do not poll or re-submit" in out["note"]
    assert "user's machine" in out["note"]
    # Model-safe: nothing but these keys.
    assert set(out) <= {"ok", "status", "task_id", "note", "deferred"}
    assert "deferred" not in out

    (created,) = wired["mgr"].created
    assert created["task_type"] == "agent_video"
    assert len(created["task_type"]) <= 20  # task_tracking.task_type VARCHAR(20)
    assert created["dbos_workflow_id"] == task_id
    meta = created["metadata"]
    assert meta["trigger"] == "agent_tool"
    assert meta["run_id"] == 4242
    assert meta["reply_to"] == {"target_kind": "conversation", "target_id": 9001}
    assert created["dedup_key"]

    (d,) = wired["dispatches"]
    assert d["task_type"] == "agent_video"
    assert d["dbos_workflow_callable"] is agent_video_workflow
    assert d["workflow_id"] == task_id
    assert d["task_id"] == task_id
    kw = d["dbos_workflow_kwargs"]
    assert kw["prompt"] == "pan left"
    assert kw["source_image_url"] == "/api/v1/generated-media/5/cover"
    assert kw["provider"] == "jimeng-local-video"
    assert kw["user_id"] == _USER
    assert (kw["run_id"], kw["turn"], kw["step"]) == (4242, 1, 3)
    assert kw["agent_id"] == "ag-1"
    assert kw["conversation_id"] == 9001
    assert (kw["reply_to_kind"], kw["reply_to_id"]) == ("conversation", 9001)
    assert wired["route_call"] == ("jimeng-local-video", _USER)


@pytest.mark.asyncio
async def test_issue_turn_prefers_the_issue_and_reports_the_deferral(wired):
    from app.services.ai.tools.generate_media_tools import GenerateMediaTools

    wired["deferred"] = True
    out = await GenerateMediaTools().generate_video(
        dict(_ARGS), _ctx(issue_id=77, conversation_id=9001)
    )

    assert out["ok"] is True and out["deferred"] is True
    kw = wired["dispatches"][0]["dbos_workflow_kwargs"]
    assert (kw["reply_to_kind"], kw["reply_to_id"]) == ("issue", 77)
    meta = wired["mgr"].created[0]["metadata"]
    assert meta["reply_to"] == {"target_kind": "issue", "target_id": 77}


@pytest.mark.asyncio
async def test_server_route_note_does_not_claim_the_users_machine(wired):
    from app.services.ai.tools.generate_media_tools import GenerateMediaTools

    wired["route"] = _SERVER
    out = await GenerateMediaTools().generate_video(dict(_ARGS), _ctx())

    assert out["ok"] is True
    assert "user's machine" not in out["note"]
    assert "inbox message" in out["note"]


@pytest.mark.asyncio
async def test_no_reply_target_is_refused_before_anything_is_filed(wired):
    from app.services.ai.tools.generate_media_tools import GenerateMediaTools

    out = await GenerateMediaTools().generate_video(
        dict(_ARGS), _ctx(issue_id=None, conversation_id=None)
    )

    assert out["ok"] is False
    assert out["error_code"] == "no_reply_target"
    assert wired["mgr"].created == []
    assert wired["dispatches"] == []


@pytest.mark.asyncio
async def test_duplicate_pending_job_is_refused(wired):
    from app.services.ai.tools.generate_media_tools import GenerateMediaTools

    wired["mgr"].active = "existing-task"
    out = await GenerateMediaTools().generate_video(dict(_ARGS), _ctx())

    assert out["ok"] is False
    assert out["error_code"] == "already_generating"
    assert "existing-task" in out["error"]
    assert wired["mgr"].created == []
    assert wired["dispatches"] == []
    user, task_type, key = wired["mgr"].lookup
    assert (user, task_type) == (_USER, "agent_video")


@pytest.mark.asyncio
async def test_dedup_key_is_per_target_and_per_request(wired):
    from app.services.ai.tools.generate_media_tools import GenerateMediaTools

    tools = GenerateMediaTools()
    await tools.generate_video(dict(_ARGS), _ctx())
    await tools.generate_video({**_ARGS, "prompt": "pan right"}, _ctx())
    await tools.generate_video(dict(_ARGS), _ctx(conversation_id=9002))
    keys = [c["dedup_key"] for c in wired["mgr"].created]
    assert len(set(keys)) == 3


@pytest.mark.asyncio
async def test_unresolvable_route_is_a_typed_refusal(wired):
    from app.services.ai.tools.generate_media_tools import GenerateMediaTools

    wired["route"] = RuntimeError("no video model configured in nous_models catalog")
    out = await GenerateMediaTools().generate_video(dict(_ARGS), _ctx())

    assert out["ok"] is False
    assert out["error_code"] == "no_video_model"
    assert wired["dispatches"] == []


@pytest.mark.asyncio
async def test_offline_daemon_on_a_local_row_fails_fast(wired):
    from app.services.ai.tools.generate_media_tools import GenerateMediaTools

    wired["online"] = False
    out = await GenerateMediaTools().generate_video(dict(_ARGS), _ctx())

    assert out["ok"] is False
    assert out["error_code"] == "daemon_offline"
    assert wired["dispatches"] == []


@pytest.mark.asyncio
async def test_missing_prompt_or_source_is_refused(wired):
    from app.services.ai.tools.generate_media_tools import GenerateMediaTools

    out = await GenerateMediaTools().generate_video({"prompt": "x"}, _ctx())
    assert out["error_code"] == "prompt_required"
    assert wired["dispatches"] == []


@pytest.mark.asyncio
async def test_dispatch_failure_is_typed(wired, monkeypatch):
    from app.services.ai.tools.generate_media_tools import GenerateMediaTools

    monkeypatch.setattr(
        "app.services.infra.dbos_orchestrator.start_workflow_routed",
        AsyncMock(side_effect=RuntimeError("DBOS orchestrator is not enabled")),
    )
    out = await GenerateMediaTools().generate_video(dict(_ARGS), _ctx())

    assert out["ok"] is False
    assert out["error_code"] == "dispatch_failed"


def test_video_tool_spec_says_it_is_asynchronous():
    from app.services.ai.tools.generate_media_specs import generate_video_tool_spec

    desc = generate_video_tool_spec()["function"]["description"]
    assert "asynchronous" in desc.lower()
    assert "inbox" in desc.lower()


def test_media_run_context_carries_the_reply_targets():
    """The tool picks issue-then-conversation, so the runner has to hand it
    both — the recorder is where they live."""
    from types import SimpleNamespace

    from app.services.ai.runner.agent_runner import AgentRunner

    rec = SimpleNamespace(
        run_id=1, user_id="u", team_id=None, issue_id=77, conversation_id=9001
    )
    composed = SimpleNamespace(agent_id="a")
    ctx = AgentRunner._media_run_context(None, rec, composed, step=2)  # type: ignore[arg-type]
    assert (ctx["issue_id"], ctx["conversation_id"]) == (77, 9001)
