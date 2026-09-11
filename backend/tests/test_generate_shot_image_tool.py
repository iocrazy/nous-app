"""Adversarial tests for GenerateShotImage (A6 — screenwriting agent layer).

GenerateShotImage dispatches the pre-existing ``script_shot_generate`` DBOS
workflow (unchanged by A6) for one shot. It sits astride two things this
suite tests SEPARATELY, same discipline as ``test_screenwriting_tools.py``:

  1. The A1 capability gate (``HighRiskCapabilityGateHook``) — media.image
     grant, the per-turn cap, and the install-wide kill switch. Runs in the
     PreToolUse chain, BEFORE the handler is ever entered.
  2. The A2 resolver (``resolve_shot``) inside the handler — a model-supplied
     shot_id must be authorized against THIS run's scope before the workflow
     is dispatched, never passed to it raw.

Every test that claims "no dispatch happened" asserts against a MOCKED
``start_workflow_routed`` (patched at its import source,
``app.services.infra.dbos_orchestrator.start_workflow_routed`` — the handler
imports it lazily inside the function body, so patching the module attribute
is what the fresh ``from ... import ...`` picks up on each call) — never just
that the tool returned an error dict, which could pass even if the workflow
had actually been kicked off.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

import app.services.ai.scope.scope_resolver as resolver_mod
import app.services.ai.scope.scoped_script_gateway as gateway_mod
import app.services.ai.tools.screenwriting_tools as tools_mod
from app.services.ai.scope.agent_run_scope import AgentRunScope
from app.services.ai.tools.screenwriting_tools import SCREENWRITING_HANDLERS
from app.services.infra.hooks import HookContext, HookRegistry
from app.services.infra.hooks.high_risk_capability_gate import (
    TOOL_REQUIREMENTS,
    HighRiskCapabilityGateHook,
)

_RUN_ID = "800100000000000009"
_USER_ID = "22222222-2222-2222-2222-222222222222"
_PROJECT_A = 900100000000000001
_PROJECT_B = 900100000000000002  # a DIFFERENT project — cross-tenant target
_TEAM_A = 900100000000000003
_SCRIPT_ID = 700100000000000004
_SCENE_ID = 700100000000000001
_SHOT_ID = 700100000000000002

_RUN_CONTEXT = {
    "run_id": _RUN_ID,
    "user_id": _USER_ID,
    "team_id": _TEAM_A,
    "agent_id": "00000000-0000-0000-0000-000000000002",
}


def _scope(project_id=_PROJECT_A, team_id=_TEAM_A, episode_id=None) -> AgentRunScope:
    return AgentRunScope(
        run_id=_RUN_ID,
        user_id=_USER_ID,
        project_id=project_id,
        team_id=team_id,
        episode_id=episode_id,
    )


def _agent(**caps) -> dict:
    return {"capability_profile": {"capabilities": dict(caps)}}


def _hook_ctx(tool_name: str = "GenerateShotImage") -> HookContext:
    return HookContext(
        run_id=_RUN_ID,
        agent_id=UUID("00000000-0000-0000-0000-000000000002"),
        agent_slug="storyboard",
        user_id=UUID(_USER_ID),
        session_id=None,
        tool_name=tool_name,
        tool_args={},
        accumulated_prompt_tokens=0,
        accumulated_completion_tokens=0,
        accumulated_cost_cents=0.0,
        iteration=1,
    )


def _shot_row(project_id, team_id=_TEAM_A, episode_id=None, status="empty"):
    return SimpleNamespace(
        id=_SHOT_ID,
        scene_id=_SCENE_ID,
        shot_number=1,
        shot_type="MS",
        status=status,
        project_id=project_id,
        team_id=team_id,
        episode_id=episode_id,
    )


class _FakeResult:
    def __init__(self, *, first_row=None):
        self._first_row = first_row

    def first(self):
        return self._first_row


class _CaptureSession:
    def __init__(self, results=None):
        self.statements: list = []
        self._results = list(results or [])

    async def execute(self, stmt):
        self.statements.append(stmt)
        return self._results.pop(0) if self._results else _FakeResult()


class _ScopeCtx:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc):
        return False


def _audit_inserts(session) -> list:
    return [s for s in session.statements if s.__class__.__name__ == "Insert"]


def _task_manager_mock(task_id: str = "task-uuid-1") -> MagicMock:
    mgr = MagicMock()
    mgr.create = AsyncMock(return_value=task_id)
    return mgr


# ====================================================================== #
# Registration — GenerateShotImage must actually be wired into all three
# lockstep collections (spec / gate requirement / dispatch name), same
# invariant test_screenwriting_tools.py's
# test_every_advertised_tool_is_dispatchable checks generically.
# ====================================================================== #


def test_generate_shot_image_is_registered_with_the_gate():
    assert "GenerateShotImage" in TOOL_REQUIREMENTS
    req = TOOL_REQUIREMENTS["GenerateShotImage"]
    assert req.media == "image"
    # Graded on the media axis, NOT the write-level ladder — a "write" grant
    # is neither necessary nor sufficient by itself; media.image is.
    assert req.write_level is None


def test_generate_shot_image_is_dispatchable():
    from app.services.ai.runner.agent_runner import SCREENWRITING_TOOL_NAMES

    assert "GenerateShotImage" in SCREENWRITING_TOOL_NAMES
    assert "GenerateShotImage" in SCREENWRITING_HANDLERS


# ====================================================================== #
# 1. Ungranted agent → gate refuses, no workflow dispatched.
# ====================================================================== #


@pytest.mark.asyncio
async def test_ungranted_agent_is_denied_at_the_gate():
    hook = HighRiskCapabilityGateHook(agent=None)
    result = await hook(_hook_ctx())
    assert result.decision == "abort"
    assert "not granted" in result.abort_reason


@pytest.mark.asyncio
async def test_ungranted_agent_never_reaches_dispatch_end_to_end():
    """Drives the REAL interaction between the PreToolUse chain and the
    tool dispatcher (mirrors how run_turn/stream_turn actually gates a call:
    the hook is evaluated BEFORE the tool-name switch ever reaches the
    handler) — not just the hook in isolation."""
    from unittest.mock import MagicMock as _MM

    from app.services.ai.runner.agent_runner import AgentRunner

    hooks = HookRegistry()
    hooks.register_pre(
        HighRiskCapabilityGateHook(agent=None),  # no capability_profile at all
        name="high_risk_capability_gate",
        priority=26,
        fail_closed=True,
    )
    runner = AgentRunner(adapter=_MM(), skill_tool=_MM(), hooks=hooks)
    composed = _MM(agent_id=UUID("00000000-0000-0000-0000-000000000002"))
    recorder = _MM(run_id=_RUN_ID, user_id=_USER_ID, team_id=_TEAM_A)
    # Task 5 (capability_denied transcript event): _run_pre_hooks now awaits
    # recorder.record_event() on the real deny path this test drives, so the
    # recorder needs an awaitable — a plain MagicMock attribute isn't.
    recorder.record_event = AsyncMock()

    with patch(
        "app.services.infra.dbos_orchestrator.start_workflow_routed",
        new=AsyncMock(),
    ) as mock_dispatch:
        pre_result = await runner._run_pre_hooks(
            composed=composed,
            recorder=recorder,
            tool_name="GenerateShotImage",
            args={"shot_id": str(_SHOT_ID)},
            iteration=1,
        )
        # Production control flow (run_turn / stream_turn): a terminal
        # abort/await_approval decision short-circuits BEFORE the tool-name
        # switch reaches _dispatch_screenwriting at all.
        if pre_result is None or pre_result.decision == "continue":
            await runner._dispatch_screenwriting(
                "GenerateShotImage", {"shot_id": str(_SHOT_ID)}, recorder, composed
            )

    assert pre_result is not None
    assert pre_result.decision == "abort"
    assert pre_result.abort_code == "capability_denied"
    recorder.record_event.assert_awaited_once()
    event_type, payload = recorder.record_event.await_args.args
    assert event_type == "capability_denied"
    assert payload["tool"] == "GenerateShotImage"
    mock_dispatch.assert_not_called()


# ====================================================================== #
# 2. Granted agent, shot outside the run's scope → resolver denies,
#    audited, no dispatch.
# ====================================================================== #


@pytest.mark.asyncio
async def test_shot_outside_scope_is_denied_audited_and_never_dispatched():
    from app.core.config import settings

    session = _CaptureSession([_FakeResult(first_row=_shot_row(_PROJECT_B))])

    with (
        patch.object(settings, "FEATURE_SHOT_GENERATE", True),
        patch.multiple(
            resolver_mod,
            read_scope=lambda: _ScopeCtx(session),
            write_scope=lambda: _ScopeCtx(session),
        ),
        patch.object(tools_mod, "scope_for_run", AsyncMock(return_value=_scope())),
        patch(
            "app.services.infra.dbos_orchestrator.start_workflow_routed",
            new=AsyncMock(),
        ) as mock_dispatch,
        patch(
            "app.services.infra.unified_task_manager.get_task_manager",
        ) as mock_get_mgr,
    ):
        result = await SCREENWRITING_HANDLERS["GenerateShotImage"](
            {"shot_id": str(_SHOT_ID)}, _RUN_CONTEXT
        )

    assert result["ok"] is False
    assert result["error_code"] == "scope_denied"
    assert "not accessible in this run" in result["error"]
    assert _audit_inserts(session), "a denied resolution wrote no audit row"
    mock_dispatch.assert_not_called()
    mock_get_mgr.assert_not_called()


@pytest.mark.asyncio
async def test_unbound_scope_denies_visibly_and_never_dispatches():
    with (
        patch.object(tools_mod, "scope_for_run", AsyncMock(return_value=None)),
        patch(
            "app.services.infra.dbos_orchestrator.start_workflow_routed",
            new=AsyncMock(),
        ) as mock_dispatch,
    ):
        result = await SCREENWRITING_HANDLERS["GenerateShotImage"](
            {"shot_id": str(_SHOT_ID)}, _RUN_CONTEXT
        )
    assert result["ok"] is False
    assert result["error_code"] == "scope_unbound"
    mock_dispatch.assert_not_called()


# ====================================================================== #
# 3. Per-call (per-turn) cap enforced — reuses A1's existing mechanism.
# ====================================================================== #


@pytest.mark.asyncio
async def test_media_cap_enforced_within_a_turn_for_generate_shot_image():
    agent = _agent(media={"image": True, "max_calls_per_turn": 2})
    hook = HighRiskCapabilityGateHook(agent=agent)
    first = await hook(_hook_ctx())
    second = await hook(_hook_ctx())
    third = await hook(_hook_ctx())
    assert first.decision == "continue"
    assert second.decision == "continue"
    assert third.decision == "abort"
    assert "cap" in third.abort_reason


@pytest.mark.asyncio
async def test_media_cap_is_shared_with_generate_image_within_one_turn():
    """The counter lives on the hook INSTANCE (one per turn), not per tool
    name — GenerateShotImage and GenerateImage draw from the same per-turn
    media budget, matching spec §2's "单次上限" (a single ceiling on media
    spend for the turn, not one ceiling per tool)."""
    agent = _agent(media={"image": True, "max_calls_per_turn": 2})
    hook = HighRiskCapabilityGateHook(agent=agent)
    assert (await hook(_hook_ctx("GenerateImage"))).decision == "continue"
    assert (await hook(_hook_ctx("GenerateShotImage"))).decision == "continue"
    third = await hook(_hook_ctx("GenerateShotImage"))
    assert third.decision == "abort"


# ====================================================================== #
# 4. Kill-switch engaged + capability granted → still refused.
# ====================================================================== #


@pytest.mark.asyncio
async def test_kill_switch_blocks_even_when_capability_granted(monkeypatch):
    monkeypatch.setenv("FEATURE_AGENT_MEDIA_TOOLS", "off")
    agent = _agent(media={"image": True})
    hook = HighRiskCapabilityGateHook(agent=agent)
    result = await hook(_hook_ctx())
    assert result.decision == "abort"
    assert "kill switch" in result.abort_reason.lower()


@pytest.mark.asyncio
async def test_kill_switch_stops_dispatch_before_the_handler_runs(monkeypatch):
    monkeypatch.setenv("FEATURE_AGENT_MEDIA_TOOLS", "off")
    agent = _agent(media={"image": True})
    hook = HighRiskCapabilityGateHook(agent=agent)

    with patch(
        "app.services.infra.dbos_orchestrator.start_workflow_routed",
        new=AsyncMock(),
    ) as mock_dispatch:
        result = await hook(_hook_ctx())
        if result.decision != "abort":
            await SCREENWRITING_HANDLERS["GenerateShotImage"](
                {"shot_id": str(_SHOT_ID)}, _RUN_CONTEXT
            )

    assert result.decision == "abort"
    mock_dispatch.assert_not_called()


# ====================================================================== #
# Bonus: authorization actually reaches the workflow with the RESOLVED
# handle, and the in-flight/status dedup this handler adds on top of A1.
# ====================================================================== #


@pytest.mark.asyncio
async def test_successful_dispatch_uses_the_resolved_id_and_the_actual_user():
    """The core A2 contract for this tool: the workflow must be handed the
    id the resolver PROVED belongs to this run's scope — never the raw model
    argument reflected straight through — and the user_id the workflow will
    act as must come from the server-bound scope, never a tool argument
    (there is none to supply it from; this pins that it comes from
    ``scope.user_id``, not e.g. run_context echoed verbatim)."""
    from app.core.config import settings

    with (
        patch.object(settings, "FEATURE_SHOT_GENERATE", True),
        patch.object(tools_mod, "scope_for_run", AsyncMock(return_value=_scope())),
        patch.object(
            tools_mod,
            "resolve_shot",
            AsyncMock(
                return_value=resolver_mod.ResolvedShot(
                    id=_SHOT_ID,
                    scene_id=_SCENE_ID,
                    project_id=_PROJECT_A,
                    team_id=_TEAM_A,
                    episode_id=None,
                    shot_number=1,
                    shot_type="MS",
                    status="empty",
                )
            ),
        ),
        patch.object(gateway_mod, "set_shot_status", AsyncMock()) as mock_set_status,
        patch(
            "app.services.infra.dbos_orchestrator.start_workflow_routed",
            new=AsyncMock(),
        ) as mock_dispatch,
        patch(
            "app.services.infra.unified_task_manager.get_task_manager",
            return_value=_task_manager_mock("task-1"),
        ),
    ):
        result = await SCREENWRITING_HANDLERS["GenerateShotImage"](
            {"shot_id": "some-other-id-the-model-typed"}, _RUN_CONTEXT
        )

    assert result["ok"] is True
    assert result["dispatched"] is True
    assert result["task_id"] == "task-1"
    # Claimed BEFORE dispatch, mirroring script_shots_router.py's REST
    # endpoint — pinned by call order below, not just call presence.
    mock_set_status.assert_called_once()
    assert mock_set_status.call_args.args[2] == "generating"
    mock_dispatch.assert_called_once()
    _, kwargs = mock_dispatch.call_args
    assert kwargs["dbos_workflow_kwargs"]["shot_id"] == str(_SHOT_ID)
    assert kwargs["dbos_workflow_kwargs"]["shot_id"] != "some-other-id-the-model-typed"
    assert kwargs["dbos_workflow_kwargs"]["user_id"] == _USER_ID


@pytest.mark.asyncio
async def test_already_generating_shot_refuses_a_second_dispatch():
    """The resolver's own read of shot.status (a server fact, never a model
    assertion) refuses a dispatch when a generation is ALREADY recorded as
    in flight for this exact shot — whether that flag was set by a human's
    click via the REST endpoint or an earlier agent dispatch's own status
    claim (see test_two_back_to_back_calls_only_one_dispatches below for the
    latter, which this read-only check alone would not catch without the
    write)."""
    from app.core.config import settings

    with (
        patch.object(settings, "FEATURE_SHOT_GENERATE", True),
        patch.object(tools_mod, "scope_for_run", AsyncMock(return_value=_scope())),
        patch.object(
            tools_mod,
            "resolve_shot",
            AsyncMock(
                return_value=resolver_mod.ResolvedShot(
                    id=_SHOT_ID,
                    scene_id=_SCENE_ID,
                    project_id=_PROJECT_A,
                    team_id=_TEAM_A,
                    episode_id=None,
                    shot_number=1,
                    shot_type="MS",
                    status="generating",
                )
            ),
        ),
        patch(
            "app.services.infra.dbos_orchestrator.start_workflow_routed",
            new=AsyncMock(),
        ) as mock_dispatch,
    ):
        result = await SCREENWRITING_HANDLERS["GenerateShotImage"](
            {"shot_id": str(_SHOT_ID)}, _RUN_CONTEXT
        )

    assert result["ok"] is False
    assert result["error_code"] == "already_generating"
    mock_dispatch.assert_not_called()


@pytest.mark.asyncio
async def test_two_back_to_back_calls_only_one_dispatches():
    """The actual race the status claim exists to close (review fix): two
    GenerateShotImage calls for the SAME shot in one turn, back-to-back.

    Fakes just enough shared state to make this a real race rather than a
    tautology — ``resolve_shot`` reads the CURRENT status out of a shared
    dict, and ``set_shot_status`` writes to that same dict, so the second
    call's resolution genuinely observes the first call's write. Without
    the status claim in the handler (i.e. the pre-fix, read-only-only
    version), both calls would observe status='empty' and both would
    dispatch — this test is the one that would have failed against that
    version."""
    from app.core.config import settings

    shot_state = {"status": "empty"}

    async def _fake_resolve_shot(shot_id, scope):
        return resolver_mod.ResolvedShot(
            id=_SHOT_ID,
            scene_id=_SCENE_ID,
            project_id=_PROJECT_A,
            team_id=_TEAM_A,
            episode_id=None,
            shot_number=1,
            shot_type="MS",
            status=shot_state["status"],
        )

    async def _fake_set_shot_status(scope, shot, status):
        shot_state["status"] = status

    with (
        patch.object(settings, "FEATURE_SHOT_GENERATE", True),
        patch.object(tools_mod, "scope_for_run", AsyncMock(return_value=_scope())),
        patch.object(tools_mod, "resolve_shot", _fake_resolve_shot),
        patch.object(gateway_mod, "set_shot_status", _fake_set_shot_status),
        patch(
            "app.services.infra.dbos_orchestrator.start_workflow_routed",
            new=AsyncMock(),
        ) as mock_dispatch,
        patch(
            "app.services.infra.unified_task_manager.get_task_manager",
            return_value=_task_manager_mock("task-race"),
        ),
    ):
        first = await SCREENWRITING_HANDLERS["GenerateShotImage"](
            {"shot_id": str(_SHOT_ID)}, _RUN_CONTEXT
        )
        second = await SCREENWRITING_HANDLERS["GenerateShotImage"](
            {"shot_id": str(_SHOT_ID)}, _RUN_CONTEXT
        )

    assert first["ok"] is True
    assert first["dispatched"] is True
    assert second["ok"] is False
    assert second["error_code"] == "already_generating"
    mock_dispatch.assert_called_once()


@pytest.mark.asyncio
async def test_feature_flag_off_refuses_without_touching_the_resolver(monkeypatch):
    """FEATURE_SHOT_GENERATE off hides the endpoint (404) on the human REST
    path; the agent tool must honor the same install-wide off-switch rather
    than becoming a bypass around it."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "FEATURE_SHOT_GENERATE", False)
    with (
        patch.object(tools_mod, "scope_for_run", AsyncMock(return_value=_scope())),
        patch.object(tools_mod, "resolve_shot", AsyncMock()) as mock_resolve,
        patch(
            "app.services.infra.dbos_orchestrator.start_workflow_routed",
            new=AsyncMock(),
        ) as mock_dispatch,
    ):
        result = await SCREENWRITING_HANDLERS["GenerateShotImage"](
            {"shot_id": str(_SHOT_ID)}, _RUN_CONTEXT
        )

    assert result["ok"] is False
    assert result["error_code"] == "feature_disabled"
    mock_resolve.assert_not_called()
    mock_dispatch.assert_not_called()


