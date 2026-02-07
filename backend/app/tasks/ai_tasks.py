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


@shared_task(bind=True, max_retries=2, default_retry_delay=60)
def extract_audio_task(self, platform_id: str, user_id: str):
    """Extract audio from a downloaded video file.

    Produces a .wav file for Whisper transcription.
    """
    logger.info(f"[AI] Starting audio extraction for {platform_id}")

    try:
        from app.core.utils import Utils
        from app.repositories.video_repository import VideoRepository

        repo = VideoRepository()
        video = run_async(repo.get_by_platform_id(platform_id))
        if not video:
            logger.error(f"[AI] Video not found: {platform_id}")
            return {"status": "failed", "error": "Video not found"}

        download_path = video.get("download_path")
        if not download_path:
            logger.error(f"[AI] No download path for video: {platform_id}")
            return {"status": "failed", "error": "No download path"}

        # Resolve full path
        base_path = Utils.get_download_base_path()
        full_video_path = os.path.join(base_path, download_path)

        if not os.path.exists(full_video_path):
            logger.error(f"[AI] Video file not found: {full_video_path}")
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
        return {
            "status": "success",
            "platform_id": platform_id,
            "audio_path": audio_path,
        }

    except Exception as e:
        logger.error(f"[AI] Audio extraction failed for {platform_id}: {e}")
        if self.request.retries < self.max_retries:
            raise self.retry(exc=e)
        return {"status": "failed", "platform_id": platform_id, "error": str(e)}


@shared_task(bind=True, max_retries=2, default_retry_delay=60)
def transcribe_audio_task(self, platform_id: str, user_id: str, audio_path: str = None):
    """Transcribe audio using Whisper.

    Args:
        platform_id: Video platform ID.
        user_id: User ID (for loading AI settings).
        audio_path: Path to audio file. If None, derived from video download path.
    """
    logger.info(f"[AI] Starting transcription for {platform_id}")

    try:
        from app.core.utils import Utils
        from app.repositories.video_repository import VideoRepository
        from app.services.whisper_service import WhisperService

        video_repo = VideoRepository()
        video = run_async(video_repo.get_by_platform_id(platform_id))
        if not video:
            return {"status": "failed", "error": "Video not found"}

        video_id = video["id"]

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
                video_id=video_id,
                audio_path=audio_path,
            )
        )

        logger.success(f"[AI] Transcription complete for {platform_id}")
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
        return {"status": "failed", "platform_id": platform_id, "error": str(e)}


@shared_task(bind=True, max_retries=2, default_retry_delay=60)
def generate_summary_task(self, platform_id: str, user_id: str):
    """Generate LLM summary from a video's transcript.

    Args:
        platform_id: Video platform ID.
        user_id: User ID (for loading AI settings).
    """
    logger.info(f"[AI] Starting summary generation for {platform_id}")

    try:
        from app.repositories.ai_repository import AIRepository
        from app.repositories.video_repository import VideoRepository
        from app.services.llm_analysis_service import LLMAnalysisService

        video_repo = VideoRepository()
        video = run_async(video_repo.get_by_platform_id(platform_id))
        if not video:
            return {"status": "failed", "error": "Video not found"}

        video_id = video["id"]

        if video.get("summary_status") == "completed":
            logger.info(f"[AI] Summary already exists for {platform_id}, skipping")
            return {"status": "skipped", "platform_id": platform_id}

        # Get transcript
        ai_repo = AIRepository()
        transcript = run_async(ai_repo.get_transcript(video_id))
        if not transcript or not transcript.get("full_text"):
            logger.warning(f"[AI] No transcript for {platform_id}, cannot summarize")
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
                video_id=video_id,
                transcript_text=transcript["full_text"],
                video_info=video_info,
                model=summary_model,
            )
        )

        logger.success(f"[AI] Summary generated for {platform_id}")
        return {
            "status": "success",
            "platform_id": platform_id,
            "summary": result.summary[:200] if result else None,
        }

    except Exception as e:
        logger.error(f"[AI] Summary generation failed for {platform_id}: {e}")
        if self.request.retries < self.max_retries:
            raise self.retry(exc=e)
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

    tasks = []

    if transcript_bool:
        tasks.append(extract_audio_task.si(platform_id, user_id))
        tasks.append(transcribe_audio_task.si(platform_id, user_id))

    if summary_bool and transcript_bool:
        tasks.append(generate_summary_task.si(platform_id, user_id))

    if tasks:
        pipeline = chain(*tasks)
        pipeline.apply_async()
        logger.info(f"[AI] Pipeline queued for {platform_id}: {len(tasks)} tasks")
    else:
        logger.info(f"[AI] No AI tasks to run for {platform_id}")
