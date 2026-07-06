"""script_shot_generate DBOS workflow — single-shot image generation (spec v3
§4, Phase B P3).

Reuses the storyboard image-provider chain (``StoryboardAIService.generate_image``
→ ``provider_registry.get_image_provider``) rather than a parallel generator:
step1 composes a prompt from the shot's cinematography tags + description + the
scene heading and runs the provider; step2 writes the produced URL onto the
shot row via ``ScriptShotRepository.update_status`` (the status write lane).

Status machine (route-C aware — ``phase`` is trigger-owned, but ``shot.status``
is a BUSINESS column this workflow may write):
    - the dispatching endpoint sets ``status='generating'`` before dispatch;
    - on success this workflow sets ``status='done'`` + ``image_url``;
    - on ANY failure it sets ``status='failed'`` and then ``raise``s, so DBOS
      records the workflow FAILED (task_tracking mirror marks the task failed)
      while the shot row honestly shows the failed state for the retry UI.
"""

from __future__ import annotations

from typing import Any, Optional

from dbos import DBOS
from loguru import logger

# Default image provider/model — same defaults as ``storyboard_image_workflow``.
# Threaded as workflow kwargs so a caller can override once shot-level provider
# selection lands; the generate endpoint is flag-gated (FEATURE_SHOT_GENERATE).
_DEFAULT_MODEL = "dall-e-3"
_DEFAULT_PROVIDER = "openai"


def _compose_prompt(shot: dict[str, Any], scene: Optional[dict[str, Any]]) -> str:
    """Build the image prompt from the shot's tags + description + scene heading.

    Tags that are present are joined as a leading cinematography clause; the
    description carries the content. Empty parts are dropped."""
    tag_parts = [
        str(shot.get("shot_type") or "").strip(),
        str(shot.get("camera_angle") or "").strip(),
        str(shot.get("camera_movement") or "").strip(),
        str(shot.get("focal_length") or "").strip(),
    ]
    tags = ", ".join(p for p in tag_parts if p)
    description = str(shot.get("description") or "").strip()
    lighting = str(shot.get("lighting") or "").strip()
    heading = ""
    if scene:
        heading = " - ".join(
            p
            for p in (
                str(scene.get("heading_int_ext") or "").strip(),
                str(scene.get("location_text") or "").strip(),
                str(scene.get("time_of_day") or "").strip(),
            )
            if p
        )

    segments: list[str] = []
    if heading:
        segments.append(f"Scene: {heading}")
    if description:
        segments.append(description)
    if tags:
        segments.append(f"Shot: {tags}")
    if lighting:
        segments.append(f"Lighting: {lighting}")
    # A shot with neither description nor tags still yields a non-empty prompt so
    # the provider is never handed an empty string.
    return ". ".join(segments) or "storyboard shot"


@DBOS.step(retries_allowed=True, max_attempts=3)
async def generate_shot_image_step(
    shot_id: str,
    model: str,
    provider: str,
) -> str:
    """Read the shot + its scene, compose the prompt, and run the image provider.

    Returns the produced ``image_url``. Raises if the shot is missing or the
    provider yields no url (so the workflow marks the shot failed)."""
    from app.repositories.script_scene_repository import get_script_scene_repository
    from app.repositories.script_shot_repository import get_script_shot_repository
    from app.services.storyboard.storyboard_ai_service import StoryboardAIService

    shot = await get_script_shot_repository().get_by_id(shot_id)
    if not shot:
        raise ValueError(f"Shot not found: {shot_id}")
    scene = await get_script_scene_repository().get_by_id(str(shot.get("scene_id")))
    prompt = _compose_prompt(shot, scene)

    svc = StoryboardAIService()
    # project_id="" → no storyboard style fragment (shots aren't storyboard
    # projects); node_id carries the shot id for the provider's logging.
    result = await svc.generate_image(
        project_id="",
        node_id=str(shot_id),
        prompt=prompt,
        model=model,
        provider_name=provider,
    )
    image_url = (result or {}).get("image_url")
    if not image_url:
        raise RuntimeError(f"Image provider returned no url for shot {shot_id}")
    logger.info(f"[script_shot_generate][step] shot {shot_id} → {image_url}")
    return image_url


@DBOS.step()
async def mark_shot_done(shot_id: str, image_url: str) -> None:
    """Write the produced url + ``status='done'`` onto the shot (status lane)."""
    from app.repositories.script_shot_repository import get_script_shot_repository

    await get_script_shot_repository().update_status(
        shot_id, "done", image_url=image_url
    )


@DBOS.step()
async def mark_shot_failed(shot_id: str) -> None:
    """Flip the shot to ``status='failed'`` for the retry UI (status lane)."""
    from app.repositories.script_shot_repository import get_script_shot_repository

    await get_script_shot_repository().update_status(shot_id, "failed")


@DBOS.workflow()
async def script_shot_generate_workflow(
    shot_id: str,
    *,
    model: str = _DEFAULT_MODEL,
    provider: str = _DEFAULT_PROVIDER,
    user_id: Optional[str] = None,
) -> dict[str, Any]:
    """DBOS orchestrator: shot → generated image url on the shot row.

    - input: shot_id (bigint str) + model/provider + user_id
    - output: {status, shot_id, image_url}
    - side-effects: sets shot.status + image_url

    On any failure the shot is flipped to ``status='failed'`` and the error is
    re-raised (route-C: DBOS records FAILED; shot.status is a business column).
    ``user_id`` is optional (frozen DBOS input compat)."""
    try:
        image_url = await generate_shot_image_step(shot_id, model, provider)
        await mark_shot_done(shot_id, image_url)
        return {"status": "success", "shot_id": shot_id, "image_url": image_url}
    except Exception:
        await mark_shot_failed(shot_id)
        raise
