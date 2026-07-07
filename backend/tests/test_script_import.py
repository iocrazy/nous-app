"""Wiring + workflow tests for screenplay import (PR-I1 Task 2).

Contracts pinned here:

1. Endpoint wf_id threading + dispatch: ``import_screenplay`` creates the script
   (team-scoped), calls ``mgr.create(..., dbos_workflow_id=wf_id)`` and
   ``start_workflow_routed(..., workflow_id=wf_id)`` with THE SAME uuid (the
   #1017 task_tracking join rule), task_type ``script_import``, and returns the
   flat ``{success, script_id, task_id}`` envelope. Oversize content 422s
   before any dispatch.

2. Fountain persist: parsed ``heading_int_ext`` is written VERBATIM (``EST`` /
   ``INT/EXT`` survive — the deterministic parser is authoritative, unlike the
   LLM convert path that clamps to INT/EXT); elements get fresh ``el_`` ids;
   scenes attach to the script (``chapter_id=None``); actor is ``import``.

3. Mode routing: fountain → parse + persist_fountain_scenes; prose → reuse the
   ``script_scene_convert`` steps (create chapter → convert → persist_scenes);
   unknown mode raises (route-C).

4. Dispatch-bundle registration (#1055): the worker registers the workflow only
   via the bundle import, or the task hangs QUEUED forever.
"""

from __future__ import annotations

import importlib
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.deps import AuthContext

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

# app.api.__init__ rebinds the module name to the APIRouter, so reach the real
# module via importlib to call the endpoint function directly.
router_mod = importlib.import_module("app.api.script_import_scenes_router")

_USER = "11111111-1111-1111-1111-111111111111"
_SCRIPT_ID = 9000000000000000123
_FOUNTAIN = "INT. ROOM - DAY\n\nSARAH\nHello."


def _auth() -> AuthContext:
    return AuthContext(user_id=_USER, auth_type="jwt")


def _body(**over):
    from app.schemas.script import ScriptImportRequest

    data = {
        "name": "Imported Script",
        "project_id": 42,
        "mode": "fountain",
        "content": _FOUNTAIN,
    }
    data.update(over)
    return ScriptImportRequest(**data)


# --------------------------------------------------------------------------- #
# Endpoint wiring
# --------------------------------------------------------------------------- #


@pytest.fixture
def wired(monkeypatch):
    """Patch the endpoint's collaborators; return the mocks for assertions."""
    mgr = AsyncMock()
    mgr.create = AsyncMock(return_value=str(uuid.uuid4()))
    monkeypatch.setattr(router_mod, "get_task_manager", lambda: mgr)

    require_team = AsyncMock(return_value="team-1")
    monkeypatch.setattr(router_mod, "require_team_id", require_team)

    svc = MagicMock()
    svc.create_project = AsyncMock(return_value={"id": _SCRIPT_ID})
    svc_cls = MagicMock(return_value=svc)
    monkeypatch.setattr(
        "app.services.storyboard.script.script_service.ScriptService", svc_cls
    )

    dispatch = AsyncMock(return_value={"mode": "dbos"})
    monkeypatch.setattr(
        "app.services.infra.dbos_orchestrator.start_workflow_routed", dispatch
    )
    return {"mgr": mgr, "require_team": require_team, "svc": svc, "dispatch": dispatch}


async def test_import_threads_shared_wf_id_and_dispatches(wired):
    result = await router_mod.import_screenplay(_auth(), _body())

    assert result == {
        "success": True,
        "script_id": str(_SCRIPT_ID),
        "task_id": wired["mgr"].create.return_value,
    }

    wired["require_team"].assert_awaited_once()
    wired["svc"].create_project.assert_awaited_once()
    wired["mgr"].create.assert_awaited_once()
    wired["dispatch"].assert_awaited_once()

    wf_id = wired["mgr"].create.call_args.kwargs["dbos_workflow_id"]
    dispatched_id = wired["dispatch"].call_args.kwargs["workflow_id"]
    uuid.UUID(wf_id)
    assert wf_id == dispatched_id

    assert wired["mgr"].create.call_args.kwargs["task_type"] == "script_import"
    assert wired["dispatch"].call_args.args[0] == "script_import"
    assert wired["dispatch"].call_args.kwargs["dbos_workflow_kwargs"] == {
        "script_id": str(_SCRIPT_ID),
        "mode": "fountain",
        "content": _FOUNTAIN,
        "user_id": _USER,
    }


async def test_import_oversize_content_422_no_dispatch(wired):
    from fastapi import HTTPException

    from app.services.script.fountain_parser import MAX_FOUNTAIN_CHARS

    oversize = _body(content="a" * (MAX_FOUNTAIN_CHARS + 1))
    with pytest.raises(HTTPException) as exc:
        await router_mod.import_screenplay(_auth(), oversize)

    assert exc.value.status_code == 422
    wired["dispatch"].assert_not_awaited()
    wired["mgr"].create.assert_not_awaited()


