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


async def _local_engine(
    model_name: str, media_type: str, user_id: Optional[str] = None
) -> tuple[str, str] | None:
    """(engine, actual_model) when the picked catalog row runs on the user's
    own machine via the paired daemon; None for server-side providers.

    Owner scoping (migration 431) is enforced here too, with ``db_registry``'s
    OWN predicate rather than a second copy of it — two visibility rules that
    have to agree is how this class of bug comes back. This check used to be
    reachable only for images and only after ``resolve_image_provider``; the
    local check now runs first and for video as well, so a row the requester
    cannot see must look like "not a local row at all" and fall through to the
    normal resolver, which raises its own scoped error.
    """
    try:
        from app.services.media.parsers.video_providers import db_registry

        rows = await db_registry._enabled_rows(media_type)  # noqa: SLF001
        for row in rows:
            if str(row.get("name")) == model_name:
                if not db_registry._visible_to(row, user_id):  # noqa: SLF001
                    return None
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
    """The catalog key a built image provider was resolved from.

    ``db_registry`` stamps it on as ``provider_key`` at build time (the only
    place that still holds the catalog row). Anything else — a hand-built
    provider, a test double — resolves to no protocol and therefore to
    ``ProviderCapabilities.none()``, which drops every knob AND names them in
    ``dropped_knobs``: wrong, but loudly wrong.
    """
    return str(getattr(provider, "provider_key", "") or "")


def _absolute_media_url(url: str) -> str:
    """The daemon fetches refs over the public API, so relative durable urls
    must be absolutised (it only accepts nous' own host — spec §10 SSRF)."""
    if url.startswith("http://") or url.startswith("https://"):
        return url
    from app.core.config import settings

    base = str(getattr(settings, "PUBLIC_API_BASE", "") or "https://api.nous.ink")
    return f"{base.rstrip('/')}{url}"


# ---------------------------------------------------------------------------
# Reference resolution (asset-library P4 — the resource bridge)
# ---------------------------------------------------------------------------
#
# ``eff.refs`` now carries TWO durable shapes: ``/api/v1/generated-media/{id}/…``
# (an earlier generation) and ``/api/v1/resources/{id}/(cover|file)`` (a library
# file an asset node contributed). Both are resolved here, through ONE
# classifier, so a branch cannot accidentally understand one shape and not the
# other.
#
# Every reference that does not resolve is REPORTED in ``dropped_refs`` beside
# ``dropped_knobs``. Before this, an unresolvable ref left no trace anywhere:
# not in the picture, not in the record, not on the node — the "选了也生成了但
# 图里没有" failure this repo has already filed once.
#
# The reason vocabulary (machine-readable; the UI labels them):
#   unknown_shape     — not a durable reference URL we serve (foreign host too)
#   not_in_scope      — the resource row is not in this generation's scope
#   no_image_file     — no materializable image bytes behind the row
#   materialize_failed— bytes exist, reading them failed
#   scope_unresolved  — the run has no user / no personal team to check against
#   unresolved        — the generated-media bridge yielded nothing and does not
#                       report which of missing-row / wrong-kind / unreadable
#                       applied (it answers Optional[str] by design, and three
#                       other callers depend on that)


async def _generation_scope_id(user_id: Optional[str]) -> Optional[int]:
    """The scope a resource reference must belong to, or None.

    The workflow carries no ``scope_id``: ``persist_canvas_generation_step``
    and the daemon dispatch BOTH derive it as the user's personal team, and so
    does ``POST /canvases/assets/zip`` when it scope-checks generated-media ids.
    Deriving it a fourth way here would be a fourth definition of "this
    generation's scope" — so this reuses the same one.

    None (no user_id, or no personal team row) is reported as
    ``scope_unresolved`` rather than treated as "everything allowed": a scope
    check that cannot run has not passed.
    """
    if not user_id:
        return None
    try:
        return int(await _resolve_personal_team_id(str(user_id)))
    except Exception as exc:  # no personal team row / DB unreachable
        logger.warning(
            "[canvas_generation][refs] could not resolve scope for user {}: {}",
            user_id,
            exc,
        )
        return None


