# backend/app/tasks/ai_tasks.py

"""
AI pipeline Celery tasks.

Tasks for audio extraction, transcription, summary generation,
and visual analysis. Designed to be chained after media download.
"""

import asyncio
import os

from celery import shared_task
from loguru import logger

from app.repositories.user_logs_repository import log_user_action


def run_async(coro):
    """Run async coroutine in synchronous Celery worker context."""
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


def _get_ai_settings(user_id: str) -> dict:
    """Load user's AI settings from the database."""
    from app.repositories.user_settings_repository import UserSettingsRepository

    repo = UserSettingsRepository()
    settings = run_async(repo.get_by_user_id(user_id))
    if settings and settings.get("settings_json"):
        return settings["settings_json"].get("ai_settings", {})
    return {}


def _get_provider_config(ai_settings: dict, provider_key: str) -> dict:
    """Extract provider config from AI settings."""
    providers = ai_settings.get("ai_providers", {})
    return providers.get(provider_key, {})


def _update_status(platform_id: str, field: str, status: str):
    """Helper to update AI status on the parsed_media table by platform_id."""
    from app.repositories.ai_repository import AIRepository
    from app.repositories.media_repository import MediaRepository

    repo = MediaRepository()
    media = run_async(repo.get_by_platform_id(platform_id))
    if media:
        ai_repo = AIRepository()
        run_async(ai_repo.update_media_ai_status(media["id"], field, status))


def _update_unified_progress(unified_task_id: str, progress: int, subtitle: str = None):
    """Update unified task progress (best-effort, never raises)."""
    if not unified_task_id:
        return
    try:
        from app.services.task_tracker import get_task_tracker
        tracker = get_task_tracker()
        run_async(tracker.update_progress(
            unified_task_id, progress=progress, subtitle=subtitle,
        ))
    except Exception as e:
        logger.debug(f"[AI] Unified progress update failed: {e}")


def _complete_unified(unified_task_id: str):
    if not unified_task_id:
        return
    try:
        from app.services.task_tracker import get_task_tracker
        run_async(get_task_tracker().complete(unified_task_id))
    except Exception:
        pass


def _fail_unified(unified_task_id: str, error_msg: str):
    if not unified_task_id:
        return
    try:
        from app.services.task_tracker import get_task_tracker
        run_async(get_task_tracker().fail(unified_task_id, error_msg[:500]))
    except Exception:
        pass


