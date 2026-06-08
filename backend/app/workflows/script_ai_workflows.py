"""script-AI DBOS workflows — async ports of the three inline script-AI
endpoints (expand-chapter / create-branches / convert-to-storyboard).

Each mirrors ``script_outline_workflow``:
    - the LLM call is its own retryable ``@DBOS.step`` (transient HTTP
      failures get one retry; the workflow_id memoizes the success)
    - persistence is a separate step so a half-written canvas doesn't
      re-charge the LLM on replay
    - service instances are constructed INSIDE steps (never passed across
      a step boundary)
    - failure → raise (DBOS marks the workflow ERROR → the migration-180
      trigger marks the task_tracking row failed)

Task-tracking rows are created by the ENDPOINT (via ``mgr.create``) and
mirrored by the migration-180 trigger — these workflows do NOT create
their own task rows (same as ``script_outline_workflow``).

Step names are globally unique (prefix ``script_ai_*``) — DBOS requires
globally-unique step names.
"""

from __future__ import annotations

from typing import Any, Optional

from dbos import DBOS
from loguru import logger

# Branch canvas offsets — must match the legacy script_ai_router handler.
BRANCH_X_OFFSET = 350
BRANCH_Y_OFFSET = 250

# Storyboard node vertical spacing — must match the legacy handler.
NODE_Y_SPACING = 300


# ---------------------------------------------------------------------------
# expand-chapter
# ---------------------------------------------------------------------------


@DBOS.step(retries_allowed=True, max_attempts=2)
async def script_ai_expand_step(
    title: str,
    summary: str,
    context: Optional[str],
    user_id: Optional[str],
) -> str:
    """Run the LLM chapter-expansion call. Returns sanitized HTML."""
    from app.services.storyboard.script.script_ai_service import ScriptAIService

    ai_svc = ScriptAIService(user_id=user_id)
    html = await ai_svc.expand_chapter(title=title, summary=summary, context=context)
    logger.info(f"[script_ai][expand][step] LLM returned {len(html)} chars")
    return html


@DBOS.step()
async def script_ai_expand_persist(chapter_id: str, html: str) -> dict[str, Any]:
    """Persist the expanded content onto the chapter."""
    from app.services.storyboard.script.script_service import ScriptService

    script_svc = ScriptService()
    await script_svc.update_chapter(chapter_id, {"content": html})
    return {"status": "success", "chapter_id": chapter_id}


@DBOS.workflow()
async def script_expand_chapter_workflow(
    script_id: str,
    chapter_id: str,
    title: str,
    summary: str,
    context: Optional[str] = None,
    user_id: Optional[str] = None,
) -> dict[str, Any]:
    """Expand a chapter summary into full screenplay HTML, then persist it.

    - input: script_id + chapter_id + title + summary + context + user_id
    - output: {status, chapter_id}
    - side-effects: updates one row in script_chapters (content column)
    """
    html = await script_ai_expand_step(title, summary, context, user_id)
    return await script_ai_expand_persist(chapter_id, html)


# ---------------------------------------------------------------------------
# create-branches
# ---------------------------------------------------------------------------


@DBOS.step(retries_allowed=True, max_attempts=2)
async def script_ai_branches_step(
    title: str,
    summary: str,
    branch_count: int,
    branch_type: str,
    context: Optional[str],
    user_id: Optional[str],
) -> list[dict[str, Any]]:
    """Run the LLM branching call. Returns a list of branch dicts."""
    from app.services.storyboard.script.script_ai_service import ScriptAIService

    ai_svc = ScriptAIService(user_id=user_id)
    branches = await ai_svc.create_branches(
        title=title,
        summary=summary,
        branch_count=branch_count,
        branch_type=branch_type,
        context=context,
    )
    logger.info(f"[script_ai][branches][step] LLM returned {len(branches)} branches")
    return branches


@DBOS.step()
async def script_ai_branches_persist(
    script_id: str,
    chapter_id: str,
    branch_type: str,
    branches: list[dict[str, Any]],
) -> dict[str, Any]:
    """Create one chapter node per branch, offset from the parent position."""
    from app.services.storyboard.script.script_service import ScriptService

    script_svc = ScriptService()
    parent = await script_svc.chapter_repo.get_by_id(chapter_id)
    parent_x = parent.get("position_x", 400) if parent else 400
    parent_y = parent.get("position_y", 100) if parent else 100

    created_ids: list[str] = []
    for i, branch in enumerate(branches):
        x_offset = (i - len(branches) / 2 + 0.5) * BRANCH_X_OFFSET
        chapter_data = {
            "script_id": script_id,
            "parent_chapter_id": chapter_id,
            "title": branch["title"],
            "summary": branch["summary"],
            "branch_label": branch["branch_label"],
            "branch_type": branch_type,
            "position_x": parent_x + x_offset,
            "position_y": parent_y + BRANCH_Y_OFFSET,
        }
        result = await script_svc.create_chapter(script_id, chapter_data)
        created_ids.append(result.get("id", ""))
    return {
        "status": "success",
        "branch_count": len(branches),
        "chapter_ids": created_ids,
    }