async def _resolve_reference_paths(
    stack: Any, refs: tuple[str, ...] | list[str], *, user_id: Optional[str]
) -> tuple[list[str], list[Dict[str, Any]]]:
    """``(local_paths, dropped_refs)`` for the branches that need FILES.

    The scope is resolved at most once, and only when a resource-shaped
    reference is actually present — a run whose refs are all generated-media
    must not start failing because the personal-team lookup is unavailable.
    """
    from app.services.library.generated_media_service import (
        classify_reference_url,
        generated_media_local_path,
        resource_local_path,
    )

    local: list[str] = []
    dropped: list[Dict[str, Any]] = []
    scope_id: Optional[int] = None
    scope_resolved = False
    for url in refs:
        kind, _row_id = classify_reference_url(url)
        if kind == "genmedia":
            path = await stack.enter_async_context(
                generated_media_local_path(url, media_kind="image")
            )
            if path:
                local.append(path)
            else:
                dropped.append({"url": str(url), "reason": "unresolved"})
            continue
        if kind == "resource":
            if not scope_resolved:
                scope_id = await _generation_scope_id(user_id)
                scope_resolved = True
            if scope_id is None:
                dropped.append({"url": str(url), "reason": "scope_unresolved"})
                continue
            out = await stack.enter_async_context(
                resource_local_path(url, scope_id=scope_id, media_kind="image")
            )
            if out.path:
                local.append(out.path)
            else:
                dropped.append(
                    {"url": str(url), "reason": out.reason or "no_image_file"}
                )
            continue
        dropped.append({"url": str(url), "reason": "unknown_shape"})
    return local, dropped


async def _resolve_reference_urls(
    refs: tuple[str, ...] | list[str], *, user_id: Optional[str]
) -> tuple[list[str], list[Dict[str, Any]]]:
    """``(absolute_urls, dropped_refs)`` for the DAEMON branch.

    The daemon fetches references over the public API, so it wants URLs, not
    paths — but a resource URL still has to clear the same two gates first
    (in scope, has image bytes) before it is handed to the user's machine.
    Generated-media URLs keep exactly their previous treatment: absolutise and
    go, with no extra round trip.
    """
    from app.services.library.generated_media_service import (
        classify_reference_url,
        resource_reference_reason,
    )

    urls: list[str] = []
    dropped: list[Dict[str, Any]] = []
    scope_id: Optional[int] = None
    scope_resolved = False
    for url in refs:
        kind, _row_id = classify_reference_url(url)
        if kind == "genmedia":
            urls.append(_absolute_media_url(url))
            continue
        if kind == "resource":
            if not scope_resolved:
                scope_id = await _generation_scope_id(user_id)
                scope_resolved = True
            if scope_id is None:
                dropped.append({"url": str(url), "reason": "scope_unresolved"})
                continue
            reason = await resource_reference_reason(
                url, scope_id=scope_id, media_kind="image"
            )
            if reason:
                dropped.append({"url": str(url), "reason": reason})
                continue
            urls.append(_absolute_media_url(url))
            continue
        dropped.append({"url": str(url), "reason": "unknown_shape"})
    return urls, dropped


