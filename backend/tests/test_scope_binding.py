"""Tests for A4's dispatch-time scope binding.

The bug A4 fixes is easy to state and was invisible for a whole stage:
``conversation_agent_turn``, ``agent_worker`` and ``subagent_task_service``
each passed a literal ``project_id=None`` into ``RunRecorder``, and
``agent_runner``'s auto-recorder passed neither scope column — so
``scope_for_run`` always produced an unbound scope and every A2 resolver
denied everything. The tools would have shipped inert.

So these tests assert two different things, deliberately:

  - the DERIVATION is right (``resolve_dispatch_scope``): inherit from a
    parent run, read a conversation's project, never widen;
  - the WIRING is right (each dispatch site actually calls it and hands the
    result to ``RunRecorder``) — including a source-level pin, because the
    original bug was a hardcoded ``None`` at a call site, which no
    behavioural test of the derivation function would ever have caught.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

import app.services.ai.scope.agent_run_scope as scope_mod
import app.services.ai.scope.scope_binding as binding_mod
from app.services.ai.scope.agent_run_scope import scope_for_run
from app.services.ai.scope.scope_binding import DispatchScope, resolve_dispatch_scope

_APP = Path(__file__).resolve().parent.parent / "app"

_PROJECT = 900100000000000001
_EPISODE = 900100000000000005
_PARENT_RUN = 800100000000000001
_CONVERSATION = 500100000000000001
_SCRIPT = 700100000000000004
_USER_ID = "22222222-2222-2222-2222-222222222222"


class _FakeResult:
    def __init__(self, first_row=None, scalar=None):
        self._first_row = first_row
        self._scalar = scalar

    def first(self):
        return self._first_row

    def scalar(self):
        return self._scalar


class _Session:
    def __init__(self, results):
        self._results = list(results)

    async def execute(self, stmt):
        return self._results.pop(0) if self._results else _FakeResult()


class _Ctx:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc):
        return False


def _agent(**caps) -> dict:
    return {"capability_profile": {"capabilities": caps}}


# ====================================================================== #
# Derivation
# ====================================================================== #


@pytest.mark.asyncio
async def test_explicit_values_are_used_verbatim():
    scope = await resolve_dispatch_scope(project_id=_PROJECT, episode_id=_EPISODE)
    assert scope == DispatchScope(_PROJECT, _EPISODE)


@pytest.mark.asyncio
async def test_an_explicit_project_survives_a_failed_conversation_lookup():
    """The regression this pins is a spend cap, not a feature (A4 review
    round 2). The first cut of the chat-service wiring re-derived the project
    from the conversation instead of using the value already in hand. In
    production both are the same column, but a failed read degraded the run
    to project_id=None — and on the issue-dispatch path that column is what
    ``count_auto_dispatches_today`` filters on, so the autopilot daily quota
    would have silently stopped counting. An explicit project must never
    depend on a lookup."""
    with patch(
        "app.db.engine.fetch_one", AsyncMock(side_effect=RuntimeError("db down"))
    ):
        scope = await resolve_dispatch_scope(
            project_id=_PROJECT, conversation_id=_CONVERSATION
        )
    assert scope.project_id == _PROJECT
    assert scope.episode_id is None  # not narrowed, but not lost either


@pytest.mark.asyncio
async def test_an_explicit_project_still_picks_up_the_conversations_episode():
    """The other half: passing the project explicitly must not cost the
    episode dimension, or the primary chat surface stays unnarrowed."""
    row = {"project_id": _PROJECT, "context_type": "script", "context_id": _SCRIPT}
    with (
        patch("app.db.engine.fetch_one", AsyncMock(return_value=row)),
        patch(
            "app.services.ai.scope.scoped_script_gateway.episode_id_for_script",
            AsyncMock(return_value=_EPISODE),
        ),
    ):
        scope = await resolve_dispatch_scope(
            project_id=_PROJECT, conversation_id=_CONVERSATION
        )
    assert scope == DispatchScope(_PROJECT, _EPISODE)


@pytest.mark.asyncio
async def test_an_episode_from_a_different_project_is_ignored_not_adopted():
    """If the two handles disagree about the project, the conversation's
    episode describes something else. Narrowing to it would deny every
    resolution; declining to narrow merely fails to help."""
    row = {"project_id": 12345, "context_type": "script", "context_id": _SCRIPT}
    with (
        patch("app.db.engine.fetch_one", AsyncMock(return_value=row)),
        patch(
            "app.services.ai.scope.scoped_script_gateway.episode_id_for_script",
            AsyncMock(return_value=_EPISODE),
        ),
    ):
        scope = await resolve_dispatch_scope(
            project_id=_PROJECT, conversation_id=_CONVERSATION
        )
    assert scope == DispatchScope(_PROJECT, None)


@pytest.mark.asyncio
async def test_child_inherits_both_project_and_episode_from_its_parent():
    """Rule 2: inheritance never widens. Copying only the project would let
    an episode-scoped agent launder itself into project-wide reach by
    delegating — an escalation that reads like a narrowing at the call
    site."""
    session = _Session(
        [_FakeResult(SimpleNamespace(project_id=_PROJECT, episode_id=_EPISODE))]
    )
    with patch.object(binding_mod, "read_scope", lambda: _Ctx(session)):
        scope = await resolve_dispatch_scope(parent_run_id=str(_PARENT_RUN))
    assert scope == DispatchScope(_PROJECT, _EPISODE)


@pytest.mark.asyncio
async def test_a_child_cannot_shed_its_parents_episode_via_cross_episode_read():
    """``cross_episode_read`` widens a run at ITS OWN dispatch, not a run it
    inherits. If the capability applied to inheritance, a narrowly-scoped
    agent could delegate to a broadly-capable peer and read the whole
    project through it."""
    session = _Session(
        [_FakeResult(SimpleNamespace(project_id=_PROJECT, episode_id=_EPISODE))]
    )
    with patch.object(binding_mod, "read_scope", lambda: _Ctx(session)):
        scope = await resolve_dispatch_scope(
            parent_run_id=str(_PARENT_RUN), agent=_agent(cross_episode_read=True)
        )
    assert scope.episode_id == _EPISODE


@pytest.mark.asyncio
async def test_missing_parent_run_yields_no_scope_rather_than_a_guess():
    session = _Session([_FakeResult(None)])
    with patch.object(binding_mod, "read_scope", lambda: _Ctx(session)):
        assert await resolve_dispatch_scope(parent_run_id="123") == DispatchScope()


@pytest.mark.asyncio
async def test_conversation_binds_its_project():
    row = {"project_id": _PROJECT, "context_type": "issue", "context_id": 1}
    with patch("app.db.engine.fetch_one", AsyncMock(return_value=row)):
        scope = await resolve_dispatch_scope(conversation_id=_CONVERSATION)
    assert scope.project_id == _PROJECT
    # An 'issue' context resolves to no episode — declining to narrow is
    # always safe, guessing is not.
    assert scope.episode_id is None


@pytest.mark.asyncio
async def test_conversation_with_a_script_context_also_binds_the_episode():
    row = {"project_id": _PROJECT, "context_type": "script", "context_id": _SCRIPT}
    with (
        patch("app.db.engine.fetch_one", AsyncMock(return_value=row)),
        patch(
            "app.services.ai.scope.scoped_script_gateway.episode_id_for_script",
            AsyncMock(return_value=_EPISODE),
        ),
    ):
        scope = await resolve_dispatch_scope(conversation_id=_CONVERSATION)
    assert scope == DispatchScope(_PROJECT, _EPISODE)


@pytest.mark.asyncio
async def test_cross_episode_read_suppresses_the_episode_narrowing():
    """A1's capability gets its first consumer: an agent the operator has
    explicitly allowed to work across episodes must not be pinned to one.
    It widens to project scope — never past it."""
    row = {"project_id": _PROJECT, "context_type": "script", "context_id": _SCRIPT}
    with (
        patch("app.db.engine.fetch_one", AsyncMock(return_value=row)),
        patch(
            "app.services.ai.scope.scoped_script_gateway.episode_id_for_script",
            AsyncMock(return_value=_EPISODE),
        ),
    ):
        scope = await resolve_dispatch_scope(
            conversation_id=_CONVERSATION, agent=_agent(cross_episode_read=True)
        )
    assert scope == DispatchScope(_PROJECT, None)


@pytest.mark.asyncio
async def test_conversation_without_a_project_stays_unbound():
    row = {"project_id": None, "context_type": None, "context_id": None}
    with patch("app.db.engine.fetch_one", AsyncMock(return_value=row)):
        assert await resolve_dispatch_scope(conversation_id=1) == DispatchScope()


@pytest.mark.asyncio
async def test_a_lookup_failure_degrades_to_unbound_instead_of_raising():
    """Binding is best-effort: a DB hiccup costs the run its screenwriting
    tools, it must never take down the dispatch."""
    with patch("app.db.engine.fetch_one", AsyncMock(side_effect=RuntimeError("boom"))):
        assert await resolve_dispatch_scope(conversation_id=1) == DispatchScope()


# ====================================================================== #
# The binding actually produces a BOUND scope — and doesn't when the
# user can't read the project it names (A2's Critical re-check).
# ====================================================================== #


@pytest.mark.asyncio
async def test_a_bound_project_yields_a_bound_scope():
    session = _Session(
        [
            _FakeResult(
                SimpleNamespace(
                    user_id=_USER_ID,
                    project_id=_PROJECT,
                    team_id=None,
                    episode_id=_EPISODE,
                )
            )
        ]
    )
    with (
        patch.object(scope_mod, "read_scope", lambda: _Ctx(session)),
        patch.object(scope_mod, "_user_can_read_project", AsyncMock(return_value=True)),
    ):
        scope = await scope_for_run(str(_PARENT_RUN))
    assert scope is not None and scope.is_bound()
    assert scope.episode_id == _EPISODE


@pytest.mark.asyncio
async def test_binding_a_project_the_user_cannot_read_yields_no_scope():
    """The prerequisite the A2 review added and A4 must respect: whatever a
    dispatch path stamps is re-verified against the run's own user. A wrong
    project produces NO scope, not a scope over that project."""
    session = _Session(
        [
            _FakeResult(
                SimpleNamespace(
                    user_id=_USER_ID,
                    project_id=_PROJECT,
                    team_id=None,
                    episode_id=None,
                )
            )
        ]
    )
    with (
        patch.object(scope_mod, "read_scope", lambda: _Ctx(session)),
        patch.object(
            scope_mod, "_user_can_read_project", AsyncMock(return_value=False)
        ),
    ):
        assert await scope_for_run(str(_PARENT_RUN)) is None


# ====================================================================== #
# Wiring — each dispatch site really uses it.
# ====================================================================== #

# Files that construct a RunRecorder WITHOUT deriving a scope, each with the
# reason it is acceptable. A4 review (Important 3) replaced a hardcoded list
# of the four paths A4 happened to fix — which structurally could not notice
# a fifth — with the inverse: enumerate every RunRecorder construction in the
# tree and require each one to either derive a scope or appear here.
#
# The shared reason for the AI-utility services below is twofold, and BOTH
# halves have to hold: they run fixed internal prompts with no project
# context to derive from, AND they build ``AgentRunner`` with no hooks, so
# ``_dispatch_screenwriting`` refuses these tools outright regardless of what
# scope a run carries. If one of them ever gains a hook registry, its
# exemption must be re-argued, not inherited.
SCOPE_EXEMPT_RUN_RECORDER_SITES: dict[str, str] = {
    "services/ai/caption/caption_service.py": (
        "internal captioning run; fixed prompt, no project context, hookless runner"
    ),
    "services/ai/classify/classify_service.py": (
        "internal classification run; fixed prompt, no project context, hookless runner"
    ),
    "services/ai/llm/llm_analysis_service.py": (
        "internal analysis run; fixed prompt, no project context, hookless runner"
    ),
    "services/ai/summarize/summarize_service.py": (
        "internal summarization run; fixed prompt, no project context, hookless runner"
    ),
    "services/ai/translate/translate_service.py": (
        "internal translation run; fixed prompt, no project context, hookless runner"
    ),
    "services/ai/visual/visual_analysis_service.py": (
        "internal image-analysis run; fixed prompt, no project context, hookless runner"
    ),
    "services/topics/topic_scorer.py": (
        "scheduled scoring run under SYSTEM_RUN_USER_ID; no human project "
        "context to bind and no user whose access could be re-verified, "
        "hookless runner"
    ),
    "services/ai/tools/forced_finish_declaration.py": (
        "not a dispatch: a follow-up bookkeeping row appended after a turn "
        "that already ran, recording the agent's outcome declaration. It "
        "mirrors the originating session's team/project columns for the Runs "
        "panel's grouping and runs no tools of its own"
    ),
}


def _files_constructing_a_run_recorder() -> list[str]:
    """Every app file with a ``RunRecorder(...)`` CALL.

    AST rather than grep on purpose: ``agent_run_scope.py`` names
    ``RunRecorder(...)`` inside its module docstring, and a docstring is a
    string expression that no comment-stripping regex would drop. Matching
    ``ast.Call`` nodes counts constructions and nothing else.
    """
    import ast

    found: list[str] = []
    for path in sorted(_APP.rglob("*.py")):
        rel = path.relative_to(_APP).as_posix()
        if rel == "services/ai/runner/run_recorder.py":
            continue  # the definition itself
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeDecodeError):  # pragma: no cover
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = getattr(func, "id", None) or getattr(func, "attr", None)
            if name == "RunRecorder":
                found.append(rel)
                break
    return found


def test_the_exemption_list_has_no_stale_entries():
    """An exemption for a file that no longer builds a RunRecorder is dead
    weight that makes the list look more considered than it is."""
    constructing = set(_files_constructing_a_run_recorder())
    stale = set(SCOPE_EXEMPT_RUN_RECORDER_SITES) - constructing
    assert not stale, f"exemptions for files that no longer dispatch: {sorted(stale)}"


def test_every_run_recorder_site_derives_a_scope_or_is_exempt():
    """The guard that can catch a path A4 never looked at.

    A behavioural test of ``resolve_dispatch_scope`` cannot catch a call
    site that never calls it — that is precisely how the primary chat route
    (``ai_library_chat_service.py``) was missed while four other paths were
    fixed. Enumerate instead of enumerating-what-we-remember.
    """
    offenders: list[str] = []
    for rel in _files_constructing_a_run_recorder():
        if rel in SCOPE_EXEMPT_RUN_RECORDER_SITES:
            continue
        text = (_APP / rel).read_text(encoding="utf-8")
        if "resolve_dispatch_scope" not in text:
            offenders.append(rel)

    assert not offenders, (
        "These files construct a RunRecorder without deriving a scope:\n  "
        + "\n  ".join(offenders)
        + "\n\nRoute the project/episode through "
        "app/services/ai/scope/scope_binding.resolve_dispatch_scope, or add "
        "an entry to SCOPE_EXEMPT_RUN_RECORDER_SITES in this test with the "
        "reason it needs no scope."
    )


@pytest.mark.parametrize(
    "rel_path",
    [
        rel
        for rel in _files_constructing_a_run_recorder()
        if rel not in SCOPE_EXEMPT_RUN_RECORDER_SITES
    ],
)
def test_no_dispatch_site_hardcodes_an_unbound_project(rel_path):
    """The original bug in its literal source form, kept alongside the
    enumerating guard above: a file could import ``resolve_dispatch_scope``
    for one recorder and still hardcode ``project_id=None`` for another."""
    text = (_APP / rel_path).read_text(encoding="utf-8")
    assert not re.search(r"^\s*project_id=None,\s*$", text, re.MULTILINE), (
        f"{rel_path} still passes a hardcoded project_id=None into "
        "RunRecorder — route it through resolve_dispatch_scope instead."
    )


@pytest.mark.asyncio
async def test_conversation_summon_stamps_the_derived_scope_onto_the_run():
    """End to end on the main @-mention summon path: the project derived
    from the conversation reaches ``RunRecorder``'s kwargs."""
    mod = "app.services.chat.conversation_agent_turn"
    agent_id = uuid4()

    agent = {
        "id": str(agent_id),
        "slug": "script_ai",
        "model": "qwen-max",
        "capability_profile": {"chat": {"enabled": True, "allowed_team_ids": []}},
    }
    repo = MagicMock()
    repo.get_by_slug = AsyncMock(return_value=agent)
    conv_repo = MagicMock()
    conv_repo.recent_messages = AsyncMock(
        return_value=[{"sender_type": "user", "type": "text", "body": {"text": "hi"}}]
    )

    runner = MagicMock()
    runner.resource_fetch_handler = None

    async def _run_turn(composed, *, user_messages, recorder):
        return {"content": "ok"}

    runner.run_turn = _run_turn
    stack = MagicMock(runner=runner, graph_facts=[], user_context=None)

    composed = MagicMock(agent_id=agent_id, model="qwen-max", tools=[])
    composed.model_copy = lambda **kw: composed
    composer = MagicMock()
    composer.compose = AsyncMock(return_value=composed)

    recorder_cm = MagicMock()
    recorder_cm.__aenter__ = AsyncMock(return_value=MagicMock())
    recorder_cm.__aexit__ = AsyncMock(return_value=False)
    recorder_cls = MagicMock(return_value=recorder_cm)

    from app.services.ai.permissions.agent_chat_caps import ChatCaps

    with (
        patch(f"{mod}.get_agent_repository", return_value=repo),
        patch(
            f"{mod}.agent_chat_caps",
            return_value=ChatCaps(
                enabled=True,
                read_team_resources=False,
                auto_broadcast=False,
                allowed_team_ids=(),
            ),
        ),
        patch(f"{mod}.get_conversation_repository", return_value=conv_repo),
        patch(f"{mod}.get_skill_repository", return_value=MagicMock()),
        patch(f"{mod}.build_agent_runner_stack", AsyncMock(return_value=stack)),
        patch(f"{mod}.PromptComposer", MagicMock(return_value=composer)),
        patch(f"{mod}.RunRecorder", recorder_cls),
        patch(f"{mod}.provider_key_for_model", return_value="qwen"),
        patch(
            f"{mod}.resolve_dispatch_scope",
            AsyncMock(return_value=DispatchScope(_PROJECT, _EPISODE)),
        ),
    ):
        from app.services.chat.conversation_agent_turn import (
            run_conversation_agent_turn,
        )

        await run_conversation_agent_turn(
            agent_slug="script_ai",
            summoner_user_id=_USER_ID,
            conversation={"id": _CONVERSATION, "scope_id": 7},
        )

    kwargs = recorder_cls.call_args.kwargs
    assert kwargs["project_id"] == _PROJECT
    assert kwargs["episode_id"] == _EPISODE
