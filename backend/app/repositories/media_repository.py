# app/repositories/media_repository.py

"""
Media Repository

Parsed media data access layer based on Supabase, providing CRUD operations and query functions.
Uses async Supabase client.
"""

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from loguru import logger

from app.core.enums import DownloadStatus
from app.db.supabase_client import get_async_supabase_admin


class MediaRepository:
    """Media Repository (async)"""

    TABLE_NAME = "parsed_media"

    # Card-view projection. Everything the library grid / feed / search cards
    # need, MINUS the heavyweight text blobs (``ai_extract_text`` /
    # ``ai_rewrite_text`` / ``ai_analyze_text`` / ``ai_generated_at``).
    # Detail endpoints (``get_by_platform_id`` / ``get_by_id``) still return
    # ``SELECT *`` so the PlayerPage sees the full record when the user
    # actually opens an item.
    #
    # CRITICAL: every column name here MUST exist on ``parsed_media``
    # verbatim. If a column is misspelled or belongs to another table
    # (e.g. AI statuses live on ``resources``, tags live on a join table),
    # PostgREST rejects the whole query with a 400 and the list / search
    # endpoints return empty. Verified against information_schema on
    # 2026-04-24.
    CARD_SELECT = (
        "id, platform_id, source_platform, "
        "title, author, description, "
        "original_url, "
        "cover_urls, dynamic_cover_url, cover_download_status, cover_download_path, "
        "like_count, comment_count, share_count, favorite_count, view_count, "
        "extract_audio_path, music_download_path, music_download_status, music_name, music_play_urls, "
        "video_download_status, video_download_urls, "
        "image_download_status, image_download_urls, image_download_path, "
        "hashtags, error_message, "
        "created_at, updated_at, published_at, last_viewed_at, "
        "media_type, media_format, duration, resolution, "
        "datasize, datasize_bytes, storage_size, keep_forever, "
        "hls_path, download_path, download_time, download_duration"
    )

    def __init__(self):
        pass

    async def _get_client(self):
        """Get async client (loop-aware, safe for Celery workers)."""
        return await get_async_supabase_admin()

    async def _get_table(self):
        """获取表引用"""
        client = await self._get_client()
        return client.table(self.TABLE_NAME)

    async def create(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        创建新的视频记录

        Args:
            data: 视频数据字典

        Returns:
            创建的记录
        """
        try:
            # 处理枚举类型
            if "video_download_status" in data and isinstance(
                data["video_download_status"], DownloadStatus
            ):
                data["video_download_status"] = data["video_download_status"].value
            if "music_download_status" in data and isinstance(
                data["music_download_status"], DownloadStatus
            ):
                data["music_download_status"] = data["music_download_status"].value
            if "cover_download_status" in data and isinstance(
                data["cover_download_status"], DownloadStatus
            ):
                data["cover_download_status"] = data["cover_download_status"].value
            if "image_download_status" in data and isinstance(
                data["image_download_status"], DownloadStatus
            ):
                data["image_download_status"] = data["image_download_status"].value

            # 处理 datetime
            if "published_at" in data and isinstance(data["published_at"], datetime):
                data["published_at"] = data["published_at"].isoformat()
            if "download_time" in data and isinstance(data["download_time"], datetime):
                data["download_time"] = data["download_time"].isoformat()

            table = await self._get_table()
            result = await table.insert(data).execute()
            logger.info(f"创建视频记录成功: {data.get('platform_id')}")
            return result.data[0] if result.data else {}
        except Exception as e:
            logger.error(f"创建视频记录失败: {e}")
            raise

    async def get_by_platform_id(self, platform_id: str) -> Optional[Dict[str, Any]]:
        """Get parsed_media record by platform_id (global, no user filtering).

        Args:
            platform_id: Unique media identifier from platform.

        Returns:
            Media record or None.

        Raises:
            Exception: DB connection or query errors (not swallowed).
        """
        client = await self._get_client()
        result = (
            await client.table("parsed_media")
            .select("*")
            .eq("platform_id", platform_id)
            .limit(1)
            .execute()
        )
        return result.data[0] if result.data else None

    async def get_by_id(self, media_id: str) -> Optional[Dict[str, Any]]:
        """Get a parsed_media record by its primary key (UUID).

        Args:
            media_id: The parsed_media UUID.

        Returns:
            Media record or None.

        Raises:
            Exception: DB connection or query errors (not swallowed).
        """
        client = await self._get_client()
        result = (
            await client.table(self.TABLE_NAME)
            .select("*")
            .eq("id", media_id)
            .limit(1)
            .execute()
        )
        return result.data[0] if result.data else None

    async def update(
        self, platform_id: str, data: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Update parsed_media record by platform_id (global).

        Args:
            platform_id: Unique media identifier from platform.
            data: Update data dict.

        Returns:
            Updated record or None.
        """
        try:
            # Handle enums
            for field in (
                "video_download_status",
                "music_download_status",
                "cover_download_status",
                "image_download_status",
            ):
                if field in data and isinstance(data[field], DownloadStatus):
                    data[field] = data[field].value

            if "published_at" in data and isinstance(data["published_at"], datetime):
                data["published_at"] = data["published_at"].isoformat()
            if "download_time" in data and isinstance(data["download_time"], datetime):
                data["download_time"] = data["download_time"].isoformat()

            data["updated_at"] = datetime.now().isoformat()

            table = await self._get_table()
            result = await table.update(data).eq("platform_id", platform_id).execute()
            logger.info(f"Updated parsed_media: {platform_id}")
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error(f"Failed to update parsed_media: {e}")
            raise

    async def mark_stale_downloads_failed(self, timeout_minutes: int = 30) -> int:
        """Mark downloads stuck in 'downloading' state as 'failed'.

        Scans both parsed_media (global file state) and resources
        (per-user state) for any download status that has been
        'downloading' for longer than timeout_minutes.

        Returns total number of status fields reset.
        """
        cutoff = (datetime.now() - timedelta(minutes=timeout_minutes)).isoformat()
        now = datetime.now().isoformat()
        client = await self._get_client()
        count = 0
        status_fields = (
            "video_download_status",
            "music_download_status",
            "cover_download_status",
            "image_download_status",
        )

        for field in status_fields:
            # Clean parsed_media (global physical file state)
            try:
                result = await (
                    client.table("parsed_media")
                    .update(
                        {
                            field: "failed",
                            "error_message": f"Download timed out (>{timeout_minutes}min)",
                            "updated_at": now,
                        }
                    )
                    .eq(field, "downloading")
                    .lt("updated_at", cutoff)
                    .execute()
                )
                count += len(result.data) if result.data else 0
            except Exception as e:
                logger.debug(f"Stale cleanup parsed_media.{field}: {e}")

            # Clean resources (per-user download state)
            try:
                result = await (
                    client.table("resources")
                    .update(
                        {
                            field: "failed",
                            "updated_at": now,
                        }
                    )
                    .eq(field, "downloading")
                    .lt("updated_at", cutoff)
                    .execute()
                )
                count += len(result.data) if result.data else 0
            except Exception as e:
                logger.debug(f"Stale cleanup resources.{field}: {e}")

        if count > 0:
            logger.info(
                f"Marked {count} stale download(s) as failed (timeout={timeout_minutes}min)"
            )
        return count

    async def delete(self, platform_id: str) -> bool:
        """Delete parsed_media record by platform_id (global).

        Args:
            platform_id: Unique media identifier from platform.

        Returns:
            True if deleted successfully.
        """
        try:
            table = await self._get_table()
            await table.delete().eq("platform_id", platform_id).execute()
            logger.info(f"Deleted parsed_media: {platform_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete parsed_media: {e}")
            return False

    async def get_downloaded_by_platform_id(
        self, platform_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        Find a downloaded video by platform_id across all users (for dedup).
        Uses admin client to bypass RLS.

        Returns:
            Video record with download info, or None

        Raises:
            Exception: DB connection or query errors (not swallowed).
        """
        client = await self._get_client()
        result = (
            await client.table(self.TABLE_NAME)
            .select(
                "id, download_path, storage_size, cover_download_path, "
                "source_platform, platform_id"
            )
            .eq("platform_id", platform_id)
            .eq("video_download_status", DownloadStatus.COMPLETED.value)
            .not_.is_("download_path", "null")
            .limit(1)
            .execute()
        )
        return result.data[0] if result.data else None

    async def check_media_existence(self, platform_id: str) -> bool:
        """检查媒体是否存在"""
        result = await self.get_by_platform_id(platform_id)
        return result is not None

    async def check_media_downloaded(self, platform_id: str) -> bool:
        """检查媒体是否已下载"""
        result = await self.get_by_platform_id(platform_id)
        if result:
            return result.get("video_download_status") == DownloadStatus.COMPLETED.value
        return False

    async def check_music_downloaded(self, platform_id: str) -> bool:
        """检查音乐是否已下载"""
        result = await self.get_by_platform_id(platform_id)
        if result:
            return result.get("music_download_status") == DownloadStatus.COMPLETED.value
        return False

    async def check_cover_downloaded(self, platform_id: str) -> bool:
        """检查封面是否已下载"""
        result = await self.get_by_platform_id(platform_id)
        if result:
            return result.get("cover_download_status") == DownloadStatus.COMPLETED.value
        return False

    async def mark_media_as_downloaded(
        self,
        platform_id: str,
        download_path: str,
        duration: float,
        storage_size: int = 0,
    ) -> Optional[Dict[str, Any]]:
        """标记视频为已下载"""
        data = {
            "video_download_status": DownloadStatus.COMPLETED.value,
            "download_path": download_path,
            "download_duration": duration,
            "download_time": datetime.now().isoformat(),
        }
        if storage_size > 0:
            data["storage_size"] = storage_size
            data["datasize_bytes"] = storage_size
        return await self.update(platform_id, data)

    async def mark_music_as_downloaded(
        self, platform_id: str
    ) -> Optional[Dict[str, Any]]:
        """标记音乐为已下载"""
        return await self.update(
            platform_id, {"music_download_status": DownloadStatus.COMPLETED.value}
        )

    async def mark_images_as_downloaded(
        self, platform_id: str, download_path: str, duration: float
    ) -> Optional[Dict[str, Any]]:
        """Mark image carousel as downloaded (uses image_download_status)."""
        return await self.update(
            platform_id,
            {
                "image_download_status": DownloadStatus.COMPLETED.value,
                "image_download_path": download_path,
                "download_duration": duration,
                "download_time": datetime.now().isoformat(),
            },
        )

    async def get_music_data(self, platform_id: str) -> Dict[str, Any]:
        """
        获取音乐下载所需数据

        Args:
            platform_id: 视频唯一标识

        Returns:
            包含音乐URL和名称的字典

        Raises:
            ValueError: 如果找不到视频记录
        """
        result = await self.get_by_platform_id(platform_id)
        if not result:
            raise ValueError(f"找不到视频数据: {platform_id}")
        return {
            "id": result.get("id"),
            "source_platform": result.get("source_platform"),
            "music_name": result.get("music_name"),
        }

    async def mark_download_failed(
        self, platform_id: str, error_message: str, is_video: bool = True
    ) -> Optional[Dict[str, Any]]:
        """标记下载失败"""
        status_field = "video_download_status" if is_video else "music_download_status"
        return await self.update(
            platform_id,
            {status_field: DownloadStatus.FAILED.value, "error_message": error_message},
        )

    async def get_pending_downloads(
        self,
        status: DownloadStatus = DownloadStatus.PENDING,
        limit: int = 100,
        user_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """获取待下载的视频列表。parsed_media 是全局表，user_id 通过 resources 表过滤。"""
        try:
            table = await self._get_table()
            query = table.select("*").eq("video_download_status", status.value)
            if user_id:
                # Filter via resources table instead of dropped user_id column
                client = await self._get_client()
                res = (
                    await client.table("resources")
                    .select("media_id")
                    .eq("creator_id", user_id)
                    .eq("is_trashed", False)
                    .execute()
                )
                media_ids = [r["media_id"] for r in res.data if r.get("media_id")]
                if not media_ids:
                    return []
                query = query.in_("id", media_ids)
            result = await query.limit(limit).execute()
            return result.data or []
        except Exception as e:
            logger.error(f"获取待下载列表失败: {e}")
            return []

    async def get_all(
        self,
        skip: int = 0,
        limit: int = 100,
        order_by: str = "created_at",
        ascending: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Get all parsed_media records (global, no user filter).
        Use get_user_media_list() for per-user queries instead.

        Returns the CARD_SELECT projection — enough for list / grid / feed /
        search cards, minus heavy AI text fields. Use get_by_platform_id or
        get_by_id for the full record.
        """
        try:
            client = await self._get_client()
            query = client.table("parsed_media").select(self.CARD_SELECT)
            query = query.order(order_by, desc=not ascending)
            query = query.range(skip, skip + limit - 1)
            result = await query.execute()
            return result.data or []
        except Exception as e:
            logger.error(f"获取视频列表失败: {e}")
            return []

    async def get_user_media_list(
        self,
        user_id: str,
        skip: int = 0,
        limit: int = 100,
        order_by: str = "created_at",
        ascending: bool = False,
    ) -> List[Dict[str, Any]]:
        """Get parsed_media list for a user by joining through resources.

        Returns parsed_media rows with user's per-resource download statuses
        overlaid. Uses the CARD_SELECT projection on parsed_media — heavy AI
        text fields are fetched lazily by the detail endpoint.
        """
        try:
            client = await self._get_client()
            # Schema note: *_download_status columns moved off `resources`
            # onto `parsed_media` (single source of truth). The CARD_SELECT
            # projection on parsed_media already includes them, so we no
            # longer need to overlay per-user statuses from `resources`.
            resource_fields = "id, media_id, created_at"
            query = (
                client.table("resources")
                .select(f"{resource_fields}," f"parsed_media!inner({self.CARD_SELECT})")
                .eq("creator_id", user_id)
                .eq("source_type", "web")
                .eq("is_trashed", False)
                .order(order_by, desc=not ascending)
                .range(skip, skip + limit - 1)
            )
            result = await query.execute()

            videos = []
            for row in result.data or []:
                media = dict(row.get("parsed_media", {}))
                media["resource_id"] = row["id"]
                videos.append(media)
            return videos
        except Exception as e:
            logger.error(f"Failed to get user media list: {e}")
            return []

    async def search(
        self,
        user_id: str,
        keyword: Optional[str] = None,
        author: Optional[str] = None,
        status: Optional[DownloadStatus] = None,
        media_type: Optional[str] = None,
        category: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        skip: int = 0,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """
        Search user's media through resources table.

        Queries resources joined with parsed_media, filtered by creator_id.
        """
        try:
            client = await self._get_client()
            # See get_user_media_list for the schema rationale: download
            # statuses live on parsed_media now, not resources.
            resource_fields = "id, media_id, created_at"
            query = (
                client.table("resources")
                .select(f"{resource_fields}, parsed_media!inner(*)")
                .eq("creator_id", user_id)
                .eq("source_type", "web")
                .eq("is_trashed", False)
            )

            if keyword:
                query = query.or_(
                    f"parsed_media.title.ilike.%{keyword}%,parsed_media.description.ilike.%{keyword}%"
                )

            if author:
                query = query.ilike("parsed_media.author", f"%{author}%")

            if status:
                query = query.eq("parsed_media.video_download_status", status.value)

            if media_type:
                query = query.eq("parsed_media.media_type", media_type)

            if start_date:
                query = query.gte("parsed_media.published_at", start_date.isoformat())

            if end_date:
                query = query.lte("parsed_media.published_at", end_date.isoformat())

            query = query.order("created_at", desc=True).range(skip, skip + limit - 1)
            result = await query.execute()

            videos = []
            for row in result.data or []:
                media = dict(row.get("parsed_media", {}))
                media["resource_id"] = row["id"]
                videos.append(media)
            return videos
        except Exception as e:
            logger.error(f"搜索视频失败: {e}")
            return []

    async def get_statistics(self, user_id: str) -> Dict[str, Any]:
        """
        Get per-user statistics through the resources table.
        """
        try:
            client = await self._get_client()
            base = (
                client.table("resources")
                .select(
                    "parsed_media(video_download_status, datasize_bytes, author)",
                    count="exact",
                )
                .eq("creator_id", user_id)
                .eq("source_type", "web")
                .eq("is_trashed", False)
            )

            # Total count
            total_result = await base.execute()
            total = total_result.count or 0
            rows = total_result.data or []

            # Statuses now read from joined parsed_media — see schema note
            # in get_user_media_list().
            def _pm_status(row):
                pm = row.get("parsed_media") or {}
                return pm.get("video_download_status")

            pending = sum(
                1 for r in rows if _pm_status(r) == DownloadStatus.PENDING.value
            )
            completed = sum(
                1 for r in rows if _pm_status(r) == DownloadStatus.COMPLETED.value
            )
            failed = sum(
                1 for r in rows if _pm_status(r) == DownloadStatus.FAILED.value
            )

            # Calculate total storage bytes and unique authors from joined parsed_media
            total_storage_bytes = 0
            authors = set()
            for row in rows:
                pm = row.get("parsed_media") or {}
                total_storage_bytes += pm.get("datasize_bytes") or 0
                author = pm.get("author")
                if author:
                    authors.add(author)
            unique_authors = len(authors)

            return {
                "total": total,
                "pending": pending,
                "completed": completed,
                "failed": failed,
                "skipped": total - pending - completed - failed,
                "total_storage_bytes": total_storage_bytes,
                "unique_authors": unique_authors,
            }
        except Exception as e:
            logger.error(f"获取统计信息失败: {e}")
            return {
                "total": 0,
                "pending": 0,
                "completed": 0,
                "failed": 0,
                "skipped": 0,
                "total_storage_bytes": 0,
                "unique_authors": 0,
            }
