# backend/app/services/whisper_service.py

"""
Whisper transcription service.

Supports two backends:
- 'openai_api': Uses OpenAI's Whisper API via AIProviderFactory.
- 'local': Uses faster-whisper for local transcription.
"""

import os
from typing import Optional

from loguru import logger

from app.repositories.ai_repository import AIRepository
from app.services.ai_provider import AIProviderFactory, TranscriptResult


class WhisperService:
    """Transcription service using Whisper (API or local)."""

    def __init__(self, provider_key: str = "openai", provider_config: dict = None):
        """
        Args:
            provider_key: AI provider to use for Whisper API (default: 'openai').
            provider_config: Config dict for the provider (api_key, base_url, model).
        """
        self._provider_key = provider_key
        self._provider_config = provider_config or {}
        self._repo = AIRepository()

    async def transcribe(
        self,
        audio_path: str,
        language: str = "auto",
        whisper_model: str = "whisper-1",
    ) -> TranscriptResult:
        """Transcribe an audio file.

        Args:
            audio_path: Path to the audio/video file.
            language: Language code (e.g. 'en', 'zh') or 'auto' for auto-detection.
            whisper_model: Model name for Whisper (default: 'whisper-1').

        Returns:
            TranscriptResult with text, segments, language, duration.

        Raises:
            FileNotFoundError: If audio_path doesn't exist.
            NotImplementedError: If the provider doesn't support transcription.
        """
        # Path resolution chain — DB stores `extract_audio_path` as a
        # relative path like `global/resources/web/douyin/<id>/audio.m4a`,
        # which `os.path.exists()` resolves against process CWD (not the
        # download root) and so usually fails. Try, in order:
        #   1. as-is (already absolute)
        #   2. joined with settings.DOWNLOAD_PATH
        #   3. glob the directory for any `<stem>.*` (handles the case
        #      where extract_audio_from_video wrote audio.m4a but the
        #      platform downloader saved audio.mp3, or vice versa)
        if not os.path.exists(audio_path):
            from app.core.config import settings as _settings

            joined = os.path.join(_settings.DOWNLOAD_PATH, audio_path)
            if os.path.exists(joined):
                logger.info(
                    f"Audio path {audio_path} relative; resolved to {joined}"
                )
                audio_path = joined
            else:
                import glob

                parent = os.path.dirname(joined) or "."
                stem = os.path.basename(audio_path).rsplit(".", 1)[0]
                candidates = sorted(glob.glob(os.path.join(parent, f"{stem}.*")))
                if candidates:
                    logger.info(
                        f"Audio file at {audio_path} missing, falling back to "
                        f"{candidates[0]}"
                    )
                    audio_path = candidates[0]
                else:
                    raise FileNotFoundError(
                        f"Audio file not found: {audio_path} "
                        f"(also tried {joined} and {parent}/{stem}.*)"
                    )

        provider = AIProviderFactory.get_provider(
            self._provider_key, self._provider_config
        )

        kwargs = {"model": whisper_model}
        if language and language != "auto":
            kwargs["language"] = language

        logger.info(
            f"Transcribing {audio_path} with {self._provider_key} ({whisper_model})"
        )
        result = await provider.transcribe(audio_path, **kwargs)
        logger.info(
            f"Transcription complete: {len(result.segments)} segments, "
            f"language={result.language}, duration={result.duration:.1f}s"
        )
        return result

    async def transcribe_and_save(
        self,
        resource_id: str,
        audio_path: str,
        language: str = "auto",
        whisper_model: str = "whisper-1",
    ) -> Optional[TranscriptResult]:
        """Transcribe audio and persist the result to the database.

        Args:
            resource_id: ID of the resource to associate the transcript with.
            audio_path: Path to the audio file.
            language: Language code or 'auto'.
            whisper_model: Whisper model name.
        """
        try:
            result = await self.transcribe(audio_path, language, whisper_model)

            segments_json = [
                {"start": s.start, "end": s.end, "text": s.text}
                for s in result.segments
            ]

            await self._repo.save_transcript(
                resource_id,
                {
                    "language": result.language,
                    "full_text": result.text,
                    "segments": segments_json,
                    "whisper_model": whisper_model,
                    "duration_seconds": result.duration,
                },
            )

            logger.info(f"Transcript saved for resource {resource_id}")
            return result

        except Exception as e:
            logger.error(f"Transcription failed for resource {resource_id}: {e}")
            raise
