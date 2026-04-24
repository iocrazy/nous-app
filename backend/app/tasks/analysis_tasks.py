"""Celery tasks for video analysis.

V4 (analyze-byo): every L1/L2 entrypoint now resolves the ``analyze``
agent's model + the user's BYO provider config from ``ai_settings`` and
hands both to :class:`VisualAnalysisService`. Before V4 this path used
global settings silently, so user-configured Doubao / Qwen-VL / Claude
keys never took effect — analyze always ran against whatever the backend
``.env`` held (often nothing). See ``_resolve_analyze_provider_config``.
"""

import asyncio
import os
import tempfile
from typing import Any, Dict, Optional, Tuple

from celery import shared_task
from loguru import logger

from app.repositories.analysis_repository import AnalysisRepository
from app.repositories.tags_repository import TagsRepository
from app.services.embedding_service import EmbeddingService
from app.services.visual_analysis_service import VisualAnalysisService
from app.tasks.ai_tasks import _get_ai_settings, _get_provider_config
from app.tasks.utils import run_async


def _update_visual_status(video_id: str, status: str):
    """Update visual_analysis_status on the videos table."""
    from app.repositories.ai_repository import AIRepository

    ai_repo = AIRepository()
    run_async(
        ai_repo.update_video_ai_status(video_id, "visual_analysis_status", status)
    )


def _resolve_analyze_provider_config(
    user_id: Optional[str],
) -> Tuple[str, Dict[str, Any], str]:
    """Resolve analyze agent's model + user's BYO provider config.

    Returns ``(provider_key, provider_config, model)``. Reads the
    ``analyze`` ``ai_agents`` row to get its ``model``, derives the
    provider prefix, then pulls the user's BYO entry for that provider
    out of ``ai_settings.ai_providers``. When the user has not configured
    that provider, the caller (:class:`VisualAnalysisService`) falls back
    to the factory's global settings path, but for BYO-only projects
    (where the global key is intentionally absent) that fallback will
    surface as a visible request-time error — the desired outcome.

    When ``user_id`` is None (legacy / no-auth path), returns empty
    config and lets the service route through the factory's default
    for whichever prefix ``model`` has.
    """
    from app.repositories.agent_repository import AgentRepository
    from app.services.ai_adapters.factory import provider_key_for_model

    agent_repo = AgentRepository()
    agent = run_async(agent_repo.get_by_slug("analyze"))
    model = ((agent or {}).get("model") or "").strip()
    if not model:
        logger.warning(
            "[AI] analyze agent row missing or has no model; "
            "VisualAnalysisService will use built-in default"
        )
        return "", {}, ""

    try:
        provider_key = provider_key_for_model(model)
    except ValueError:
        logger.warning(
            f"[AI] analyze agent model '{model}' has unknown provider prefix; "
            "falling back to generic OpenAI-compatible adapter"
        )
        provider_key = ""

    if not user_id or not provider_key:
        return provider_key, {"model": model}, model

    ai_settings = _get_ai_settings(user_id)
    provider_config = dict(_get_provider_config(ai_settings, provider_key))
    provider_config["model"] = model
    return provider_key, provider_config, model


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def analyze_video_l1_task(
    self,
    media_id: int,
    cover_url: str,
    title: str = "",
    description: str = "",
    user_id: Optional[str] = None,
):
    """
    L1 Analysis: Analyze video cover image.

    This task:
    1. Resolves the ``analyze`` agent's model + the user's BYO provider
       config from ``ai_settings.ai_providers``
    2. Analyzes the cover image through the resolved multimodal provider
    3. Stores results in resource_analysis
    4. Updates tags based on detected category
    5. Generates embedding for semantic search

    ``user_id`` is the owner of the parsed_media / resource being
    analyzed. Required for BYO credentials to take effect; when None
    (e.g. system-initiated backfill with no user context) the service
    falls back to whatever the factory resolves from global settings.
    """
    logger.info(f"Starting L1 analysis for media {media_id} (user={user_id})")

    provider_key, provider_config, agent_model = _resolve_analyze_provider_config(
        user_id
    )

    async def _analyze():
        analysis_service = VisualAnalysisService(
            provider_key=provider_key,
            provider_config=provider_config,
        )
        embedding_service = EmbeddingService()
        analysis_repo = AnalysisRepository()
        tags_repo = TagsRepository()

        result = await analysis_service.analyze_l1(cover_url, user_id=user_id)

        if not result:
            logger.warning(f"L1 analysis returned no result for media {media_id}")
            return None

        # Store analysis results
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

        # Add tag based on detected category
        if result.category and result.category != "Other":
            tag = await tags_repo.get_tag_by_name(result.category)
            if tag:
                await tags_repo.add_tag_to_resource(
                    resource_id=media_id,
                    tag_id=tag["id"],
                    confidence=0.8,  # AI-based confidence
                    source="ai",
                )
                logger.info(f"Added AI tag '{result.category}' to media {media_id}")

        # Generate embedding
        # Get existing tags for this media
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
            logger.info(f"Generated embedding for media {media_id}")

        logger.info(
            f"Completed L1 analysis for media {media_id}, cost: ${result.cost:.4f}"
        )
        return {"media_id": media_id, "category": result.category, "cost": result.cost}

    try:
        return run_async(_analyze())
    except Exception as e:
        logger.error(f"L1 analysis failed for media {media_id}: {e}")
        raise self.retry(exc=e)


