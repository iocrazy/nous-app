"""Repository for Video Analysis data access (异步)."""

from datetime import datetime
from typing import List, Optional

from loguru import logger

from app.db.supabase_client import get_async_supabase_admin


class AnalysisRepository:
    """Repository for video analysis CRUD operations (异步)."""

    TABLE_NAME = "resource_analysis"

    def __init__(self):
        pass

    async def _get_client(self):
        """Get async client (loop-aware, safe for Celery workers)."""
        return await get_async_supabase_admin()

    async def _get_table(self):
        """获取表引用"""
        client = await self._get_client()
        return client.table(self.TABLE_NAME)

    async def get_analysis(self, resource_id: int) -> Optional[dict]:
        """Get analysis for a video."""
        table = await self._get_table()
        result = (
            await table.select("*").eq("resource_id", resource_id).maybe_single().execute()
        )
        return result.data

    async def create_analysis(self, resource_id: int, **kwargs) -> dict:
        """Create analysis record for a video."""
        data = {
            "resource_id": resource_id,
            "analysis_level": kwargs.get("analysis_level", "none"),
            "visual_description": kwargs.get("visual_description"),
            "detected_objects": kwargs.get("detected_objects", []),
            "detected_scenes": kwargs.get("detected_scenes", []),
            "detected_people": kwargs.get("detected_people", []),
            "detected_text": kwargs.get("detected_text"),
            "full_text_for_embedding": kwargs.get("full_text_for_embedding"),
            "analysis_model": kwargs.get("analysis_model"),
            "analysis_cost": kwargs.get("analysis_cost", 0),
            "analyzed_at": datetime.utcnow().isoformat(),
        }

        # Filter out None values
        data = {k: v for k, v in data.items() if v is not None}

        table = await self._get_table()
        result = await table.insert(data).execute()
        logger.info(f"Created analysis for video {resource_id}")
        return result.data[0]

    async def update_analysis(self, resource_id: int, **kwargs) -> Optional[dict]:
        """Update analysis record."""
        # Filter out None values
        update_data = {k: v for k, v in kwargs.items() if v is not None}

        if not update_data:
            return await self.get_analysis(resource_id)

        update_data["analyzed_at"] = datetime.utcnow().isoformat()

        table = await self._get_table()
        result = await table.update(update_data).eq("resource_id", resource_id).execute()
        return result.data[0] if result.data else None

    async def upsert_analysis(self, resource_id: int, **kwargs) -> dict:
        """Create or update analysis record."""
        existing = await self.get_analysis(resource_id)

        if existing:
            return await self.update_analysis(resource_id, **kwargs)
        else:
            return await self.create_analysis(resource_id, **kwargs)

    async def update_embedding(
        self, resource_id: int, embedding: List[float], full_text: str
    ) -> dict:
        """Update the embedding vector for a video."""
        # Convert list to PostgreSQL vector format
        embedding_str = f"[{','.join(map(str, embedding))}]"

        table = await self._get_table()
        result = (
            await table.update(
                {
                    "content_embedding": embedding_str,
                    "full_text_for_embedding": full_text,
                }
            )
            .eq("resource_id", resource_id)
            .execute()
        )

        logger.info(f"Updated embedding for video {resource_id}")
        return result.data[0] if result.data else None

    async def get_videos_without_analysis(self, limit: int = 100) -> List[dict]:
        """Get videos that don't have analysis yet."""
        table = await self._get_table()
        client = await self._get_client()

        # Get video IDs that have analysis
        analyzed = await table.select("resource_id").execute()
        analyzed_ids = [r["resource_id"] for r in analyzed.data]

        # Get videos not in that list
        query = (
            client.table("parsed_media")
            .select("id, title, description, cover_url")
            .limit(limit)
        )

        if analyzed_ids:
            query = query.not_.in_("id", analyzed_ids)

        result = await query.execute()
        return result.data

    async def get_videos_by_analysis_level(
        self, level: str, limit: int = 100
    ) -> List[dict]:
        """Get videos with a specific analysis level."""
        table = await self._get_table()
        result = (
            await table.select("*, resources(id, title, description, cover_url)")
            .eq("analysis_level", level)
            .limit(limit)
            .execute()
        )

        return result.data

    async def search_by_embedding(
        self, embedding: List[float], limit: int = 10, threshold: float = 0.7
    ) -> List[dict]:
        """Search for similar videos using vector similarity.

        Note: Requires the 'match_videos_by_embedding' RPC function to be created in Supabase.
        """
        client = await self._get_client()
        # Use Supabase's vector similarity search via RPC
        embedding_str = f"[{','.join(map(str, embedding))}]"

        result = await client.rpc(
            "match_videos_by_embedding",
            {
                "query_embedding": embedding_str,
                "match_threshold": threshold,
                "match_count": limit,
            },
        ).execute()

        return result.data

    async def delete_analysis(self, resource_id: int) -> bool:
        """Delete analysis record for a video."""
        table = await self._get_table()
        result = await table.delete().eq("resource_id", resource_id).execute()
        return len(result.data) > 0

    async def get_analysis_stats(self) -> dict:
        """Get statistics about video analysis coverage."""
        client = await self._get_client()
        table = await self._get_table()

        # Count by analysis level
        result = await client.rpc("get_analysis_stats").execute()

        if result.data:
            return result.data

        # Fallback: manual count if RPC doesn't exist
        all_analysis = await table.select("analysis_level").execute()

        stats = {"none": 0, "L1": 0, "L2": 0, "L3": 0, "total": len(all_analysis.data)}

        for record in all_analysis.data:
            level = record.get("analysis_level", "none")
            if level in stats:
                stats[level] += 1

        return stats
