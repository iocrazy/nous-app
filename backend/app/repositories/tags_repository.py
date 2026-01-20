"""Repository for Tags data access (异步)."""
from typing import Optional, List

from app.db.supabase_client import get_async_supabase_admin
from loguru import logger


class TagsRepository:
    """Repository for tags CRUD operations (异步)."""

    TABLE_NAME = "tags"
    VIDEO_TAGS_TABLE = "video_tags"

    def __init__(self):
        self._client = None

    async def _get_client(self):
        """获取异步客户端"""
        if self._client is None:
            self._client = await get_async_supabase_admin()
        return self._client

    async def _get_table(self):
        """获取表引用"""
        client = await self._get_client()
        return client.table(self.TABLE_NAME)

    async def _get_video_tags_table(self):
        """获取视频标签关联表引用"""
        client = await self._get_client()
        return client.table(self.VIDEO_TAGS_TABLE)

    async def get_all_tags(self, user_id: Optional[str] = None) -> List[dict]:
        """Get all tags (system + time + user's own tags)."""
        table = await self._get_table()

        # Get system tags
        system_result = await table.select("*").eq("type", "system").execute()

        # Get time tags
        time_result = await table.select("*").eq("type", "time").execute()

        tags = system_result.data + time_result.data

        # Get user tags if user_id provided
        if user_id:
            user_result = await table.select("*").eq("user_id", user_id).execute()
            tags.extend(user_result.data)

        return tags

    async def get_tag_by_id(self, tag_id: str) -> Optional[dict]:
        """Get a single tag by ID."""
        table = await self._get_table()
        result = await table.select("*").eq("id", tag_id).maybe_single().execute()
        return result.data if result.data else None

    async def get_tag_by_name(self, name: str, user_id: Optional[str] = None) -> Optional[dict]:
        """Get a tag by name (checks system tags first, then user tags)."""
        table = await self._get_table()

        # Check system tags
        result = await table.select("*").eq("name", name).eq("type", "system").maybe_single().execute()
        if result.data:
            return result.data

        # Check time tags
        result = await table.select("*").eq("name", name).eq("type", "time").maybe_single().execute()
        if result.data:
            return result.data

        # Check user tags
        if user_id:
            result = await table.select("*").eq("name", name).eq("user_id", user_id).maybe_single().execute()
            if result.data:
                return result.data

        return None

    async def create_tag(
        self,
        name: str,
        user_id: str,
        color: str = "#6366f1",
        icon: Optional[str] = None
    ) -> dict:
        """Create a new user tag."""
        data = {
            "name": name,
            "type": "user",
            "user_id": user_id,
            "color": color,
            "icon": icon
        }

        table = await self._get_table()
        result = await table.insert(data).execute()
        logger.info(f"Created tag: {name} for user: {user_id}")
        return result.data[0]

    async def update_tag(self, tag_id: str, user_id: str, **kwargs) -> Optional[dict]:
        """Update a user tag."""
        # Filter out None values
        update_data = {k: v for k, v in kwargs.items() if v is not None}

        if not update_data:
            return await self.get_tag_by_id(tag_id)

        table = await self._get_table()
        result = await table.update(update_data).eq("id", tag_id).eq("user_id", user_id).execute()
        return result.data[0] if result.data else None

    async def delete_tag(self, tag_id: str, user_id: str) -> bool:
        """Delete a user tag."""
        table = await self._get_table()
        result = await table.delete().eq("id", tag_id).eq("user_id", user_id).eq("type", "user").execute()
        return len(result.data) > 0

    async def add_tag_to_video(
        self,
        video_id: int,
        tag_id: str,
        confidence: Optional[float] = None,
        source: str = "manual"
    ) -> dict:
        """Add a tag to a video.

        Args:
            video_id: The video ID (BIGINT in database)
            tag_id: The tag UUID
            confidence: Optional confidence score for auto-assigned tags
            source: How the tag was added ('manual', 'auto', 'ai')
        """
        data = {
            "video_id": video_id,
            "tag_id": tag_id,
            "source": source
        }
        if confidence is not None:
            data["confidence"] = confidence

        video_tags_table = await self._get_video_tags_table()
        result = await video_tags_table.upsert(data).execute()
        logger.info(f"Added tag {tag_id} to video {video_id}")
        return result.data[0]

    async def remove_tag_from_video(self, video_id: int, tag_id: str) -> bool:
        """Remove a tag from a video."""
        video_tags_table = await self._get_video_tags_table()
        result = await video_tags_table.delete().eq("video_id", video_id).eq("tag_id", tag_id).execute()
        return len(result.data) > 0

    async def get_video_tags(self, video_id: int) -> List[dict]:
        """Get all tags for a video with tag details."""
        video_tags_table = await self._get_video_tags_table()
        result = await video_tags_table.select(
            "*, tags(*)"
        ).eq("video_id", video_id).execute()

        return result.data

    async def get_videos_by_tag(
        self,
        tag_id: str,
        user_id: str,
        limit: int = 50,
        offset: int = 0
    ) -> List[dict]:
        """Get all videos with a specific tag."""
        video_tags_table = await self._get_video_tags_table()
        result = await video_tags_table.select(
            "video_id, douyin_videos(*)"
        ).eq("tag_id", tag_id).range(offset, offset + limit - 1).execute()

        return [r["douyin_videos"] for r in result.data if r.get("douyin_videos")]

    async def bulk_add_tags_to_video(
        self,
        video_id: int,
        tag_ids: List[str],
        source: str = "manual"
    ) -> List[dict]:
        """Add multiple tags to a video at once."""
        data = [
            {
                "video_id": video_id,
                "tag_id": tag_id,
                "source": source
            }
            for tag_id in tag_ids
        ]

        video_tags_table = await self._get_video_tags_table()
        result = await video_tags_table.upsert(data).execute()
        logger.info(f"Added {len(tag_ids)} tags to video {video_id}")
        return result.data

    async def get_tag_counts(self, user_id: str) -> List[dict]:
        """Get tag usage counts for a user's videos."""
        # This would need a custom RPC or view in Supabase for optimal performance
        # For now, we return an empty list - implement with Supabase function if needed
        logger.warning("get_tag_counts not implemented - requires Supabase RPC")
        return []
