"""Pin tests for convert-to-scenes (Phase B Task 6).

Three contracts, each a past production bug if broken:

1. wf_id threading (#1017): the endpoint must call ``mgr.create(...,
   dbos_workflow_id=wf_id)`` and ``start_workflow_routed(..., workflow_id=
   wf_id)`` with THE SAME uuid — otherwise the INSERT into task_tracking
   (dbos_workflow_id NOT NULL) 500s, or the lifecycle mirror trigger can never
   associate the two rows and the task hangs QUEUED forever. Same rule pinned
   for the other 4 script-AI endpoints in test_script_ai_endpoint_dispatch.py.

2. DB-governed provider config (#1025/#1030): the workflow step must resolve
   ``resolve_script_provider_config(user_id)`` and pass provider_key /
   provider_config into ScriptAIService — a bare ScriptAIService() falls back to
   the stale ENV key (401 on prod). Mirrors
   test_script_ai_workflow_provider_config.py.

3. Guard + belongs-check + flat envelope: the endpoint depends on the shared
   ``verify_script_access`` and 404s when the chapter doesn't belong to the
   script (str-coerce compare, #1006). The dispatch envelope is flat
   ``{"success": True, "task_id": ...}`` (#1019).

Plus el_ id generation / element-type coercion / genesis ledger persistence,
which are this task's own logic (scene_ops.ELEMENT_TYPES coercion + actor).
"""

from __future__ import annotations

import importlib
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.deps import AuthContext

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

# See test_script_ai_endpoint_dispatch.py: app.api.__init__ rebinds the
# `script_ai_router` name to the APIRouter, so reach the real module via
# importlib to call the endpoint functions directly.
script_ai_router = importlib.import_module("app.api.script_ai_router")

_USER = "11111111-1111-1111-1111-111111111111"
_SCRIPT = "9000000000000000001"
_CHAPTER = "9000000000000000002"

# Sentinel resolver return — (provider_key, provider_config, model, agent_slug).
_RESOLVED = (
    "doubao",
    {"api_key": "k", "base_url": "u", "model": "m"},
    "m",
    "script_ai",
)


def _auth() -> AuthContext:
    return AuthContext(user_id=_USER, auth_type="jwt")


# ---------------------------------------------------------------------------
# Endpoint: wf_id threading + guard + belongs-check + flat envelope
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_task_manager(monkeypatch):
    mgr = AsyncMock()
    mgr.create = AsyncMock(return_value=str(uuid.uuid4()))
    monkeypatch.setattr(script_ai_router, "get_task_manager", lambda: mgr)
    return mgr


@pytest.fixture
def mock_verify_access(monkeypatch):
    mock = AsyncMock(return_value=None)
    monkeypatch.setattr(script_ai_router, "verify_script_access", mock)
    return mock


def _patch_chapter(script_id):
    """Patch ScriptService so chapter_repo.get_by_id returns a chapter owned by
    ``script_id`` (script_id read back as a native int, the prod shape)."""
    from app.services.storyboard.script.script_service import ScriptService

    chapter_repo = MagicMock()
    chapter_repo.get_by_id = AsyncMock(
        return_value={"id": int(_CHAPTER), "script_id": int(script_id)}
    )

    def _init(self):
        self.chapter_repo = chapter_repo

    return patch.object(ScriptService, "__init__", _init), chapter_repo


