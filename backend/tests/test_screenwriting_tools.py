"""Adversarial tests for the A4 screenwriting tools — ListScenes / ReadScene /
CreateShot / UpdateShot / ProposeEdit.

Three enforcement layers are exercised SEPARATELY, because each fails in a
different place and a test that only covers one would pass while the other is
broken:

  1. The A1 capability gate (``HighRiskCapabilityGateHook``) — runs in the
     PreToolUse chain, i.e. BEFORE any handler is entered. Tested by driving
     the hook directly with each tool name.
  2. The A2 resolver — runs inside each handler, turning a model-supplied id
     into an authorized row or a ``Denied``. Tested by handing a handler an
     id whose row belongs to another project and asserting BOTH that the
     result is a visible error and that an audit row was written.
  3. The gateway's own rules (shot numbering, field whitelist, proposal
     validation) — tested against a fake session.

Unit suite, no DSN: the same fake-session style as ``test_scope_resolver.py``.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest

import app.services.ai.scope.scope_resolver as resolver_mod
import app.services.ai.scope.scoped_script_gateway as gateway_mod
import app.services.ai.scope.script_selection as selection_mod
import app.services.ai.tools.screenwriting_tools as tools_mod
from app.services.ai.scope.agent_run_scope import AgentRunScope
from app.services.ai.scope.scope_resolver import ResolvedScene, ResolvedShot
from app.services.ai.tools.screenwriting_tools import SCREENWRITING_HANDLERS
from app.services.infra.hooks import HookContext
from app.services.infra.hooks.high_risk_capability_gate import (
    TOOL_REQUIREMENTS,
    HighRiskCapabilityGateHook,
)

_RUN_ID = "800100000000000009"
_USER_ID = "22222222-2222-2222-2222-222222222222"
_PROJECT_A = 900100000000000001
_PROJECT_B = 900100000000000002  # a DIFFERENT project — the cross-tenant target
_TEAM_A = 900100000000000003
_SCRIPT_ID = 700100000000000004
_SCENE_ID = 700100000000000001
_SHOT_ID = 700100000000000002

_READ_TOOLS = ("ListScenes", "ReadScene")
# A5 adds ApplyEdit at "write": ProposeEdit returns a revision, ApplyEdit
# commits one, and the ordinal ladder is what keeps a propose-graded agent
# out of the second.
_WRITE_TOOLS = ("CreateShot", "UpdateShot", "ApplyEdit")
_PROPOSE_TOOLS = ("ProposeEdit",)
_ALL_TOOLS = _READ_TOOLS + _WRITE_TOOLS + _PROPOSE_TOOLS

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


def _agent(write_level: str | None = None, **caps) -> dict:
    profile: dict = dict(caps)
    if write_level is not None:
        profile["write_level"] = write_level
    return {"capability_profile": {"capabilities": profile}}


def _hook_ctx(tool_name: str) -> HookContext:
    return HookContext(
        run_id=_RUN_ID,
        agent_id=UUID("00000000-0000-0000-0000-000000000002"),
        agent_slug="screenwriter",
        user_id=UUID(_USER_ID),
        session_id=None,
        tool_name=tool_name,
        tool_args={},
        accumulated_prompt_tokens=0,
        accumulated_completion_tokens=0,
        accumulated_cost_cents=0.0,
        iteration=1,
    )


# ====================================================================== #
# Layer 1 — the A1 capability gate. Every tool is gated; no tool is a
# free baseline; and a denial is a VISIBLE abort, not a silent pass.
# ====================================================================== #


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", _ALL_TOOLS)
async def test_every_screenwriting_tool_is_registered_with_the_gate(tool_name):
    """A tool absent from TOOL_REQUIREMENTS passes the gate unconditionally.
    Forgetting one entry is therefore a silent hole, not a crash — pin it."""
    assert tool_name in TOOL_REQUIREMENTS
    assert TOOL_REQUIREMENTS[tool_name].write_level is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", _ALL_TOOLS)
async def test_ungranted_agent_is_denied_every_tool(tool_name):
    """Fail-closed default: an agent with no capability_profile at all — the
    entire existing fleet — reaches none of these tools, not even the reads."""
    hook = HighRiskCapabilityGateHook(agent=None)
    result = await hook(_hook_ctx(tool_name))
    assert result.decision == "abort"
    # The denial must be legible, not an empty abort: the model and the
    # transcript both surface abort_reason.
    assert tool_name in result.abort_reason
    assert "none" in result.abort_reason


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", _ALL_TOOLS)
async def test_write_granted_agent_is_allowed_every_tool(tool_name):
    hook = HighRiskCapabilityGateHook(agent=_agent("write"))
    result = await hook(_hook_ctx(tool_name))
    assert result.decision == "continue"


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", _WRITE_TOOLS)
async def test_propose_only_agent_cannot_write_shots(tool_name):
    """The grading is ordinal and the middle tier must not leak upward: an
    agent trusted to SUGGEST revisions is not thereby trusted to commit
    storyboard cards."""
    hook = HighRiskCapabilityGateHook(agent=_agent("propose"))
    result = await hook(_hook_ctx(tool_name))
    assert result.decision == "abort"
    assert "'write'" in result.abort_reason and "'propose'" in result.abort_reason


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", _READ_TOOLS + _PROPOSE_TOOLS)
async def test_propose_only_agent_may_read_and_propose(tool_name):
    hook = HighRiskCapabilityGateHook(agent=_agent("propose"))
    assert (await hook(_hook_ctx(tool_name))).decision == "continue"


@pytest.mark.asyncio
async def test_read_only_agent_cannot_propose_edits():
    hook = HighRiskCapabilityGateHook(agent=_agent("read"))
    result = await hook(_hook_ctx("ProposeEdit"))
    assert result.decision == "abort"
    assert "'propose'" in result.abort_reason and "'read'" in result.abort_reason


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", _READ_TOOLS)
async def test_read_only_agent_may_read(tool_name):
    hook = HighRiskCapabilityGateHook(agent=_agent("read"))
    assert (await hook(_hook_ctx(tool_name))).decision == "continue"


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", _ALL_TOOLS)
async def test_malformed_write_level_denies(tool_name):
    """A profile that says ``write_level: true`` (or "WRITE", or 1) grants
    nothing — the fail-closed parser only accepts the exact literals."""
    hook = HighRiskCapabilityGateHook(
        agent={"capability_profile": {"capabilities": {"write_level": True}}}
    )
    assert (await hook(_hook_ctx(tool_name))).decision == "abort"


# ====================================================================== #
# Layer 1b — the gate must be PRESENT, not merely correct when present.
#
# These drive the RUNNER, not the hook: the hook tests above all construct
# HighRiskCapabilityGateHook by hand, which silently assumes something
# registered it. Seven services build AgentRunner with no hooks at all
# (script_ai / summarize / caption / classify / translate / visual_analysis
# / topic_scorer) while composing through the same
# PromptComposer that advertises these tools — on those runners the whole
# PreToolUse chain is a no-op, so a hook-level test proves nothing about
# whether the call was gated.
# ====================================================================== #


def _runner(*, with_gate: bool):
    from unittest.mock import MagicMock

    from app.services.ai.runner.agent_runner import AgentRunner
    from app.services.infra.hooks import HookRegistry

    hooks = None
    if with_gate:
        hooks = HookRegistry()
        hooks.register_pre(
            HighRiskCapabilityGateHook(agent=_agent("write")),
            name="high_risk_capability_gate",
            priority=26,
            fail_closed=True,
        )
    return AgentRunner(adapter=MagicMock(), skill_tool=MagicMock(), hooks=hooks)


def _composed():
    from unittest.mock import MagicMock

    return MagicMock(agent_id=UUID("00000000-0000-0000-0000-000000000002"))


def _recorder():
    from unittest.mock import MagicMock

    return MagicMock(run_id=_RUN_ID, user_id=_USER_ID, team_id=_TEAM_A)


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", _ALL_TOOLS)
async def test_a_hookless_runner_refuses_every_screenwriting_tool(tool_name):
    """A runner with no capability gate has no business running
    capability-gated tools. Without this, the seven hookless services would
    execute these tools with NO write grading whatsoever — the gate isn't
    bypassed, it simply never runs."""
    called = False

    async def _tripwire(args, ctx):
        nonlocal called
        called = True
        return {"ok": True}

    with patch.dict(
        tools_mod.SCREENWRITING_HANDLERS, {tool_name: _tripwire}, clear=False
    ):
        result = await _runner(with_gate=False)._dispatch_screenwriting(
            tool_name, {}, _recorder(), _composed()
        )

    assert result["ok"] is False
    assert result["error_code"] == "capability_gate_missing"
    assert not called, "the handler ran on a runner with no capability gate"


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", _ALL_TOOLS)
async def test_a_runner_carrying_the_gate_reaches_the_handler(tool_name):
    """The other half: the refusal above must be about the gate's ABSENCE,
    not a blanket block that would make the tools unreachable everywhere."""
    called = False

    async def _tripwire(args, ctx):
        nonlocal called
        called = True
        return {"ok": True, "marker": tool_name}

    with patch.dict(
        tools_mod.SCREENWRITING_HANDLERS, {tool_name: _tripwire}, clear=False
    ):
        result = await _runner(with_gate=True)._dispatch_screenwriting(
            tool_name, {}, _recorder(), _composed()
        )

    assert called and result == {"ok": True, "marker": tool_name}


@pytest.mark.asyncio
async def test_gate_detection_is_by_type_not_by_registration_name():
    """A hook registered under the gate's NAME but of some other type must
    not satisfy the check — the type is what implements the decision."""
    from unittest.mock import MagicMock

    from app.services.ai.runner.agent_runner import AgentRunner
    from app.services.infra.hooks import HookRegistry, HookResult

    async def _impostor(ctx):
        return HookResult(decision="continue")

    hooks = HookRegistry()
    hooks.register_pre(_impostor, name="high_risk_capability_gate", priority=26)
    runner = AgentRunner(adapter=MagicMock(), skill_tool=MagicMock(), hooks=hooks)

    assert runner._high_risk_gate_registered() is False
    result = await runner._dispatch_screenwriting(
        "ReadScene", {}, _recorder(), _composed()
    )
    assert result["error_code"] == "capability_gate_missing"


def test_the_seven_hookless_services_still_construct_runners_without_hooks():
    """Documents WHY the check above exists, and fails loudly if someone
    "fixes" these services by adding hooks — at which point the refusal
    stops being the thing protecting them and their exemption in
    test_scope_binding.py needs re-arguing."""
    import re as _re
    from pathlib import Path

    app_dir = Path(__file__).resolve().parent.parent / "app"
    hookless = (
        "services/storyboard/script/script_ai_service.py",
        "services/ai/summarize/summarize_service.py",
        "services/ai/caption/caption_service.py",
        "services/ai/classify/classify_service.py",
        "services/ai/translate/translate_service.py",
        "services/ai/visual/visual_analysis_service.py",
        "services/topics/topic_scorer.py",
    )
    for rel in hookless:
        text = (app_dir / rel).read_text(encoding="utf-8")
        assert _re.search(r"AgentRunner\(", text), f"{rel} no longer builds a runner"
        assert "hooks=" not in text, (
            f"{rel} now passes hooks= — re-check whether "
            "HighRiskCapabilityGateHook is among them, and revisit this "
            "file's exemption in test_scope_binding.py"
        )


# ====================================================================== #
# Layer 2 — the A2 resolver, inside each handler.
# ====================================================================== #


class _FakeResult:
    def __init__(self, *, first_row=None, scalar=None, all_rows=None):
        self._first_row = first_row
        self._scalar = scalar
        self._all = all_rows or []

    def first(self):
        return self._first_row

    def scalar(self):
        return self._scalar

    def scalars(self):
        return SimpleNamespace(all=lambda: self._all, first=lambda: self._first_row)

    def all(self):
        return self._all


class _CaptureSession:
    def __init__(self, results=None):
        self.statements: list = []
        self._results = list(results or [])

    async def execute(self, stmt):
        self.statements.append(stmt)
        return self._results.pop(0) if self._results else _FakeResult()

    async def scalar(self, stmt):
        self.statements.append(stmt)
        result = self._results.pop(0) if self._results else _FakeResult()
        return result._scalar if isinstance(result, _FakeResult) else result


class _ScopeCtx:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc):
        return False


def _scene_row(project_id, team_id=_TEAM_A, episode_id=None):
    return SimpleNamespace(
        id=_SCENE_ID,
        script_id=_SCRIPT_ID,
        heading_int_ext="INT",
        location_text="Kitchen",
        time_of_day="DAY",
        content_json=[{"id": "el_1", "type": "action", "text": "She waits."}],
        content_version=3,
        project_id=project_id,
        team_id=team_id,
        episode_id=episode_id,
    )


def _shot_row(project_id, team_id=_TEAM_A, episode_id=None):
    return SimpleNamespace(
        id=_SHOT_ID,
        scene_id=_SCENE_ID,
        shot_number=1,
        shot_type="MS",
        status="empty",
        project_id=project_id,
        team_id=team_id,
        episode_id=episode_id,
    )


def _audit_inserts(session) -> list:
    return [s for s in session.statements if s.__class__.__name__ == "Insert"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool_name,args,row_factory",
    [
        ("ReadScene", {"scene_id": str(_SCENE_ID)}, _scene_row),
        ("CreateShot", {"scene_id": str(_SCENE_ID)}, _scene_row),
        ("ProposeEdit", {"scene_id": str(_SCENE_ID)}, _scene_row),
        ("UpdateShot", {"shot_id": str(_SHOT_ID)}, _shot_row),
    ],
)
async def test_id_outside_the_runs_scope_is_denied_and_audited(
    tool_name, args, row_factory
):
    """The core threat (spec §3.1): the model supplies a real, existing id
    that belongs to ANOTHER project. The row is found — so a naive
    "does it exist" check would pass — and must still be refused."""
    session = _CaptureSession([_FakeResult(first_row=row_factory(_PROJECT_B))])
    if tool_name == "ProposeEdit":
        args = {**args, "element_ids": ["el_1"], "proposed_text": "New line."}

    with (
        patch.multiple(
            resolver_mod,
            read_scope=lambda: _ScopeCtx(session),
            write_scope=lambda: _ScopeCtx(session),
        ),
        patch.object(tools_mod, "scope_for_run", AsyncMock(return_value=_scope())),
    ):
        result = await SCREENWRITING_HANDLERS[tool_name](args, _RUN_CONTEXT)

    assert result["ok"] is False
    assert result["error_code"] == "scope_denied"
    # Visible, not a silent empty success: the model is told the id failed.
    assert "not accessible in this run" in result["error"]
    # ...and the attempt left a trail.
    assert _audit_inserts(session), "a denied resolution wrote no audit row"


@pytest.mark.asyncio
async def test_denial_does_not_leak_which_project_owns_the_id():
    """Anti-enumeration: the message handed back to the model must not
    distinguish "no such scene" from "someone else's scene"."""
    session = _CaptureSession([_FakeResult(first_row=_scene_row(_PROJECT_B))])
    with (
        patch.multiple(
            resolver_mod,
            read_scope=lambda: _ScopeCtx(session),
            write_scope=lambda: _ScopeCtx(session),
        ),
        patch.object(tools_mod, "scope_for_run", AsyncMock(return_value=_scope())),
    ):
        denied = await SCREENWRITING_HANDLERS["ReadScene"](
            {"scene_id": str(_SCENE_ID)}, _RUN_CONTEXT
        )

    missing = _CaptureSession([_FakeResult(first_row=None)])
    with (
        patch.multiple(
            resolver_mod,
            read_scope=lambda: _ScopeCtx(missing),
            write_scope=lambda: _ScopeCtx(missing),
        ),
        patch.object(tools_mod, "scope_for_run", AsyncMock(return_value=_scope())),
    ):
        absent = await SCREENWRITING_HANDLERS["ReadScene"](
            {"scene_id": str(_SCENE_ID)}, _RUN_CONTEXT
        )

    assert denied["error"] == absent["error"]
    assert str(_PROJECT_B) not in denied["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", _ALL_TOOLS)
async def test_unbound_scope_denies_every_tool_visibly(tool_name):
    """The failure mode A4 exists to fix, kept as a regression pin: a run
    with no project stamped must refuse every tool AND say why — a silent
    empty scene list would teach the model the script is blank."""
    with patch.object(tools_mod, "scope_for_run", AsyncMock(return_value=None)):
        result = await SCREENWRITING_HANDLERS[tool_name]({}, _RUN_CONTEXT)
    assert result["ok"] is False
    assert result["error_code"] == "scope_unbound"
    assert "No script project is bound" in result["error"]


@pytest.mark.asyncio
async def test_scope_bound_to_a_project_but_scene_in_same_team_is_still_denied():
    """Same user, same TEAM, different project: still denied. Membership is
    not the authorization model for a dispatched run (scope is)."""
    session = _CaptureSession(
        [_FakeResult(first_row=_scene_row(_PROJECT_B, team_id=_TEAM_A))]
    )
    with (
        patch.multiple(
            resolver_mod,
            read_scope=lambda: _ScopeCtx(session),
            write_scope=lambda: _ScopeCtx(session),
        ),
        patch.object(tools_mod, "scope_for_run", AsyncMock(return_value=_scope())),
    ):
        result = await SCREENWRITING_HANDLERS["ReadScene"](
            {"scene_id": str(_SCENE_ID)}, _RUN_CONTEXT
        )
    assert result["ok"] is False


@pytest.mark.asyncio
async def test_episode_scoped_run_denies_a_scene_from_another_episode():
    """Now that mig 404 makes scope.episode_id real, a run narrowed to
    episode A must not reach episode B's scenes even inside its own
    project."""
    episode_a, episode_b = 111, 222
    session = _CaptureSession(
        [_FakeResult(first_row=_scene_row(_PROJECT_A, episode_id=episode_b))]
    )
    with (
        patch.multiple(
            resolver_mod,
            read_scope=lambda: _ScopeCtx(session),
            write_scope=lambda: _ScopeCtx(session),
        ),
        patch.object(
            tools_mod,
            "scope_for_run",
            AsyncMock(return_value=_scope(episode_id=episode_a)),
        ),
    ):
        result = await SCREENWRITING_HANDLERS["ReadScene"](
            {"scene_id": str(_SCENE_ID)}, _RUN_CONTEXT
        )
    assert result["ok"] is False
    assert result["error_code"] == "scope_denied"


# ====================================================================== #
# Layer 3 — gateway rules: shot numbering, field whitelist, proposals.
# ====================================================================== #


def _resolved_scene(content=None, version=3) -> ResolvedScene:
    return ResolvedScene(
        id=_SCENE_ID,
        script_id=_SCRIPT_ID,
        project_id=_PROJECT_A,
        team_id=_TEAM_A,
        episode_id=None,
        heading_int_ext="INT",
        location_text="Kitchen",
        time_of_day="DAY",
        content_json=(
            content
            if content is not None
            else [{"id": "el_1", "type": "action", "text": "She waits."}]
        ),
        content_version=version,
    )


def _resolved_shot() -> ResolvedShot:
    return ResolvedShot(
        id=_SHOT_ID,
        scene_id=_SCENE_ID,
        project_id=_PROJECT_A,
        team_id=_TEAM_A,
        episode_id=None,
        shot_number=4,
        shot_type="MS",
        status="empty",
    )


@pytest.mark.asyncio
async def test_create_shot_assigns_the_next_scene_internal_integer():
    """A4's shot-numbering decision, pinned: ``shot_number`` stays the
    existing scene-scoped INTEGER assigned server-side as MAX+1 — the same
    ladder ``ScriptShotRepository.create_many`` uses, so agent-created and
    Auto-Storyboard-created cards interleave instead of colliding."""
    session = _CaptureSession(
        [
            _FakeResult(scalar=7),  # MAX(shot_number)
            _FakeResult(scalar=7000),  # MAX(sort_order)
            _FakeResult(
                first_row=SimpleNamespace(
                    id=_SHOT_ID,
                    shot_number=8,
                    shot_type="CU",
                    camera_angle=None,
                    camera_movement=None,
                    focal_length="85mm",
                    lighting=None,
                    description="Her hands.",
                    status="empty",
                )
            ),
            _FakeResult(),  # ledger INSERT
        ]
    )
    with (
        patch.object(gateway_mod, "write_scope", lambda: _ScopeCtx(session)),
        patch.object(gateway_mod, "scene_no_for", AsyncMock(return_value="3A")),
    ):
        shot = await gateway_mod.create_shot(
            _scope(),
            _resolved_scene(),
            {"shot_type": "CU", "focal_length": "85mm", "description": "Her hands."},
        )

    insert_stmt = next(
        s for s in session.statements if s.__class__.__name__ == "Insert"
    )
    values = insert_stmt.compile().params
    assert values["shot_number"] == 8
    assert values["sort_order"] == 7000 + gateway_mod._SORT_ORDER_STEP
    # And the composite label is DERIVED, never stored.
    assert shot["shot_label"] == "3A-08"
    assert "shot_label" not in values


@pytest.mark.asyncio
async def test_create_shot_ignores_a_model_supplied_shot_number_and_status():
    """The model must not be able to choose its own number (that is how
    duplicate shot ids happen) or declare a card already rendered."""
    session = _CaptureSession(
        [
            _FakeResult(scalar=0),
            _FakeResult(scalar=0),
            _FakeResult(
                first_row=SimpleNamespace(
                    id=_SHOT_ID,
                    shot_number=1,
                    shot_type=None,
                    camera_angle=None,
                    camera_movement=None,
                    focal_length=None,
                    lighting=None,
                    description="x",
                    status="empty",
                )
            ),
            _FakeResult(),  # ledger INSERT
        ]
    )
    with (
        patch.object(gateway_mod, "write_scope", lambda: _ScopeCtx(session)),
        patch.object(gateway_mod, "scene_no_for", AsyncMock(return_value="1")),
    ):
        await gateway_mod.create_shot(
            _scope(),
            _resolved_scene(),
            {
                "shot_number": 99,
                "status": "done",
                "image_url": "https://evil.example/x.png",
                "description": "x",
            },
        )

    values = (
        next(s for s in session.statements if s.__class__.__name__ == "Insert")
        .compile()
        .params
    )
    assert values["shot_number"] == 1
    assert "status" not in values
    assert "image_url" not in values


@pytest.mark.asyncio
async def test_update_shot_rejects_status_and_url_writes():
    """``UpdateShot`` shares the repository's disjoint-lane discipline: the
    status machine and the produced-media URLs belong to the generate
    workflow, never to an agent's parameter edit."""
    session = _CaptureSession(
        [
            _FakeResult(
                first_row=SimpleNamespace(
                    shot_type="MS",
                    camera_angle=None,
                    camera_movement=None,
                    focal_length=None,
                    lighting=None,
                    description=None,
                )
            ),  # SELECT ... FOR UPDATE 旧值
            _FakeResult(
                first_row=SimpleNamespace(
                    id=_SHOT_ID,
                    shot_number=4,
                    shot_type="MS",
                    camera_angle="LOW",
                    camera_movement=None,
                    focal_length=None,
                    lighting=None,
                    description=None,
                    status="empty",
                )
            ),
            _FakeResult(),  # ledger INSERT
        ]
    )
    with (
        patch.object(gateway_mod, "write_scope", lambda: _ScopeCtx(session)),
        patch.object(gateway_mod, "scene_no_for_shot", AsyncMock(return_value="3A")),
    ):
        updated = await gateway_mod.update_shot(
            _scope(),
            _resolved_shot(),
            {"camera_angle": "LOW", "status": "done", "video_url": "x"},
        )

    values = (
        next(s for s in session.statements if s.__class__.__name__ == "Update")
        .compile()
        .params
    )
    assert values["camera_angle"] == "LOW"
    assert "status" not in values
    assert "video_url" not in values
    # A4 review (Minor): UpdateShot used to hand back shot_label=None while
    # create/list returned a real label — the model revised a card and
    # watched its own reference vanish.
    assert updated["shot_label"] == "3A-04"


