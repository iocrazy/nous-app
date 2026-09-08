"""Engine path and direct-composer path must build the SAME ComposerInput.

The 2026-08-13 override bug: ``_run_session_turn_inner`` has two branches
that both build a ``ComposerInput`` — the ContextEngine branch (the only
one that runs in production, since ``agent_framework_init`` registers the
chat engine unconditionally) and the direct-``PromptComposer`` fallback
(the only one unit tests ever exercised). The fallback passed
``override_user_id`` / ``override_team_id`` / ``graph_facts`` /
``user_context`` / ``agent_memory_facts``; the engine branch silently
dropped all five, because ``ChatContextEngine.assemble``'s request
contract had no such keys. Result: per-user agent overrides and every
memory-recall injection were invisible to the composed prompt in
production while the suite stayed green.

So the guard here is *equivalence itself*, not any one field: the parity
test walks every dataclass field of ``ComposerInput``, so the next field
added to one branch and forgotten in the other fails immediately.
"""

from __future__ import annotations

import sys
from dataclasses import fields
from types import ModuleType, SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from app.agent_framework import ContextEngineRegistry
from app.schemas.ai_library import ComposedSystemPrompt
from app.services.ai.chat.chat_context_engine import ChatContextEngine
from app.services.ai.prompts.prompt_composer import ComposerInput, PromptComposer

# Values deliberately non-empty / non-None: with the dataclass defaults
# (``[]`` / ``None``) a dropped field would compare equal to a passed one
# and the parity assertion would pass vacuously.
_USER_ID = uuid4()
_TEAM_ID = 4242
_AGENT_ID = uuid4()
_SESSION_ID = "1900000000000000001"
_GRAPH_FACTS = ["fact: user prefers terse replies"]
_USER_CONTEXT = "- writes screenplays\n- dislikes filler"
_AGENT_MEMORY_FACTS = ["remembered: project Snowfall"]

_BASE_MODEL = "doubao-seed-2-0-lite-260428"
_OVERRIDE_MODEL = "deepseek-v4-pro"


# ─── Doubles ──────────────────────────────────────────────────────────


class _FakeStore:
    """Minimal MessageStore stub (same shape as test_ai_library_chat)."""

    def __init__(self, session_row: Dict[str, Any]) -> None:
        self._session_row = session_row
        self.appended: List[Dict[str, Any]] = []

    async def get_session(self, *, session_id: Any) -> Optional[Dict[str, Any]]:
        return dict(self._session_row)

    async def get_messages(
        self, *, session_id: Any, limit: int = 200
    ) -> List[Dict[str, Any]]:
        # Non-empty history keeps the turn off the first-turn commitment
        # path, so both runs stay byte-identical.
        return [{"role": "user", "content": "earlier turn"}]

    async def append_user_message(
        self,
        *,
        session_id: Any,
        user_id: str,
        content: str,
        attachments: Any = None,
    ) -> Dict[str, Any]:
        row = {"id": str(uuid4()), "role": "user", "content": content}
        self.appended.append(row)
        return row

    async def latest_assistant_open_question(self, *, session_id: Any = None):
        return None  # phase 2a: no open typed question in this fake

    async def mark_question_answered(
        self, *, message_id: Any = None, value: Any = None, superseded: bool = False
    ) -> None:
        return None

    async def append_assistant_message(
        self,
        *,
        session_id: Any,
        agent_id: Optional[str],
        content: str,
        prompt_tokens: int,
        completion_tokens: int,
        metadata: dict,
    ) -> Dict[str, Any]:
        row = {"id": str(uuid4()), "role": "assistant", "content": content}
        self.appended.append(row)
        return row

    async def bump_counters(
        self, *, session_id: Any, add_tokens: int, add_messages: int
    ) -> None:
        return None


class _RunRecorderCM:
    def __init__(self, recorder: MagicMock) -> None:
        self._recorder = recorder

    async def __aenter__(self) -> MagicMock:
        return self._recorder

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return False