# --------------------------------------------------------------------------- #
# Fountain persist step
# --------------------------------------------------------------------------- #


async def test_persist_fountain_preserves_heading_and_builds_ids():
    from app.workflows import script_import as m

    drafts = [
        {
            "heading_int_ext": "EST",
            "location_text": "Ridge",
            "time_of_day": "DAWN",
            "elements": [
                {"type": "action", "text": "Wide vista."},
                {"type": "character", "text": "SAM"},
                {"type": "dialogue", "text": "Here we are."},
            ],
        },
        {
            "heading_int_ext": "INT/EXT",
            "location_text": "Car",
            "time_of_day": "",
            "elements": [{"type": "action", "text": "They drive."}],
        },
    ]

    captured = []

    async def _cwc(data, elements, actor):
        captured.append((data, elements, actor))
        return {"id": 100 + len(captured)}

    repo = MagicMock()
    repo.create_with_content = AsyncMock(side_effect=_cwc)

    with patch(
        "app.repositories.script_scene_repository.ScriptSceneRepository",
        MagicMock(return_value=repo),
    ):
        result = await m.persist_fountain_scenes("9001", drafts)

    assert result["scene_count"] == 2
    assert result["scene_ids"] == ["101", "102"]

    # Headings preserved VERBATIM (not clamped to INT/EXT like the convert path).
    assert captured[0][0]["heading_int_ext"] == "EST"
    assert captured[1][0]["heading_int_ext"] == "INT/EXT"
    # Scenes attach to the script, not a chapter.
    assert captured[0][0]["chapter_id"] is None
    assert captured[0][0]["script_id"] == "9001"

    els = captured[0][1]
    assert [e["type"] for e in els] == ["action", "character", "dialogue"]
    assert all(e["id"].startswith("el_") for e in els)
    assert len({e["id"] for e in els}) == 3
    assert captured[0][2] == "import"


async def test_persist_fountain_keeps_heading_only_scene_skips_truly_empty():
    from app.workflows import script_import as m

    drafts = [
        {
            "heading_int_ext": "INT",
            "location_text": "Void",
            "time_of_day": "",
            "elements": [],
        },
        {"heading_int_ext": "", "location_text": "", "time_of_day": "", "elements": []},
    ]

    captured = []

    async def _cwc(data, elements, actor):
        captured.append((data, elements))
        return {"id": 1}

    repo = MagicMock()
    repo.create_with_content = AsyncMock(side_effect=_cwc)

    with patch(
        "app.repositories.script_scene_repository.ScriptSceneRepository",
        MagicMock(return_value=repo),
    ):
        result = await m.persist_fountain_scenes("9001", drafts)

    # Heading-only scene is kept (a stub); the truly-empty draft is skipped.
    assert result["scene_count"] == 1
    assert captured[0][0]["heading_int_ext"] == "INT"
    assert captured[0][1] == []


# --------------------------------------------------------------------------- #
# Mode routing
# --------------------------------------------------------------------------- #


async def test_workflow_fountain_route(monkeypatch):
    from app.workflows import script_import as m

    drafts = [{"heading_int_ext": "INT", "elements": []}]
    parse = MagicMock(return_value=drafts)
    persist = AsyncMock(return_value={"status": "success", "scene_count": 1})
    monkeypatch.setattr(m, "parse_fountain", parse)
    monkeypatch.setattr(m, "persist_fountain_scenes", persist)

    result = await m._run_import("9001", "fountain", _FOUNTAIN, _USER)

    parse.assert_called_once_with(_FOUNTAIN)
    persist.assert_awaited_once_with("9001", drafts)
    assert result["scene_count"] == 1


async def test_workflow_prose_route_reuses_convert_steps(monkeypatch):
    from app.workflows import script_import as m

    create_chapter = AsyncMock(return_value="ch-77")
    convert = AsyncMock(return_value=[{"heading_int_ext": "INT", "elements": []}])
    persist = AsyncMock(return_value={"status": "success", "scene_count": 3})
    monkeypatch.setattr(m, "create_prose_chapter", create_chapter)
    monkeypatch.setattr(m, "convert_chapter_to_scenes", convert)
    monkeypatch.setattr(m, "persist_scenes", persist)

    result = await m._run_import("9001", "prose", "Some prose.", _USER)

    create_chapter.assert_awaited_once_with("9001", "Some prose.")
    convert.assert_awaited_once_with("ch-77", _USER)
    persist.assert_awaited_once_with("9001", "ch-77", convert.return_value)
    assert result["scene_count"] == 3


async def test_workflow_unknown_mode_raises():
    from app.workflows import script_import as m

    with pytest.raises(ValueError):
        await m._run_import("9001", "fdx", "x", _USER)


# --------------------------------------------------------------------------- #
# Dispatch-bundle registration (#1055)
# --------------------------------------------------------------------------- #


def test_workflow_registered_in_dispatch_bundle():
    from app.workflows import _dispatch_bundle

    assert hasattr(_dispatch_bundle, "script_import_workflow")