@shared_task(bind=True, max_retries=2, default_retry_delay=120)
def analyze_video_l2_task(
    self,
    media_id: int,
    cover_url: str,
    video_path: str,
    title: str = "",
    description: str = "",
    user_id: Optional[str] = None,
):
    """
    L2 Analysis: Analyze cover + keyframes.

    This task:
    1. Resolves the ``analyze`` agent model + user BYO provider config
    2. Extracts keyframes from video using FFmpeg
    3. Analyzes cover + keyframes through the resolved multimodal provider
    4. Updates analysis record
    5. Regenerates embedding with richer data

    See ``analyze_video_l1_task`` for the ``user_id`` contract.
    """
    logger.info(f"Starting L2 analysis for media {media_id} (user={user_id})")

    provider_key, provider_config, agent_model = _resolve_analyze_provider_config(
        user_id
    )

    async def _analyze():
        analysis_service = VisualAnalysisService(
            provider_key=provider_key,
            provider_config=provider_config,
        )
        embedding_service = EmbeddingService()
        analysis_repo = AnalysisRepository()
        tags_repo = TagsRepository()

        # Extract keyframes using FFmpeg
        keyframe_paths = []

        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                # Get video duration
                probe_cmd = [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    video_path,
                ]
                proc = await asyncio.create_subprocess_exec(
                    *probe_cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                stdout_bytes, _ = await proc.communicate()
                duration = float(stdout_bytes.decode().strip())

                # Extract frames at 25%, 50%, 75%
                for i, pct in enumerate([0.25, 0.50, 0.75]):
                    timestamp = duration * pct
                    output_path = os.path.join(tmpdir, f"frame_{i}.jpg")

                    extract_cmd = [
                        "ffmpeg",
                        "-y",
                        "-ss",
                        str(timestamp),
                        "-i",
                        video_path,
                        "-vframes",
                        "1",
                        "-q:v",
                        "2",
                        output_path,
                    ]
                    extract_proc = await asyncio.create_subprocess_exec(
                        *extract_cmd,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    await extract_proc.communicate()

                    if os.path.exists(output_path):
                        keyframe_paths.append(output_path)

                logger.info(
                    f"Extracted {len(keyframe_paths)} keyframes for media {media_id}"
                )

                # Run L2 analysis
                result = await analysis_service.analyze_l2(
                    cover_url, keyframe_paths, user_id=user_id
                )

                if not result:
                    logger.warning(
                        f"L2 analysis returned no result for media {media_id}"
                    )
                    return None

                # Update analysis results
                await analysis_repo.upsert_analysis(
                    media_id=media_id,
                    analysis_level="L2",
                    visual_description=result.visual_description,
                    detected_objects=result.detected_objects,
                    detected_scenes=result.detected_scenes,
                    detected_people=result.detected_people,
                    detected_text=result.detected_text,
                    analysis_model=agent_model or "unknown",
                    analysis_cost=result.cost,
                )

                # Update tag if category changed
                if result.category and result.category != "Other":
                    tag = await tags_repo.get_tag_by_name(result.category)
                    if tag:
                        await tags_repo.add_tag_to_resource(
                            resource_id=media_id,
                            tag_id=tag["id"],
                            confidence=0.9,  # Higher confidence for L2
                            source="ai",
                        )

                # Regenerate embedding
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
                    await analysis_repo.update_embedding(
                        media_id, embedding, embedding_text
                    )

                logger.info(
                    f"Completed L2 analysis for media {media_id}, cost: ${result.cost:.4f}"
                )
                return {
                    "media_id": media_id,
                    "category": result.category,
                    "cost": result.cost,
                }

        except subprocess.CalledProcessError as e:
            logger.error(f"FFmpeg failed for media {media_id}: {e}")
            return None
        except FileNotFoundError:
            logger.error(f"Video file not found: {video_path}")
            return None

    try:
        return run_async(_analyze())
    except Exception as e:
        logger.error(f"L2 analysis failed for media {media_id}: {e}")
        raise self.retry(exc=e)


@shared_task
def batch_analyze_l1_task(
    media_ids: list,
    batch_size: int = 10,
    user_id: Optional[str] = None,
):
    """
    Batch L1 analysis for multiple media items.
    Dispatches individual L1 tasks.

    ``user_id`` is the user whose BYO provider config should be used for
    every dispatched L1 job. Required when the caller is a real user
    request (REST endpoint); the scheduled-backfill path passes None and
    the dispatched L1 tasks will each resolve the owner from their own
    ``parsed_media.user_id`` — but for now, passing None means each L1
    task falls back to global settings.

    Note: Uses run_async helper to work with async Supabase client.
    Celery workers run in separate processes and don't share event loops.
    """

    async def _batch_dispatch():
        from app.db.supabase_client import get_async_supabase_admin

        supabase = await get_async_supabase_admin()
        dispatched = 0

        for media_id in media_ids[:batch_size]:
            # Get media info
            result = (
                await supabase.table("parsed_media")
                .select("id, title, description, cover_urls")
                .eq("id", media_id)
                .maybe_single()
                .execute()
            )

            if result.data:
                media = result.data
                cover_url = (media.get("cover_urls") or [""])[0]
                if cover_url:
                    analyze_video_l1_task.delay(
                        media_id=media["id"],
                        cover_url=cover_url,
                        title=media.get("title", ""),
                        description=media.get("description", ""),
                        user_id=user_id,
                    )
                    dispatched += 1
                    logger.info(f"Dispatched L1 analysis for media {media_id}")
                else:
                    logger.warning(f"Media {media_id} has no cover_urls, skipping")
            else:
                logger.warning(f"Media {media_id} not found, skipping")

        return {
            "dispatched": dispatched,
            "total_requested": len(media_ids),
            "message": f"Dispatched {dispatched} L1 analysis tasks",
        }

    return run_async(_batch_dispatch())


@shared_task
def analyze_pending_videos_task(limit: int = 50):
    """
    Find videos without analysis and dispatch L1 tasks.
    Useful for backfilling analysis on existing videos.
    """

    async def _dispatch():
        analysis_repo = AnalysisRepository()
        videos = await analysis_repo.get_videos_without_analysis(limit=limit)

        media_ids = [v["id"] for v in videos if v.get("cover_urls")]

        if media_ids:
            batch_analyze_l1_task.delay(media_ids, batch_size=limit)
            logger.info(
                f"Dispatched batch analysis for {len(media_ids)} pending media items"
            )
            return {"dispatched": len(media_ids)}

        logger.info("No pending media items for analysis")
        return {"dispatched": 0}

    return run_async(_dispatch())
