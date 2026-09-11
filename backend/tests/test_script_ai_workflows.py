"""script-AI DBOS workflow body tests.

Drives the real workflow bodies (``inspect.unwrap`` past the
@DBOS.workflow decorator — same approach as test_upload_postprocess_workflow,
since the decorator refuses to run before DBOS.launch() but preserves the
inner coroutine via @wraps).

Each @DBOS.step constructs its service internally, so we patch the
underlying service / repo methods rather than the steps:

  - ScriptAIService.expand_chapter / create_branches / split_chapter_to_scenes
  - ScriptService.update_chapter / create_chapter
  - ScriptService.chapter_repo.get_by_id / project_repo.get_by_id

Cases per workflow: happy path persists the right data; a step failure
propagates (raises).
"""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

_USER = "11111111-1111-1111-1111-111111111111"
_SCRIPT = "9000000000000000001"
_CHAPTER = "9000000000000000002"


def _expand_body():
    from app.workflows import script_ai_workflows as m

    return inspect.unwrap(m.script_expand_chapter_workflow)


def _branches_body():
    from app.workflows import script_ai_workflows as m

    return inspect.unwrap(m.script_create_branches_workflow)


# ---------------------------------------------------------------------------
# expand-chapter
# ---------------------------------------------------------------------------


async def test_expand_chapter_persists_content():
    expand = AsyncMock(return_value="<p>full prose</p>")
    update_chapter = AsyncMock(return_value={"id": _CHAPTER})

    from app.services.storyboard.script.script_ai_service import ScriptAIService
    from app.services.storyboard.script.script_service import ScriptService

    with (
        patch.object(ScriptAIService, "expand_chapter", expand),
        patch.object(ScriptService, "update_chapter", update_chapter),
    ):
        result = await _expand_body()(
            script_id=_SCRIPT,
            chapter_id=_CHAPTER,
            title="Act I",
            summary="A summary",
            context="ctx",
            user_id=_USER,
        )

    assert result["status"] == "success"
    expand.assert_awaited_once_with(title="Act I", summary="A summary", context="ctx")
    # 3a: the attribution argument rides along; None = no run behind this call.
    update_chapter.assert_awaited_once_with(
        _CHAPTER,
        {"content": "<p>full prose</p>"},
        attributed_to_run_id=None,
        turn=None,
        step=None,
    )


async def test_expand_chapter_llm_failure_propagates():
    expand = AsyncMock(side_effect=RuntimeError("llm boom"))
    update_chapter = AsyncMock()

    from app.services.storyboard.script.script_ai_service import ScriptAIService
    from app.services.storyboard.script.script_service import ScriptService

    with (
        patch.object(ScriptAIService, "expand_chapter", expand),
        patch.object(ScriptService, "update_chapter", update_chapter),
    ):
        with pytest.raises(RuntimeError, match="llm boom"):
            await _expand_body()(
                script_id=_SCRIPT,
                chapter_id=_CHAPTER,
                title="Act I",
                summary="A summary",
                user_id=_USER,
            )

    update_chapter.assert_not_awaited()


# ---------------------------------------------------------------------------
# create-branches
# ---------------------------------------------------------------------------


async def test_create_branches_creates_one_chapter_per_branch():
    branches = [
        {"title": "Fight", "summary": "s1", "branch_label": "Fight"},
        {"title": "Flee", "summary": "s2", "branch_label": "Flee"},
    ]
    create_branches = AsyncMock(return_value=branches)
    create_chapter = AsyncMock(side_effect=[{"id": "c1"}, {"id": "c2"}])

    from app.services.storyboard.script.script_ai_service import ScriptAIService
    from app.services.storyboard.script.script_service import ScriptService

    fake_repo = MagicMock()
    fake_repo.get_by_id = AsyncMock(return_value={"position_x": 400, "position_y": 100})

    with (
        patch.object(ScriptAIService, "create_branches", create_branches),
        patch.object(ScriptService, "create_chapter", create_chapter),
        patch.object(
            ScriptService,
            "__init__",
            lambda self: setattr(self, "chapter_repo", fake_repo),
        ),
    ):
        result = await _branches_body()(
            script_id=_SCRIPT,
            chapter_id=_CHAPTER,
            title="Act I",
            summary="A summary",
            branch_count=2,
            branch_type="choice",
            context=None,
            user_id=_USER,
        )

    assert result["status"] == "success"
    assert result["branch_count"] == 2
    assert result["chapter_ids"] == ["c1", "c2"]
    assert create_chapter.await_count == 2
    # offset math: parent at (400,100), 2 branches → x offsets -175 / +175.
    first_call = create_chapter.await_args_list[0].args
    assert first_call[0] == _SCRIPT
    first_data = first_call[1]
    assert first_data["parent_chapter_id"] == _CHAPTER
    assert first_data["position_x"] == 400 + (0 - 1 + 0.5) * 350
    assert first_data["position_y"] == 100 + 250
    assert first_data["branch_label"] == "Fight"


async def test_create_branches_llm_failure_propagates():
    create_branches = AsyncMock(side_effect=RuntimeError("llm boom"))
    create_chapter = AsyncMock()

    from app.services.storyboard.script.script_ai_service import ScriptAIService
    from app.services.storyboard.script.script_service import ScriptService

    fake_repo = MagicMock()
    fake_repo.get_by_id = AsyncMock(return_value={})

    with (
        patch.object(ScriptAIService, "create_branches", create_branches),
        patch.object(ScriptService, "create_chapter", create_chapter),
        patch.object(
            ScriptService,
            "__init__",
            lambda self: setattr(self, "chapter_repo", fake_repo),
        ),
    ):
        with pytest.raises(RuntimeError, match="llm boom"):
            await _branches_body()(
                script_id=_SCRIPT,
                chapter_id=_CHAPTER,
                title="Act I",
                summary="A summary",
                user_id=_USER,
            )

    create_chapter.assert_not_awaited()
