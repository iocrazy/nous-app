"""script_outline (script_outline_gen) DBOS workflow — port of legacy
`script_tasks.generate_script_outline`.

Original task is already async-shaped: ScriptAIService.generate_outline
runs the LLM call and returns a `[{title, summary}, ...]` list, then
ScriptService.chapter_repo.create persists each chapter node onto a
canvas grid.

DBOS layering:
    - LLM call is its own retryable step (transient HTTP failures get
      one retry; workflow_id memoizes the eventual success)
    - chapter persistence is a separate step so a half-written canvas
      doesn't re-charge the LLM on replay
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from dbos import DBOS
from loguru import logger

_VERTICAL_GAP = 200
_START_X = 400
_START_Y = 100


@DBOS.step(retries_allowed=True, max_attempts=2)
def generate_outline_chapters(
    premise: str,
    chapter_count: int,
    style_guide: Optional[str],
) -> list[dict[str, Any]]:
    """Run the LLM outline call. Returns list of {title, summary} dicts."""
    from app.services.script_ai_service import ScriptAIService

    async def _call() -> list[dict[str, Any]]:
        ai_svc = ScriptAIService()
        return await ai_svc.generate_outline(premise, chapter_count, style_guide)

    chapters = asyncio.run(_call())
    logger.info(f"[script_outline][step] LLM returned {len(chapters)} chapters")
    return chapters


@DBOS.step()
def persist_outline_chapters(
    script_id: str,
    chapters: list[dict[str, Any]],
) -> dict[str, Any]:
    """Create chapter nodes on the script canvas. Idempotent at the
    workflow_id level — a replay returns the cached list of created ids
    rather than re-inserting."""
    from app.services.script_service import ScriptService

    async def _persist() -> list[str]:
        script_svc = ScriptService()
        created_ids: list[str] = []
        for i, ch in enumerate(chapters):
            chapter_data = {
                "script_id": script_id,
                "title": ch["title"],
                "summary": ch["summary"],
                "chapter_number": i + 1,
                "position_x": _START_X,
                "position_y": _START_Y + i * _VERTICAL_GAP,
            }
            result = await script_svc.chapter_repo.create(chapter_data)
            created_ids.append(result.get("id", ""))
        return created_ids

    created_ids = asyncio.run(_persist())
    return {
        "status": "success",
        "chapter_count": len(chapters),
        "chapter_ids": created_ids,
    }


@DBOS.workflow()
def script_outline_workflow(
    script_id: str,
    premise: str,
    *,
    chapter_count: int = 5,
    style_guide: Optional[str] = None,
) -> dict[str, Any]:
    """DBOS port of generate_script_outline.

    - input: script_id (uuid str) + premise + chapter_count + style_guide
    - output: {status, chapter_count, chapter_ids}
    - side-effects: inserts N rows into script_chapters

    Note: the legacy task accepted `task_id` for task_tracking progress
    events. DBOS owns the workflow lifecycle now — TaskCenter UI should
    subscribe to dbos workflow status (D4 work) instead of the
    task_tracking bridge.
    """
    chapters = generate_outline_chapters(premise, chapter_count, style_guide)
    return persist_outline_chapters(script_id, chapters)
