"""Celery tasks for video analysis."""
import asyncio
import subprocess
import tempfile
import os
from typing import Optional

from celery import shared_task
from loguru import logger

from app.repositories.analysis_repository import AnalysisRepository
from app.repositories.tags_repository import TagsRepository
from app.services.visual_analysis_service import VisualAnalysisService
from app.services.embedding_service import EmbeddingService


def run_async(coro):
    """Helper to run async code in Celery task."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(asyncio.run, coro)
                return future.result()
        else:
            return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def analyze_video_l1_task(
    self,
    video_id: int,
    cover_url: str,
    title: str = "",
    description: str = ""
):
    """
    L1 Analysis: Analyze video cover image.

    This task:
    1. Analyzes the cover image using GPT-4o
    2. Stores results in video_analysis table
    3. Updates tags based on detected category
    4. Generates embedding for semantic search
    """
    logger.info(f"Starting L1 analysis for video {video_id}")

    async def _analyze():
        analysis_service = VisualAnalysisService()
        embedding_service = EmbeddingService()
        analysis_repo = AnalysisRepository()
        tags_repo = TagsRepository()

        # Run visual analysis
        result = await analysis_service.analyze_l1(cover_url)

        if not result:
            logger.warning(f"L1 analysis returned no result for video {video_id}")
            return None

        # Store analysis results
        await analysis_repo.upsert_analysis(
            video_id=video_id,
            analysis_level="L1",
            visual_description=result.visual_description,
            detected_objects=result.detected_objects,
            detected_scenes=result.detected_scenes,
            detected_people=result.detected_people,
            detected_text=result.detected_text,
            analysis_model="gpt-4o",
            analysis_cost=result.cost
        )

        # Add tag based on detected category
        if result.category and result.category != "Other":
            tag = await tags_repo.get_tag_by_name(result.category)
            if tag:
                await tags_repo.add_tag_to_video(
                    video_id=video_id,
                    tag_id=tag["id"],
                    confidence=0.8,  # AI-based confidence
                    source="ai"
                )
                logger.info(f"Added AI tag '{result.category}' to video {video_id}")

        # Generate embedding
        # Get existing tags for this video
        video_tags = await tags_repo.get_video_tags(video_id)
        tag_names = [t["tags"]["name"] for t in video_tags if t.get("tags")]

        embedding_text = embedding_service.build_embedding_text(
            title=title,
            description=description,
            tags=tag_names,
            visual_description=result.visual_description,
            detected_objects=result.detected_objects,
            detected_scenes=result.detected_scenes,
            detected_text=result.detected_text
        )

        embedding = await embedding_service.generate_embedding(embedding_text)

        if embedding:
            await analysis_repo.update_embedding(video_id, embedding, embedding_text)
            logger.info(f"Generated embedding for video {video_id}")

        logger.info(f"Completed L1 analysis for video {video_id}, cost: ${result.cost:.4f}")
        return {
            "video_id": video_id,
            "category": result.category,
            "cost": result.cost
        }

    try:
        return run_async(_analyze())
    except Exception as e:
        logger.error(f"L1 analysis failed for video {video_id}: {e}")
        raise self.retry(exc=e)


@shared_task(bind=True, max_retries=2, default_retry_delay=120)
def analyze_video_l2_task(
    self,
    video_id: int,
    cover_url: str,
    video_path: str,
    title: str = "",
    description: str = ""
):
    """
    L2 Analysis: Analyze cover + keyframes.

    This task:
    1. Extracts keyframes from video using FFmpeg
    2. Analyzes cover + keyframes using GPT-4o
    3. Updates analysis record
    4. Regenerates embedding with richer data
    """
    logger.info(f"Starting L2 analysis for video {video_id}")

    async def _analyze():
        analysis_service = VisualAnalysisService()
        embedding_service = EmbeddingService()
        analysis_repo = AnalysisRepository()
        tags_repo = TagsRepository()

        # Extract keyframes using FFmpeg
        keyframe_paths = []

        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                # Get video duration
                probe_cmd = [
                    "ffprobe", "-v", "error",
                    "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1",
                    video_path
                ]
                duration_output = subprocess.check_output(probe_cmd, stderr=subprocess.DEVNULL)
                duration = float(duration_output.decode().strip())

                # Extract frames at 25%, 50%, 75%
                for i, pct in enumerate([0.25, 0.50, 0.75]):
                    timestamp = duration * pct
                    output_path = os.path.join(tmpdir, f"frame_{i}.jpg")

                    extract_cmd = [
                        "ffmpeg", "-y", "-ss", str(timestamp),
                        "-i", video_path,
                        "-vframes", "1",
                        "-q:v", "2",
                        output_path
                    ]
                    subprocess.run(extract_cmd, capture_output=True, check=True)

                    if os.path.exists(output_path):
                        keyframe_paths.append(output_path)

                logger.info(f"Extracted {len(keyframe_paths)} keyframes for video {video_id}")

                # Run L2 analysis
                result = await analysis_service.analyze_l2(cover_url, keyframe_paths)

                if not result:
                    logger.warning(f"L2 analysis returned no result for video {video_id}")
                    return None

                # Update analysis results
                await analysis_repo.upsert_analysis(
                    video_id=video_id,
                    analysis_level="L2",
                    visual_description=result.visual_description,
                    detected_objects=result.detected_objects,
                    detected_scenes=result.detected_scenes,
                    detected_people=result.detected_people,
                    detected_text=result.detected_text,
                    analysis_model="gpt-4o",
                    analysis_cost=result.cost
                )

                # Update tag if category changed
                if result.category and result.category != "Other":
                    tag = await tags_repo.get_tag_by_name(result.category)
                    if tag:
                        await tags_repo.add_tag_to_video(
                            video_id=video_id,
                            tag_id=tag["id"],
                            confidence=0.9,  # Higher confidence for L2
                            source="ai"
                        )

                # Regenerate embedding
                video_tags = await tags_repo.get_video_tags(video_id)
                tag_names = [t["tags"]["name"] for t in video_tags if t.get("tags")]

                embedding_text = embedding_service.build_embedding_text(
                    title=title,
                    description=description,
                    tags=tag_names,
                    visual_description=result.visual_description,
                    detected_objects=result.detected_objects,
                    detected_scenes=result.detected_scenes,
                    detected_text=result.detected_text
                )

                embedding = await embedding_service.generate_embedding(embedding_text)

                if embedding:
                    await analysis_repo.update_embedding(video_id, embedding, embedding_text)

                logger.info(f"Completed L2 analysis for video {video_id}, cost: ${result.cost:.4f}")
                return {
                    "video_id": video_id,
                    "category": result.category,
                    "cost": result.cost
                }

        except subprocess.CalledProcessError as e:
            logger.error(f"FFmpeg failed for video {video_id}: {e}")
            return None
        except FileNotFoundError:
            logger.error(f"Video file not found: {video_path}")
            return None

    try:
        return run_async(_analyze())
    except Exception as e:
        logger.error(f"L2 analysis failed for video {video_id}: {e}")
        raise self.retry(exc=e)


@shared_task
def batch_analyze_l1_task(video_ids: list, batch_size: int = 10):
    """
    Batch L1 analysis for multiple videos.
    Dispatches individual L1 tasks.
    """
    from app.db.supabase_client import get_supabase_admin

    supabase = get_supabase_admin()
    dispatched = 0

    for video_id in video_ids[:batch_size]:
        # Get video info
        result = supabase.table("douyin_videos").select(
            "id, title, desc, cover_url"
        ).eq("id", video_id).maybe_single().execute()

        if result.data:
            video = result.data
            cover_url = video.get("cover_url", "")
            if cover_url:
                analyze_video_l1_task.delay(
                    video_id=video["id"],
                    cover_url=cover_url,
                    title=video.get("title", ""),
                    description=video.get("desc", "")
                )
                dispatched += 1
                logger.info(f"Dispatched L1 analysis for video {video_id}")
            else:
                logger.warning(f"Video {video_id} has no cover_url, skipping")
        else:
            logger.warning(f"Video {video_id} not found, skipping")

    return {
        "dispatched": dispatched,
        "total_requested": len(video_ids),
        "message": f"Dispatched {dispatched} L1 analysis tasks"
    }


@shared_task
def analyze_pending_videos_task(limit: int = 50):
    """
    Find videos without analysis and dispatch L1 tasks.
    Useful for backfilling analysis on existing videos.
    """
    async def _dispatch():
        analysis_repo = AnalysisRepository()
        videos = await analysis_repo.get_videos_without_analysis(limit=limit)

        video_ids = [v["id"] for v in videos if v.get("cover_url")]

        if video_ids:
            batch_analyze_l1_task.delay(video_ids, batch_size=limit)
            logger.info(f"Dispatched batch analysis for {len(video_ids)} pending videos")
            return {"dispatched": len(video_ids)}

        logger.info("No pending videos for analysis")
        return {"dispatched": 0}

    return run_async(_dispatch())