@pytest.mark.asyncio
async def test_update_shot_with_no_writable_fields_reports_instead_of_no_op():
    """A silent success on an empty update teaches the model its edit
    landed. Say nothing was writable instead."""
    with (
        patch.object(tools_mod, "scope_for_run", AsyncMock(return_value=_scope())),
        patch.object(
            tools_mod,
            "resolve_shot",
            AsyncMock(return_value=_resolved_shot()),
        ),
        patch.object(gateway_mod, "update_shot", AsyncMock(return_value=None)),
    ):
        result = await SCREENWRITING_HANDLERS["UpdateShot"](
            {"shot_id": str(_SHOT_ID), "status": "done"}, _RUN_CONTEXT
        )
    assert result["ok"] is False
    assert result["error_code"] == "no_fields"


@pytest.mark.asyncio
async def test_propose_edit_rejects_element_ids_not_in_the_scene():
    """The proposal's anchors must exist in the scene the resolver
    authorized — a hallucinated element_id is caught here, not left for the
    apply path to fail on later."""
    with (
        patch.object(tools_mod, "scope_for_run", AsyncMock(return_value=_scope())),
        patch.object(
            selection_mod, "resolve_scene", AsyncMock(return_value=_resolved_scene())
        ),
        patch.object(selection_mod, "audit_resolution", AsyncMock()),
    ):
        result = await SCREENWRITING_HANDLERS["ProposeEdit"](
            {
                "scene_id": str(_SCENE_ID),
                "element_ids": ["el_1", "el_does_not_exist"],
                "proposed_text": "New line.",
            },
            _RUN_CONTEXT,
        )
    assert result["ok"] is False
    assert result["error_code"] == "unknown_element"
    assert "el_does_not_exist" in result["error"]