# ====================================================================== #
# Spec advertising — a UX filter, gated on media.image + kill switch,
# NOT on write_level (the axis the other five screenwriting tools use).
# ====================================================================== #


def test_spec_only_advertised_when_media_image_allowed():
    from app.services.ai.tools.screenwriting_specs import screenwriting_tool_specs

    def names(write_level, media_image_allowed):
        return {
            s["function"]["name"]
            for s in screenwriting_tool_specs(
                write_level, media_image_allowed=media_image_allowed
            )
        }

    # Even a full "write" grant does not surface it without the media flag.
    assert "GenerateShotImage" not in names("write", False)
    assert "GenerateShotImage" not in names("none", False)
    # And the media flag surfaces it regardless of write_level, including
    # "none" — it is not on the write ladder at all.
    assert "GenerateShotImage" in names("none", True)
    assert "GenerateShotImage" in names("write", True)


def test_workflow_module_untouched_by_a6():
    """A6 is scoped to wiring a tool onto the EXISTING workflow — the plan is
    explicit that script_shot_generate.py is "already complete and correct
    end-to-end" and must not be rewritten. Pin its dispatch-relevant public
    surface so a future edit here is a deliberate, reviewed decision."""
    import inspect

    from app.workflows.script_shot_generate import script_shot_generate_workflow

    params = list(inspect.signature(script_shot_generate_workflow).parameters)
    # 3a (harness p4 phase 3a, Task 2) is the deliberate, reviewed edit this pin
    # exists to force: the dispatching run's coordinates now ride through DBOS
    # so the produced image can be registered as that run's deliverable. All
    # three are keyword-defaulted, so frozen inputs from before 3a still load.
    assert params == [
        "shot_id",
        "model",
        "provider",
        "user_id",
        "run_id",
        "turn",
        "step",
    ]