async def test_convert_to_scenes_threads_shared_wf_id(
    monkeypatch, mock_task_manager, mock_verify_access
):
    dispatch = AsyncMock(return_value={"mode": "dbos"})
    monkeypatch.setattr(
        "app.services.infra.dbos_orchestrator.start_workflow_routed", dispatch
    )
    chapter_patch, _ = _patch_chapter(_SCRIPT)

    with chapter_patch:
        result = await script_ai_router.convert_to_scenes(_auth(), _SCRIPT, _CHAPTER)

    # Flat envelope (#1019).
    assert result == {"success": True, "task_id": mock_task_manager.create.return_value}

    # Guard was consulted (dependency declared + awaited).
    mock_verify_access.assert_awaited_once()

    mock_task_manager.create.assert_awaited_once()
    dispatch.assert_awaited_once()

    # Shared, valid uuid on both sides.
    wf_id = mock_task_manager.create.call_args.kwargs.get("dbos_workflow_id")
    dispatched_id = dispatch.call_args.kwargs.get("workflow_id")
    assert wf_id is not None
    uuid.UUID(wf_id)
    assert wf_id == dispatched_id, (
        "task_tracking.dbos_workflow_id must equal the dispatched DBOS "
        "workflow_id or the lifecycle mirror can never join the rows"
    )
    # task_type + dispatched workflow name are the Task-6 contract.
    assert mock_task_manager.create.call_args.kwargs["task_type"] == (
        "script_scene_convert"
    )
    assert dispatch.call_args.args[0] == "script_scene_convert"
    assert dispatch.call_args.kwargs["dbos_workflow_kwargs"] == {
        "script_id": _SCRIPT,
        "chapter_id": _CHAPTER,
        "user_id": _USER,
    }


async def test_convert_to_scenes_404_when_chapter_not_in_script(
    monkeypatch, mock_task_manager, mock_verify_access
):
    """A chapter belonging to a DIFFERENT script must 404 — never dispatch."""
    from fastapi import HTTPException

    dispatch = AsyncMock(return_value={"mode": "dbos"})
    monkeypatch.setattr(
        "app.services.infra.dbos_orchestrator.start_workflow_routed", dispatch
    )
    # Chapter reports a different owning script id.
    chapter_patch, _ = _patch_chapter("9000000000000000999")

    with chapter_patch:
        with pytest.raises(HTTPException) as exc_info:
            await script_ai_router.convert_to_scenes(_auth(), _SCRIPT, _CHAPTER)

    assert exc_info.value.status_code == 404
    mock_task_manager.create.assert_not_awaited()
    dispatch.assert_not_awaited()


async def test_convert_to_scenes_404_when_chapter_missing(
    monkeypatch, mock_task_manager, mock_verify_access
):
    from fastapi import HTTPException

    from app.services.storyboard.script.script_service import ScriptService

    dispatch = AsyncMock(return_value={"mode": "dbos"})
    monkeypatch.setattr(
        "app.services.infra.dbos_orchestrator.start_workflow_routed", dispatch
    )
    chapter_repo = MagicMock()
    chapter_repo.get_by_id = AsyncMock(return_value=None)

    def _init(self):
        self.chapter_repo = chapter_repo

    with patch.object(ScriptService, "__init__", _init):
        with pytest.raises(HTTPException) as exc_info:
            await script_ai_router.convert_to_scenes(_auth(), _SCRIPT, _CHAPTER)

    assert exc_info.value.status_code == 404
    dispatch.assert_not_awaited()


# ---------------------------------------------------------------------------
# Workflow step 1: provider config resolution
# ---------------------------------------------------------------------------


async def test_convert_step_resolves_and_passes_provider_config():
    from app.services.storyboard.script.script_service import ScriptService
    from app.workflows import script_scene_convert as m

    scenes = [
        {
            "heading_int_ext": "INT",
            "location_text": "Office",
            "time_of_day": "DAY",
            "elements": [{"type": "action", "text": "She enters."}],
        }
    ]
    instance = MagicMock()
    instance.split_chapter_to_screenplay_scenes = AsyncMock(return_value=scenes)
    svc_cls = MagicMock(return_value=instance)

    chapter_repo = MagicMock()
    chapter_repo.get_by_id = AsyncMock(
        return_value={"title": "Act I", "summary": "s", "content": "She enters."}
    )

    def _init(self):
        self.chapter_repo = chapter_repo

    with (
        patch(
            "app.services.ai.providers.ai_provider_helpers."
            "resolve_script_provider_config",
            AsyncMock(return_value=_RESOLVED),
        ) as resolver,
        patch(
            "app.services.storyboard.script.script_ai_service.ScriptAIService",
            svc_cls,
        ),
        patch.object(ScriptService, "__init__", _init),
    ):
        result = await m.convert_chapter_to_scenes(_CHAPTER, _USER)

    assert result == scenes
    resolver.assert_awaited_once_with(_USER)
    svc_cls.assert_called_once_with(
        user_id=_USER,
        agent_slug="script_ai",
        provider_key="doubao",
        provider_config={"api_key": "k", "base_url": "u", "model": "m"},
    )


