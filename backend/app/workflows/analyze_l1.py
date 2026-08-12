"""analyze_l1 (ai_extract) DBOS workflow — port of legacy
`analysis_tasks.analyze_video_l1_task`.

Original task body is one inner `_analyze()` async function calling
VisualAnalysisService + EmbeddingService + AnalysisRepository + TagsRepository.
We delegate to the existing service code; the DBOS layer adds idempotency
(workflow_id) + a per-step retry policy on the multimodal LLM call.
"""

from __future__ import annotations

import os
from contextlib import nullcontext
from typing import Any, Optional

from dbos import DBOS
from sqlalchemy import select

from app.db.scope import is_enforced, system_request_scope


def _dsn() -> str:
    url = os.environ.get("DBOS_DATABASE_URL")
    if not url:
        raise RuntimeError("DBOS_DATABASE_URL not configured")
    return url + ("&" if "?" in url else "?") + "sslmode=disable"


def _analyze_resource_lookup_stmt(media_id: int, user_id: Optional[str]):
    """resources.id for a parsed_media, preferring the triggering user's own
    resource (falls back to the media's earliest resource). Column-level
    select (not entity-level — the B4 row-shape lesson). Factored out so a
    real-aiosqlite row-shape test can import and exercise the exact
    production statement."""
    from app.models import ParsedMedia, Resources

    creator_match = Resources.creator_id == user_id
    return (
        select(Resources.id.label("resource_id"))
        .select_from(ParsedMedia)
        .join(Resources, Resources.media_id == ParsedMedia.id)
        .where(ParsedMedia.id == media_id)
        .order_by(creator_match.desc().nulls_last(), Resources.created_at.asc())
        .limit(1)
    )


@DBOS.step()
async def resolve_analyze_provider(user_id: Optional[str]) -> dict[str, Any]:
    """Resolve provider key + config + model name + fallback models. Mirrors
    analysis_tasks._resolve_analyze_provider_config.

    §2.4b: async-native — awaits the (now async) helper directly on the
    workflow's event loop instead of bridging the repo reads through a
    fresh-loop run_async shim (ORM-incompatible).

    Calls ``resolve_task_ai_config`` directly instead of the tuple-shim
    ``resolve_analyze_provider_config`` (which discards ``fallback_models``
    down to 4 positional fields) so the typed ``ResolvedAIConfig.fallback_models``
    rides along into the step's return dict, threaded to ``call_analyze_l1`` ->
    ``VisualAnalysisService`` (spec 2026-08-11-batch-llm-fallback §4)."""
    from app.services.ai.providers.ai_provider_helpers import (
        DEFAULT_ANALYZE_AGENT_SLUG,
        resolve_task_ai_config,
    )

    cfg = await resolve_task_ai_config(
        user_id, "visual_analysis", DEFAULT_ANALYZE_AGENT_SLUG
    )
    return {
        "provider_key": cfg.provider_key,
        "provider_config": cfg.provider_config or {},
        "agent_model": cfg.model,
        "agent_slug": cfg.agent_slug,
        "fallback_models": list(cfg.fallback_models),
    }


