"""canvas_generation DBOS workflow — smart-canvas image/video generation
(Infinite-Canvas parity Phase 2 G4-B1).

One workflow run = ONE generated asset. The composer fans a ``count`` request
out into N independent tasks at dispatch time (the DBOS queue is the
concurrency governor — the frontend never opens its own parallelism, unlike
Infinite which POSTs count× from the browser).

Route C discipline:
  - the task_tracking row is created by the dispatching endpoint
    (``canvases_router``) with business metadata (canvas_id/node_id/kind);
  - phase/status/progress mirror from DBOS via the lifecycle trigger — this
    module NEVER touches them;
  - the durable result (``result_url``/``generated_media_id``) is a business
    decoration field, patched into task metadata by the workflow on success;
  - failures ``raise`` — never a failed-dict (a returned dict would read as
    SUCCESS and strand the UI on a green task with no result).

Provider resolution mirrors G4-B0: the ``mediahub_models`` catalog via
``db_registry`` (jimeng-cli local files / Ark remote URLs); persistence goes
through the Tier-1 generated-media store which mints the durable same-origin
``/cover`` (image) or ``/stream`` (video) URL a bare <img>/<video> can load.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from dbos import DBOS
from loguru import logger

from app.services.library.generated_media_service import (
    GenerationOrigin,
    register_generated_media,
)
from app.services.library.resources_service import _resolve_personal_team_id
from app.workflows.script_shot_generate import _reap_scratch_dir

_KIND_MIME = {"image": "image/png", "video": "video/mp4"}
_KIND_ENDPOINT = {"image": "cover", "video": "stream"}


@DBOS.step(retries_allowed=True, max_attempts=2)
async def generate_canvas_media_step(
    kind: str,
    prompt: str,
    model: str,
    params: Dict[str, Any],
    source_url: Optional[str],
) -> Dict[str, Any]:
    """Run the DB-catalog provider; returns the raw product location.

    ``remote_url`` (Ark) and ``local_path`` (jimeng-cli) are mutually
    exclusive; exactly one is set on success. An explicit caller ``model``
    wins over the catalog row's ``actual_model``.
    """
    from app.services.media.parsers.video_providers import db_registry

    if kind == "video":
        from app.services.library.generated_media_service import (
            resolve_generated_media_local_path,
        )

        provider, actual_model = await db_registry.resolve_video_provider(model or None)
        gen_model = model or actual_model
        image_path = (
            await resolve_generated_media_local_path(source_url, media_kind="image")
            if source_url
            else None
        )
        result = await provider.generate_video(
            prompt=prompt,
            aspect=str(params.get("aspect") or ""),
            model_version=gen_model or None,
            image_path=image_path,
        )
        local_path = getattr(result, "local_path", None)
        if not local_path:
            raise RuntimeError("video provider returned no file")
        return {
            "media_kind": "video",
            "local_path": local_path,
            "remote_url": None,
            "provider": "jimeng-cli",
            "model": gen_model or "",
        }

    provider, actual_model = await db_registry.resolve_image_provider(model or None)
    gen_model = model or actual_model
    result = await provider.generate(
        prompt,
        gen_model,
        aspect_ratio=str(params.get("ratio") or ""),
        reference_image_url=source_url,
    )
    remote_url = getattr(result, "image_url", None) or None
    local_path = getattr(result, "image_path", None) or None
    if not remote_url and not local_path:
        raise RuntimeError("image provider returned neither url nor file")
    return {
        "media_kind": "image",
        "local_path": local_path,
        "remote_url": remote_url,
        "provider": getattr(result, "provider", "") or "",
        "model": gen_model or "",
    }


@DBOS.step()
async def persist_canvas_generation_step(
    media: Dict[str, Any],
    user_id: Optional[str],
    canvas_id: Optional[int],
    node_id: Optional[str],
    prompt: str,
    params: Dict[str, Any],
) -> Dict[str, Any]:
    """Persist the product through the generated-media store → durable URL.

    Registration is REQUIRED (it is the only way to mint a servable URL for
    local files, and it makes remote products durable too) — any failure
    raises (route C). The jimeng scratch dir is reaped after ingest.
    """
    local_path = media.get("local_path")
    try:
        if not user_id:
            raise ValueError("canvas generation persist has no user_id")
        media_kind = str(media.get("media_kind") or "image")
        scope_id = int(await _resolve_personal_team_id(str(user_id)))
        row = await register_generated_media(
            user_id=str(user_id),
            scope_id=scope_id,
            source_path=str(local_path) if local_path else None,
            source_url=(
                str(media.get("remote_url")) if media.get("remote_url") else None
            ),
            mime=_KIND_MIME.get(media_kind, "application/octet-stream"),
            origin=GenerationOrigin(
                kind="canvas_run",
                run_id=None,
                canvas_id=canvas_id,
                node_id=node_id,
                prompt=prompt,
                model=media.get("model"),
                provider=media.get("provider"),
                params=params,
                derivation_kind=f"{media_kind}_gen",
            ),
        )
        gen_id = row.get("id")
        if gen_id is None:
            raise RuntimeError("register_generated_media returned no id")
        result_url = (
            f"/api/v1/generated-media/{gen_id}/"
            f"{_KIND_ENDPOINT.get(media_kind, 'file')}"
        )
        logger.info(
            "[canvas_generation][persist] canvas={} node={} → generated_media {} ({})",
            canvas_id,
            node_id,
            gen_id,
            result_url,
        )
        return {
            "generated_media_id": gen_id,
            "result_url": result_url,
            "media_kind": media_kind,
        }
    finally:
        if local_path:
            _reap_scratch_dir(str(local_path))


@DBOS.step()
async def record_canvas_generation_result_step(result: Dict[str, Any]) -> None:
    """Patch the durable result into the task row (business decoration)."""
    from app.services.infra.unified_task_manager import get_task_manager

    task_id = DBOS.workflow_id
    if not task_id:
        return
    await get_task_manager().patch_metadata(
        task_id,
        {
            "result_url": result.get("result_url"),
            "generated_media_id": result.get("generated_media_id"),
            "media_kind": result.get("media_kind"),
        },
    )


@DBOS.workflow()
async def canvas_generation_workflow(
    kind: str,
    prompt: str,
    model: str,
    params: Dict[str, Any],
    canvas_id: Optional[int],
    node_id: Optional[str],
    user_id: Optional[str],
    source_url: Optional[str] = None,
) -> Dict[str, Any]:
    media = await generate_canvas_media_step(kind, prompt, model, params, source_url)
    result = await persist_canvas_generation_step(
        media=media,
        user_id=user_id,
        canvas_id=canvas_id,
        node_id=node_id,
        prompt=prompt,
        params=params,
    )
    await record_canvas_generation_result_step(result)
    return result