async def test_convert_step_raises_when_chapter_missing():
    from app.services.storyboard.script.script_service import ScriptService
    from app.workflows import script_scene_convert as m

    chapter_repo = MagicMock()
    chapter_repo.get_by_id = AsyncMock(return_value=None)

    def _init(self):
        self.chapter_repo = chapter_repo

    with patch.object(ScriptService, "__init__", _init):
        with pytest.raises(ValueError):
            await m.convert_chapter_to_scenes(_CHAPTER, _USER)


# ---------------------------------------------------------------------------
# Workflow step 2: el_ id generation, type coercion, genesis ledger
# ---------------------------------------------------------------------------


async def test_persist_scenes_generates_ids_coerces_types_and_ledger():
    from app.workflows import script_scene_convert as m

    scenes = [
        {
            "heading_int_ext": "EXT",
            "location_text": "Rooftop",
            "time_of_day": "NIGHT",
            "elements": [
                {"type": "action", "text": "Wind howls."},
                {"type": "character", "text": "ANNA"},
                {"type": "dialogue", "text": "We're too late."},
                {"type": "bogus", "text": "coerce me to action"},
                {"type": "action", "text": "   "},  # dropped: blank text
            ],
        }
    ]

    captured = {}

    async def _create_with_content(data, elements, actor):
        captured["data"] = data
        captured["elements"] = elements
        captured["actor"] = actor
        return {"id": 12345}

    repo = MagicMock()
    repo.create_with_content = AsyncMock(side_effect=_create_with_content)

    with patch(
        "app.repositories.script_scene_repository.ScriptSceneRepository",
        MagicMock(return_value=repo),
    ):
        result = await m.persist_scenes(_SCRIPT, _CHAPTER, scenes)

    assert result["status"] == "success"
    assert result["scene_count"] == 1
    assert result["scene_ids"] == ["12345"]

    els = captured["elements"]
    # Blank-text element dropped; 4 remain.
    assert len(els) == 4
    # Every element gets a fresh el_ id.
    assert all(e["id"].startswith("el_") for e in els)
    assert len({e["id"] for e in els}) == 4
    # Unknown type coerced to action; valid types preserved.
    assert [e["type"] for e in els] == ["action", "character", "dialogue", "action"]
    assert captured["actor"] == "copilot"
    assert captured["data"]["script_id"] == _SCRIPT
    assert captured["data"]["chapter_id"] == _CHAPTER
    assert captured["data"]["heading_int_ext"] == "EXT"


async def test_persist_scenes_defaults_bad_heading_to_int():
    from app.workflows import script_scene_convert as m

    scenes = [
        {
            "heading_int_ext": "somewhere",
            "elements": [{"type": "action", "text": "A beat."}],
        }
    ]
    captured = {}

    async def _create_with_content(data, elements, actor):
        captured["data"] = data
        return {"id": 1}

    repo = MagicMock()
    repo.create_with_content = AsyncMock(side_effect=_create_with_content)

    with patch(
        "app.repositories.script_scene_repository.ScriptSceneRepository",
        MagicMock(return_value=repo),
    ):
        await m.persist_scenes(_SCRIPT, _CHAPTER, scenes)

    assert captured["data"]["heading_int_ext"] == "INT"


async def test_persist_scenes_skips_scene_with_no_usable_elements():
    from app.workflows import script_scene_convert as m

    scenes = [{"heading_int_ext": "INT", "elements": [{"type": "action", "text": ""}]}]
    repo = MagicMock()
    repo.create_with_content = AsyncMock()

    with patch(
        "app.repositories.script_scene_repository.ScriptSceneRepository",
        MagicMock(return_value=repo),
    ):
        result = await m.persist_scenes(_SCRIPT, _CHAPTER, scenes)

    assert result["scene_count"] == 0
    repo.create_with_content.assert_not_awaited()