@pytest.mark.asyncio
async def test_propose_edit_writes_nothing_and_flags_a_stale_base_version():
    """ProposeEdit is A4's boundary with A5: it produces a reviewable
    proposal and NEVER touches the script. It also surfaces the cheap half
    of the §5.2 concurrency contract — a base_content_version that no longer
    matches means the writer edited meanwhile."""
    with (
        patch.object(tools_mod, "scope_for_run", AsyncMock(return_value=_scope())),
        patch.object(
            selection_mod,
            "resolve_scene",
            AsyncMock(return_value=_resolved_scene(version=9)),
        ),
        patch.object(gateway_mod, "scene_no_for", AsyncMock(return_value="4")),
    ):
        fresh = await SCREENWRITING_HANDLERS["ProposeEdit"](
            {
                "scene_id": str(_SCENE_ID),
                "element_ids": ["el_1"],
                "proposed_text": "New line.",
                "base_content_version": 9,
            },
            _RUN_CONTEXT,
        )
        stale = await SCREENWRITING_HANDLERS["ProposeEdit"](
            {
                "scene_id": str(_SCENE_ID),
                "element_ids": ["el_1"],
                "proposed_text": "New line.",
                "base_content_version": 7,
            },
            _RUN_CONTEXT,
        )

    assert fresh["ok"] is True
    assert fresh["applied"] is False
    assert fresh["stale"] is False
    # A5 (review, Critical): the proposal echoes the version the MODEL quoted,
    # never the scene's current one — handing back the current value is what
    # used to let a model turn off the write precondition. Same number here
    # only because this proposal is not stale.
    assert fresh["proposal"]["base_content_version"] == 9
    assert stale["stale"] is True
    assert stale["proposal"]["base_content_version"] == 7  # NOT the scene's 9
    # "Re-read" is now literal: only ReadScene refreshes the server-side
    # record that the write precondition compares against.
    assert "ReadScene" in stale["note"]


