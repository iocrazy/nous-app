"""script_shot_breakdown DBOS workflow — AI breakdown of a scene's elements
into a storyboard shot list (spec v3 §3.3, Phase B P3).

Two-step DBOS shape mirrors ``script_scene_convert``:
    - step1 ``breakdown_scene_to_shots`` is the retryable LLM call: it loads the
      scene's ``content_json`` elements + heading, resolves the DB-governed
      provider config, and asks the model to split the scene into 3-8 shots. A
      transient HTTP failure gets one retry; the workflow_id memoizes the
      eventual success so a downstream replay never re-charges the model.
    - step2 ``persist_shots`` writes the whole shot list via
      ``ScriptShotRepository.create_many`` (one transaction). Separated so a
      half-written shot set doesn't re-invoke the LLM on replay.

Route-C discipline: failure paths ``raise`` (never ``return {"status":
"failed"}``) so DBOS records the workflow as FAILED and the task_tracking mirror
never marks a broken breakdown as completed.
"""

from __future__ import annotations

from typing import Any, Optional

from dbos import DBOS
from loguru import logger

# Hard cap on the breakdown size (defensive: the prompt asks for 3-8, but a
# runaway model response is truncated rather than persisting a huge shot set).
_MAX_SHOTS = 20


def _scene_heading(scene: dict[str, Any]) -> str:
    """Compose a human heading line from the scene's INT/EXT + location for the
    prompt (best-effort; empty parts are dropped)."""
    parts = [
        str(scene.get("heading_int_ext") or "").strip(),
        str(scene.get("location_text") or "").strip(),
        str(scene.get("time_of_day") or "").strip(),
    ]
    return " - ".join(p for p in parts if p)


@DBOS.step(retries_allowed=True, max_attempts=2)
async def breakdown_scene_to_shots(
    scene_id: str,
    user_id: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Load the scene's elements and run the LLM shot-breakdown call.

    Returns the raw shot dict list from the model. Shape validation happens in
    the service (raises on a non-array body) so persistence never sees garbage.
    Raises if the scene is missing or has no elements to break down.
    """
    from app.repositories.script_scene_repository import get_script_scene_repository
    from app.services.ai.providers.ai_provider_helpers import (
        resolve_script_provider_config,
    )
    from app.services.storyboard.script.script_ai_service import ScriptAIService

    scene = await get_script_scene_repository().get_by_id(scene_id)
    if not scene:
        raise ValueError(f"Scene not found: {scene_id}")
    elements = scene.get("content_json") or []
    if not isinstance(elements, list) or not elements:
        raise ValueError(f"Scene {scene_id} has no elements to break down")

    provider_key, provider_config, _model, agent_slug = (
        await resolve_script_provider_config(user_id)
    )
    ai_svc = ScriptAIService(
        user_id=user_id,
        agent_slug=agent_slug,
        provider_key=provider_key,
        provider_config=provider_config,
    )
    shots = await ai_svc.scene_to_shots(elements, heading=_scene_heading(scene))
    logger.info(f"[script_shot_breakdown][step] LLM returned {len(shots)} shots")
    return shots


@DBOS.step()
async def persist_shots(
    scene_id: str,
    shots: list[dict[str, Any]],
) -> dict[str, Any]:
    """Persist the shot list via ``create_many`` (one transaction).

    Only dict shots survive; each is trimmed to the shot columns by the repo's
    write-values filter. ``shot_number`` / ``sort_order`` / ``status='empty'``
    are stamped by ``create_many`` (never trusted from the model). Idempotent at
    the workflow_id level: a replay returns the cached created ids."""
    from app.repositories.script_shot_repository import get_script_shot_repository

    clean = [s for s in shots if isinstance(s, dict)][:_MAX_SHOTS]
    if not clean:
        raise ValueError("shot breakdown produced no usable shots")
    created = await get_script_shot_repository().create_many(scene_id, clean)
    return {
        "status": "success",
        "shot_count": len(created),
        "shot_ids": [str(s.get("id", "")) for s in created],
    }


@DBOS.workflow()
async def script_shot_breakdown_workflow(
    scene_id: str,
    *,
    user_id: Optional[str] = None,
) -> dict[str, Any]:
    """DBOS orchestrator: scene elements → persisted storyboard shots.

    - input: scene_id (bigint str) + user_id
    - output: {status, shot_count, shot_ids}
    - side-effects: inserts N rows into script_shots

    ``user_id`` is optional so replayed workflows started before this field
    existed keep working (DBOS input is frozen at workflow start).
    """
    shots = await breakdown_scene_to_shots(scene_id, user_id)
    return await persist_shots(scene_id, shots)
