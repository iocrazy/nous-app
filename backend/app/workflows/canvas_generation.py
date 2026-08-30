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

from typing import TYPE_CHECKING, Any, Dict, Optional

from dbos import DBOS
from loguru import logger

from app.services.generation.request import GenerationRequest
from app.services.library.generated_media_service import (
    GenerationOrigin,
    register_generated_media,
)
from app.services.library.resources_service import _resolve_personal_team_id
from app.workflows.script_shot_generate import _reap_scratch_dir

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.services.ai.provider_protocols.base import ProviderCapabilities

_KIND_MIME = {"image": "image/png", "video": "video/mp4"}
_KIND_ENDPOINT = {"image": "cover", "video": "stream"}


_LOCAL_ENGINES = {"codex-local": "codex", "jimeng-local": "dreamina"}


async def _local_engine(model_name: str, media_type: str) -> tuple[str, str] | None:
    """(engine, actual_model) when the picked catalog row runs on the user's
    own machine via the paired daemon; None for server-side providers."""
    try:
        from app.services.media.parsers.video_providers import db_registry

        rows = await db_registry._enabled_rows(media_type)  # noqa: SLF001
        for row in rows:
            if str(row.get("name")) == model_name:
                engine = _LOCAL_ENGINES.get(
                    str(row.get("actual_provider") or "").lower()
                )
                if engine:
                    return engine, str(row.get("actual_model") or "")
                return None
    except Exception:
        return None
    return None


async def _capabilities_for(actual_provider: str) -> "ProviderCapabilities":
    """Capabilities of the protocol serving `actual_provider`; restrictive
    default when unknown (drops loudly rather than ignoring quietly)."""
    from app.services.ai.provider_protocols import resolve_generation_protocol
    from app.services.ai.provider_protocols.base import ProviderCapabilities

    proto = resolve_generation_protocol((actual_provider or "").lower())
    return proto.capabilities if proto else ProviderCapabilities.none()


def _actual_provider_of(provider: Any) -> str:
    """The catalog key a built image provider was resolved from. Providers
    built by a protocol carry it as `provider_key`; older ones fall back to
    their `provider` name (codex / jimeng-cli / ark ⇒ doubao)."""
    key = getattr(provider, "provider_key", None) or getattr(provider, "provider", "")
    key = key or ""
    return {"ark": "doubao"}.get(str(key), str(key))


