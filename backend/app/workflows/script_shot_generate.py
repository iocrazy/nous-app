"""script_shot_generate DBOS workflow — single-shot image generation (spec v3
§4, Phase B P3).

Reuses the generic image-provider chain (``ImageGenerationService.generate_image``
→ ``provider_registry.get_image_provider``) rather than a parallel generator:
step1 composes a prompt from the shot's cinematography tags + description + the
scene heading and runs the provider; step2 persists the produced image through
the Tier-1 ``generated_media`` store (the durable single point) and rewrites the
shot's ``image_url`` / ``thumbnail_url`` to same-origin serving URLs so the
stored value can never rot when the provider's ephemeral CDN url expires (L5).

Durability (L5 go-live gate): the image provider hands back an EPHEMERAL CDN
url. Writing that straight onto the shot would rot. So between generation and
the status write we ``register_generated_media(...)`` — downloading the blob
into Tier-1 (filesystem, or the object store when the flag is on) and inserting
a ``generated_media`` row. The shot then stores durable relative URLs:
    - ``image_url``     = ``/api/v1/generated-media/{gen_id}/cover``
    - ``thumbnail_url`` = ``/api/v1/generated-media/{gen_id}/cover``
Both point at ``/cover`` (the UNAUTHENTICATED serving endpoint) because the
storyboard ShotCard renders them in a bare ``<img src>`` — the auth-gated
``/file`` endpoint can't carry a Bearer header from an ``<img>`` tag. ``/cover``
serves the full image bytes (there is no downscaled thumbnail), so full-res and
img-safe coincide. Frontend serves same-origin via the Vercel rewrite.
Persistence is best-effort: on ANY failure the provider url is kept (logged
loudly) rather than failing the whole generation.

Status machine (route-C aware — ``phase`` is trigger-owned, but ``shot.status``
is a BUSINESS column this workflow may write):
    - the dispatching endpoint sets ``status='generating'`` before dispatch;
    - on success this workflow sets ``status='done'`` + durable urls;
    - on ANY failure it sets ``status='failed'`` and then ``raise``s, so DBOS
      records the workflow FAILED (task_tracking mirror marks the task failed)
      while the shot row honestly shows the failed state for the retry UI.
"""

from __future__ import annotations

import os
import shutil
from typing import Any, Optional

from dbos import DBOS
from loguru import logger

# Prefix of the scratch dir the jimeng-cli provider writes its product into
# (``tempfile.mkdtemp(prefix="jimeng_")``). Only a dir with this prefix is ever
# reaped after ingest, so cleanup can never nuke an arbitrary path.
_JIMENG_SCRATCH_PREFIX = "jimeng_"

# Default image provider/model. ``provider=None`` means "resolve from the DB
# mediahub_models catalog" — the image ``provider_registry`` ships EMPTY, so a
# named provider like "openai" would only KeyError; passing None lets
# ``ImageGenerationService.generate_image`` resolve the admin-enabled image model
# (house rule: provider config lives in the DB, not env). A caller may still
# thread an explicit provider/model to override. ``_DEFAULT_MODEL`` stays the
# legacy sentinel: generate_image treats it as "use the catalog row's model".
_DEFAULT_MODEL = "dall-e-3"
_DEFAULT_PROVIDER: Optional[str] = None


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
    provider: Optional[str],
    user_id: Optional[str] = None,
) -> str:
    """Read the shot + its scene, compose the prompt, and run the image provider.

    Returns the produced ``image_url``. Raises if the shot is missing or the
    provider yields no url (so the workflow marks the shot failed)."""
    from app.repositories.script_scene_repository import get_script_scene_repository
    from app.repositories.script_shot_repository import get_script_shot_repository
    from app.services.ai.media.image_generation_service import ImageGenerationService

    shot = await get_script_shot_repository().get_by_id(shot_id)
    if not shot:
        raise ValueError(f"Shot not found: {shot_id}")
    scene = await get_script_scene_repository().get_by_id(str(shot.get("scene_id")))
    prompt = _compose_prompt(shot, scene)

    svc = ImageGenerationService()
    # project_id="" → no storyboard style fragment (shots aren't storyboard
    # projects); node_id carries the shot id for the provider's logging.
    result = await svc.generate_image(
        project_id="",
        node_id=str(shot_id),
        prompt=prompt,
        model=model,
        provider_name=provider,
        user_id=user_id,
    )
    # URL providers (Ark) return image_url; the jimeng-cli adapter returns a
    # local image_path instead (the CLI wrote the file to disk, no URL). Carry
    # whichever is present through to persist, which routes local paths to
    # register_generated_media(source_path=...).
    produced = (result or {}).get("image_url") or (result or {}).get("image_path")
    if not produced:
        raise RuntimeError(f"Image provider returned no image for shot {shot_id}")
    logger.info(f"[script_shot_generate][step] shot {shot_id} → {produced}")
    return produced


def _reap_scratch_dir(local_path: str) -> None:
    """Remove the jimeng-cli scratch dir holding ``local_path`` (H1).

    Best-effort and defensive: only a dir whose basename starts with the
    ``jimeng_`` prefix is ever removed, so a stray non-scratch path can never
    trigger deletion of an arbitrary directory."""
    parent = os.path.dirname(local_path)
    if os.path.basename(parent).startswith(_JIMENG_SCRATCH_PREFIX):
        shutil.rmtree(parent, ignore_errors=True)