@DBOS.step(retries_allowed=True, max_attempts=1)
async def call_analyze_l1(
    media_id: int,
    cover_url: str,
    title: str,
    description: str,
    user_id: Optional[str],
    provider_key: str,
    provider_config: dict[str, Any],
    agent_model: Optional[str],
    agent_slug: str = "analyze",
    wf_id: Optional[str] = None,
    fallback_models: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Run the multimodal analysis + persist results. Returns a digest dict.

    PR #237 audit: was sync ``def`` with ``asyncio.run(_analyze())``
    inside. Now async — same fix as workflow_health_sweeper / sweeper."""
    from sqlalchemy import update

    from app.db.session import read_scope, write_scope
    from app.models import Resources
    from app.repositories.analysis_repository import get_analysis_repository
    from app.repositories.tags_repository import get_tags_repository
    from app.services.ai.providers.embedding_service import EmbeddingService
    from app.services.ai.visual.visual_analysis_service import VisualAnalysisService

    # resource_analysis (+ tags + embedding) are keyed by resources.id, NOT
    # parsed_media.id — the table moved to resource-keying (mig 076/262) but this
    # workflow kept threading media_id, so every write was cross-domain (the
    # FK to resources.id would reject a parsed_media.id). Resolve media → the
    # triggering user's resource (falling back to the media's earliest resource),
    # exactly like the working ai_transcription pipeline does.
    #
    # Resources carries UserScoped(creator_id); SCOPE_ENFORCE_RESOURCES
    # defaults false in code but production sets it true via
    # secrets/backend.env (CLAUDE.md 部署陷阱). This step has no ambient
    # per-request scope of its own (it's a DBOS step), so the wrap is
    # LOAD-BEARING once the flag is on. Gated on is_enforced to stay
    # byte-for-byte legacy where the flag is off.
    scope_cm = (
        system_request_scope(
            reason="analyze_l1 workflow: resolve resource for parsed_media"
        )
        if is_enforced("resources")
        else nullcontext()
    )
    async with scope_cm:
        async with read_scope() as session:
            resource_id_val = await session.scalar(
                _analyze_resource_lookup_stmt(media_id, user_id)
            )
    if resource_id_val is None:
        raise RuntimeError(f"no resource for parsed_media id={media_id}")
    resource_id = int(resource_id_val)

    # Make resources.visual_analysis_status authoritative for the whole run:
    # 'processing' now, 'completed' on success (below), 'failed' on the no-result
    # path. The UI keys its Visual Analysis panel off this column, so a terminal
    # value here is what unsticks the "Analyzing…" state (instead of relying on
    # the frontend's optimistic local flag, which never resolved on failure).
    #
    # Bulk Core UPDATE on Resources (a UserScoped model) is FORBIDDEN under a
    # real user Scope (app/db/scope.py's write-path guard) — this is a
    # system-side status flip triggered by the analysis pipeline, not a user
    # action, so SYSTEM scope is correct (same rationale as ai_transcription's
    # mark_transcript_* steps).
    scope_cm = (
        system_request_scope(
            reason="analyze_l1 workflow: mark visual_analysis_status processing"
        )
        if is_enforced("resources")
        else nullcontext()
    )
    async with scope_cm:
        async with write_scope() as session:
            await session.execute(
                update(Resources)
                .where(Resources.id == resource_id)
                .values(visual_analysis_status="processing")
            )

    analysis_service = VisualAnalysisService(
        provider_key=provider_key,
        provider_config=provider_config,
        agent_slug=agent_slug or "analyze",
    )
    embedding_service = EmbeddingService()
    analysis_repo = get_analysis_repository()
    tags_repo = get_tags_repository()

    # Real-milestone progress: map the service's hooks (image downloaded →
    # model called) onto task_tracking so the UI's progress bar reflects
    # actual stages instead of sitting at 20% for the whole LLM call.
    # Best-effort — a progress write must never fail the analysis.
    async def _progress(pct: int, subtitle: str) -> None:
        if not wf_id:
            return
        from app.services.infra.unified_task_manager import get_task_manager

        try:
            await get_task_manager().update_progress(wf_id, pct, subtitle=subtitle)
        except Exception:  # noqa: BLE001 — progress is decorative
            pass

    async def _mark_visual_analysis_failed() -> None:
        # Mark the resource failed so the UI's existing 'failed' branch (retry
        # button) shows instead of a stuck "Analyzing…". Same SYSTEM-scope
        # rationale as the 'processing' write above.
        scope_cm = (
            system_request_scope(
                reason="analyze_l1 workflow: mark visual_analysis_status failed"
            )
            if is_enforced("resources")
            else nullcontext()
        )
        async with scope_cm:
            async with write_scope() as session:
                await session.execute(
                    update(Resources)
                    .where(Resources.id == resource_id)
                    .values(visual_analysis_status="failed")
                )

    await _progress(30, "Preparing analysis...")
    try:
        result = await analysis_service.analyze_l1(
            cover_url,
            user_id=user_id,
            on_progress=_progress,
            # task ↔ run bidirectional linkage (mig 282): RunRecorder writes
            # agent_runs.task_id and stamps agent_id + metadata.run_id back onto
            # this workflow's task_tracking row.
            task_id=wf_id,
            fallback_models=fallback_models,
        )
    except Exception:
        # final-review C2: VisualAnalysisService's recorder path now lets
        # LLM-class exceptions (AllModelsFailed/LLMCallError) PROPAGATE
        # instead of swallowing them to None (spec §3/§4) — which used to be
        # the ONLY way this step reached the 'failed' write below. An
        # exception here skipped straight past it, leaving
        # resources.visual_analysis_status stuck on 'processing' forever even
        # though the workflow's own tail except correctly failed
        # task_tracking. Mark it failed here too, then re-raise UNCHANGED so
        # the workflow's record_ai_error_code / record_workflow_failure /
        # raise (Route-C rule 4) still runs exactly as before.
        await _mark_visual_analysis_failed()
        raise

    if not result:
        await _mark_visual_analysis_failed()
        # No result == the provider call failed (VisualAnalysisService caught the
        # error, logged it, and returned None — e.g. the assigned provider is
        # unreachable OR is not a vision/multimodal model). RAISE rather than
        # return a dict: returning would let the workflow report "Analysis
        # complete" + DBOS SUCCESS (CLAUDE.md task-discipline #4), so the task
        # would show completed while nothing was written. Raising routes through
        # the workflow's record_workflow_failure → task_tracking is marked
        # failed with this message, so the user sees WHY instead of a silent
        # "Analyzing…" that never resolves.
        raise RuntimeError(
            "visual analysis produced no result — the provider call failed "
            "(check that the model assigned to Visual Analysis supports vision/"
            "images and is reachable; see application_logs for the provider error)"
        )

    await _progress(85, "Saving analysis results...")
    await analysis_repo.upsert_analysis(
        resource_id,
        analysis_level="L1",
        visual_description=result.visual_description,
        detected_objects=result.detected_objects,
        detected_scenes=result.detected_scenes,
        detected_people=result.detected_people,
        detected_text=result.detected_text,
        analysis_model=agent_model or "unknown",
        analysis_cost=result.cost,
    )

    # Reflect completion in the per-user status column the UI reads (mig 067).
    # Same SYSTEM-scope rationale as the 'processing'/'failed' writes above.
    scope_cm = (
        system_request_scope(
            reason="analyze_l1 workflow: mark visual_analysis_status completed"
        )
        if is_enforced("resources")
        else nullcontext()
    )
    async with scope_cm:
        async with write_scope() as session:
            await session.execute(
                update(Resources)
                .where(Resources.id == resource_id)
                .values(visual_analysis_status="completed")
            )

    if result.category and result.category != "Other":
        tag = await tags_repo.get_tag_by_name(result.category)
        if tag:
            await tags_repo.add_tag_to_resource(
                resource_id=resource_id,
                tag_id=tag["id"],
                confidence=0.8,
                source="ai",
            )

    media_tags = await tags_repo.get_resource_tags(resource_id)
    tag_names = [t["tags"]["name"] for t in media_tags if t.get("tags")]

    embedding_text = embedding_service.build_embedding_text(
        title=title,
        description=description,
        tags=tag_names,
        visual_description=result.visual_description,
        detected_objects=result.detected_objects,
        detected_scenes=result.detected_scenes,
        detected_text=result.detected_text,
    )
    embedding = await embedding_service.generate_embedding(embedding_text)
    if embedding:
        await analysis_repo.update_embedding(resource_id, embedding, embedding_text)

    return {
        "status": "ok",
        "media_id": media_id,
        "category": result.category,
        "cost": result.cost,
        "embedded": embedding is not None,
    }


@DBOS.workflow()
async def analyze_l1_workflow(
    media_id: int,
    cover_url: str,
    *,
    title: str = "",
    description: str = "",
    user_id: Optional[str] = None,
) -> dict[str, Any]:
    """DBOS port of analyze_video_l1_task.

    workflow_id idempotency: re-running with same id replays the cached
    analysis result (no double LLM cost + no double embedding write).
    """
    from app.services.infra.unified_task_manager import get_task_manager
    from app.workflows._failure_handler import record_workflow_failure

    manager = get_task_manager()
    wf_id = DBOS.workflow_id

    try:
        cfg = await resolve_analyze_provider(user_id)
        await manager.update_progress(wf_id, 20, subtitle="Provider resolved")
        result = await call_analyze_l1(
            media_id=media_id,
            cover_url=cover_url,
            title=title,
            description=description,
            user_id=user_id,
            provider_key=cfg["provider_key"],
            provider_config=cfg["provider_config"],
            agent_model=cfg["agent_model"],
            agent_slug=cfg.get("agent_slug") or "analyze",
            wf_id=wf_id,
            fallback_models=cfg.get("fallback_models") or [],
        )
        await manager.update_progress(wf_id, 100, subtitle="Analysis complete")
        return result
    except Exception as e:  # noqa: BLE001
        # Translate the raw failure into a stable error code the frontend
        # can turn into actionable copy (an AllModelsFailed(429) otherwise
        # reaches the user as "Step ... exceeded its maximum of N retries").
        # Writes metadata only — error_msg/phase stay trigger-owned — and
        # never raises, so the failure path below is unchanged (mirrors
        # caption_asset.py / caption_slide.py — final-review C3).
        from app.services.ai.error_catalog import record_ai_error_code

        await record_ai_error_code(DBOS.workflow_id, e)
        # Route-C rule 4: record for task_tracking/UI, then RE-RAISE so
        # DBOS records ERROR — returning the dict made DBOS mark this
        # workflow SUCCESS while task_tracking said failed (observed live
        # 2026-08-08, wf 5a872175/1e63f80b).
        await record_workflow_failure(
            workflow_id=DBOS.workflow_id,
            error=e,
            context={
                "workflow": "analyze_l1",
                "media_id": media_id,
                "user_id": user_id,
            },
        )
        raise