def _absolute_media_url(url: str) -> str:
    """The daemon fetches refs over the public API, so relative durable urls
    must be absolutised (it only accepts nous' own host — spec §10 SSRF)."""
    if url.startswith("http://") or url.startswith("https://"):
        return url
    from app.core.config import settings

    base = str(getattr(settings, "PUBLIC_API_BASE", "") or "https://api.nous.ink")
    return f"{base.rstrip('/')}{url}"


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

    # Parse the caller's knobs ONCE; branches read this object rather than
    # re-deriving their own dict out of ``params`` (the drift this contract
    # exists to end). Only the server IMAGE branch reconciles against
    # provider capabilities so far — the other three still send what they
    # always sent and report an empty ``dropped_knobs``.
    req = GenerationRequest.from_params(
        kind=kind, prompt=prompt, model=model, params=params, source_url=source_url
    )

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
        # IC video modes: params.video_mode picks the CLI command family —
        # 'frames' maps the first two refs to first/last (frames2video),
        # 'multimodal' hands ALL refs over (multimodal2video 全能参考);
        # otherwise the single source drives image2video / text2video.
        from contextlib import AsyncExitStack

        raw_refs = params.get("source_urls")
        ref_urls = [
            u
            for u in (raw_refs if isinstance(raw_refs, list) else [])
            if isinstance(u, str) and u
        ][:9] or ([source_url] if source_url else [])
        video_mode = str(params.get("video_mode") or "")
        raw_duration = params.get("duration")
        async with AsyncExitStack() as stack:
            local_refs: list[str] = []
            for u in ref_urls:
                local = await stack.enter_async_context(
                    generated_media_local_path(u, media_kind="image")
                )
                if local:
                    local_refs.append(local)
            kwargs: dict = {
                "prompt": prompt,
                "aspect": str(params.get("aspect") or ""),
                "model_version": gen_model or None,
                "duration": int(raw_duration) if raw_duration else None,
                "resolution": str(params.get("resolution") or "") or None,
            }
            if video_mode == "frames" and len(local_refs) >= 2:
                kwargs["first_frame"] = local_refs[0]
                kwargs["last_frame"] = local_refs[1]
            elif video_mode == "multimodal" and local_refs:
                kwargs["image_paths"] = local_refs
            else:
                kwargs["image_path"] = local_refs[0] if local_refs else None
            result = await provider.generate_video(**kwargs)
        local_path = getattr(result, "local_path", None)
        if not local_path:
            raise RuntimeError("video provider returned no file")
        return {
            "media_kind": "video",
            "local_path": local_path,
            "remote_url": None,
            "provider": "jimeng-cli",
            "model": gen_model or "",
            "dropped_knobs": [],
        }

    # C 方案: a catalog row whose actual_provider is 'codex-local' is not a
    # server-side provider at all — the work runs on the USER's machine via
    # their paired daemon (spec §6). Offline is a typed failure at dispatch
    # time, not a hang.
    local = (
        await _local_engine(model, kind if kind in ("image", "video") else "image")
        if (model or "").strip()
        else None
    )
    if local:
        engine, engine_model = local
        from app.services.codex.daemon_dispatch import dispatch_to_daemon

        raw_refs = params.get("source_urls")
        ref_urls = [
            u
            for u in (raw_refs if isinstance(raw_refs, list) else [])
            if isinstance(u, str) and u
        ][:9] or ([source_url] if source_url else [])
        ref_urls = [_absolute_media_url(u) for u in ref_urls]

        if engine == "dreamina":
            # Build the exact dreamina argv server-side (single source of
            # truth: the same pure builders the server provider uses). Refs
            # become {ref:N} placeholders the daemon swaps for local paths.
            from app.services.media.parsers.video_providers.jimeng_cli import (
                build_image_args,
                build_video_args,
            )

            placeholders = [f"{{ref:{i}}}" for i in range(len(ref_urls))]
            if kind == "video":
                submit_args = build_video_args(
                    prompt=prompt,
                    aspect=str(params.get("aspect") or params.get("ratio") or ""),
                    poll=90,
                    image_paths=placeholders,
                    duration=(
                        int(params["duration"]) if params.get("duration") else None
                    ),
                    model_version=engine_model or None,
                    resolution=str(params.get("resolution") or "") or None,
                )
            else:
                submit_args = build_image_args(
                    prompt=prompt,
                    aspect=str(params.get("ratio") or ""),
                    poll=60,
                    resolution_type=str(params.get("resolution") or "") or None,
                    model_version=engine_model or None,
                )
            payload = {
                "engine": "dreamina",
                "submit_args": submit_args,
                "media_kind": kind,
                "ref_urls": ref_urls,
            }
        else:
            payload = {
                "engine": "codex",
                "prompt": prompt,
                "size": str(params.get("size") or ""),
                "model": str(params.get("actual_model") or ""),
                "ref_urls": ref_urls,
            }

        result = await dispatch_to_daemon(
            user_id=str(user_id),
            scope_id=int(await _resolve_personal_team_id(str(user_id))),
            kind=kind if kind in ("image", "video") else "image",
            payload=payload,
        )
        return {
            "media_kind": kind if kind in ("image", "video") else "image",
            "local_path": None,
            "remote_url": None,
            "existing_gen_id": result.get("gen_id"),
            "provider": f"{engine}-local",
            "model": model or "",
            "dropped_knobs": [],
        }

    provider, actual_model = await db_registry.resolve_image_provider(
        model or None, user_id=user_id
    )
    # Same row-name-vs-actual_model rule as the video branch above.
    gen_model = actual_model or model
    # Knobs this provider cannot honour are dropped HERE, once, and named in
    # ``dropped_knobs`` — a 21:9 that ark would quietly render as a square is
    # a lie the caller never sees otherwise.
    caps = await _capabilities_for(_actual_provider_of(provider))
    eff, dropped = req.reconcile(caps)
    # Multi-reference i2i (IC 图1/图2 semantics): the prompt's full input
    # set rides in params.source_urls; each durable url is materialized to
    # a LOCAL file for providers whose CLI only eats files (codex). The
    # original remote url still goes out as reference_image_url for
    # URL-based providers (ark). IC caps references at 9. Iterating
    # ``eff.refs`` means a provider with max_refs=0 never downloads one.
    from contextlib import AsyncExitStack

    from app.services.library.generated_media_service import (
        generated_media_local_path,
    )

    async with AsyncExitStack() as stack:
        local_refs: list[str] = []
        for u in eff.refs:
            local = await stack.enter_async_context(
                generated_media_local_path(u, media_kind="image")
            )
            if local:
                local_refs.append(local)
        result = await provider.generate(
            prompt,
            gen_model,
            aspect_ratio=eff.ratio or "",
            reference_image_url=source_url if eff.refs else None,
            reference_image_paths=local_refs or None,
            # IC ⑨ quality pill — consumed by the codex adapter, ignored by
            # providers without a quality knob (ark/jimeng take **kwargs).
            quality=eff.quality,
            resolution=eff.resolution,
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
        "dropped_knobs": dropped,
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
    # C 方案: a daemon-produced file was already registered by the upload
    # endpoint (it holds the bytes, we never did) — skip re-registration and
    # just mint the URL shape the rest of the chain expects.
    existing_gen_id = media.get("existing_gen_id")
    if existing_gen_id:
        media_kind = str(media.get("media_kind") or "image")
        endpoint = "cover" if media_kind == "image" else "stream"
        return {
            "generated_media_id": str(existing_gen_id),
            "result_url": f"/api/v1/generated-media/{existing_gen_id}/{endpoint}",
            "media_kind": media_kind,
            "provider": str(media.get("provider") or ""),
            "model": str(media.get("model") or ""),
            "dropped_knobs": list(media.get("dropped_knobs") or []),
        }

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
            "dropped_knobs": list(media.get("dropped_knobs") or []),
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
            # Business decoration only (route C): the lifecycle columns stay
            # the trigger's. An empty list is the honest "nothing dropped".
            "dropped_knobs": list(result.get("dropped_knobs") or []),
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