class _CapturingComposer:
    """Records the ComposerInput it was handed, returns a canned prompt."""

    def __init__(self) -> None:
        self.received: Optional[ComposerInput] = None

    async def compose(self, inp: ComposerInput) -> ComposedSystemPrompt:
        self.received = inp
        return ComposedSystemPrompt(
            agent_id=_AGENT_ID,
            agent_slug=inp.agent_slug,
            model=inp.model_override or _BASE_MODEL,
            temperature=0.7,
            max_tokens=4096,
            system_message="SYSTEM",
            tools=[],
            skill_manifest=[],
            cache_fingerprint="fp",
            prefix_fingerprint="fp",
            dynamic_fingerprint="dfp",
            recalled_memory_ids=[],
        )


class _OverrideAwareAgentRepo:
    """Agent repo double that honours the per-user override layer, the
    way ``AgentRepository._apply_overrides`` does for system presets."""

    def __init__(self) -> None:
        self.override_kwargs_seen: List[Dict[str, Any]] = []

    async def get_by_slug(
        self,
        slug: str,
        *,
        override_user_id: Any = None,
        override_team_id: Any = None,
    ) -> Dict[str, Any]:
        self.override_kwargs_seen.append(
            {"user": override_user_id, "team": override_team_id}
        )
        row = {
            "id": str(_AGENT_ID),
            "slug": slug,
            "model": _BASE_MODEL,
            "is_system_preset": True,
            "agent_md": "base instructions",
            "temperature": 0.7,
            "max_tokens": 4096,
            "fallback_models": [],
            "budget_per_run_cents": None,
        }
        if override_user_id is not None and str(override_user_id) == str(_USER_ID):
            row["model"] = _OVERRIDE_MODEL
        return row

    async def get_skill_ids(self, agent_id: UUID) -> List[UUID]:
        return []

    async def list_persistent(self) -> List[Dict[str, Any]]:
        return []


class _EmptySkillRepo:
    async def list_by_ids(self, ids: Any) -> List[Dict[str, Any]]:
        return []


# ─── Turn driver ──────────────────────────────────────────────────────


def _session_row() -> Dict[str, Any]:
    return {
        "id": _SESSION_ID,
        "user_id": str(_USER_ID),
        "agent_slug": "script_ai",
        "agent_id": str(_AGENT_ID),
        "total_tokens": 0,
        "message_count": 0,
        "team_id": _TEAM_ID,
        "project_id": None,
    }


