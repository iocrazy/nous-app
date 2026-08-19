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
    user_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Run the DB-catalog provider; returns the raw product location.

    ``remote_url`` (Ark) and ``local_path`` (jimeng-cli) are mutually
    exclusive; exactly one is set on success. An explicit caller ``model``
    wins over the catalog row's ``actual_model``.
    """
    from app.services.media.parsers.video_providers import db_registry

    if kind == "video":
        from app.services.library.generated_media_service import (
            generated_media_local_path,
        )

        provider, actual_model = await db_registry.resolve_video_provider(
            model or None, user_id=user_id
        )
        # ``model`` is the picker's CATALOG ROW NAME (that's what resolve
        # matched on); upstream must get the row's actual_model. Sending the
        # row name upstream was the 2026-08-18 codex HTTP-400 incident.
        gen_model = actual_model or model
        async with generated_media_local_path(
            source_url, media_kind="image"
        ) as image_path:
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

    provider, actual_model = await db_registry.resolve_image_provider(
        model or None, user_id=user_id
    )
    # Same row-name-vs-actual_model rule as the video branch above.
    gen_model = actual_model or model
    # Multi-reference i2i (IC 图1/图2 semantics): the prompt's full input
    # set rides in params.source_urls; each durable url is materialized to
    # a LOCAL file for providers whose CLI only eats files (codex). The
    # original remote url still goes out as reference_image_url for
    # URL-based providers (ark). IC caps references at 9.
    from contextlib import AsyncExitStack

    from app.services.library.generated_media_service import (
        generated_media_local_path,
    )

    raw_refs = params.get("source_urls")
    ref_urls = [
        u
        for u in (raw_refs if isinstance(raw_refs, list) else [])
        if isinstance(u, str) and u
    ][:9] or ([source_url] if source_url else [])
    async with AsyncExitStack() as stack:
        local_refs: list[str] = []
        for u in ref_urls:
            local = await stack.enter_async_context(
                generated_media_local_path(u, media_kind="image")
            )
            if local:
                local_refs.append(local)
        result = await provider.generate(
            prompt,
            gen_model,
            aspect_ratio=str(params.get("ratio") or ""),
            reference_image_url=source_url,
            reference_image_paths=local_refs or None,
            # IC ⑨ quality pill — consumed by the codex adapter, ignored by
            # providers without a quality knob (ark/jimeng take **kwargs).
            quality=str(params.get("quality") or "") or None,
            resolution=str(params.get("resolution") or "") or None,
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


@DBOS.step()
async def backfill_shot_from_generation_step(
    canvas_id: Optional[int],
    node_id: Optional[str],
    result_url: str,
) -> None:
    """When the completed node is a bound shot node, also write the result
    onto ``script_shots`` — shot three-write-lanes lane (c), the generation
    workflow lane (facts §7). NEVER writes ``script_shot_ops`` — that
    ledger is lane (b), the agent gateway's, and is deliberately excluded
    from human/generation writes (mig 415).

    Node type / ``data.shot_id`` are read server-side from
    ``canvases.nodes_json`` — the workflow never trusts a client-supplied
    node payload at completion time. Reuses
    ``ScriptShotRepository.update_status`` — the SAME write lane
    ``script_shot_generate.py``'s ``mark_shot_done`` already uses; it
    internally fires ``fire_surface_sync_for_shot`` once ``status='done'``
    lands (never raises).

    Best-effort at the lookup layer: no canvas/node target, canvas missing,
    node not found/not a shot, ``data.shot_id`` unset, the shot row gone, or
    a cross-project shot_id (never trust the client) — every one of these
    logs (at most) a warning and returns, so the generation itself still
    succeeds. The actual DB WRITE is NOT best-effort: a failure there
    raises (DBOS retries this step; ``generated_media`` registration
    already happened and is idempotent, so a retry is safe).
    """
    if canvas_id is None or not node_id:
        return

    from app.repositories.canvas_repository import CanvasRepository

    canvas = await CanvasRepository().get_by_id(str(canvas_id))
    if not canvas:
        logger.warning(
            "[canvas_generation][shot-backfill] canvas {} not found, skipping "
            "(node={})",
            canvas_id,
            node_id,
        )
        return

    nodes = canvas.get("nodes_json") or []
    node = next(
        (n for n in nodes if isinstance(n, dict) and str(n.get("id")) == str(node_id)),
        None,
    )
    if not node or node.get("type") != "shot":
        return

    data = node.get("data")
    shot_id = data.get("shot_id") if isinstance(data, dict) else None
    if not shot_id:
        return
    shot_id = str(shot_id)

    from app.repositories.script_shot_repository import get_script_shot_repository

    shot_repo = get_script_shot_repository()
    shot_project_id = await shot_repo.get_project_id(shot_id)
    if shot_project_id is None:
        logger.warning(
            "[canvas_generation][shot-backfill] shot {} not found (canvas={} "
            "node={}), skipping",
            shot_id,
            canvas_id,
            node_id,
        )
        return

    canvas_project_id = canvas.get("project_id")
    if canvas_project_id is None or int(shot_project_id) != int(canvas_project_id):
        logger.warning(
            "[canvas_generation][shot-backfill] shot {} belongs to project {} "
            "but canvas {} belongs to project {}, rejecting cross-project "
            "backfill",
            shot_id,
            shot_project_id,
            canvas_id,
            canvas_project_id,
        )
        return

    await shot_repo.update_status(shot_id, "done", image_url=result_url)
    logger.info(
        "[canvas_generation][shot-backfill] shot {} ← canvas={} node={}",
        shot_id,
        canvas_id,
        node_id,
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
    media = await generate_canvas_media_step(
        kind, prompt, model, params, source_url, user_id
    )
    result = await persist_canvas_generation_step(
        media=media,
        user_id=user_id,
        canvas_id=canvas_id,
        node_id=node_id,
        prompt=prompt,
        params=params,
    )
    await backfill_shot_from_generation_step(
        canvas_id=canvas_id,
        node_id=node_id,
        result_url=str(result.get("result_url")),
    )
    await record_canvas_generation_result_step(result)
    return result
