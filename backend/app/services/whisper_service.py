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
        if not os.path.exists(audio_path):
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

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
        media_id: str,
        audio_path: str,
        language: str = "auto",
        whisper_model: str = "whisper-1",
    ) -> Optional[TranscriptResult]:
        """Transcribe audio and persist the result to the database.

        Updates media status to 'processing' before starting and
        'completed' or 'failed' after.
        """
        await self._repo.update_media_ai_status(
            media_id, "transcript_status", "processing"
        )

        try:
            result = await self.transcribe(audio_path, language, whisper_model)

            # Persist to database
            segments_json = [
                {"start": s.start, "end": s.end, "text": s.text}
                for s in result.segments
            ]

            await self._repo.save_transcript(
                media_id,
                {
                    "language": result.language,
                    "full_text": result.text,
                    "segments": segments_json,
                    "whisper_model": whisper_model,
                    "duration_seconds": result.duration,
                },
            )

            await self._repo.update_media_ai_status(
                media_id, "transcript_status", "completed"
            )
            logger.info(f"Transcript saved for media {media_id}")
            return result

        except Exception as e:
            logger.error(f"Transcription failed for media {media_id}: {e}")
            await self._repo.update_media_ai_status(
                media_id, "transcript_status", "failed"
            )
            raise