@shared_task(bind=True, max_retries=2, default_retry_delay=60)
def extract_audio_task(self, platform_id: str, user_id: str, unified_task_id: str = None):
    """Extract audio from a downloaded video file.

    Produces a .wav file for Whisper transcription.
    """
    logger.info(f"[AI] Starting audio extraction for {platform_id}")
    _update_unified_progress(unified_task_id, 5, "Extracting audio...")

    try:
        from app.core.utils import Utils
        from app.repositories.media_repository import MediaRepository

        repo = MediaRepository()
        video = run_async(repo.get_by_platform_id(platform_id))
        if not video:
            logger.error(f"[AI] Video not found: {platform_id}")
            _update_status(platform_id, "transcript_status", "failed")
            return {"status": "failed", "error": "Video not found"}

        # Mark as processing at the start of audio extraction
        _update_status(platform_id, "transcript_status", "processing")

        download_path = video.get("download_path")
        if not download_path:
            logger.error(f"[AI] No download path for video: {platform_id}")
            _update_status(platform_id, "transcript_status", "failed")
            return {"status": "failed", "error": "No download path"}

        # Resolve full path
        base_path = Utils.get_download_base_path()
        full_video_path = os.path.join(base_path, download_path)

        if not os.path.exists(full_video_path):
            logger.error(f"[AI] Video file not found: {full_video_path}")
            _update_status(platform_id, "transcript_status", "failed")
            return {"status": "failed", "error": f"File not found: {full_video_path}"}

        # Output audio path alongside the video
        video_dir = os.path.dirname(full_video_path)
        audio_filename = f"{platform_id}_audio.wav"
        audio_path = os.path.join(video_dir, audio_filename)

        # Extract audio using ffmpeg
        import subprocess

        cmd = [
            "ffmpeg",
            "-i",
            full_video_path,
            "-vn",
            "-acodec",
            "pcm_s16le",
            "-ar",
            "16000",
            "-ac",
            "1",
            "-y",
            audio_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg failed: {result.stderr[:500]}")

        logger.success(f"[AI] Audio extracted: {audio_path}")
        _update_unified_progress(unified_task_id, 33, "Audio extracted")
        run_async(
            log_user_action(
                user_id=user_id,
                action="ai",
                message=f"Audio extracted: {platform_id}",
                status="success",
                aweme_id=platform_id,
            )
        )
        return {
            "status": "success",
            "platform_id": platform_id,
            "audio_path": audio_path,
        }

    except Exception as e:
        logger.error(f"[AI] Audio extraction failed for {platform_id}: {e}")
        if self.request.retries < self.max_retries:
            raise self.retry(exc=e)
        _update_status(platform_id, "transcript_status", "failed")
        _fail_unified(unified_task_id, f"Audio extraction failed: {e}")
        run_async(
            log_user_action(
                user_id=user_id,
                action="ai",
                message=f"Audio extraction failed: {platform_id}",
                status="error",
                aweme_id=platform_id,
                details={"error": str(e)[:200]},
            )
        )
        return {"status": "failed", "platform_id": platform_id, "error": str(e)}


@shared_task(bind=True, max_retries=2, default_retry_delay=60)
def transcribe_audio_task(self, platform_id: str, user_id: str, audio_path: str = None, unified_task_id: str = None):
    """Transcribe audio using Whisper.

    Args:
        platform_id: Video platform ID.
        user_id: User ID (for loading AI settings).
        audio_path: Path to audio file. If None, derived from video download path.
        unified_task_id: Optional unified task ID for progress tracking.
    """
    logger.info(f"[AI] Starting transcription for {platform_id}")
    _update_unified_progress(unified_task_id, 35, "Transcribing...")

    try:
        from app.core.utils import Utils
        from app.repositories.media_repository import MediaRepository
        from app.services.whisper_service import WhisperService

        video_repo = MediaRepository()
        video = run_async(video_repo.get_by_platform_id(platform_id))
        if not video:
            _update_status(platform_id, "transcript_status", "failed")
            return {"status": "failed", "error": "Video not found"}

        media_id = video["id"]

        if video.get("transcript_status") == "completed":
            logger.info(f"[AI] Transcript already exists for {platform_id}, skipping")
            return {"status": "skipped", "platform_id": platform_id}

        # Resolve audio path
        if not audio_path:
            base_path = Utils.get_download_base_path()
            download_path = video.get("download_path", "")
            video_dir = os.path.dirname(os.path.join(base_path, download_path))
            audio_path = os.path.join(video_dir, f"{platform_id}_audio.wav")

        if not os.path.exists(audio_path):
            logger.error(f"[AI] Audio file not found: {audio_path}")
            _update_status(platform_id, "transcript_status", "failed")
            return {"status": "failed", "error": f"Audio file not found: {audio_path}"}

        # Load user AI settings
        ai_settings = _get_ai_settings(user_id)
        provider_key = "openai"  # Whisper is only available via OpenAI
        provider_config = _get_provider_config(ai_settings, provider_key)

        # Fall back to env config
        if not provider_config.get("api_key"):
            from app.core.config import settings

            provider_config["api_key"] = settings.OPENAI_API_KEY

        service = WhisperService(
            provider_key=provider_key,
            provider_config=provider_config,
        )

        result = run_async(
            service.transcribe_and_save(
                media_id=media_id,
                audio_path=audio_path,
            )
        )

        logger.success(f"[AI] Transcription complete for {platform_id}")
        _update_unified_progress(unified_task_id, 66, "Transcription complete")
        run_async(
            log_user_action(
                user_id=user_id,
                action="ai",
                message=f"Transcription completed: {platform_id}",
                status="success",
                aweme_id=platform_id,
                details={"provider": provider_key},
            )
        )
        return {
            "status": "success",
            "platform_id": platform_id,
            "language": result.language if result else None,
            "duration": result.duration if result else None,
        }

    except Exception as e:
        logger.error(f"[AI] Transcription failed for {platform_id}: {e}")
        if self.request.retries < self.max_retries:
            raise self.retry(exc=e)
        _update_status(platform_id, "transcript_status", "failed")
        _fail_unified(unified_task_id, f"Transcription failed: {e}")
        run_async(
            log_user_action(
                user_id=user_id,
                action="ai",
                message=f"Transcription failed: {platform_id}",
                status="error",
                aweme_id=platform_id,
                details={"error": str(e)[:200], "provider": provider_key},
            )
        )
        return {"status": "failed", "platform_id": platform_id, "error": str(e)}


@shared_task(bind=True, max_retries=2, default_retry_delay=60)
def generate_summary_task(self, platform_id: str, user_id: str, unified_task_id: str = None):
    """Generate LLM summary from a video's transcript.

    Args:
        platform_id: Video platform ID.
        user_id: User ID (for loading AI settings).
        unified_task_id: Optional unified task ID for progress tracking.
    """
    logger.info(f"[AI] Starting summary generation for {platform_id}")
    _update_unified_progress(unified_task_id, 68, "Generating summary...")

    try:
        from app.repositories.ai_repository import AIRepository
        from app.repositories.media_repository import MediaRepository
        from app.services.llm_analysis_service import LLMAnalysisService

        video_repo = MediaRepository()
        video = run_async(video_repo.get_by_platform_id(platform_id))
        if not video:
            _update_status(platform_id, "summary_status", "failed")
            return {"status": "failed", "error": "Video not found"}

        media_id = video["id"]

        if video.get("summary_status") == "completed":
            logger.info(f"[AI] Summary already exists for {platform_id}, skipping")
            return {"status": "skipped", "platform_id": platform_id}

        # Get transcript
        ai_repo = AIRepository()
        transcript = run_async(ai_repo.get_transcript(media_id))
        if not transcript or not transcript.get("full_text"):
            logger.warning(f"[AI] No transcript for {platform_id}, cannot summarize")
            _update_status(platform_id, "summary_status", "failed")
            return {"status": "failed", "error": "No transcript available"}

        # Load user AI settings
        ai_settings = _get_ai_settings(user_id)
        summary_model = ai_settings.get("default_summary_model", "gpt-4o-mini")

        # Determine provider from settings
        provider_key = "openai"
        provider_config = _get_provider_config(ai_settings, provider_key)

        if not provider_config.get("api_key"):
            from app.core.config import settings

            provider_config["api_key"] = settings.OPENAI_API_KEY

        service = LLMAnalysisService(
            provider_key=provider_key,
            provider_config=provider_config,
        )

        video_info = {
            "title": video.get("title"),
            "description": video.get("description"),
            "author": video.get("author"),
        }

        result = run_async(
            service.generate_summary_and_save(
                media_id=media_id,
                transcript_text=transcript["full_text"],
                video_info=video_info,
                model=summary_model,
            )
        )

        logger.success(f"[AI] Summary generated for {platform_id}")
        _complete_unified(unified_task_id)
        run_async(
            log_user_action(
                user_id=user_id,
                action="ai",
                message=f"Summary generated: {platform_id}",
                status="success",
                aweme_id=platform_id,
                details={"model": summary_model},
            )
        )
        return {
            "status": "success",
            "platform_id": platform_id,
            "summary": result.summary[:200] if result else None,
        }

    except Exception as e:
        logger.error(f"[AI] Summary generation failed for {platform_id}: {e}")
        if self.request.retries < self.max_retries:
            raise self.retry(exc=e)
        _update_status(platform_id, "summary_status", "failed")
        _fail_unified(unified_task_id, f"Summary generation failed: {e}")
        run_async(
            log_user_action(
                user_id=user_id,
                action="ai",
                message=f"Summary generation failed: {platform_id}",
                status="error",
                aweme_id=platform_id,
                details={"error": str(e)[:200], "model": summary_model},
            )
        )
        return {"status": "failed", "platform_id": platform_id, "error": str(e)}


def chain_ai_pipeline(
    platform_id: str,
    user_id: str,
    transcript_bool: bool = True,
    summary_bool: bool = True,
):
    """Create a Celery chain for the AI pipeline.

    Chain: extract_audio → transcribe → generate_summary

    Args:
        platform_id: Video platform ID.
        user_id: User ID.
        transcript_bool: Whether to run transcription.
        summary_bool: Whether to run summary after transcription.
    """
    from celery import chain

    # Create unified task for the whole pipeline
    unified_task_id = None
    try:
        from app.services.task_tracker import get_task_tracker
        tracker = get_task_tracker()
        unified_task_id = run_async(tracker.create(
            user_id=user_id,
            task_type="ai_pipeline",
            title=f"AI Analysis: {platform_id}",
            media_id=platform_id,
        ))
        run_async(tracker.start(unified_task_id))
    except Exception as e:
        logger.warning(f"[AI] Failed to create unified task: {e}")

    tasks = []

    if transcript_bool:
        tasks.append(extract_audio_task.si(platform_id, user_id, unified_task_id))
        tasks.append(transcribe_audio_task.si(platform_id, user_id, None, unified_task_id))

    if summary_bool and transcript_bool:
        tasks.append(generate_summary_task.si(platform_id, user_id, unified_task_id))

    if tasks:
        pipeline = chain(*tasks)
        pipeline.apply_async()
        logger.info(f"[AI] Pipeline queued for {platform_id}: {len(tasks)} tasks")
        run_async(
            log_user_action(
                user_id=user_id,
                action="ai",
                message=f"AI pipeline started: {platform_id} ({len(tasks)} tasks)",
                status="pending",
                aweme_id=platform_id,
            )
        )
    else:
        # No tasks to run, mark unified task as completed
        _complete_unified(unified_task_id)
        logger.info(f"[AI] No AI tasks to run for {platform_id}")
