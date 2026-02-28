# backend/app/tasks/ai_tasks.py

"""
AI pipeline Celery tasks.

Tasks for audio extraction, transcription, summary generation,
and visual analysis. Designed to be chained after media download.
"""

import os

from celery import shared_task
from loguru import logger

from app.repositories.user_logs_repository import log_user_action
from app.tasks.utils import run_async


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
    """Helper to update AI status on the parsed_media table by platform_id (legacy fallback)."""
    from app.repositories.ai_repository import AIRepository
    from app.repositories.media_repository import MediaRepository

    repo = MediaRepository()
    media = run_async(repo.get_by_platform_id(platform_id))
    if media:
        ai_repo = AIRepository()
        run_async(ai_repo.update_media_ai_status(media["id"], field, status))


def _update_resource_status(resource_id: str, field: str, status: str):
    """Update AI status on the resources table (per-user)."""
    if not resource_id:
        return
    try:
        from app.repositories.resources_repository import ResourcesRepository
        repo = ResourcesRepository()
        run_async(repo.update_resource(resource_id, {field: status}))
    except Exception as e:
        logger.debug(f"[AI] Resource status update failed: {e}")


def _start_unified(task_id: str):
    """Start a unified task (best-effort, never raises)."""
    if not task_id:
        return
    try:
        from app.services.task_tracker import get_task_tracker
        run_async(get_task_tracker().start(task_id))
    except Exception:
        pass


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
def extract_audio_task(self, platform_id: str, user_id: str, resource_id: str = None, unified_task_id: str = None, next_task_id: str = None, _dedup_key: str = None):
    """Extract audio from a downloaded video file.

    Produces a .wav file for Whisper transcription.
    """
    # Store orchestrator keys in request for signal handlers
    self.request.kwargs = getattr(self.request, 'kwargs', {}) or {}
    self.request.kwargs['_dedup_key'] = _dedup_key
    self.request.kwargs['_unified_task_id'] = unified_task_id

    logger.info(f"[AI] Starting audio extraction for {platform_id}")
    _update_unified_progress(unified_task_id, 5, "Extracting audio...")

    try:
        from app.core.utils import Utils
        from app.repositories.media_repository import MediaRepository

        repo = MediaRepository()
        video = run_async(repo.get_by_platform_id(platform_id))
        if not video:
            logger.error(f"[AI] Video not found: {platform_id}")
            _update_resource_status(resource_id, "transcript_status", "failed")
            _update_status(platform_id, "transcript_status", "failed")
            return {"status": "failed", "error": "Video not found"}

        # Mark as processing at the start of audio extraction
        _update_resource_status(resource_id, "transcript_status", "processing")
        _update_status(platform_id, "transcript_status", "processing")

        download_path = video.get("download_path")
        if not download_path:
            logger.error(f"[AI] No download path for video: {platform_id}")
            _update_resource_status(resource_id, "transcript_status", "failed")
            _update_status(platform_id, "transcript_status", "failed")
            return {"status": "failed", "error": "No download path"}

        # Resolve full path
        base_path = Utils.get_download_base_path()
        full_video_path = os.path.join(base_path, download_path)

        if not os.path.exists(full_video_path):
            logger.error(f"[AI] Video file not found: {full_video_path}")
            _update_resource_status(resource_id, "transcript_status", "failed")
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
        _update_unified_progress(unified_task_id, 100, "Audio extracted")
        _complete_unified(unified_task_id)
        _start_unified(next_task_id)
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
        _update_resource_status(resource_id, "transcript_status", "failed")
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
def transcribe_audio_task(self, platform_id: str, user_id: str, audio_path: str = None, resource_id: str = None, unified_task_id: str = None, next_task_id: str = None, _dedup_key: str = None):
    """Transcribe audio using Whisper.

    Args:
        platform_id: Video platform ID.
        user_id: User ID (for loading AI settings).
        audio_path: Path to audio file. If None, derived from video download path.
        resource_id: Optional resource ID for status updates.
        unified_task_id: Optional unified task ID for progress tracking.
        next_task_id: Optional next unified task ID to start on completion.
        _dedup_key: Optional dedup key for orchestrator signal handlers.
    """
    # Store orchestrator keys in request for signal handlers
    self.request.kwargs = getattr(self.request, 'kwargs', {}) or {}
    self.request.kwargs['_dedup_key'] = _dedup_key
    self.request.kwargs['_unified_task_id'] = unified_task_id

    logger.info(f"[AI] Starting transcription for {platform_id}")
    _update_unified_progress(unified_task_id, 5, "Transcribing...")

    try:
        from app.core.utils import Utils
        from app.repositories.media_repository import MediaRepository
        from app.services.whisper_service import WhisperService

        video_repo = MediaRepository()
        video = run_async(video_repo.get_by_platform_id(platform_id))
        if not video:
            _update_resource_status(resource_id, "transcript_status", "failed")
            _update_status(platform_id, "transcript_status", "failed")
            return {"status": "failed", "error": "Video not found"}

        media_id = video["id"]

        # Resolve audio path
        if not audio_path:
            base_path = Utils.get_download_base_path()
            download_path = video.get("download_path", "")
            video_dir = os.path.dirname(os.path.join(base_path, download_path))
            audio_path = os.path.join(video_dir, f"{platform_id}_audio.wav")

        if not os.path.exists(audio_path):
            logger.error(f"[AI] Audio file not found: {audio_path}")
            _update_resource_status(resource_id, "transcript_status", "failed")
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
        _update_unified_progress(unified_task_id, 100, "Transcription complete")
        _complete_unified(unified_task_id)
        _start_unified(next_task_id)
        _update_resource_status(resource_id, "transcript_status", "completed")
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
        _update_resource_status(resource_id, "transcript_status", "failed")
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
def generate_summary_task(self, platform_id: str, user_id: str, resource_id: str = None, unified_task_id: str = None, _dedup_key: str = None):
    """Generate LLM summary from a video's transcript.

    Args:
        platform_id: Video platform ID.
        user_id: User ID (for loading AI settings).
        resource_id: Optional resource ID for status updates.
        unified_task_id: Optional unified task ID for progress tracking.
        _dedup_key: Optional dedup key for orchestrator signal handlers.
    """
    # Store orchestrator keys in request for signal handlers
    self.request.kwargs = getattr(self.request, 'kwargs', {}) or {}
    self.request.kwargs['_dedup_key'] = _dedup_key
    self.request.kwargs['_unified_task_id'] = unified_task_id

    logger.info(f"[AI] Starting summary generation for {platform_id}")
    _update_unified_progress(unified_task_id, 5, "Generating summary...")

    try:
        from app.repositories.ai_repository import AIRepository
        from app.repositories.media_repository import MediaRepository
        from app.services.llm_analysis_service import LLMAnalysisService

        video_repo = MediaRepository()
        video = run_async(video_repo.get_by_platform_id(platform_id))
        if not video:
            _update_resource_status(resource_id, "summary_status", "failed")
            _update_status(platform_id, "summary_status", "failed")
            return {"status": "failed", "error": "Video not found"}

        media_id = video["id"]

        # Get transcript
        ai_repo = AIRepository()
        transcript = run_async(ai_repo.get_transcript(media_id))
        if not transcript or not transcript.get("full_text"):
            logger.warning(f"[AI] No transcript for {platform_id}, cannot summarize")
            _update_resource_status(resource_id, "summary_status", "failed")
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
        _update_resource_status(resource_id, "summary_status", "completed")
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
        _update_resource_status(resource_id, "summary_status", "failed")
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
    resource_id: str = None,
    transcript_bool: bool = True,
    summary_bool: bool = True,
):
    """Create a Celery chain for the AI pipeline.

    Chain: extract_audio → transcribe → generate_summary

    Each step gets its own unified_task row, linked by a shared group_id.

    Args:
        platform_id: Video platform ID.
        user_id: User ID.
        resource_id: Optional resource ID for status updates.
        transcript_bool: Whether to run transcription.
        summary_bool: Whether to run summary after transcription.
    """
    import uuid

    from celery import chain

    group_id = str(uuid.uuid4())
    task_ids: dict[str, str] = {}

    # ── Orchestrator dedup check for AI pipeline ──
    dedup_key = None
    try:
        from app.services.task_orchestrator import get_orchestrator
        orchestrator = get_orchestrator()
        orchestrator_result = run_async(orchestrator.acquire_or_subscribe(
            task_type="ai_extract",
            dedup_identifier=platform_id,
            user_id=user_id,
            resource_id=resource_id or "",
        ))
        dedup_key = orchestrator_result.get("dedup_key")

        if orchestrator_result["action"] == "subscribed":
            logger.info(f"[AI] Already processing {platform_id}, subscribed to existing pipeline")
            return
        if orchestrator_result["action"] == "completed":
            logger.info(f"[AI] AI pipeline already completed for {platform_id}")
            return
    except Exception as e:
        logger.warning(f"[AI] Dedup check failed, proceeding normally: {e}")

    try:
        from app.services.task_tracker import get_task_tracker
        tracker = get_task_tracker()

        if transcript_bool:
            task_ids['extract'] = run_async(tracker.create(
                user_id=user_id,
                task_type="ai_extract",
                title=f"Audio Extract: {platform_id}",
                media_id=platform_id,
                resource_id=resource_id,
                group_id=group_id,
            ))
            task_ids['transcribe'] = run_async(tracker.create(
                user_id=user_id,
                task_type="ai_transcription",
                title=f"Transcribe: {platform_id}",
                media_id=platform_id,
                resource_id=resource_id,
                group_id=group_id,
            ))

        if summary_bool and transcript_bool:
            task_ids['summary'] = run_async(tracker.create(
                user_id=user_id,
                task_type="ai_summary",
                title=f"Summarize: {platform_id}",
                media_id=platform_id,
                resource_id=resource_id,
                group_id=group_id,
            ))

        # Start the first task
        if 'extract' in task_ids:
            run_async(tracker.start(task_ids['extract']))

    except Exception as e:
        logger.warning(f"[AI] Failed to create unified tasks: {e}")

    # Update resource status to pending
    if resource_id and transcript_bool:
        _update_resource_status(resource_id, "transcript_status", "pending")
    if resource_id and summary_bool and transcript_bool:
        _update_resource_status(resource_id, "summary_status", "pending")

    tasks = []
    if transcript_bool:
        # extract_audio_task(platform_id, user_id, resource_id, unified_task_id, next_task_id, _dedup_key)
        tasks.append(extract_audio_task.si(
            platform_id, user_id, resource_id,
            task_ids.get('extract'), task_ids.get('transcribe'),
            _dedup_key=dedup_key,
        ))
        # transcribe_audio_task(platform_id, user_id, audio_path, resource_id, unified_task_id, next_task_id, _dedup_key)
        tasks.append(transcribe_audio_task.si(
            platform_id, user_id, None, resource_id,
            task_ids.get('transcribe'), task_ids.get('summary'),
            _dedup_key=dedup_key,
        ))

    if summary_bool and transcript_bool:
        # generate_summary_task(platform_id, user_id, resource_id, unified_task_id, _dedup_key)
        tasks.append(generate_summary_task.si(
            platform_id, user_id, resource_id,
            task_ids.get('summary'),
            _dedup_key=dedup_key,
        ))

    if tasks:
        pipeline = chain(*tasks)
        pipeline.apply_async()
        logger.info(f"[AI] Pipeline queued for {platform_id}: {len(tasks)} tasks, group={group_id}")
        run_async(
            log_user_action(
                user_id=user_id,
                action="ai",
                message=f"AI pipeline started: {platform_id} ({len(tasks)} tasks)",
                status="info",
                aweme_id=platform_id,
            )
        )
    else:
        logger.info(f"[AI] No AI tasks to run for {platform_id}")
