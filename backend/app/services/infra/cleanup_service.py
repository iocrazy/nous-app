"""Cleanup suggestions service - Optimized version using PostgreSQL RPC functions (异步)."""

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

from loguru import logger
from sqlalchemy import select, text

from app.db.session import read_scope, write_scope
from app.repositories._orm_helpers import _plain


def _serialize_row(row: Any) -> Dict[str, Any]:
    """Row mapping → JSON-safe dict (enum→str, timestamptz→ISO str, uuid→str),
    matching the PostgREST shapes cleanup consumers string-compare against
    (e.g. ``last_viewed_at < cutoff_iso``) and store on CleanupSuggestion."""
    out: Dict[str, Any] = {}
    for key, value in row.items():
        value = _plain(value)
        if hasattr(value, "isoformat"):
            out[key] = value.isoformat()
        elif isinstance(value, UUID):
            out[key] = str(value)
        else:
            out[key] = value
    return out


@dataclass
class CleanupSuggestion:
    """A cleanup suggestion."""

    media_id: int
    title: str
    cover_url: Optional[str]
    author: Optional[str]
    reason: str
    reason_detail: str
    storage_size: int
    created_at: datetime
    last_viewed_at: Optional[datetime]
    view_count: int
    similarity_to: Optional[int] = None
    similarity_score: Optional[float] = None