@DBOS.workflow()
async def script_create_branches_workflow(
    script_id: str,
    chapter_id: str,
    title: str,
    summary: str,
    branch_count: int = 2,
    branch_type: str = "choice",
    context: Optional[str] = None,
    user_id: Optional[str] = None,
) -> dict[str, Any]:
    """Generate alternative story branches, then create chapter nodes.

    - input: script_id + chapter_id + title + summary + branch_count +
      branch_type + context + user_id
    - output: {status, branch_count, chapter_ids}
    - side-effects: inserts N rows into script_chapters
    """
    branches = await script_ai_branches_step(
        title, summary, branch_count, branch_type, context, user_id
    )
    return await script_ai_branches_persist(
        script_id, chapter_id, branch_type, branches
    )


# ---------------------------------------------------------------------------
# convert-to-storyboard
# ---------------------------------------------------------------------------


@DBOS.step(retries_allowed=True, max_attempts=2)
async def script_ai_scenes_step(
    script_id: str,
    chapter_id: str,
    user_id: Optional[str],
) -> list[dict[str, Any]]:
    """Read the chapter + project style guide, then run the LLM scene-split
    call. Returns a list of scene dicts.

    Chapter/project reads moved INTO the workflow (the endpoint only
    verifies access + dispatches chapter_id/storyboard_project_id now).
    """
    from app.services.storyboard.script.script_ai_service import ScriptAIService
    from app.services.storyboard.script.script_service import ScriptService

    script_svc = ScriptService()
    chapter = await script_svc.chapter_repo.get_by_id(chapter_id)
    if not chapter:
        raise ValueError(f"Chapter not found: {chapter_id}")

    project = await script_svc.project_repo.get_by_id(script_id)
    style_guide = None
    if project and project.get("settings_json"):
        style_guide = project["settings_json"].get("style_guide")

    ai_svc = ScriptAIService(user_id=user_id)
    scenes = await ai_svc.split_chapter_to_scenes(
        title=chapter.get("title", ""),
        summary=chapter.get("summary", ""),
        content=chapter.get("content"),
        style_guide=style_guide,
    )
    logger.info(f"[script_ai][scenes][step] LLM returned {len(scenes)} scenes")
    return scenes


@DBOS.step()
async def script_ai_scenes_persist(
    chapter_id: str,
    storyboard_project_id: str,
    scenes: list[dict[str, Any]],
) -> dict[str, Any]:
    """Create one storyboard node per scene + a script_storyboard_link."""
    from app.repositories.storyboard_repository import (
        get_storyboard_node_repository,
    )
    from app.services.storyboard.script.script_service import ScriptService

    script_svc = ScriptService()
    node_repo = get_storyboard_node_repository()

    created_nodes: list[dict[str, Any]] = []
    for scene in scenes:
        node_data = {
            "project_id": storyboard_project_id,
            "node_type": "storyboard_split",
            "position_x": 100,
            "position_y": scene["scene_number"] * NODE_Y_SPACING,
            # scene_number / description / camera_notes are NOT columns on
            # storyboard_nodes — nest them in the data_json jsonb (mirrors
            # how workflows/storyboard.py persists split-scene node data).
            "data_json": {
                "source": "script_conversion",
                "scene_number": scene["scene_number"],
                "description": scene["description"],
                "camera_notes": scene.get("camera_notes", ""),
            },
        }
        rows = await node_repo.bulk_upsert(storyboard_project_id, [node_data])
        if rows:
            created_nodes.append(rows[0])

    for node in created_nodes:
        await script_svc.create_storyboard_link(
            {
                "chapter_id": chapter_id,
                "storyboard_project_id": storyboard_project_id,
                "storyboard_node_id": node.get("id"),
            }
        )

    return {
        "status": "success",
        "scene_count": len(scenes),
        "node_count": len(created_nodes),
    }


@DBOS.workflow()
async def script_to_storyboard_workflow(
    script_id: str,
    chapter_id: str,
    storyboard_project_id: Optional[str] = None,
    user_id: Optional[str] = None,
) -> dict[str, Any]:
    """Convert a script chapter into storyboard scenes via AI.

    - input: script_id + chapter_id + storyboard_project_id + user_id
    - output: {status, scene_count, node_count}
    - side-effects: inserts storyboard_nodes + script_storyboard_links rows
      (only when storyboard_project_id is provided)
    """
    scenes = await script_ai_scenes_step(script_id, chapter_id, user_id)
    if storyboard_project_id:
        return await script_ai_scenes_persist(chapter_id, storyboard_project_id, scenes)
    return {"status": "success", "scene_count": len(scenes), "node_count": 0}