async def _resolve_scope_id(scene: Optional[dict[str, Any]], user_id: str) -> int:
    """Owning team for the shot: scene(script_id)→script_projects(team_id).

    Falls back to the caller's personal team when the chain yields no team_id
    (e.g. a script row missing team_id). Ids come off ORM rows as native ints;
    they are coerced to ``int`` before use (#1006 — never compare/pass a bigint
    across the int/str seam)."""
    script_id = (scene or {}).get("script_id")
    if script_id is not None:
        from app.repositories.script_repository import get_script_project_repository

        script = await get_script_project_repository().get_by_id(str(script_id))
        team_id = (script or {}).get("team_id")
        if team_id is not None:
            return int(team_id)
    from app.services.library.resources_service import _resolve_personal_team_id

    return int(await _resolve_personal_team_id(str(user_id)))


@DBOS.step()
async def persist_generation(
    shot_id: str,
    provider_url: str,
    model: str,
    provider: Optional[str],
    user_id: Optional[str],
    run_id: Optional[int] = None,
    turn: Optional[int] = None,
    step: Optional[int] = None,
) -> dict[str, str]:
    """Persist the provider's ephemeral image through the generated-media store.

    Returns durable same-origin ``{image_url, thumbnail_url}`` (both ``/cover``,
    see module docstring). Best-effort: on ANY failure — missing user_id,
    unresolvable scope, download error, insert error — keeps the provider url
    for both fields and logs loudly. Durability is the point of this step, so a
    fallback is a real regression worth a WARNING, but it must never fail the
    generation the user already paid for."""
    from app.repositories.script_shot_repository import get_script_shot_repository
    from app.services.library.generated_media_service import (
        GenerationOrigin,
        register_generated_media,
    )

    # provider_url is a URL (Ark) or a local file path (jimeng-cli). The local
    # file lives in a `jimeng_` scratch dir the provider created; once the store
    # has copied it in (or we've given up), that dir is reaped in the `finally`
    # so the worker /tmp never grows unboundedly (H1).
    is_url = provider_url.startswith(("http://", "https://"))
    try:
        if not user_id:
            logger.warning(
                "[script_shot_generate][persist] shot {} has no user_id — keeping "
                "EPHEMERAL provider url (will rot): {}",
                shot_id,
                provider_url,
            )
            return {"image_url": provider_url, "thumbnail_url": provider_url}

        try:
            shot = await get_script_shot_repository().get_by_id(shot_id)
            if not shot:
                raise ValueError(f"Shot not found: {shot_id}")
            from app.repositories.script_scene_repository import (
                get_script_scene_repository,
            )

            scene = await get_script_scene_repository().get_by_id(
                str(shot.get("scene_id"))
            )
            prompt = _compose_prompt(shot, scene)
            scope_id = await _resolve_scope_id(scene, str(user_id))

            # Route to the matching ingest input — register takes exactly one.
            row = await register_generated_media(
                user_id=str(user_id),
                scope_id=scope_id,
                source_url=provider_url if is_url else None,
                source_path=None if is_url else provider_url,
                mime="image/png",
                origin=GenerationOrigin(
                    kind="shot_generate",
                    node_id=str(shot_id),
                    prompt=prompt,
                    model=model,
                    provider=provider,
                    derivation_kind="shot_generate",
                    # 3a：run 上下文在派发时就丢了，这里回填，否则这条路
                    # 产出的图永远没有 run 可挂（真栈缺口，spec §1.3）。
                    run_id=run_id,
                    turn=turn,
                    step=step,
                ),
            )
            gen_id = row.get("id")
            if gen_id is None:
                raise RuntimeError("register_generated_media returned no id")
            durable = f"/api/v1/generated-media/{gen_id}/cover"
            logger.info(
                "[script_shot_generate][persist] shot {} → generated_media {} ({})",
                shot_id,
                gen_id,
                durable,
            )
            return {"image_url": durable, "thumbnail_url": durable}
        except Exception:
            logger.opt(exception=True).warning(
                "[script_shot_generate][persist] durable persist FAILED for shot {} "
                "— keeping EPHEMERAL provider url (will rot): {}",
                shot_id,
                provider_url,
            )
            return {"image_url": provider_url, "thumbnail_url": provider_url}
    finally:
        if not is_url:
            _reap_scratch_dir(provider_url)


@DBOS.step()
async def mark_shot_done(
    shot_id: str, image_url: str, thumbnail_url: Optional[str] = None
) -> None:
    """Write the produced urls + ``status='done'`` onto the shot (status lane)."""
    from app.repositories.script_shot_repository import get_script_shot_repository

    await get_script_shot_repository().update_status(
        shot_id, "done", image_url=image_url, thumbnail_url=thumbnail_url
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
    provider: Optional[str] = _DEFAULT_PROVIDER,
    user_id: Optional[str] = None,
    # 3a: the dispatching run, threaded through so the produced image can be
    # registered against it. DBOS freezes a workflow's input, so these are
    # keyword-defaulted — older frozen inputs simply have no coordinates.
    run_id: Optional[int] = None,
    turn: Optional[int] = None,
    step: Optional[int] = None,
) -> dict[str, Any]:
    """DBOS orchestrator: shot → generated image url on the shot row.

    - input: shot_id (bigint str) + model/provider + user_id
    - output: {status, shot_id, image_url}
    - side-effects: sets shot.status + image_url

    On any failure the shot is flipped to ``status='failed'`` and the error is
    re-raised (route-C: DBOS records FAILED; shot.status is a business column).
    ``user_id`` is optional (frozen DBOS input compat)."""
    try:
        provider_url = await generate_shot_image_step(shot_id, model, provider, user_id)
        urls = await persist_generation(
            shot_id, provider_url, model, provider, user_id, run_id, turn, step
        )
        await mark_shot_done(shot_id, urls["image_url"], urls["thumbnail_url"])
        return {
            "status": "success",
            "shot_id": shot_id,
            "image_url": urls["image_url"],
        }
    except Exception:
        await mark_shot_failed(shot_id)
        raise