class CleanupService:
    """Service for generating and managing cleanup suggestions (异步)."""

    def __init__(self):
        pass

    async def get_cleanup_data(
        self, user_id: str, limit: int = 50, include_duplicates: bool = True
    ) -> dict:
        """
        Get all cleanup data in a single RPC call for optimal performance.

        Returns dict with suggestions, stats, and categories.
        """
        try:
            # get_cleanup_data(...) RETURNS jsonb (scalar). Named-arg notation
            # matches the PostgREST .rpc() named call (others default).
            async with read_scope() as session:
                raw = (
                    await session.execute(
                        text(
                            "SELECT get_cleanup_data("
                            "p_user_id => :p_user_id, p_limit => :p_limit)"
                        ),
                        {"p_user_id": user_id, "p_limit": limit},
                    )
                ).scalar()
            # asyncpg hands raw jsonb back as text for an untyped text() query.
            data = json.loads(raw) if isinstance(raw, str) else (raw or {})

            suggestions = []
            for video in data.get("suggestions", []):
                suggestions.append(
                    CleanupSuggestion(
                        media_id=video["media_id"],
                        title=video.get("title", ""),
                        cover_url=(video.get("cover_urls") or [None])[0],
                        author=video.get("author"),
                        reason=video.get("reason", "unknown"),
                        reason_detail=video.get("reason_detail", ""),
                        storage_size=video.get("storage_size", 0) or 0,
                        created_at=video.get("created_at", datetime.utcnow()),
                        last_viewed_at=video.get("last_viewed_at"),
                        view_count=video.get("view_count", 0),
                    )
                )

            stats = data.get("stats", {})
            categories = data.get("categories", {})
            categories["duplicate_content"] = (
                0  # Will be populated separately if needed
            )

            # Include duplicates if requested (separate call since it uses pgvector)
            if include_duplicates:
                try:
                    duplicates = await self._get_duplicates_via_rpc(user_id)
                    existing_ids = {s.media_id for s in suggestions}
                    for dup in duplicates:
                        if dup["media_id"] not in existing_ids:
                            suggestions.append(
                                CleanupSuggestion(
                                    media_id=dup["media_id"],
                                    title=dup.get("title", ""),
                                    cover_url=(dup.get("cover_urls") or [None])[0],
                                    author=dup.get("author"),
                                    reason="duplicate_content",
                                    reason_detail=f"Similar to another video ({dup['similarity_score']:.0%} match)",
                                    storage_size=dup.get("storage_size", 0) or 0,
                                    created_at=dup.get("created_at", datetime.utcnow()),
                                    last_viewed_at=dup.get("last_viewed_at"),
                                    view_count=dup.get("view_count", 0),
                                    similarity_to=dup.get("similar_to"),
                                    similarity_score=dup.get("similarity_score"),
                                )
                            )
                            categories["duplicate_content"] = (
                                categories.get("duplicate_content", 0) + 1
                            )
                except Exception as e:
                    logger.warning(f"Duplicate detection failed: {e}")

            return {
                "suggestions": suggestions,
                "stats": stats,
                "categories": categories,
            }

        except Exception as e:
            logger.warning(f"Combined RPC failed, falling back: {e}")
            # Fallback to separate calls
            suggestions, categories = await self.get_suggestions(
                user_id, limit, include_duplicates
            )
            stats = await self.get_cleanup_stats(user_id)
            return {
                "suggestions": suggestions,
                "stats": stats,
                "categories": categories,
            }

    async def get_suggestions(
        self, user_id: str, limit: int = 50, include_duplicates: bool = True
    ) -> Tuple[List[CleanupSuggestion], dict]:
        """
        Get cleanup suggestions for a user.

        Optimized version using PostgreSQL RPC functions for better performance.
        Returns tuple of (suggestions, category_counts).
        """
        suggestions = []
        category_counts = {
            "never_viewed": 0,
            "old_unused": 0,
            "duplicate_content": 0,
            "large_file": 0,
        }

        try:
            # Run queries in parallel for better performance
            tasks = [self._get_suggestions_via_rpc(user_id, limit)]
            if include_duplicates:
                tasks.append(self._get_duplicates_via_rpc(user_id))

            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Process main suggestions
            main_suggestions = (
                results[0] if not isinstance(results[0], Exception) else []
            )
            if isinstance(results[0], Exception):
                logger.warning(
                    f"RPC get_cleanup_suggestions failed, falling back: {results[0]}"
                )
                main_suggestions = await self._get_suggestions_fallback(user_id, limit)

            for video in main_suggestions:
                reason = video.get("reason", "unknown")
                suggestions.append(
                    CleanupSuggestion(
                        media_id=video["media_id"],
                        title=video.get("title", ""),
                        cover_url=(video.get("cover_urls") or [None])[0],
                        author=video.get("author"),
                        reason=reason,
                        reason_detail=video.get("reason_detail", ""),
                        storage_size=video.get("storage_size", 0) or 0,
                        created_at=video.get("created_at", datetime.utcnow()),
                        last_viewed_at=video.get("last_viewed_at"),
                        view_count=video.get("view_count", 0),
                    )
                )
                if reason in category_counts:
                    category_counts[reason] += 1

            # Process duplicates if requested
            if include_duplicates and len(results) > 1:
                duplicates = results[1] if not isinstance(results[1], Exception) else []
                if isinstance(results[1], Exception):
                    logger.warning(f"RPC find_duplicate_videos failed: {results[1]}")
                    duplicates = []

                existing_ids = {s.media_id for s in suggestions}
                for dup in duplicates:
                    if dup["media_id"] not in existing_ids:
                        suggestions.append(
                            CleanupSuggestion(
                                media_id=dup["media_id"],
                                title=dup.get("title", ""),
                                cover_url=(dup.get("cover_urls") or [None])[0],
                                author=dup.get("author"),
                                reason="duplicate_content",
                                reason_detail=f"Similar to another video ({dup['similarity_score']:.0%} match)",
                                storage_size=dup.get("storage_size", 0) or 0,
                                created_at=dup.get("created_at", datetime.utcnow()),
                                last_viewed_at=dup.get("last_viewed_at"),
                                view_count=dup.get("view_count", 0),
                                similarity_to=dup.get("similar_to"),
                                similarity_score=dup.get("similarity_score"),
                            )
                        )
                        category_counts["duplicate_content"] += 1

        except Exception as e:
            logger.error(f"Error getting cleanup suggestions: {e}")
            # Fallback to old method if RPC fails
            return await self._get_suggestions_legacy(
                user_id, limit, include_duplicates
            )

        # Sort by storage size (largest first) and limit
        suggestions.sort(key=lambda x: x.storage_size, reverse=True)
        suggestions = suggestions[:limit]

        return suggestions, category_counts

    async def _get_suggestions_via_rpc(self, user_id: str, limit: int) -> List[dict]:
        """Get suggestions using PostgreSQL RPC function."""
        async with read_scope() as session:
            rows = (
                (
                    await session.execute(
                        text(
                            "SELECT * FROM get_cleanup_suggestions("
                            "p_user_id => :p_user_id, "
                            "p_never_viewed_days => :p_never_viewed_days, "
                            "p_old_unused_days => :p_old_unused_days, "
                            "p_limit => :p_limit)"
                        ),
                        {
                            "p_user_id": user_id,
                            "p_never_viewed_days": 7,
                            "p_old_unused_days": 30,
                            "p_limit": limit,
                        },
                    )
                )
                .mappings()
                .all()
            )
        return [_serialize_row(r) for r in rows]

    async def _get_duplicates_via_rpc(
        self, user_id: str, threshold: float = 0.85
    ) -> List[dict]:
        """Find duplicates using PostgreSQL RPC function with pgvector."""
        async with read_scope() as session:
            rows = (
                (
                    await session.execute(
                        text(
                            "SELECT * FROM find_duplicate_videos("
                            "p_user_id => :p_user_id, "
                            "similarity_threshold => :similarity_threshold, "
                            "max_results => :max_results)"
                        ),
                        {
                            "p_user_id": user_id,
                            "similarity_threshold": threshold,
                            "max_results": 20,
                        },
                    )
                )
                .mappings()
                .all()
            )
        return [_serialize_row(r) for r in rows]

    async def _get_suggestions_fallback(self, user_id: str, limit: int) -> List[dict]:
        """Fallback method using parallel direct queries if RPC not available."""
        cutoff_7 = (datetime.utcnow() - timedelta(days=7)).isoformat()
        cutoff_30 = (datetime.utcnow() - timedelta(days=30)).isoformat()

        # Run all queries in parallel
        never_viewed_task = self._query_never_viewed(user_id, cutoff_7)
        old_unused_task = self._query_old_unused(user_id, cutoff_30)
        large_files_task = self._query_large_files(user_id)

        results = await asyncio.gather(
            never_viewed_task, old_unused_task, large_files_task, return_exceptions=True
        )

        suggestions = []

        # Never viewed
        if not isinstance(results[0], Exception):
            for v in results[0]:
                suggestions.append(
                    {
                        **v,
                        "media_id": v["id"],
                        "reason": "never_viewed",
                        "reason_detail": "Downloaded over 7 days ago but never viewed",
                    }
                )

        # Old unused
        if not isinstance(results[1], Exception):
            for v in results[1]:
                suggestions.append(
                    {
                        **v,
                        "media_id": v["id"],
                        "reason": "old_unused",
                        "reason_detail": "Not viewed in over 30 days",
                    }
                )

        # Large files
        if not isinstance(results[2], Exception):
            existing_ids = {s["media_id"] for s in suggestions}
            for v in results[2]:
                if v["id"] not in existing_ids:
                    suggestions.append(
                        {
                            **v,
                            "media_id": v["id"],
                            "reason": "large_file",
                            "reason_detail": f"Large file: {self._format_size(v.get('storage_size', 0))}",
                        }
                    )

        return suggestions[:limit]

    async def _get_user_media_ids(self, user_id: str) -> list:
        """Get user's media IDs via resources table."""
        from app.models import Resources

        async with read_scope() as session:
            rows = (
                (
                    await session.execute(
                        select(Resources.media_id)
                        .where(Resources.creator_id == user_id)
                        .where(Resources.is_trashed.is_(False))
                    )
                )
                .scalars()
                .all()
            )
        return [m for m in rows if m is not None]

    async def _query_never_viewed(self, user_id: str, cutoff: str) -> List[dict]:
        """Query never viewed videos."""
        from app.models import ParsedMedia

        media_ids = await self._get_user_media_ids(user_id)
        if not media_ids:
            return []
        cutoff_dt = datetime.fromisoformat(cutoff)
        async with read_scope() as session:
            rows = (
                (
                    await session.execute(
                        select(
                            ParsedMedia.id,
                            ParsedMedia.title,
                            ParsedMedia.cover_urls,
                            ParsedMedia.author,
                            ParsedMedia.storage_size,
                            ParsedMedia.created_at,
                            ParsedMedia.view_count,
                        )
                        .where(ParsedMedia.id.in_(media_ids))
                        .where(ParsedMedia.keep_forever.is_(False))
                        .where(ParsedMedia.view_count == 0)
                        .where(ParsedMedia.created_at < cutoff_dt)
                        .limit(50)
                    )
                )
                .mappings()
                .all()
            )
        return [_serialize_row(r) for r in rows]

    async def _query_old_unused(self, user_id: str, cutoff: str) -> List[dict]:
        """Query old unused videos."""
        from app.models import ParsedMedia

        media_ids = await self._get_user_media_ids(user_id)
        if not media_ids:
            return []
        cutoff_dt = datetime.fromisoformat(cutoff)
        async with read_scope() as session:
            rows = (
                (
                    await session.execute(
                        select(
                            ParsedMedia.id,
                            ParsedMedia.title,
                            ParsedMedia.cover_urls,
                            ParsedMedia.author,
                            ParsedMedia.storage_size,
                            ParsedMedia.created_at,
                            ParsedMedia.last_viewed_at,
                            ParsedMedia.view_count,
                        )
                        .where(ParsedMedia.id.in_(media_ids))
                        .where(ParsedMedia.keep_forever.is_(False))
                        .where(ParsedMedia.view_count > 0)
                        .where(ParsedMedia.last_viewed_at < cutoff_dt)
                        .limit(50)
                    )
                )
                .mappings()
                .all()
            )
        return [_serialize_row(r) for r in rows]

    async def _query_large_files(self, user_id: str) -> List[dict]:
        """Query large files."""
        from app.models import ParsedMedia

        media_ids = await self._get_user_media_ids(user_id)
        if not media_ids:
            return []

        total = len(media_ids)

        # Get top N by size
        top_n = max(int(total * 0.1), 5)

        async with read_scope() as session:
            rows = (
                (
                    await session.execute(
                        select(
                            ParsedMedia.id,
                            ParsedMedia.title,
                            ParsedMedia.cover_urls,
                            ParsedMedia.author,
                            ParsedMedia.storage_size,
                            ParsedMedia.created_at,
                            ParsedMedia.last_viewed_at,
                            ParsedMedia.view_count,
                        )
                        .where(ParsedMedia.id.in_(media_ids))
                        .where(ParsedMedia.keep_forever.is_(False))
                        .where(ParsedMedia.storage_size.isnot(None))
                        .order_by(ParsedMedia.storage_size.desc())
                        .limit(top_n)
                    )
                )
                .mappings()
                .all()
            )
        return [_serialize_row(r) for r in rows]

    async def _get_suggestions_legacy(
        self, user_id: str, limit: int, include_duplicates: bool
    ) -> Tuple[List[CleanupSuggestion], dict]:
        """Legacy fallback method (slower, but works without RPC functions)."""
        logger.warning("Using legacy cleanup suggestions method")
        suggestions = []
        category_counts = {
            "never_viewed": 0,
            "old_unused": 0,
            "duplicate_content": 0,
            "large_file": 0,
        }

        # Get suggestions via fallback
        fallback_results = await self._get_suggestions_fallback(user_id, limit)

        for video in fallback_results:
            reason = video.get("reason", "unknown")
            suggestions.append(
                CleanupSuggestion(
                    media_id=video["media_id"],
                    title=video.get("title", ""),
                    cover_url=(video.get("cover_urls") or [None])[0],
                    author=video.get("author"),
                    reason=reason,
                    reason_detail=video.get("reason_detail", ""),
                    storage_size=video.get("storage_size", 0) or 0,
                    created_at=video.get("created_at", datetime.utcnow()),
                    last_viewed_at=video.get("last_viewed_at"),
                    view_count=video.get("view_count", 0),
                )
            )
            if reason in category_counts:
                category_counts[reason] += 1

        # Skip duplicates in legacy mode (too slow)
        if include_duplicates:
            logger.info("Skipping duplicate detection in legacy mode (too slow)")

        suggestions.sort(key=lambda x: x.storage_size, reverse=True)
        return suggestions[:limit], category_counts

    def _format_size(self, size_bytes: int) -> str:
        """Format bytes as human-readable string."""
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f} KB"
        elif size_bytes < 1024 * 1024 * 1024:
            return f"{size_bytes / (1024 * 1024):.1f} MB"
        else:
            return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"

    async def get_cleanup_stats(self, user_id: str) -> dict:
        """Get overall cleanup statistics using RPC for better performance."""
        try:
            # Try RPC first (TABLE-returning → one row of counts).
            async with read_scope() as session:
                rows = (
                    (
                        await session.execute(
                            text(
                                "SELECT * FROM get_cleanup_stats("
                                "p_user_id => :p_user_id)"
                            ),
                            {"p_user_id": user_id},
                        )
                    )
                    .mappings()
                    .all()
                )

            if rows:
                stats = rows[0]
                return {
                    "total_videos": stats.get("total_videos", 0),
                    "total_storage_bytes": stats.get("total_storage_bytes", 0),
                    "videos_never_viewed": stats.get("videos_never_viewed", 0),
                    "videos_not_viewed_30_days": stats.get(
                        "videos_not_viewed_30_days", 0
                    ),
                    "potential_duplicates": 0,
                    "videos_marked_keep": stats.get("videos_marked_keep", 0),
                    "reclaimable_bytes": stats.get("reclaimable_bytes", 0),
                }
        except Exception as e:
            logger.warning(f"RPC get_cleanup_stats failed, using fallback: {e}")

        # Fallback to direct query
        return await self._get_cleanup_stats_fallback(user_id)

    async def _get_cleanup_stats_fallback(self, user_id: str) -> dict:
        """Fallback stats calculation."""
        from app.models import ParsedMedia

        media_ids = await self._get_user_media_ids(user_id)
        if not media_ids:
            return {
                "total_videos": 0,
                "total_storage_bytes": 0,
                "videos_never_viewed": 0,
                "videos_not_viewed_30_days": 0,
                "potential_duplicates": 0,
                "videos_marked_keep": 0,
                "reclaimable_bytes": 0,
            }
        async with read_scope() as session:
            rows = (
                (
                    await session.execute(
                        select(
                            ParsedMedia.id,
                            ParsedMedia.storage_size,
                            ParsedMedia.view_count,
                            ParsedMedia.last_viewed_at,
                            ParsedMedia.keep_forever,
                        ).where(ParsedMedia.id.in_(media_ids))
                    )
                )
                .mappings()
                .all()
            )
        # last_viewed_at → ISO str so the ``< cutoff_30`` string compares below
        # stay byte-identical to the PostgREST path.
        videos = [_serialize_row(r) for r in rows]
        total_videos = len(videos)
        total_storage = sum(v.get("storage_size", 0) or 0 for v in videos)

        never_viewed = sum(1 for v in videos if v.get("view_count", 0) == 0)
        keep_forever = sum(1 for v in videos if v.get("keep_forever", False))

        cutoff_30 = (datetime.utcnow() - timedelta(days=30)).isoformat()
        not_viewed_30 = sum(
            1
            for v in videos
            if v.get("last_viewed_at") and v["last_viewed_at"] < cutoff_30
        )

        reclaimable = sum(
            v.get("storage_size", 0) or 0
            for v in videos
            if not v.get("keep_forever")
            and (
                v.get("view_count", 0) == 0
                or (v.get("last_viewed_at") and v["last_viewed_at"] < cutoff_30)
            )
        )

        return {
            "total_videos": total_videos,
            "total_storage_bytes": total_storage,
            "videos_never_viewed": never_viewed,
            "videos_not_viewed_30_days": not_viewed_30,
            "potential_duplicates": 0,
            "videos_marked_keep": keep_forever,
            "reclaimable_bytes": reclaimable,
        }

    async def mark_keep_forever(self, media_id: int, user_id: str) -> bool:
        """Mark a media item to keep forever (exclude from suggestions)."""
        from sqlalchemy import update as sa_update

        from app.models import ParsedMedia, Resources

        # Verify ownership via resources table
        async with read_scope() as session:
            owned = (
                await session.execute(
                    select(Resources.id)
                    .where(Resources.media_id == int(media_id))
                    .where(Resources.creator_id == user_id)
                    .limit(1)
                )
            ).first()
        if owned is None:
            return False
        async with write_scope() as session:
            updated = (
                await session.execute(
                    sa_update(ParsedMedia)
                    .where(ParsedMedia.id == int(media_id))
                    .values(keep_forever=True)
                    .returning(ParsedMedia.id)
                )
            ).all()
        return len(updated) > 0

    async def unmark_keep_forever(self, media_id: int, user_id: str) -> bool:
        """Remove keep forever mark from a media item."""
        from sqlalchemy import update as sa_update

        from app.models import ParsedMedia, Resources

        # Verify ownership via resources table
        async with read_scope() as session:
            owned = (
                await session.execute(
                    select(Resources.id)
                    .where(Resources.media_id == int(media_id))
                    .where(Resources.creator_id == user_id)
                    .limit(1)
                )
            ).first()
        if owned is None:
            return False
        async with write_scope() as session:
            updated = (
                await session.execute(
                    sa_update(ParsedMedia)
                    .where(ParsedMedia.id == int(media_id))
                    .values(keep_forever=False)
                    .returning(ParsedMedia.id)
                )
            ).all()
        return len(updated) > 0
