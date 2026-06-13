"""analyze_l1 (ai_extract) DBOS workflow — port of legacy
`analysis_tasks.analyze_video_l1_task`.

Original task body is one inner `_analyze()` async function calling
VisualAnalysisService + EmbeddingService + AnalysisRepository + TagsRepository.
We delegate to the existing service code; the DBOS layer adds idempotency
(workflow_id) + a per-step retry policy on the multimodal LLM call.
"""

from __future__ import annotations

import os
from typing import Any, Optional

from dbos import DBOS


def _dsn() -> str:
    url = os.environ.get("DBOS_DATABASE_URL")
    if not url:
        raise RuntimeError("DBOS_DATABASE_URL not configured")
    return url + ("&" if "?" in url else "?") + "sslmode=disable"


@DBOS.step()
async def resolve_analyze_provider(user_id: Optional[str]) -> dict[str, Any]:
    """Resolve provider key + config + model name. Mirrors
    analysis_tasks._resolve_analyze_provider_config.

    §2.4b: async-native — awaits the (now async) helper directly on the
    workflow's event loop instead of bridging the repo reads through a
    fresh-loop run_async shim (ORM-incompatible)."""
    from app.services.ai.providers.ai_provider_helpers import (
        resolve_analyze_provider_config,
    )

    (
        provider_key,
        provider_config,
        agent_model,
        agent_slug,
    ) = await resolve_analyze_provider_config(user_id)
    return {
        "provider_key": provider_key,
        "provider_config": provider_config or {},
        "agent_model": agent_model,
        "agent_slug": agent_slug,
    }


@DBOS.step(retries_allowed=True, max_attempts=2)
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
) -> dict[str, Any]:
    """Run the multimodal analysis + persist results. Returns a digest dict.

    PR #237 audit: was sync ``def`` with ``asyncio.run(_analyze())``
    inside. Now async — same fix as workflow_health_sweeper / sweeper."""
    from app.db import engine as db_engine
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
    media_row = await db_engine.fetch_one(
        "SELECT r.id AS resource_id "
        "FROM public.parsed_media pm "
        "JOIN public.resources r ON r.media_id = pm.id "
        "WHERE pm.id = :pid "
        "ORDER BY (r.creator_id = :uid) DESC NULLS LAST, r.created_at ASC "
        "LIMIT 1",
        {"pid": media_id, "uid": user_id},
    )
    if not media_row:
        raise RuntimeError(f"no resource for parsed_media id={media_id}")
    resource_id = int(media_row["resource_id"])

    # Make resources.visual_analysis_status authoritative for the whole run:
    # 'processing' now, 'completed' on success (below), 'failed' on the no-result
    # path. The UI keys its Visual Analysis panel off this column, so a terminal
    # value here is what unsticks the "Analyzing…" state (instead of relying on
    # the frontend's optimistic local flag, which never resolved on failure).
    await db_engine.execute(
        "UPDATE public.resources SET visual_analysis_status = 'processing' "
        "WHERE id = :rid",
        {"rid": resource_id},
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

    await _progress(30, "Preparing analysis...")
    result = await analysis_service.analyze_l1(
        cover_url,
        user_id=user_id,
        on_progress=_progress,
        # task ↔ run bidirectional linkage (mig 282): RunRecorder writes
        # agent_runs.task_id and stamps agent_id + metadata.run_id back onto
        # this workflow's task_tracking row.
        task_id=wf_id,
    )
    if not result:
        # Mark the resource failed so the UI's existing 'failed' branch (retry
        # button) shows instead of a stuck "Analyzing…".
        await db_engine.execute(
            "UPDATE public.resources SET visual_analysis_status = 'failed' "
            "WHERE id = :rid",
            {"rid": resource_id},
        )
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
    await db_engine.execute(
        "UPDATE public.resources SET visual_analysis_status = 'completed' "
        "WHERE id = :rid",
        {"rid": resource_id},
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
        )
        await manager.update_progress(wf_id, 100, subtitle="Analysis complete")
        return result
    except Exception as e:  # noqa: BLE001
        return await record_workflow_failure(
            workflow_id=DBOS.workflow_id,
            error=e,
            context={
                "workflow": "analyze_l1",
                "media_id": media_id,
                "user_id": user_id,
            },
        )