async def _drive_turn(
    *,
    engine: Any,
    fallback_composer: Any,
    agent_repo: Any,
) -> MagicMock:
    """Run one chat turn. ``engine`` None → the direct-composer branch.

    Returns the runner mock so callers can read the ``composed`` that the
    turn actually handed to ``run_turn``.
    """
    from app.services.ai.chat.ai_library_chat_service import AILibraryChatService

    store = _FakeStore(_session_row())

    runner = MagicMock()
    runner.run_turn = AsyncMock(return_value={"content": "ok", "raw": {}})

    recorder = MagicMock()
    recorder.run_id = uuid4()
    recorder.prompt_tokens = 1
    recorder.completion_tokens = 1
    recorder.set_summaries = MagicMock()

    stack = MagicMock()
    stack.runner = runner
    stack.graph_facts = list(_GRAPH_FACTS)
    stack.user_context = _USER_CONTEXT
    stack.agent_memory_facts = list(_AGENT_MEMORY_FACTS)
    stack.primary_model = _BASE_MODEL
    stack.fallback_chain_active = False

    # The service late-imports ``app.main`` to reach the registry. Stub the
    # module so the branch is selected deterministically, without dragging
    # the whole FastAPI app (and its lifespan) into a unit test.
    fake_main = ModuleType("app.main")
    state = SimpleNamespace()
    if engine is not None:
        registry = ContextEngineRegistry()
        registry.register(engine)
        state.context_engines = registry
    fake_main.app = SimpleNamespace(state=state)  # type: ignore[attr-defined]

    with (
        patch.dict(sys.modules, {"app.main": fake_main}),
        patch(
            "app.services.ai.chat.ai_library_chat_service.get_agent_repository",
            return_value=agent_repo,
        ),
        patch(
            "app.services.ai.chat.ai_library_chat_service.get_skill_repository",
            return_value=_EmptySkillRepo(),
        ),
        patch(
            "app.services.ai.chat.ai_library_chat_service.build_agent_runner_stack",
            AsyncMock(return_value=stack),
        ),
        patch(
            "app.services.ai.chat.ai_library_chat_service.PromptComposer",
            return_value=fallback_composer,
        ),
        patch(
            "app.services.ai.chat.ai_library_chat_service.AgentRunner",
            return_value=runner,
        ),
        patch(
            "app.services.ai.chat.ai_library_chat_service.SkillToolService",
            return_value=MagicMock(),
        ),
        patch(
            "app.services.ai.chat.ai_library_chat_service.RunRecorder",
            side_effect=lambda **kw: _RunRecorderCM(recorder),
        ),
    ):
        svc = AILibraryChatService(store=store)
        await svc.chat(_SESSION_ID, user_id=_USER_ID, content="Hello")

    return runner


# ─── The parity guard ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_engine_and_direct_paths_build_identical_composer_input() -> None:
    """Every ComposerInput field must survive the engine path intact.

    Field-by-field over ``dataclasses.fields`` on purpose: a per-field
    assertion list would have to be maintained by the same person who
    just forgot to forward the field.
    """
    engine_composer = _CapturingComposer()
    direct_composer = _CapturingComposer()

    await _drive_turn(
        engine=ChatContextEngine(composer=engine_composer),
        fallback_composer=MagicMock(compose=AsyncMock()),
        agent_repo=_OverrideAwareAgentRepo(),
    )
    await _drive_turn(
        engine=None,
        fallback_composer=direct_composer,
        agent_repo=_OverrideAwareAgentRepo(),
    )

    via_engine = engine_composer.received
    via_direct = direct_composer.received
    assert via_engine is not None, "engine branch never reached the composer"
    assert via_direct is not None, "fallback branch never reached the composer"

    mismatched = {
        f.name: (getattr(via_engine, f.name), getattr(via_direct, f.name))
        for f in fields(ComposerInput)
        if getattr(via_engine, f.name) != getattr(via_direct, f.name)
    }
    assert not mismatched, (
        "engine path and direct-composer path disagree on ComposerInput "
        f"fields (engine, direct): {mismatched}"
    )

    # Sanity: the comparison above is only meaningful if the payload the
    # two branches agree on is actually populated.
    assert via_engine.override_user_id is not None
    assert via_engine.graph_facts == _GRAPH_FACTS
    assert via_engine.user_context == _USER_CONTEXT
    assert via_engine.agent_memory_facts == _AGENT_MEMORY_FACTS


# ─── End-to-end: a user-layer override reaches composed.model ─────────


@pytest.mark.asyncio
async def test_user_override_reaches_composed_model_via_engine() -> None:
    """The production path (engine registered) must compose with the
    caller's overridden model, not the system preset's base model."""
    agent_repo = _OverrideAwareAgentRepo()
    real_composer = PromptComposer(agent_repo, _EmptySkillRepo())

    runner = await _drive_turn(
        engine=ChatContextEngine(composer=real_composer),
        fallback_composer=MagicMock(compose=AsyncMock()),
        agent_repo=agent_repo,
    )

    composed = runner.run_turn.await_args.args[0]
    assert composed.model == _OVERRIDE_MODEL, (
        "engine path composed with the base model — per-user agent "
        "override never reached PromptComposer"
    )