@pytest.mark.asyncio
async def test_read_scene_surfaces_element_ids_and_the_version_token():
    with (
        patch.object(tools_mod, "scope_for_run", AsyncMock(return_value=_scope())),
        patch.object(
            tools_mod, "resolve_scene", AsyncMock(return_value=_resolved_scene())
        ),
        patch.object(gateway_mod, "scene_no_for", AsyncMock(return_value="2")),
        patch.object(gateway_mod, "list_shots_for_scene", AsyncMock(return_value=[])),
    ):
        result = await SCREENWRITING_HANDLERS["ReadScene"](
            {"scene_id": str(_SCENE_ID)}, _RUN_CONTEXT
        )
    assert result["ok"] is True
    assert result["scene_no_in_episode"] == "2"
    assert result["content_version"] == 3
    assert result["elements"] == [
        {
            "element_id": "el_1",
            "type": "action",
            "text": "She waits.",
            "character_id": None,
        }
    ]


@pytest.mark.asyncio
async def test_scene_no_for_shot_walks_shot_to_scene_to_script():
    """``ResolvedShot`` carries scene_id but not script_id, so the label
    derivation needs one extra hop. It stays inside the gateway (and off any
    model-supplied id) because a ResolvedShot only exists once resolve_shot
    proved the whole shot -> scene -> script chain is in scope."""
    session = _CaptureSession(
        [
            _FakeResult(scalar=_SCRIPT_ID),  # script_id for the shot's scene
            _FakeResult(first_row=SimpleNamespace(numbering_locked_at=None)),
            _FakeResult(scalar=None),  # no frozen scene_number (unlocked)
            _FakeResult(all_rows=[]),
        ]
    )

    class _Scalars:
        def __init__(self, ids):
            self._ids = ids

        def all(self):
            return self._ids

    class _IdsResult(_FakeResult):
        def scalars(self):
            return _Scalars([111, _SCENE_ID, 333])

    session._results[-1] = _IdsResult()

    with patch.object(gateway_mod, "read_scope", lambda: _ScopeCtx(session)):
        label = await gateway_mod.scene_no_for_shot(_scope(), _resolved_shot())

    # _SCENE_ID is at index 1 in canonical order → writing-phase number "2".
    assert label == "2"


