# backend/app/repositories/ai_repository.py

"""
AI data access layer.

Handles CRUD operations for resource_transcripts, resource_summaries,
and AI-related status fields on the resources table.
"""

from typing import Any, Dict, List, Optional

from loguru import logger

from app.db.supabase_client import get_async_supabase_admin


class AIRepository:
    """Repository for AI transcript/summary data."""

    def __init__(self):
        pass

    async def _get_client(self):
        """Get async client (loop-aware, safe for Celery workers)."""
        return await get_async_supabase_admin()

    # ------------------------------------------------------------------
    # Transcripts
    # ------------------------------------------------------------------

    async def save_transcript(
        self, media_id: str, data: Dict[str, Any]
    ) -> Optional[Dict]:
        """Save a transcript record for a video.

        Args:
            media_id: UUID of the video.
            data: Dict with keys: language, full_text, segments (JSONB),
                  whisper_model, duration_seconds.
        """
        try:
            client = await self._get_client()
            row = {"resource_id": media_id, **data}
            result = (
                await client.table("resource_transcripts")
                .upsert(row, on_conflict="resource_id")
                .execute()
            )
            if result.data:
                logger.info(f"Saved transcript for video {media_id}")
                return result.data[0]
            return None
        except Exception as e:
            logger.error(f"Failed to save transcript for video {media_id}: {e}")
            return None

    async def get_transcript(self, media_id: str) -> Optional[Dict]:
        """Get transcript for a video."""
        try:
            client = await self._get_client()
            result = (
                await client.table("resource_transcripts")
                .select("*")
                .eq("resource_id", media_id)
                .maybe_single()
                .execute()
            )
            return result.data
        except Exception as e:
            logger.error(f"Failed to get transcript for video {media_id}: {e}")
            return None

    # ------------------------------------------------------------------
    # Summaries
    # ------------------------------------------------------------------

    async def save_summary(self, media_id: str, data: Dict[str, Any]) -> Optional[Dict]:
        """Save a summary record for a video.

        Args:
            media_id: UUID of the video.
            data: Dict with keys: summary_type, summary_text, key_points (JSONB),
                  topics (JSONB), llm_model, llm_provider.
        """
        try:
            client = await self._get_client()
            row = {"resource_id": media_id, **data}
            result = (
                await client.table("resource_summaries")
                .upsert(row, on_conflict="resource_id")
                .execute()
            )
            if result.data:
                logger.info(f"Saved summary for video {media_id}")
                return result.data[0]
            return None
        except Exception as e:
            logger.error(f"Failed to save summary for video {media_id}: {e}")
            return None

    async def get_summary(self, media_id: str) -> Optional[Dict]:
        """Get summary for a video."""
        try:
            client = await self._get_client()
            result = (
                await client.table("resource_summaries")
                .select("*")
                .eq("resource_id", media_id)
                .maybe_single()
                .execute()
            )
            return result.data
        except Exception as e:
            logger.error(f"Failed to get summary for video {media_id}: {e}")
            return None

    # ------------------------------------------------------------------
    # Video AI status
    # ------------------------------------------------------------------

    async def update_media_ai_status(
        self, media_id: str, field: str, status: str
    ) -> bool:
        """Update an AI status field on the resources table.

        Args:
            media_id: UUID of the media.
            field: One of 'transcript_status', 'summary_status', 'visual_analysis_status'.
            status: One of 'pending', 'processing', 'completed', 'failed', 'skipped'.
        """
        valid_fields = {"transcript_status", "summary_status", "visual_analysis_status"}
        if field not in valid_fields:
            raise ValueError(
                f"Invalid status field: {field}. Must be one of {valid_fields}"
            )

        try:
            client = await self._get_client()
            await (
                client.table("resources")
                .update({field: status})
                .eq("id", media_id)
                .execute()
            )
            logger.info(f"Updated media {media_id} {field} = {status}")
            return True
        except Exception as e:
            logger.error(f"Failed to update {field} for media {media_id}: {e}")
            return False

    async def get_videos_needing_transcription(self, limit: int = 20) -> List[Dict]:
        """Get videos that need transcription (status = 'pending')."""
        try:
            client = await self._get_client()
            result = (
                await client.table("resources")
                .select(
                    "id, platform_id, title, download_path, duration, source_platform"
                )
                .eq("transcript_status", "pending")
                .not_.is_("download_path", "null")
                .limit(limit)
                .execute()
            )
            return result.data or []
        except Exception as e:
            logger.error(f"Failed to get videos needing transcription: {e}")
            return []

    async def get_videos_needing_summary(self, limit: int = 20) -> List[Dict]:
        """Get videos that need summary (transcript completed, summary pending)."""
        try:
            client = await self._get_client()
            result = (
                await client.table("resources")
                .select("id, platform_id, title, description, source_platform")
                .eq("transcript_status", "completed")
                .eq("summary_status", "pending")
                .limit(limit)
                .execute()
            )
            return result.data or []
        except Exception as e:
            logger.error(f"Failed to get videos needing summary: {e}")
            return []
