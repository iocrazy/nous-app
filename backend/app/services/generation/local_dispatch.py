"""Run one generation on the user's OWN machine through their paired daemon.

Extracted from ``workflows/canvas_generation.py`` so every caller that can
route a catalog row to the user's machine (engines ``codex`` and ``dreamina``)
goes through ONE seam: the engine lookup, the provider-card gate, the
capability reconcile, the argv/payload build, the dispatch, and the failure
recording. The canvas keeps what is canvas-specific: its scope rule, its
reference resolution, and the attribution it stamps on the ticket.

Call order for a caller (the canvas is the reference):

1. ``resolve_local_engine(model_name, media_type, user_id)`` - ``None`` means
   "not a local row", fall through to the server-side resolver;
2. ``reconcile_for_engine(engine, request)`` - ``(eff, dropped_knobs)``;
3. resolve ``eff.refs`` to absolute URLs the daemon may fetch (caller-owned:
   the scope a reference must belong to is the caller's rule);
4. ``dispatch_local_generation(...)`` - ``{"gen_id": ...}`` on success,
   ``{"failed": <one ASCII line>}`` for a deterministic failure (the caller
   must hand that back to its workflow, which raises via ``raise_if_failed``),
   and a plain ``RuntimeError`` raise for anything worth a DBOS retry.

Failures are recorded on the ambient workflow's task row (``DBOS.workflow_id``)
under ``metadata.failure`` - business decoration, route C section 3. The
lifecycle columns are never touched here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Optional

from dbos import DBOS
from loguru import logger

from app.services.codex.daemon_dispatch import DEFAULT_TIMEOUT_S

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.services.ai.provider_protocols.base import ProviderCapabilities
    from app.services.generation.request import GenerationRequest


# catalog ``actual_provider`` -> daemon engine.
LOCAL_ENGINES = {"codex-local": "codex", "jimeng-local": "dreamina"}
# ...and back: capabilities live under the catalog key, not the engine name.
_ENGINE_PROVIDER_KEY = {engine: key for key, engine in LOCAL_ENGINES.items()}

# The daemon's own budget for one dreamina job - tools/codex-daemon/index.mjs
# ``runDreaminaJob``: ``runCommand('dreamina', ..., {timeoutMs: 20 * 60_000})``
# for the generation, then ``query_result`` with ``timeoutMs: 5 * 60_000``.
# Change these together with that file.
DAEMON_DREAMINA_RUN_BUDGET_S = 20 * 60
DAEMON_DREAMINA_QUERY_BUDGET_S = 5 * 60
# Ref downloads before the run, the product upload after it, and the Redis
# hops either side - none of which the daemon's timers count.
DAEMON_GRACE_S = 120
VIDEO_DISPATCH_TIMEOUT_S = (
    DAEMON_DREAMINA_RUN_BUDGET_S + DAEMON_DREAMINA_QUERY_BUDGET_S + DAEMON_GRACE_S
)
IMAGE_DISPATCH_TIMEOUT_S = DEFAULT_TIMEOUT_S


def dispatch_timeout_for(media_kind: str) -> float:
    """How long to wait for the daemon's answer.

    Video waits out the daemon's whole dreamina budget plus grace: giving up
    earlier abandons a job the user's machine is still legitimately running
    (and paying for). Image keeps the long-standing default.
    """
    if media_kind == "video":
        return VIDEO_DISPATCH_TIMEOUT_S
    return IMAGE_DISPATCH_TIMEOUT_S


# Failures whose second attempt is guaranteed to be the first attempt again,
# or worse than useless:
#   content_refused        - the model's ANSWER to these exact words; a retry
#                            buys a second daemon job on the user's quota
#                            (measured 2026-09-05: every refusal ran twice).
#   provider_card_disabled - the card is still off one second later.
#   daemon_offline         - nobody starts their daemon in DBOS's retry gap.
#   daemon_timeout         - the job was delivered and may still be running;
#                            a retry re-submits a PAID job after a wait of up
#                            to ``VIDEO_DISPATCH_TIMEOUT_S``, and the first one
#                            can still upload its product later.
NON_RETRYABLE_FAILURE_CODES = frozenset(
    {"content_refused", "provider_card_disabled", "daemon_offline", "daemon_timeout"}
)


async def resolve_local_engine(
    model_name: str, media_type: str, user_id: Optional[str] = None
) -> tuple[str, str] | None:
    """(engine, actual_model) when the picked catalog row runs on the user's
    own machine via the paired daemon; None for server-side providers.

    Owner scoping (migration 431) is enforced here too, with ``db_registry``'s
    OWN predicate rather than a second copy of it - two visibility rules that
    have to agree is how this class of bug comes back. A row the requester
    cannot see must look like "not a local row at all" and fall through to
    the normal resolver, which raises its own scoped error.
    """
    try:
        from app.services.media.parsers.video_providers import db_registry

        rows = await db_registry._enabled_rows(media_type)  # noqa: SLF001
        for row in rows:
            if str(row.get("name")) == model_name:
                if not db_registry._visible_to(row, user_id):  # noqa: SLF001
                    return None
                engine = LOCAL_ENGINES.get(
                    str(row.get("actual_provider") or "").lower()
                )
                if engine:
                    actual = str(row.get("actual_model") or "")
                    # The user's Codex provider card (Settings > AI >
                    # Providers) names the orchestrator model; it beats the
                    # catalog default. Codex only: dreamina has no such knob.
                    if engine == "codex" and user_id:
                        from app.services.codex import provider_card

                        actual = (
                            await provider_card.codex_orchestrator_model(str(user_id))
                        ) or actual
                    return engine, actual
                return None
    except Exception:
        return None
    return None


async def require_provider_card(engine: str, user_id: Optional[str]) -> None:
    """The Providers page is the one management entry (2026-09-06): a codex
    run for a user whose Codex card is off is refused with a typed, actionable
    ``ProviderCardDisabledError`` - never run on the strength of the daemon
    merely being online. Dreamina has no card yet; nothing to require."""
    if engine != "codex" or not user_id:
        return
    from app.services.codex import provider_card

    if not await provider_card.card_enabled(str(user_id)):
        raise provider_card.ProviderCardDisabledError()


async def capabilities_for(actual_provider: str) -> "ProviderCapabilities":
    """Capabilities of the protocol serving `actual_provider`; restrictive
    default when unknown (drops loudly rather than ignoring quietly)."""
    from app.services.ai.provider_protocols import resolve_generation_protocol
    from app.services.ai.provider_protocols.base import ProviderCapabilities

    proto = resolve_generation_protocol((actual_provider or "").lower())
    return proto.capabilities if proto else ProviderCapabilities.none()


async def reconcile_for_engine(
    engine: str, request: "GenerationRequest"
) -> tuple["GenerationRequest", list[str]]:
    """``request.reconcile`` against the local engine's capabilities.

    Capabilities live under the catalog's actual_provider, so the engine is
    mapped back to it. Neither name a caller has in hand is that key:
    ``engine_model`` ("gpt-image-2") resolves to no protocol, and ``engine``
    only appears to work - "codex" happens to hit the SERVER protocol, while
    "dreamina" hits nothing. A miss collapses to ``none()``, which drops the
    ratio again.
    """
    caps = await capabilities_for(_ENGINE_PROVIDER_KEY.get(engine, engine))
    return request.reconcile(caps)


def build_local_payload(
    *,
    engine: str,
    engine_model: str,
    media_kind: str,
    request: "GenerationRequest",
    ref_urls: list[str],
) -> Dict[str, Any]:
    """The job payload the daemon receives. ``request`` is the RECONCILED one.

    dreamina: the exact argv is built server-side from the same pure builders
    the server provider uses (single source of truth); refs become ``{ref:N}``
    placeholders the daemon swaps for the files it downloaded.
    codex: every knob the caller picked, reconciled once and sent as one shape.
    """
    if engine != "dreamina":
        return request.to_codex_daemon_payload(
            engine_model=engine_model, ref_urls=ref_urls
        )

    from app.services.media.parsers.video_providers.jimeng_cli import (
        build_image_args,
        build_video_args,
    )

    placeholders = [f"{{ref:{i}}}" for i in range(len(ref_urls))]
    if request.kind == "video":
        # Same three-way choice the server video branch makes: frames ->
        # first/last (frames2video), multimodal -> every ref (multimodal2video),
        # otherwise the single source drives image2video / text2video. Handing
        # every ref to ``image_paths`` regardless turned a first/last-frame
        # request into a multimodal one in silence.
        frame_kwargs: dict
        if request.video_mode == "frames" and len(placeholders) >= 2:
            frame_kwargs = {
                "first_frame": placeholders[0],
                "last_frame": placeholders[1],
            }
        elif request.video_mode == "multimodal" and placeholders:
            frame_kwargs = {"image_paths": placeholders}
        else:
            frame_kwargs = {"image_path": placeholders[0] if placeholders else None}
        submit_args = build_video_args(
            prompt=request.prompt,
            aspect=request.ratio or "",
            poll=90,
            duration=request.duration,
            model_version=engine_model or None,
            resolution=request.resolution,
            **frame_kwargs,
        )
    else:
        submit_args = build_image_args(
            prompt=request.prompt,
            aspect=request.ratio or "",
            poll=60,
            resolution_type=request.resolution,
            model_version=engine_model or None,
        )
    return {
        "engine": "dreamina",
        "submit_args": submit_args,
        "media_kind": media_kind,
        "ref_urls": ref_urls,
    }


async def dispatch_local_generation(
    *,
    engine: str,
    engine_model: str,
    media_kind: str,
    request: "GenerationRequest",
    ref_urls: list[str],
    user_id: str,
    scope_id: int,
    attribution: Dict[str, Any],
    timeout_s: Optional[float] = None,
) -> Dict[str, Any]:
    """Gate, build, send, wait. ``{"gen_id": ...}`` or ``{"failed": msg}``.

    ``request`` must already be reconciled (``reconcile_for_engine``) - the
    caller needs ``eff.refs`` before it can resolve ``ref_urls``.
    ``scope_id`` / ``attribution`` ride the upload ticket: the daemon's upload
    endpoint, not the caller, creates the ``generated_media`` row.
    ``timeout_s`` defaults to ``dispatch_timeout_for(media_kind)``.

    Every typed failure - card off, daemon offline, daemon timeout, a failure
    the daemon reported - is recorded through ``record_failure_detail``, which
    returns the ``failed`` marker for deterministic ones and raises a plain
    ``RuntimeError`` for the rest.
    """
    from app.services.codex import daemon_dispatch
    from app.services.codex.provider_card import ProviderCardDisabledError

    try:
        await require_provider_card(engine, user_id)
    except ProviderCardDisabledError as exc:
        return await record_failure_detail(exc)

    payload = build_local_payload(
        engine=engine,
        engine_model=engine_model,
        media_kind=media_kind,
        request=request,
        ref_urls=ref_urls,
    )
    try:
        # Looked up on the module at call time: the dispatch seam tests patch.
        return await daemon_dispatch.dispatch_to_daemon(
            user_id=str(user_id),
            scope_id=scope_id,
            kind=media_kind,
            payload=payload,
            attribution=attribution,
            timeout_s=(
                timeout_s if timeout_s is not None else dispatch_timeout_for(media_kind)
            ),
        )
    except (
        daemon_dispatch.DaemonJobFailedError,
        daemon_dispatch.DaemonOfflineError,
        daemon_dispatch.DaemonTimeoutError,
    ) as exc:
        # Raises for transient codes; returns {"failed": ...} for
        # deterministic ones - the caller must check for that key.
        return await record_failure_detail(exc)


async def record_failure_detail(exc: BaseException) -> Dict[str, Any]:
    """Persist a failed generation's explanation; return a marker or re-raise.

    Both codex image paths funnel through here - the user's own daemon and
    the in-container subprocess - because a user cannot tell which one ran
    and neither should read differently.

    Why the explanation goes to ``metadata``: a content refusal's useful part
    is the model's own prose, routinely non-ASCII, and
    ``task_tracking.error_msg`` is derived from the pickled exception by
    ``public.dbos_error_to_text()`` (migration 219), which splits on every
    byte >= 0x80 and keeps the longest chunk. So the words go to jsonb and
    the exception carries one ASCII line.

    Two exits, chosen by the failure code:

    * **Deterministic** (``NON_RETRYABLE_FAILURE_CODES``): return
      ``{"failed": <one ASCII line>}``. The step returns normally, so DBOS
      does NOT retry it; ``raise_if_failed`` in the workflow turns the marker
      into the raise that fails the task. Route C section 4 forbids the
      WORKFLOW returning a failed dict (the mirror would mark the task
      completed) - a step returning one, with the workflow raising, is
      exactly how you opt a deterministic failure out of step retries.
    * **Anything else**: raise a plain ``RuntimeError`` (pickled across the
      DBOS boundary; the typed error's extra fields would not survive
      anyway) so the step's ``max_attempts`` still buys a second try for a
      daemon crash or a network blip.
    """
    from app.services.generation.failure import describe_generation_failure

    message, patch = describe_generation_failure(exc)
    task_id = DBOS.workflow_id
    if task_id:
        await patch_task_metadata(task_id, patch)
    if patch["failure"]["code"] in NON_RETRYABLE_FAILURE_CODES:
        return {"failed": message}
    raise RuntimeError(message) from exc


def raise_if_failed(media: Dict[str, Any]) -> Dict[str, Any]:
    """The workflow-side half of ``record_failure_detail``: a step result
    carrying ``failed`` becomes the raise that fails the task (route C §4).
    Anything else passes through untouched."""
    failed = media.get("failed") if isinstance(media, dict) else None
    if failed:
        raise RuntimeError(str(failed))
    return media


async def patch_task_metadata(task_id: str, patch: Dict[str, Any]) -> None:
    """Write business decoration onto this run's task row (route C §3).

    Deliberately swallow-and-log: this is called on the failure path, and a
    metadata write that fails must not replace the failure the user actually
    needs to see. Logged rather than passed, per CLAUDE.md's rule on
    catch-swallowing.
    """
    from app.services.infra.unified_task_manager import get_task_manager

    try:
        await get_task_manager().patch_metadata(task_id, patch)
    except Exception as exc:  # noqa: BLE001 - see docstring
        logger.error("[local-gen] could not record failure metadata: {}", exc)


__all__ = [
    "DAEMON_DREAMINA_QUERY_BUDGET_S",
    "DAEMON_DREAMINA_RUN_BUDGET_S",
    "DAEMON_GRACE_S",
    "IMAGE_DISPATCH_TIMEOUT_S",
    "LOCAL_ENGINES",
    "NON_RETRYABLE_FAILURE_CODES",
    "VIDEO_DISPATCH_TIMEOUT_S",
    "build_local_payload",
    "capabilities_for",
    "dispatch_local_generation",
    "dispatch_timeout_for",
    "patch_task_metadata",
    "raise_if_failed",
    "reconcile_for_engine",
    "record_failure_detail",
    "require_provider_card",
    "resolve_local_engine",
]