@pytest.mark.asyncio
async def test_list_scenes_returns_nothing_for_an_unbound_scope():
    """Gateway-level fail-closed, independent of the handler's own check."""
    assert await gateway_mod.list_scenes_in_scope(_scope(project_id=None)) == []


# ====================================================================== #
# RLS 第三层 (PR-2b) — the caller_scope boundary. The scope is derived on
# postgres FIRST (it reads agent_runs, which authenticated has no RLS grant
# on), and ONLY the tenant scene/shot access runs inside caller_scope.
# ====================================================================== #


@pytest.mark.asyncio
async def test_scope_is_resolved_on_postgres_before_caller_scope_is_entered():
    """The load-bearing ordering: ``scope_for_run`` (agent_runs — infra, no RLS
    grant for authenticated) must resolve on the normal postgres connection
    BEFORE ``caller_scope`` drops to authenticated, and the tenant scene read
    must run INSIDE caller_scope. A caller_scope that swallowed scope_for_run
    would make the run's own scope invisible to itself and break every tool."""
    from contextlib import asynccontextmanager

    order: list[str] = []

    async def _scope_for_run(run_id):
        order.append("scope_for_run")
        return _scope()

    @asynccontextmanager
    async def _spy_caller_scope(user_id):
        # the SERVER-BOUND scope's user, never a model-supplied value
        assert user_id == _USER_ID
        order.append("caller_scope_enter")
        try:
            yield object()
        finally:
            order.append("caller_scope_exit")

    async def _scene_no(scope, scene):
        order.append("gateway_read_inside_caller_scope")
        return "2"

    with (
        patch.object(tools_mod, "scope_for_run", _scope_for_run),
        patch.object(tools_mod, "caller_scope", _spy_caller_scope),
        patch.object(
            tools_mod, "resolve_scene", AsyncMock(return_value=_resolved_scene())
        ),
        patch.object(gateway_mod, "scene_no_for", _scene_no),
        patch.object(gateway_mod, "read_scene_elements", AsyncMock(return_value=[])),
        patch.object(gateway_mod, "list_shots_for_scene", AsyncMock(return_value=[])),
    ):
        result = await SCREENWRITING_HANDLERS["ReadScene"](
            {"scene_id": str(_SCENE_ID)}, _RUN_CONTEXT
        )

    assert result["ok"] is True
    assert order == [
        "scope_for_run",
        "caller_scope_enter",
        "gateway_read_inside_caller_scope",
        "caller_scope_exit",
    ]


