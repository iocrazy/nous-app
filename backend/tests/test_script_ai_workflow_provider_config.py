"""Regression tests: the 4 script-AI DBOS steps must resolve the DB-governed
provider config (``resolve_script_provider_config``) and pass it into
``ScriptAIService`` — not construct the service bare.

Background (confirmed via prod worker logs + code): the 4 script-AI
generation workflows (outline / expand / branches / convert-to-storyboard)
built ``ScriptAIService()`` / ``ScriptAIService(user_id=user_id)`` with no
``provider_key`` / ``provider_config``. With neither supplied, the service's
adapter factory falls back to ``get_adapter(model, settings)`` which, for a
``doubao-*`` model, reads ``settings.DOUBAO_API_KEY`` — the stale ENV key,
401-unauthorized on prod. The working key lives in the DB (``mediahub_models``
/ platform provider config) and is only reachable via
``resolve_script_provider_config(user_id)`` — the same helper
``topics_router.generate_script`` already uses correctly.

These tests patch ``resolve_script_provider_config`` (so no real DB call
happens) and ``ScriptAIService`` (recording constructor kwargs) to assert
each of the 4 AI steps builds the service with the resolved
``agent_slug`` / ``provider_key`` / ``provider_config`` — parity with
``topics_router.generate_script``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

_USER = "11111111-1111-1111-1111-111111111111"
_SCRIPT = "9000000000000000001"
_CHAPTER = "9000000000000000002"

# Sentinel resolver return — mirrors resolve_script_provider_config's
# (provider_key, provider_config, model, agent_slug) tuple shape.
_RESOLVED = (
    "doubao",
    {"api_key": "k", "base_url": "u", "model": "m"},
    "m",
    "script_ai",
)


def _resolver_mock():
    return AsyncMock(return_value=_RESOLVED)


def _service_mock(**method_returns: object) -> MagicMock:
    """A MagicMock standing in for the ScriptAIService class: calling it
    returns an AsyncMock instance whose methods resolve to the given
    per-method return values."""
    instance = MagicMock()
    for name, value in method_returns.items():
        setattr(instance, name, AsyncMock(return_value=value))
    cls = MagicMock(return_value=instance)
    return cls


# ---------------------------------------------------------------------------
# expand-chapter
# ---------------------------------------------------------------------------


async def test_expand_step_resolves_and_passes_provider_config():
    from app.workflows import script_ai_workflows as m

    svc_cls = _service_mock(expand_chapter="<p>content</p>")

    with (
        patch(
            "app.services.ai.providers.ai_provider_helpers.resolve_script_provider_config",
            _resolver_mock(),
        ) as resolver,
        patch(
            "app.services.storyboard.script.script_ai_service.ScriptAIService",
            svc_cls,
        ),
    ):
        html = await m.script_ai_expand_step("Act I", "summary", None, _USER)

    assert html == "<p>content</p>"
    resolver.assert_awaited_once_with(_USER)
    svc_cls.assert_called_once_with(
        user_id=_USER,
        agent_slug="script_ai",
        provider_key="doubao",
        provider_config={"api_key": "k", "base_url": "u", "model": "m"},
    )


# ---------------------------------------------------------------------------
# create-branches
# ---------------------------------------------------------------------------


async def test_branches_step_resolves_and_passes_provider_config():
    from app.workflows import script_ai_workflows as m

    branches = [{"title": "Fight", "summary": "s", "branch_label": "Fight"}]
    svc_cls = _service_mock(create_branches=branches)

    with (
        patch(
            "app.services.ai.providers.ai_provider_helpers.resolve_script_provider_config",
            _resolver_mock(),
        ) as resolver,
        patch(
            "app.services.storyboard.script.script_ai_service.ScriptAIService",
            svc_cls,
        ),
    ):
        result = await m.script_ai_branches_step(
            "Act I", "summary", 1, "choice", None, _USER
        )

    assert result == branches
    resolver.assert_awaited_once_with(_USER)
    svc_cls.assert_called_once_with(
        user_id=_USER,
        agent_slug="script_ai",
        provider_key="doubao",
        provider_config={"api_key": "k", "base_url": "u", "model": "m"},
    )


# ---------------------------------------------------------------------------
# convert-to-storyboard
# ---------------------------------------------------------------------------


async def test_scenes_step_resolves_and_passes_provider_config():
    from app.services.storyboard.script.script_service import ScriptService
    from app.workflows import script_ai_workflows as m

    scenes = [{"scene_number": 1, "description": "d", "camera_notes": "c"}]
    svc_cls = _service_mock(split_chapter_to_scenes=scenes)

    chapter_repo = MagicMock()
    chapter_repo.get_by_id = AsyncMock(
        return_value={"title": "Act I", "summary": "s", "content": None}
    )
    project_repo = MagicMock()
    project_repo.get_by_id = AsyncMock(return_value=None)

    def _init(self):
        self.chapter_repo = chapter_repo
        self.project_repo = project_repo

    with (
        patch(
            "app.services.ai.providers.ai_provider_helpers.resolve_script_provider_config",
            _resolver_mock(),
        ) as resolver,
        patch(
            "app.services.storyboard.script.script_ai_service.ScriptAIService",
            svc_cls,
        ),
        patch.object(ScriptService, "__init__", _init),
    ):
        result = await m.script_ai_scenes_step(_SCRIPT, _CHAPTER, _USER)

    assert result == scenes
    resolver.assert_awaited_once_with(_USER)
    svc_cls.assert_called_once_with(
        user_id=_USER,
        agent_slug="script_ai",
        provider_key="doubao",
        provider_config={"api_key": "k", "base_url": "u", "model": "m"},
    )


# ---------------------------------------------------------------------------
# generate-outline
# ---------------------------------------------------------------------------


async def test_outline_step_resolves_and_passes_provider_config():
    from app.workflows import script_outline as m

    chapters = [{"title": "Ch 1", "summary": "s"}]
    svc_cls = _service_mock(generate_outline=chapters)

    with (
        patch(
            "app.services.ai.providers.ai_provider_helpers.resolve_script_provider_config",
            _resolver_mock(),
        ) as resolver,
        patch(
            "app.services.storyboard.script.script_ai_service.ScriptAIService",
            svc_cls,
        ),
    ):
        result = await m.generate_outline_chapters("premise", 3, None, _USER)

    assert result == chapters
    resolver.assert_awaited_once_with(_USER)
    svc_cls.assert_called_once_with(
        user_id=_USER,
        agent_slug="script_ai",
        provider_key="doubao",
        provider_config={"api_key": "k", "base_url": "u", "model": "m"},
    )


async def test_outline_step_defaults_user_id_to_none():
    """Old in-flight workflows replayed without user_id must still work —
    the param is optional so DBOS's frozen input on old rows doesn't break."""
    from app.workflows import script_outline as m

    svc_cls = _service_mock(generate_outline=[])

    with (
        patch(
            "app.services.ai.providers.ai_provider_helpers.resolve_script_provider_config",
            _resolver_mock(),
        ) as resolver,
        patch(
            "app.services.storyboard.script.script_ai_service.ScriptAIService",
            svc_cls,
        ),
    ):
        await m.generate_outline_chapters("premise", 3, None)

    resolver.assert_awaited_once_with(None)
    svc_cls.assert_called_once_with(
        user_id=None,
        agent_slug="script_ai",
        provider_key="doubao",
        provider_config={"api_key": "k", "base_url": "u", "model": "m"},
    )
