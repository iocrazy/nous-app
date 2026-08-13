"""ScriptAIService must compose with the caller's agent override.

The script editor's AI actions (outline / expand / branch / convert) all
funnel through ``_run_agent``, which composed against the pristine system
preset — and then derived the adapter from ``composed.model``. So unlike
1:1 chat (where the adapter side happened to honour the override), this
path ignored it end to end: a user who set a different model for
``script_ai`` in Settings got the preset's model, persona and
instructions everywhere in the script editor.

Only the user layer is resolved here — not as a leak guard, but because
this is a single-user writing surface that team context was never wired
into (1:1 chat, by contrast, resolves user ?? team). The user id also
reaches the repo as a real UUID regardless of what the caller passed:
``get_override`` swallows its errors, so a str/UUID mismatch would fall
back to the base agent without a word.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

_AGENT_ID = uuid4()
_USER_ID = uuid4()
_BASE_MODEL = "doubao-seed-2-0-lite-260428"
_OVERRIDE_MODEL = "deepseek-v4-pro"


class _OverrideAwareAgentRepo:
    """Agent repo double honouring the per-user override layer."""

    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []

    async def get_by_slug(
        self,
        slug: str,
        *,
        override_user_id: Any = None,
        override_team_id: Any = None,
    ) -> Dict[str, Any]:
        self.calls.append({"user": override_user_id, "team": override_team_id})
        row = {
            "id": str(_AGENT_ID),
            "slug": slug,
            "model": _BASE_MODEL,
            "is_system_preset": True,
            "agent_md": "base instructions",
            "temperature": 0.7,
            "max_tokens": 4096,
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


class _RunRecorderCM:
    def __init__(self) -> None:
        self.recorder = MagicMock()
        self.recorder.set_summaries = MagicMock()

    async def __aenter__(self) -> MagicMock:
        return self.recorder

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return False


async def _run_once(user_id: Optional[Any]) -> tuple[Any, _OverrideAwareAgentRepo]:
    """Drive one ``_run_agent`` turn; return (composed, agent_repo)."""
    from app.services.ai.runner import agent_runner as ar_module
    from app.services.storyboard.script import script_ai_service as svc_module

    agent_repo = _OverrideAwareAgentRepo()
    run_turn = AsyncMock(return_value={"content": "written"})

    scope = MagicMock()
    scope.as_recorder_kwargs = MagicMock(return_value={})

    with (
        patch.object(svc_module, "get_agent_repository", return_value=agent_repo),
        patch.object(
            svc_module, "get_skill_repository", return_value=_EmptySkillRepo()
        ),
        patch.object(
            svc_module, "resolve_dispatch_scope", AsyncMock(return_value=scope)
        ),
        patch.object(
            svc_module, "RunRecorder", side_effect=lambda **kw: _RunRecorderCM()
        ),
        patch(
            "app.services.ai.providers.ai_provider_helpers.resolve_db_adapter",
            new=AsyncMock(return_value=object()),
        ),
        patch.object(ar_module.AgentRunner, "run_turn", run_turn),
    ):
        svc = svc_module.ScriptAIService(user_id=user_id)
        out = await svc._run_agent("Task: outline.", "a detective in a snowstorm")

    assert out == "written"
    composed = run_turn.await_args.args[0]
    return composed, agent_repo


@pytest.mark.asyncio
async def test_run_agent_composes_with_user_override() -> None:
    composed, agent_repo = await _run_once(_USER_ID)

    assert composed.model == _OVERRIDE_MODEL, (
        "script editor composed with the system preset's model — the "
        "user's agent override never reached PromptComposer"
    )
    assert agent_repo.calls == [
        {"user": _USER_ID, "team": None}
    ], "override resolution must be user-layer only on this path"


@pytest.mark.asyncio
async def test_run_agent_coerces_str_user_id_before_override_lookup() -> None:
    """The production callers pass a str — it must reach the repo as a UUID.

    ``AgentRepository.get_by_slug`` declares ``Optional[UUID]`` and
    ``get_override`` returns None on any error, so handing it a str is the
    kind of mismatch that degrades to the base agent silently rather than
    raising. Asserting the type (not just the resolved model) is what makes
    this fail if the raw value is ever passed through again.
    """
    composed, agent_repo = await _run_once(str(_USER_ID))

    assert composed.model == _OVERRIDE_MODEL
    assert agent_repo.calls == [{"user": _USER_ID, "team": None}]
    assert isinstance(agent_repo.calls[0]["user"], UUID), (
        "override lookup got the caller's raw str instead of a UUID — "
        f"got {type(agent_repo.calls[0]['user']).__name__}"
    )


@pytest.mark.asyncio
async def test_run_agent_without_user_falls_back_to_base_agent() -> None:
    """No user_id (smoke tests, internal callers) → pristine system agent."""
    composed, agent_repo = await _run_once(None)

    assert composed.model == _BASE_MODEL
    assert agent_repo.calls == [{"user": None, "team": None}]