@pytest.mark.asyncio
async def test_apply_edit_runs_the_ops_write_inside_caller_scope():
    """The write path's boundary: resolve_selection (which also writes audit
    rows to agent_run_events — infra) runs on postgres, and the ops-channel
    write runs inside caller_scope so mig-408's WITH CHECK enforces tenancy."""
    from contextlib import asynccontextmanager

    order: list[str] = []

    @asynccontextmanager
    async def _spy_caller_scope(user_id):
        order.append("enter")
        try:
            yield object()
        finally:
            order.append("exit")

    async def _apply(scope, scene, edits, *, quoted_base_version, actor, step=None):
        order.append("apply_element_edit")
        return gateway_mod.EditApplied(
            scene_id=_SCENE_ID,
            element_ids=tuple(edits),
            content_version=10,
            rebased_from=None,
            observed_version=9,
            quoted_base_version=quoted_base_version,
        )

    with (
        patch.object(tools_mod, "scope_for_run", AsyncMock(return_value=_scope())),
        patch.object(tools_mod, "caller_scope", _spy_caller_scope),
        patch.object(
            selection_mod, "resolve_scene", AsyncMock(return_value=_resolved_scene())
        ),
        patch.object(selection_mod, "audit_resolution", AsyncMock()),
        patch.object(gateway_mod, "apply_element_edit", _apply),
        patch.object(gateway_mod, "scene_no_for", AsyncMock(return_value="2")),
    ):
        result = await SCREENWRITING_HANDLERS["ApplyEdit"](
            {
                "scene_id": str(_SCENE_ID),
                "element_ids": ["el_1"],
                "proposed_text": "New line.",
                "base_content_version": 9,
            },
            _RUN_CONTEXT,
        )

    assert result["ok"] is True and result["applied"] is True
    # the write happened strictly between enter and exit
    assert order == ["enter", "apply_element_edit", "exit"]


