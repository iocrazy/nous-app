"""Video Collection repository for database operations."""

from typing import Optional, List, Dict, Any
from loguru import logger

from app.db.supabase_client import get_async_supabase_admin


class VideoCollectionRepository:
    """Repository for video collection database operations."""

    async def get_user_collections(self, user_id: str) -> List[Dict[str, Any]]:
        """Get all collections the user has access to."""
        client = await get_async_supabase_admin()

        # Get team IDs user is member of
        memberships = await client.table("team_members").select("team_id").eq("user_id", user_id).execute()
        team_ids = [m["team_id"] for m in (memberships.data or [])]

        # Build query for collections
        # User owns OR team_id in user's teams
        if team_ids:
            # Use RPC or manual filter since complex OR is tricky
            collections = await client.table("collections").select("*, video_collections(count)").order("created_at", desc=True).execute()

            # Filter in Python
            filtered = [
                c for c in (collections.data or [])
                if c["owner_id"] == user_id or c.get("team_id") in team_ids
            ]
        else:
            collections = await client.table("collections").select("*, video_collections(count)").eq("owner_id", user_id).order("created_at", desc=True).execute()
            filtered = collections.data or []

        # Get thumbnails for each collection
        result = []
        for c in filtered:
            thumbnail_url = await self._get_collection_thumbnail(c["id"])
            result.append({
                **c,
                "video_count": c.get("video_collections", [{}])[0].get("count", 0) if c.get("video_collections") else 0,
                "is_shared": bool(c.get("team_id")),
                "thumbnail_url": thumbnail_url
            })

        return result

    async def _get_collection_thumbnail(self, collection_id: int) -> Optional[str]:
        """Get thumbnail URL for a collection (first video's cover)."""
        client = await get_async_supabase_admin()

        # Get first video in collection
        first_video = await client.table("video_collections").select("video_id").eq("collection_id", collection_id).order("added_at", desc=True).limit(1).execute()

        if not first_video.data:
            return None

        video_id = first_video.data[0]["video_id"]

        # Get video's cover URL
        video = await client.table("douyin_videos").select("cover_download_path, dynamic_cover_url").eq("id", video_id).execute()

        if not video.data:
            return None

        return video.data[0].get("cover_download_path") or video.data[0].get("dynamic_cover_url")

    async def create_collection(
        self,
        name: str,
        owner_id: str,
        team_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Create a new collection."""
        client = await get_async_supabase_admin()

        result = await client.table("collections").insert({
            "name": name,
            "owner_id": owner_id,
            "team_id": team_id
        }).select().single().execute()

        if not result.data:
            raise Exception("Failed to create collection")

        return {
            **result.data,
            "video_count": 0,
            "is_shared": bool(team_id)
        }

    async def get_collection_by_id(
        self,
        collection_id: str,
        user_id: str
    ) -> Optional[Dict[str, Any]]:
        """Get a collection by ID if user has access."""
        client = await get_async_supabase_admin()

        # Get collection
        collection = await client.table("collections").select("*").eq("id", int(collection_id)).execute()

        if not collection.data:
            return None

        c = collection.data[0]

        # Check access
        if c["owner_id"] != user_id:
            # Check team membership
            if c.get("team_id"):
                membership = await client.table("team_members").select("*").eq("team_id", c["team_id"]).eq("user_id", user_id).execute()
                if not membership.data:
                    return None
            else:
                return None

        return c

    async def update_collection(
        self,
        collection_id: str,
        user_id: str,
        **updates
    ) -> Optional[Dict[str, Any]]:
        """Update a collection."""
        # Check access first
        collection = await self.get_collection_by_id(collection_id, user_id)
        if not collection:
            return None

        client = await get_async_supabase_admin()

        result = await client.table("collections").update(updates).eq("id", int(collection_id)).select().single().execute()

        return result.data

    async def delete_collection(self, collection_id: str, user_id: str) -> bool:
        """Delete a collection."""
        # Check access first
        collection = await self.get_collection_by_id(collection_id, user_id)
        if not collection:
            return False

        client = await get_async_supabase_admin()

        await client.table("collections").delete().eq("id", int(collection_id)).execute()

        return True

    async def add_video_to_collection(
        self,
        collection_id: str,
        video_aweme_id: str,
        user_id: str
    ) -> bool:
        """Add a video to a collection."""
        # Check access first
        collection = await self.get_collection_by_id(collection_id, user_id)
        if not collection:
            return False

        client = await get_async_supabase_admin()

        # Get video ID from aweme_id
        video = await client.table("douyin_videos").select("id").eq("aweme_id", video_aweme_id).execute()

        if not video.data:
            raise Exception("Video not found")

        video_id = video.data[0]["id"]

        # Add to collection
        try:
            await client.table("video_collections").insert({
                "collection_id": int(collection_id),
                "video_id": video_id,
                "added_by": user_id
            }).execute()
        except Exception as e:
            if "23505" in str(e):
                # Duplicate, already in collection - that's fine
                return True
            raise

        return True

    async def remove_video_from_collection(
        self,
        collection_id: str,
        video_aweme_id: str,
        user_id: str
    ) -> bool:
        """Remove a video from a collection."""
        # Check access first
        collection = await self.get_collection_by_id(collection_id, user_id)
        if not collection:
            return False

        client = await get_async_supabase_admin()

        # Get video ID from aweme_id
        video = await client.table("douyin_videos").select("id").eq("aweme_id", video_aweme_id).execute()

        if not video.data:
            raise Exception("Video not found")

        video_id = video.data[0]["id"]

        await client.table("video_collections").delete().eq("collection_id", int(collection_id)).eq("video_id", video_id).execute()

        return True

    async def get_video_collections(
        self,
        video_aweme_id: str,
        user_id: str
    ) -> List[str]:
        """Get all collection IDs a video belongs to."""
        client = await get_async_supabase_admin()

        # Get video ID from aweme_id
        video = await client.table("douyin_videos").select("id").eq("aweme_id", video_aweme_id).execute()

        if not video.data:
            return []

        video_id = video.data[0]["id"]

        # Get collection IDs
        result = await client.table("video_collections").select("collection_id").eq("video_id", video_id).execute()

        return [str(vc["collection_id"]) for vc in (result.data or [])]

    async def get_collection_video_aweme_ids(
        self,
        collection_id: str,
        user_id: str
    ) -> List[str]:
        """Get all video aweme_ids in a collection."""
        # Check access first
        collection = await self.get_collection_by_id(collection_id, user_id)
        if not collection:
            return []

        client = await get_async_supabase_admin()

        # Get video IDs in collection
        video_collections = await client.table("video_collections").select("video_id").eq("collection_id", int(collection_id)).execute()

        if not video_collections.data:
            return []

        video_ids = [vc["video_id"] for vc in video_collections.data]

        # Get aweme_ids from video IDs
        videos = await client.table("douyin_videos").select("aweme_id").in_("id", video_ids).execute()

        return [v["aweme_id"] for v in (videos.data or [])]

    async def get_multiple_collections_video_aweme_ids(
        self,
        collection_ids: List[str],
        user_id: str
    ) -> List[str]:
        """Get all unique video aweme_ids from multiple collections."""
        if not collection_ids:
            return []

        # Filter to only accessible collections
        accessible_ids = []
        for cid in collection_ids:
            collection = await self.get_collection_by_id(cid, user_id)
            if collection:
                accessible_ids.append(int(cid))

        if not accessible_ids:
            return []

        client = await get_async_supabase_admin()

        # Get video IDs in these collections
        video_collections = await client.table("video_collections").select("video_id").in_("collection_id", accessible_ids).execute()

        if not video_collections.data:
            return []

        video_ids = list(set(vc["video_id"] for vc in video_collections.data))

        # Get aweme_ids from video IDs
        videos = await client.table("douyin_videos").select("aweme_id").in_("id", video_ids).execute()

        return [v["aweme_id"] for v in (videos.data or [])]
