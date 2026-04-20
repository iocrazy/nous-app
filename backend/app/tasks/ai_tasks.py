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
        from app.services.unified_task_manager import get_task_manager
        run_async(get_task_manager().start(task_id))
    except Exception as e:
        logger.error(f"[AI] _start_unified silent exception: {e}")


def _update_unified_progress(unified_task_id: str, progress: int, subtitle: str = None):
    """Update unified task progress (best-effort, never raises)."""
    if not unified_task_id:
        return
    try:
        from app.services.unified_task_manager import get_task_manager
        tracker = get_task_manager()
        run_async(tracker.update_progress(
            unified_task_id, progress=progress, subtitle=subtitle,
        ))
    except Exception as e:
        logger.debug(f"[AI] Unified progress update failed: {e}")


def _complete_unified(unified_task_id: str):
    if not unified_task_id:
        return
    try:
        from app.services.unified_task_manager import get_task_manager
        run_async(get_task_manager().complete(unified_task_id))
    except Exception as e:
        logger.error(f"[AI] _complete_unified silent exception: {e}")


def _fail_unified(unified_task_id: str, error_msg: str):
    if not unified_task_id:
        return
    try:
        from app.services.unified_task_manager import get_task_manager
        run_async(get_task_manager().fail(unified_task_id, error_msg[:500]))
    except Exception as e:
        logger.error(f"[AI] _fail_unified silent exception: {e}")


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

        # Reuse existing audio file (m4a/mp3) if present, skip ffmpeg extraction
        video_dir = os.path.dirname(full_video_path)
        existing_audio = None
        for name in ["audio.m4a", "audio.mp3"]:
            candidate = os.path.join(video_dir, name)
            if os.path.exists(candidate):
                existing_audio = candidate
                break

        if existing_audio:
            audio_path = existing_audio
            logger.info(f"[AI] Reusing existing audio: {audio_path}")
        else:
            audio_path = os.path.join(video_dir, "audio.m4a")

            import subprocess

            cmd = [
                "ffmpeg",
                "-i",
                full_video_path,
                "-vn",
                "-codec:a", "aac",
                "-b:a", "128k",
                "-ar", "16000",
                "-ac", "1",
                "-y",
                audio_path,
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

            if result.returncode != 0:
                raise RuntimeError(f"ffmpeg failed: {result.stderr[:500]}")

            logger.success(f"[AI] Audio extracted: {audio_path}")

            # Write extract_audio_path to DB
            from app.core.utils import Utils as _Utils
            _base = _Utils.get_download_base_path()
            _rel = os.path.relpath(audio_path, _base)
            run_async(MediaRepository().update(platform_id, {"extract_audio_path": _rel}))

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

    provider_key = "unknown"
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

        # Resolve resource_id — required for per-resource transcript storage
        if not resource_id:
            # Legacy path: find a resource linked to this media
            from app.repositories.resources_repository import ResourcesRepository
            res_repo = ResourcesRepository()
            resource = run_async(res_repo.get_resource_by_media_id(media_id))
            if resource:
                resource_id = str(resource["id"])
            else:
                logger.error(f"[AI] No resource found for media {media_id}")
                _update_status(platform_id, "transcript_status", "failed")
                return {"status": "failed", "error": "No resource linked to this media"}

        # Resolve audio path — check mp3 first (already extracted by downloader), then wav
        if not audio_path:
            base_path = Utils.get_download_base_path()
            download_path = video.get("download_path", "")
            video_dir = os.path.dirname(os.path.join(base_path, download_path))
            for candidate_name in ["audio.m4a", "audio.mp3"]:
                candidate = os.path.join(video_dir, candidate_name)
                if os.path.exists(candidate):
                    audio_path = candidate
                    break
            if not audio_path:
                audio_path = os.path.join(video_dir, f"{platform_id}_audio.wav")

        if not os.path.exists(audio_path):
            logger.error(f"[AI] Audio file not found: {audio_path}")
            _update_resource_status(resource_id, "transcript_status", "failed")
            _update_status(platform_id, "transcript_status", "failed")
            return {"status": "failed", "error": f"Audio file not found: {audio_path}"}

        # Load user AI settings and determine transcription provider
        from app.core.config import settings

        ai_settings = _get_ai_settings(user_id)
        transcription_assignment = ai_settings.get("task_assignment", {}).get("transcription", "openai:whisper-1")

        # Check if using a Nous platform model
        if transcription_assignment.startswith("nous-"):
            from app.repositories.nous_repository import NousRepository
            nous_repo = NousRepository()
            nous_model = run_async(nous_repo.get_by_name(transcription_assignment))
            if nous_model:
                # Override provider config with Nous platform config
                transcription_assignment = f"{nous_model['actual_provider']}:{nous_model['actual_model']}"
                ai_settings = {
                    **ai_settings,
                    "ai_providers": {
                        nous_model["actual_provider"]: {
                            "api_key": nous_model["api_key"],
                            "app_id": nous_model.get("app_id", ""),
                            "base_url": nous_model.get("base_url", ""),
                            "enabled": True,
                        }
                    },
                }
                logger.info(f"[AI] Using Nous model '{nous_model['name']}' -> {transcription_assignment}")

        provider_key = transcription_assignment.split(":")[0] if ":" in transcription_assignment else transcription_assignment

        if provider_key == "volcengine":
            # Use Volcengine Seed-ASR (requires public audio URL)
            from app.services.volcengine_asr_service import VolcengineASRService

            _update_unified_progress(unified_task_id, 10, "Preparing audio...")

            volcengine_config = _get_provider_config(ai_settings, "volcengine")

            # Build public audio URL with signed media token
            download_path = video.get("download_path", "")
            video_dir = os.path.dirname(download_path)
            audio_filename = os.path.basename(audio_path)

            import hashlib, hmac as hmac_mod, time as time_mod
            expires_at = int(time_mod.time()) + 3600
            payload = f"{user_id}.{expires_at}"
            from app.api.media_auth import _get_secret
            sig = hmac_mod.new(
                _get_secret().encode(), payload.encode(), hashlib.sha256
            ).hexdigest()[:32]
            media_token = f"{payload}.{sig}"

            media_public_url = getattr(settings, "MEDIA_PUBLIC_URL", "https://mediahubserver.heygo.cn:88")
            audio_url = (
                f"{media_public_url}/media/"
                f"{video_dir}/{audio_filename}?token={media_token}"
            )

            _update_unified_progress(unified_task_id, 20, "Transcribing via Volcengine...")

            ext = os.path.splitext(audio_path)[1].lstrip(".").lower()
            audio_format = ext if ext in ("mp3", "wav", "ogg") else "wav"

            # Determine model version from task assignment (e.g. "volcengine:seed-asr" or "volcengine:bigasr")
            from app.services.volcengine_asr_service import RESOURCE_V1, RESOURCE_V2
            model_part = transcription_assignment.split(":", 1)[1] if ":" in transcription_assignment else "seed-asr"
            asr_resource = RESOURCE_V1 if "bigasr" in model_part or "1.0" in model_part else RESOURCE_V2

            service = VolcengineASRService(
                app_id=volcengine_config.get("app_id", ""),
                access_token=volcengine_config.get("api_key", ""),
                asr_resource_id=asr_resource,
            )
            result = run_async(
                service.transcribe_and_save(
                    resource_id=resource_id,
                    audio_url=audio_url,
                    audio_format=audio_format,
                )
            )
        else:
            # Default: OpenAI Whisper API
            provider_config = _get_provider_config(ai_settings, provider_key or "openai")

            if not provider_config.get("api_key"):
                provider_config["api_key"] = settings.OPENAI_API_KEY

            service = WhisperService(
                provider_key=provider_key or "openai",
                provider_config=provider_config,
            )
            result = run_async(
                service.transcribe_and_save(
                    resource_id=resource_id,
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

    summary_model = "unknown"
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

        # Resolve resource_id for per-resource transcript/summary storage
        if not resource_id:
            from app.repositories.resources_repository import ResourcesRepository
            res_repo = ResourcesRepository()
            resource = run_async(res_repo.get_resource_by_media_id(media_id))
            if resource:
                resource_id = str(resource["id"])
            else:
                logger.error(f"[AI] No resource found for media {media_id}")
                _update_status(platform_id, "summary_status", "failed")
                return {"status": "failed", "error": "No resource linked to this media"}

        # Get transcript by resource_id
        ai_repo = AIRepository()
        transcript = run_async(ai_repo.get_transcript(resource_id))
        if not transcript or not transcript.get("full_text"):
            logger.warning(f"[AI] No transcript for resource {resource_id}, cannot summarize")
            _update_resource_status(resource_id, "summary_status", "failed")
            _update_status(platform_id, "summary_status", "failed")
            return {"status": "failed", "error": "No transcript available"}

        # Load user AI settings
        ai_settings = _get_ai_settings(user_id)

        # Read summary task assignment (e.g. "openai:gpt-4o-mini", "volcengine:doubao-...")
        # Frontend stores this under "summarization"; "summary" is a legacy key kept
        # here as a safety net for any old rows. default_summary_model may itself
        # already be provider-qualified ("doubao:doubao-...") — don't re-prefix it.
        _ta = ai_settings.get("task_assignment", {})
        summary_assignment = _ta.get("summarization") or _ta.get("summary")
        if not summary_assignment:
            _default = ai_settings.get("default_summary_model", "gpt-4o-mini")
            summary_assignment = _default if ":" in _default else f"openai:{_default}"

        # Resolve Nous platform models to their actual provider
        if summary_assignment.startswith("nous-"):
            from app.repositories.nous_repository import NousRepository
            nous_repo = NousRepository()
            nous_model = run_async(nous_repo.get_by_name(summary_assignment))
            if nous_model:
                summary_assignment = f"{nous_model['actual_provider']}:{nous_model['actual_model']}"
                ai_settings = {
                    **ai_settings,
                    "ai_providers": {
                        nous_model["actual_provider"]: {
                            "api_key": nous_model["api_key"],
                            "app_id": nous_model.get("app_id", ""),
                            "base_url": nous_model.get("base_url", ""),
                            "enabled": True,
                        }
                    },
                }
                logger.info(f"[AI] Using Nous model '{nous_model['name']}' -> {summary_assignment}")

        # Split provider:model
        if ":" in summary_assignment:
            provider_key, summary_model = summary_assignment.split(":", 1)
        else:
            provider_key, summary_model = "openai", summary_assignment

        provider_config = _get_provider_config(ai_settings, provider_key)

        # Fallback: use global OpenAI key if user didn't configure a per-user one for OpenAI
        if provider_key == "openai" and not provider_config.get("api_key"):
            from app.core.config import settings
            provider_config["api_key"] = settings.OPENAI_API_KEY

        language = ai_settings.get("preferred_language") or "auto"
        logger.info(
            f"[AI] Summary using provider={provider_key}, model={summary_model}, "
            f"language={language}"
        )

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
                resource_id=resource_id,
                transcript_text=transcript["full_text"],
                video_info=video_info,
                model=summary_model,
                language=language,
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
        from celery.exceptions import Retry as _CeleryRetry
        # Celery's Retry is a subclass of Exception — let it propagate unchanged
        if isinstance(e, _CeleryRetry):
            raise
        logger.error(f"[AI] Summary generation failed for {platform_id} "
                     f"(model={summary_model}): {e}")

        # Prefix provider:model so the user can tell which LLM was failing.
        err_msg = f"[{summary_model}] {str(e)}"[:200]
        retrying = self.request.retries < self.max_retries
        if retrying:
            # Update unified_task with retry progress so user sees it's not dead
            try:
                from app.services.unified_task_manager import get_task_manager
                _tracker = get_task_manager()
                run_async(_tracker.update_progress(
                    unified_task_id,
                    10,
                    subtitle=f"Retrying ({self.request.retries + 1}/{self.max_retries}): {err_msg}",
                ))
            except Exception:
                pass
            raise self.retry(exc=e)

        _update_resource_status(resource_id, "summary_status", "failed")
        _update_status(platform_id, "summary_status", "failed")
        _fail_unified(unified_task_id, f"Summary generation failed: {err_msg}")
        run_async(
            log_user_action(
                user_id=user_id,
                action="ai",
                message=f"Summary generation failed: {platform_id}",
                status="error",
                aweme_id=platform_id,
                details={"error": err_msg, "model": summary_model},
            )
        )
        return {"status": "failed", "platform_id": platform_id, "error": err_msg}


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

    # ── Dedup check for AI pipeline ──
    dedup_key = None
    try:
        from app.services.unified_task_manager import get_task_manager
        mgr = get_task_manager()
        dedup_result = run_async(mgr.acquire_or_subscribe(
            task_type="ai_extract",
            dedup_identifier=platform_id,
            user_id=user_id,
            resource_id=resource_id or "",
        ))
        dedup_key = dedup_result.get("dedup_key")

        if dedup_result["action"] == "subscribed":
            logger.info(f"[AI] Already processing {platform_id}, subscribed to existing pipeline")
            return
        if dedup_result["action"] == "completed":
            logger.info(f"[AI] AI pipeline already completed for {platform_id}")
            return
    except Exception as e:
        logger.warning(f"[AI] Dedup check failed, proceeding normally: {e}")

    # Check if audio file already exists BEFORE creating tasks.
    # Priority: DB extract_audio_path → DB music_download_path → disk audio.m4a/mp3
    existing_audio = None
    if transcript_bool:
        try:
            from app.core.utils import Utils
            from app.repositories.media_repository import MediaRepository
            repo = MediaRepository()
            video = run_async(repo.get_by_platform_id(platform_id))
            if video:
                base_path = Utils.get_download_base_path()

                # 1. Check DB extract_audio_path (written by _extract_audio_from_video)
                for db_field in ["extract_audio_path", "music_download_path"]:
                    db_path = video.get(db_field)
                    if db_path and not db_path.startswith("http"):
                        full = os.path.join(base_path, db_path)
                        if os.path.exists(full):
                            existing_audio = full
                            break

                # 2. Fallback: scan disk for known audio filenames
                if not existing_audio:
                    download_path = video.get("download_path", "")
                    video_dir = os.path.dirname(os.path.join(base_path, download_path))
                    for name in ["audio.m4a", "audio.mp3"]:
                        candidate = os.path.join(video_dir, name)
                        if os.path.exists(candidate):
                            existing_audio = candidate
                            break
        except Exception as e:
            logger.debug(f"[AI] Audio existence check failed: {e}")

    try:
        from app.services.unified_task_manager import get_task_manager
        tracker = get_task_manager()

        if transcript_bool:
            # Only create extract task if no existing audio
            if not existing_audio:
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
        first_task = 'extract' if 'extract' in task_ids else 'transcribe'
        if first_task in task_ids:
            run_async(tracker.start(task_ids[first_task]))

    except Exception as e:
        logger.warning(f"[AI] Failed to create unified tasks: {e}")

    # Update resource status to pending
    if resource_id and transcript_bool:
        _update_resource_status(resource_id, "transcript_status", "pending")
    if resource_id and summary_bool and transcript_bool:
        _update_resource_status(resource_id, "summary_status", "pending")

    tasks = []
    if transcript_bool:
        if existing_audio:
            logger.info(f"[AI] Audio already exists, skipping extract: {existing_audio}")
            # Go straight to transcribe with the existing audio path
            tasks.append(transcribe_audio_task.si(
                platform_id, user_id, existing_audio, resource_id,
                task_ids.get('transcribe'), task_ids.get('summary'),
                _dedup_key=dedup_key,
            ))
        else:
            # No audio yet, run full extract → transcribe chain
            tasks.append(extract_audio_task.si(
                platform_id, user_id, resource_id,
                task_ids.get('extract'), task_ids.get('transcribe'),
                _dedup_key=dedup_key,
            ))
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
        async_result = pipeline.apply_async()

        # Link celery_task_id back to each unified_task so the admin panel
        # and the retry flow can locate the Celery job. Without this the
        # admin page showed "Celery Task ID: -" for every AI task and the
        # reaper couldn't distinguish a dispatched task from a lost one.
        try:
            from app.services.unified_task_manager import get_task_manager
            _mgr = get_task_manager()
            # chain() returns an AsyncResult whose .parent chain ends at
            # the first task; walk the list in reverse to grab all IDs.
            celery_ids: list[str] = []
            _cur = async_result
            while _cur is not None:
                celery_ids.append(_cur.id)
                _cur = getattr(_cur, "parent", None)
            celery_ids = list(reversed(celery_ids))  # extract → transcribe → summary

            ordered_keys = []
            if transcript_bool:
                ordered_keys.append("extract" if "extract" in task_ids else None)
                ordered_keys.append("transcribe")
            if summary_bool and transcript_bool:
                ordered_keys.append("summary")

            for key, cid in zip([k for k in ordered_keys if k], celery_ids):
                utid = task_ids.get(key)
                if utid and cid:
                    run_async(_mgr._atomic_update(utid, {"celery_task_id": cid}))
        except Exception as e:
            logger.debug(f"[AI] Failed to link celery_task_ids: {e}")

        logger.info(f"[AI] Pipeline queued for {platform_id}: {len(tasks)} tasks, group={group_id}")
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
        logger.info(f"[AI] No AI tasks to run for {platform_id}")
