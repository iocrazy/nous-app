"""script-AI DBOS workflows — async ports of the three inline script-AI
endpoints (expand-chapter / create-branches).

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
    from app.services.ai.providers.ai_provider_helpers import (
        resolve_script_provider_config,
    )
    from app.services.storyboard.script.script_ai_service import ScriptAIService

    provider_key, provider_config, _model, agent_slug = (
        await resolve_script_provider_config(user_id)
    )
    ai_svc = ScriptAIService(
        user_id=user_id,
        agent_slug=agent_slug,
        provider_key=provider_key,
        provider_config=provider_config,
    )
    html = await ai_svc.expand_chapter(title=title, summary=summary, context=context)
    logger.info(f"[script_ai][expand][step] LLM returned {len(html)} chars")
    return html


@DBOS.step()
async def script_ai_expand_persist(
    chapter_id: str,
    html: str,
    run_id: Optional[int] = None,
    turn: Optional[int] = None,
    step: Optional[int] = None,
) -> dict[str, Any]:
    """Persist the expanded content onto the chapter.

    3a: the chapter write has no run id of its own anywhere in its call chain,
    so the run is threaded down from the workflow and handed to the service as
    an attribution argument."""
    from app.services.storyboard.script.script_service import ScriptService

    script_svc = ScriptService()
    await script_svc.update_chapter(
        chapter_id,
        {"content": html},
        attributed_to_run_id=run_id,
        turn=turn,
        step=step,
    )
    return {"status": "success", "chapter_id": chapter_id}


@DBOS.workflow()
async def script_expand_chapter_workflow(
    script_id: str,
    chapter_id: str,
    title: str,
    summary: str,
    context: Optional[str] = None,
    user_id: Optional[str] = None,
    # 3a: the dispatching run, for deliverable attribution. Keyword-defaulted
    # for frozen DBOS input compat.
    #
    # ⚠️ NO CALLER PASSES THIS TODAY. The only dispatcher of this workflow is
    # ``app/api/script_ai_router.py`` (the human "expand" / "branch" buttons),
    # and a REST request has no agent run behind it — so every chapter write
    # registers nothing, and ``script_chapter`` lineage is empty in practice.
    # The plumbing is kept deliberately (3a spec lists the kind, and the review
    # ruling was: keep it, do not invent a producer). When an agent tool or
    # workflow starts expanding chapters, it passes its run id here and the
    # whole chain — service attribution argument included — already works.
    run_id: Optional[int] = None,
    turn: Optional[int] = None,
    step: Optional[int] = None,
) -> dict[str, Any]:
    """Expand a chapter summary into full screenplay HTML, then persist it.

    - input: script_id + chapter_id + title + summary + context + user_id
    - output: {status, chapter_id}
    - side-effects: updates one row in script_chapters (content column)
    """
    html = await script_ai_expand_step(title, summary, context, user_id)
    return await script_ai_expand_persist(chapter_id, html, run_id, turn, step)


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
    from app.services.ai.providers.ai_provider_helpers import (
        resolve_script_provider_config,
    )
    from app.services.storyboard.script.script_ai_service import ScriptAIService

    provider_key, provider_config, _model, agent_slug = (
        await resolve_script_provider_config(user_id)
    )
    ai_svc = ScriptAIService(
        user_id=user_id,
        agent_slug=agent_slug,
        provider_key=provider_key,
        provider_config=provider_config,
    )
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
    run_id: Optional[int] = None,
    turn: Optional[int] = None,
    step: Optional[int] = None,
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
        result = await script_svc.create_chapter(
            script_id,
            chapter_data,
            attributed_to_run_id=run_id,
            turn=turn,
            step=step,
        )
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
    # 3a: the dispatching run, for deliverable attribution. Keyword-defaulted
    # for frozen DBOS input compat.
    #
    # ⚠️ NO CALLER PASSES THIS TODAY. The only dispatcher of this workflow is
    # ``app/api/script_ai_router.py`` (the human "expand" / "branch" buttons),
    # and a REST request has no agent run behind it — so every chapter write
    # registers nothing, and ``script_chapter`` lineage is empty in practice.
    # The plumbing is kept deliberately (3a spec lists the kind, and the review
    # ruling was: keep it, do not invent a producer). When an agent tool or
    # workflow starts expanding chapters, it passes its run id here and the
    # whole chain — service attribution argument included — already works.
    run_id: Optional[int] = None,
    turn: Optional[int] = None,
    step: Optional[int] = None,
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
        script_id, chapter_id, branch_type, branches, run_id, turn, step
    )