# ====================================================================== #
# Spec advertising — a UX filter, never the enforcement.
# ====================================================================== #


def test_specs_are_advertised_by_write_level():
    from app.services.ai.tools.screenwriting_specs import screenwriting_tool_specs

    def names(level):
        return {s["function"]["name"] for s in screenwriting_tool_specs(level)}

    assert names("none") == set()
    assert names("read") == set(_READ_TOOLS)
    assert names("propose") == set(_READ_TOOLS + _PROPOSE_TOOLS)
    assert names("write") == set(_ALL_TOOLS)


def test_every_advertised_tool_is_dispatchable():
    """A spec the runner cannot route is a tool that fails mysteriously
    mid-turn. Keep the three lists in lockstep."""
    from app.services.ai.runner.agent_runner import (
        SCREENWRITING_TOOL_NAMES,
        SUPPORTED_TOOLS,
    )
    from app.services.ai.tools.screenwriting_specs import SCREENWRITING_TOOL_SPECS

    assert set(SCREENWRITING_TOOL_SPECS) == set(SCREENWRITING_TOOL_NAMES)
    assert set(SCREENWRITING_HANDLERS) == set(SCREENWRITING_TOOL_NAMES)
    assert SCREENWRITING_TOOL_NAMES <= SUPPORTED_TOOLS