@DBOS.step(retries_allowed=True, max_attempts=2)
async def generate_canvas_media_step(
    kind: str,
    prompt: str,
    model: str,
    params: Dict[str, Any],
    source_url: Optional[str],
    user_id: Optional[str] = None,
    canvas_id: Optional[int] = None,
    node_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Run the DB-catalog provider; returns the raw product location.

    ``remote_url`` (Ark) and ``local_path`` (jimeng-cli) are mutually
    exclusive; exactly one is set on success. An explicit caller ``model``
    wins over the catalog row's ``actual_model``.

    ``canvas_id``/``node_id`` are needed by the DAEMON branch alone: that
    product is registered by the upload endpoint rather than by
    ``persist_canvas_generation_step``, so the attribution the persist step
    would have written has to travel out with the job instead.
    """
    from app.services.media.parsers.video_providers import db_registry

    # Parse the caller's knobs ONCE; branches read this object rather than
    # re-deriving their own dict out of ``params`` (the drift this contract
    # exists to end). All four arms now do the same three things — resolve a
    # provider, ``reconcile`` against its capabilities, build from ``eff`` —
    # and every one of them returns the resulting ``dropped_knobs``:
    #   - the DAEMON branch (first below — "whose machine?" is the routing
    #     question, asked before the kind-specific server branches) reconciles
    #     once above its codex/dreamina split, so both arms report the same
    #     ``dropped_knobs`` and both build from ``eff`` alone;
    #   - server VIDEO reconciles against the video provider's capabilities;
    #   - server IMAGE (last) against the image provider's.
    req = GenerationRequest.from_params(
        kind=kind, prompt=prompt, model=model, params=params, source_url=source_url
    )

    # C 方案: a catalog row whose actual_provider is 'codex-local' is not a
    # server-side provider at all — the work runs on the USER's machine via
    # their paired daemon (spec §6). Offline is a typed failure at dispatch
    # time, not a hang.
    local = (
        await _local_engine(
            model,
            kind if kind in ("image", "video") else "image",
            user_id=user_id,
        )
        if (model or "").strip()
        else None
    )
    if local:
        engine, engine_model = local
        from app.services.codex.daemon_dispatch import dispatch_to_daemon

        # Capabilities live under the catalog's actual_provider, so map the
        # engine back to it. Neither name in hand is that key: ``engine_model``
        # ("gpt-image-2") resolves to no protocol, and ``engine`` only appears
        # to work — "codex" happens to hit the SERVER protocol (same knobs
        # today, a coincidence), while "dreamina" hits nothing. A miss
        # collapses to ``none()``, which drops the ratio again — the very bug
        # this branch is here to fix.
        caps = await _capabilities_for(
            "codex-local" if engine == "codex" else "jimeng-local"
        )
        eff, dropped = req.reconcile(caps)

        ref_urls, dropped_refs = await _resolve_reference_urls(
            eff.refs, user_id=user_id
        )

        if engine == "dreamina":
            # Build the exact dreamina argv server-side (single source of
            # truth: the same pure builders the server provider uses). Refs
            # become {ref:N} placeholders the daemon swaps for local paths.
            from app.services.media.parsers.video_providers.jimeng_cli import (
                build_image_args,
                build_video_args,
            )

            placeholders = [f"{{ref:{i}}}" for i in range(len(ref_urls))]
            if eff.kind == "video":
                # Same three-way choice the server video branch makes: frames
                # → first/last (frames2video), multimodal → every ref
                # (multimodal2video 全能参考), otherwise the single source
                # drives image2video / text2video. Handing every ref to
                # ``image_paths`` regardless — what this did before — turned a
                # first/last-frame request into a multimodal one in silence.
                frame_kwargs: dict
                if eff.video_mode == "frames" and len(placeholders) >= 2:
                    frame_kwargs = {
                        "first_frame": placeholders[0],
                        "last_frame": placeholders[1],
                    }
                elif eff.video_mode == "multimodal" and placeholders:
                    frame_kwargs = {"image_paths": placeholders}
                else:
                    frame_kwargs = {
                        "image_path": placeholders[0] if placeholders else None
                    }
                submit_args = build_video_args(
                    prompt=eff.prompt,
                    aspect=eff.ratio or "",
                    poll=90,
                    duration=eff.duration,
                    model_version=engine_model or None,
                    resolution=eff.resolution,
                    **frame_kwargs,
                )
            else:
                submit_args = build_image_args(
                    prompt=eff.prompt,
                    aspect=eff.ratio or "",
                    poll=60,
                    resolution_type=eff.resolution,
                    model_version=engine_model or None,
                )
            payload = {
                "engine": "dreamina",
                "submit_args": submit_args,
                "media_kind": kind,
                "ref_urls": ref_urls,
            }
        else:
            # Every knob the caller picked, reconciled once and sent as one
            # shape. It used to read ``params.get("size")`` (the frontend only
            # ever sends ``ratio``) and ``params.get("actual_model")`` (nothing
            # sets it) — both resolved to "" and the daemon fell back to its
            # own default, which is why a 16:9 pick came back portrait.
            payload = eff.to_codex_daemon_payload(
                engine_model=engine_model, ref_urls=ref_urls
            )

        # The same five identity fields ``persist_canvas_generation_step``
        # stamps on a server-side product, plus the three-part contract. It
        # rides the ticket because the daemon's upload — not this workflow —
        # is what creates the row. Deliberately NOT the payload: that carries
        # ref urls and the augmented prompt, and this sits in Redis.
        result = await dispatch_to_daemon(
            user_id=str(user_id),
            scope_id=int(await _resolve_personal_team_id(str(user_id))),
            kind=kind if kind in ("image", "video") else "image",
            payload=payload,
            attribution={
                "canvas_id": canvas_id,
                "node_id": node_id,
                "prompt": prompt,
                # The RESOLVED model, exactly as the server branches record
                # ``gen_model = actual_model or model``. Writing the catalog
                # ROW NAME here (what this used to do) made
                # ``generated_media.model`` mean two different things
                # depending on which branch wrote the row — unqueryable, and
                # querying these records is the whole point.
                "model": engine_model or model or "",
                "provider": f"{engine}-local",
                "requested": req.knobs_dict(),
                "effective": eff.knobs_dict(),
                "dropped": dropped,
            },
        )
        return {
            "media_kind": kind if kind in ("image", "video") else "image",
            "local_path": None,
            "remote_url": None,
            "existing_gen_id": result.get("gen_id"),
            "provider": f"{engine}-local",
            "model": engine_model or model or "",
            "dropped_knobs": dropped,
            "dropped_refs": dropped_refs,
            # Both halves ride along as JSON-safe primitives: DBOS persists a
            # step's return value, and the record downstream is only worth
            # keeping if it can tell "we never sent it" from "they ignored it".
            "requested_params": req.knobs_dict(),
            "effective_params": eff.knobs_dict(),
        }

    if kind == "video":
        provider, actual_model = await db_registry.resolve_video_provider(
            model or None, user_id=user_id
        )
        # ``model`` is the picker's CATALOG ROW NAME (that's what resolve
        # matched on); upstream must get the row's actual_model. Sending the
        # row name upstream was the 2026-08-18 codex HTTP-400 incident.
        gen_model = actual_model or model
        # Same reconcile the image branch does, against the video protocol's
        # capabilities: a mode or ratio this provider cannot honour is dropped
        # HERE and NAMED. Before this, the branch read ``params`` raw and
        # always reported an empty ``dropped_knobs`` — a frames2video request
        # to a provider without frames2video went out as one anyway.
        caps = await _capabilities_for(_actual_provider_of(provider))
        eff, dropped = req.reconcile(caps)
        # IC video modes: eff.video_mode picks the CLI command family —
        # 'frames' maps the first two refs to first/last (frames2video),
        # 'multimodal' hands ALL refs over (multimodal2video 全能参考);
        # otherwise the single source drives image2video / text2video.
        # ``reconcile`` has already emptied ``eff.refs`` for a provider that
        # declares no video mode at all, so that arm downloads nothing.
        from contextlib import AsyncExitStack

        async with AsyncExitStack() as stack:
            local_refs, dropped_refs = await _resolve_reference_paths(
                stack, eff.refs, user_id=user_id
            )
            kwargs: dict = {
                "prompt": eff.prompt,
                "aspect": eff.ratio or "",
                "model_version": gen_model or None,
                "duration": eff.duration,
                "resolution": eff.resolution,
            }
            if eff.video_mode == "frames" and len(local_refs) >= 2:
                kwargs["first_frame"] = local_refs[0]
                kwargs["last_frame"] = local_refs[1]
            elif eff.video_mode == "multimodal" and local_refs:
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
            "dropped_knobs": dropped,
            "dropped_refs": dropped_refs,
            "requested_params": req.knobs_dict(),
            "effective_params": eff.knobs_dict(),
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

    async with AsyncExitStack() as stack:
        local_refs, dropped_refs = await _resolve_reference_paths(
            stack, eff.refs, user_id=user_id
        )
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
        "dropped_refs": dropped_refs,
        "requested_params": req.knobs_dict(),
        "effective_params": eff.knobs_dict(),
    }


async def _outcome_of(media: Dict[str, Any], media_kind: str) -> Dict[str, Any]:
    """The four-key outcome block for a product that has just been made.

    Measurement is best-effort by construction: the asset already exists and
    has already been paid for, so a probe that cannot read it records
    ``measured: null`` (and therefore no verdict) rather than failing a run
    that succeeded. Remote-url products (ark) are not on disk at this point,
    so they take that same honest ``null`` — P2 measures what it can reach.

    That null is STRUCTURAL for remote-url providers, not a probe that
    happened to fail: we do not hold the bytes here at all. Measuring after
    ingest is a real follow-up — ``register_generated_media`` does fetch them
    — but it is a shared choke point every generation goes through, and the
    one provider it would serve has had no production traffic in 90 days, so
    it is deliberately out of this branch's scope. Any query over these
    records must therefore treat ``honored: null`` as "no verdict", never as
    a failure (see the plan's Task 5 Step 2, and its divergence from spec
    §6.2's "其余三条必须相符").
    """
    from app.services.generation.measure import measure_image, measure_video
    from app.services.generation.outcome import build_outcome_params_from_dicts

    measured = None
    local_path = media.get("local_path")
    if local_path:
        try:
            measured = (
                measure_image(str(local_path))
                if media_kind == "image"
                else await measure_video(str(local_path))
            )
        except Exception as exc:  # a broken probe must not break a good run
            logger.warning("[canvas_generation][persist] measure failed: {}", exc)
    return build_outcome_params_from_dicts(
        requested=media.get("requested_params") or {},
        effective=media.get("effective_params") or {},
        dropped=list(media.get("dropped_knobs") or []),
        measured=measured,
    )


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

    The product is measured here, on the way in, and the outcome block is
    merged into the params the row keeps: this is the last point where the
    file is still on disk (``finally`` reaps the scratch dir below).
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
            "dropped_refs": list(media.get("dropped_refs") or []),
        }

    local_path = media.get("local_path")
    try:
        if not user_id:
            raise ValueError("canvas generation persist has no user_id")
        media_kind = str(media.get("media_kind") or "image")
        scope_id = int(await _resolve_personal_team_id(str(user_id)))
        params = {**(params or {}), **await _outcome_of(media, media_kind)}
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
            # Orthogonal to dropped_knobs and reported beside it, never nested
            # inside it: a run can drop a knob, drop a reference, or both, and
            # a caller that reads one and not the other reads a truncated run
            # as a clean one.
            "dropped_refs": list(media.get("dropped_refs") or []),
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
            # References the run could not resolve, each with its reason
            # ({url, reason}). Same terminal point as dropped_knobs so the
            # frontend reads both from one metadata read — reading one and
            # not the other is how a backend field ends up with no consumer.
            "dropped_refs": list(result.get("dropped_refs") or []),
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
        kind, prompt, model, params, source_url, user_id, canvas_id, node_id
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
