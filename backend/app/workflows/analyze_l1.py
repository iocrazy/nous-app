"""analyze_l1 (ai_extract) DBOS workflow — port of legacy
`analysis_tasks.analyze_video_l1_task`.

Original task body is one inner `_analyze()` async function calling
VisualAnalysisService + EmbeddingService + AnalysisRepository + TagsRepository.
We delegate to the existing service code; the DBOS layer adds idempotency
(workflow_id) + a per-step retry policy on the multimodal LLM call.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any, Optional

from dbos import DBOS


def _dsn() -> str:
    url = os.environ.get("DBOS_DATABASE_URL")
    if not url:
        raise RuntimeError("DBOS_DATABASE_URL not configured")
    return url + ("&" if "?" in url else "?") + "sslmode=disable"


@DBOS.step()
def resolve_analyze_provider(user_id: Optional[str]) -> dict[str, Any]:
    """Resolve provider key + config + model name. Mirrors
    analysis_tasks._resolve_analyze_provider_config."""
    from app.services.ai.providers.ai_provider_helpers import resolve_analyze_provider_config

    provider_key, provider_config, agent_model = resolve_analyze_provider_config(
        user_id
    )
    return {
        "provider_key": provider_key,
        "provider_config": provider_config or {},
        "agent_model": agent_model,
    }


@DBOS.step(retries_allowed=True, max_attempts=2)
def call_analyze_l1(
    media_id: int,
    cover_url: str,
    title: str,
    description: str,
    user_id: Optional[str],
    provider_key: str,
    provider_config: dict[str, Any],
    agent_model: Optional[str],
) -> dict[str, Any]:
    """Run the multimodal analysis + persist results. Returns a digest dict."""
    from app.repositories.analysis_repository import AnalysisRepository
    from app.repositories.tags_repository import TagsRepository
    from app.services.ai.providers.embedding_service import EmbeddingService
    from app.services.ai.visual.visual_analysis_service import VisualAnalysisService

    async def _analyze() -> dict[str, Any]:
        analysis_service = VisualAnalysisService(
            provider_key=provider_key, provider_config=provider_config
        )
        embedding_service = EmbeddingService()
        analysis_repo = AnalysisRepository()
        tags_repo = TagsRepository()

        result = await analysis_service.analyze_l1(cover_url, user_id=user_id)
        if not result:
            return {"status": "no_result", "media_id": media_id}

        await analysis_repo.upsert_analysis(
            media_id=media_id,
            analysis_level="L1",
            visual_description=result.visual_description,
            detected_objects=result.detected_objects,
            detected_scenes=result.detected_scenes,
            detected_people=result.detected_people,
            detected_text=result.detected_text,
            analysis_model=agent_model or "unknown",
            analysis_cost=result.cost,
        )

        if result.category and result.category != "Other":
            tag = await tags_repo.get_tag_by_name(result.category)
            if tag:
                await tags_repo.add_tag_to_resource(
                    resource_id=media_id,
                    tag_id=tag["id"],
                    confidence=0.8,
                    source="ai",
                )

        media_tags = await tags_repo.get_resource_tags(media_id)
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
            await analysis_repo.update_embedding(media_id, embedding, embedding_text)

        return {
            "status": "ok",
            "media_id": media_id,
            "category": result.category,
            "cost": result.cost,
            "embedded": embedding is not None,
        }

    return asyncio.run(_analyze())


@DBOS.workflow()
def analyze_l1_workflow(
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
    from app.workflows._failure_handler import record_workflow_failure

    try:
        cfg = resolve_analyze_provider(user_id)
        return call_analyze_l1(
            media_id=media_id,
            cover_url=cover_url,
            title=title,
            description=description,
            user_id=user_id,
            provider_key=cfg["provider_key"],
            provider_config=cfg["provider_config"],
            agent_model=cfg["agent_model"],
        )
    except Exception as e:  # noqa: BLE001
        return record_workflow_failure(
            workflow_id=DBOS.workflow_id,
            error=e,
            context={
                "workflow": "analyze_l1",
                "media_id": media_id,
                "user_id": user_id,
            },
        )
