"""Cleanup suggestions service."""
from typing import Optional, List, Tuple
from datetime import datetime, timedelta
from dataclasses import dataclass

from loguru import logger

from app.db.supabase_client import get_supabase_admin
from app.repositories.analysis_repository import AnalysisRepository


@dataclass
class CleanupSuggestion:
    """A cleanup suggestion."""
    video_id: int
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
    """Service for generating and managing cleanup suggestions."""

    def __init__(self):
        self.supabase = get_supabase_admin()

    async def get_suggestions(
        self,
        user_id: str,
        limit: int = 50,
        include_duplicates: bool = True
    ) -> Tuple[List[CleanupSuggestion], dict]:
        """
        Get cleanup suggestions for a user.

        Returns tuple of (suggestions, category_counts).
        """
        suggestions = []
        category_counts = {
            "never_viewed": 0,
            "old_unused": 0,
            "duplicate_content": 0,
            "large_file": 0
        }

        # 1. Videos never viewed (downloaded > 7 days ago, view_count = 0)
        never_viewed = await self._get_never_viewed_videos(user_id)
        for video in never_viewed:
            suggestions.append(CleanupSuggestion(
                video_id=video["id"],
                title=video.get("title", ""),
                cover_url=video.get("cover_url"),
                author=video.get("author"),
                reason="never_viewed",
                reason_detail="Downloaded over 7 days ago but never viewed",
                storage_size=video.get("storage_size", 0) or 0,
                created_at=video["created_at"],
                last_viewed_at=None,
                view_count=0
            ))
            category_counts["never_viewed"] += 1

        # 2. Old unused videos (not viewed in 30 days, view_count > 0)
        old_unused = await self._get_old_unused_videos(user_id)
        for video in old_unused:
            suggestions.append(CleanupSuggestion(
                video_id=video["id"],
                title=video.get("title", ""),
                cover_url=video.get("cover_url"),
                author=video.get("author"),
                reason="old_unused",
                reason_detail=f"Not viewed in over 30 days (last: {video.get('last_viewed_at', 'unknown')})",
                storage_size=video.get("storage_size", 0) or 0,
                created_at=video["created_at"],
                last_viewed_at=video.get("last_viewed_at"),
                view_count=video.get("view_count", 0)
            ))
            category_counts["old_unused"] += 1

        # 3. Large files (top 10% by size)
        large_files = await self._get_large_files(user_id)
        existing_ids = {s.video_id for s in suggestions}
        for video in large_files:
            if video["id"] not in existing_ids:
                suggestions.append(CleanupSuggestion(
                    video_id=video["id"],
                    title=video.get("title", ""),
                    cover_url=video.get("cover_url"),
                    author=video.get("author"),
                    reason="large_file",
                    reason_detail=f"Large file: {self._format_size(video.get('storage_size', 0))}",
                    storage_size=video.get("storage_size", 0) or 0,
                    created_at=video["created_at"],
                    last_viewed_at=video.get("last_viewed_at"),
                    view_count=video.get("view_count", 0)
                ))
                category_counts["large_file"] += 1

        # 4. Potential duplicates (similar content based on embeddings)
        if include_duplicates:
            duplicates = await self._find_potential_duplicates(user_id)
            existing_ids = {s.video_id for s in suggestions}
            for dup in duplicates:
                if dup["video_id"] not in existing_ids:
                    suggestions.append(CleanupSuggestion(
                        video_id=dup["video_id"],
                        title=dup.get("title", ""),
                        cover_url=dup.get("cover_url"),
                        author=dup.get("author"),
                        reason="duplicate_content",
                        reason_detail=f"Similar to another video ({dup['similarity_score']:.0%} match)",
                        storage_size=dup.get("storage_size", 0) or 0,
                        created_at=dup["created_at"],
                        last_viewed_at=dup.get("last_viewed_at"),
                        view_count=dup.get("view_count", 0),
                        similarity_to=dup.get("similar_to"),
                        similarity_score=dup.get("similarity_score")
                    ))
                    category_counts["duplicate_content"] += 1

        # Sort by storage size (largest first) and limit
        suggestions.sort(key=lambda x: x.storage_size, reverse=True)
        suggestions = suggestions[:limit]

        return suggestions, category_counts

    async def _get_never_viewed_videos(self, user_id: str) -> List[dict]:
        """Get videos never viewed, downloaded > 7 days ago."""
        cutoff = (datetime.utcnow() - timedelta(days=7)).isoformat()

        result = self.supabase.table("douyin_videos").select(
            "id, title, cover_url, author, storage_size, created_at"
        ).eq("user_id", user_id).eq("keep_forever", False).eq("view_count", 0).lt("created_at", cutoff).limit(50).execute()

        return result.data

    async def _get_old_unused_videos(self, user_id: str) -> List[dict]:
        """Get videos not viewed in 30 days."""
        cutoff = (datetime.utcnow() - timedelta(days=30)).isoformat()

        result = self.supabase.table("douyin_videos").select(
            "id, title, cover_url, author, storage_size, created_at, last_viewed_at, view_count"
        ).eq("user_id", user_id).eq("keep_forever", False).gt("view_count", 0).lt("last_viewed_at", cutoff).limit(50).execute()

        return result.data

    async def _get_large_files(self, user_id: str, top_percent: float = 0.1) -> List[dict]:
        """Get largest files (top 10% by size)."""
        # Get total count
        count_result = self.supabase.table("douyin_videos").select("id", count="exact").eq("user_id", user_id).execute()
        total = count_result.count or 0

        if total == 0:
            return []

        # Get top N by size
        top_n = max(int(total * top_percent), 5)

        result = self.supabase.table("douyin_videos").select(
            "id, title, cover_url, author, storage_size, created_at, last_viewed_at, view_count"
        ).eq("user_id", user_id).eq("keep_forever", False).not_.is_("storage_size", "null").order("storage_size", desc=True).limit(top_n).execute()

        return result.data

    async def _find_potential_duplicates(self, user_id: str, threshold: float = 0.85) -> List[dict]:
        """Find videos with similar content using embeddings."""
        duplicates = []

        # Get all videos with embeddings for this user
        videos_result = self.supabase.table("douyin_videos").select("id").eq("user_id", user_id).execute()
        user_video_ids = [v["id"] for v in videos_result.data]

        if len(user_video_ids) < 2:
            return []

        # Get embeddings
        analysis_result = self.supabase.table("video_analysis").select(
            "video_id, content_embedding"
        ).in_("video_id", user_video_ids).not_.is_("content_embedding", "null").execute()

        embeddings = {r["video_id"]: r["content_embedding"] for r in analysis_result.data}

        if len(embeddings) < 2:
            return []

        # Compare embeddings (simplified - in production use a more efficient algorithm)
        video_ids = list(embeddings.keys())
        compared = set()

        for i, vid1 in enumerate(video_ids[:50]):  # Limit comparisons
            for vid2 in video_ids[i+1:50]:
                pair = tuple(sorted([vid1, vid2]))
                if pair in compared:
                    continue
                compared.add(pair)

                similarity = self._calculate_similarity(embeddings[vid1], embeddings[vid2])

                if similarity >= threshold:
                    # Get video details
                    video = self.supabase.table("douyin_videos").select(
                        "id, title, cover_url, author, storage_size, created_at, last_viewed_at, view_count"
                    ).eq("id", vid2).maybe_single().execute()

                    if video.data:
                        duplicates.append({
                            **video.data,
                            "video_id": vid2,
                            "similar_to": vid1,
                            "similarity_score": similarity
                        })

        return duplicates[:20]  # Limit results

    def _calculate_similarity(self, emb1_str: str, emb2_str: str) -> float:
        """Calculate cosine similarity between two embedding strings."""
        try:
            emb1 = self._parse_embedding(emb1_str)
            emb2 = self._parse_embedding(emb2_str)

            if not emb1 or not emb2:
                return 0.0

            dot_product = sum(a * b for a, b in zip(emb1, emb2))
            norm1 = sum(a * a for a in emb1) ** 0.5
            norm2 = sum(b * b for b in emb2) ** 0.5

            if norm1 == 0 or norm2 == 0:
                return 0.0

            return dot_product / (norm1 * norm2)

        except Exception:
            return 0.0

    def _parse_embedding(self, embedding_str: str) -> Optional[List[float]]:
        """Parse embedding from PostgreSQL vector string format."""
        try:
            if isinstance(embedding_str, list):
                return embedding_str
            clean_str = embedding_str.strip("[]")
            return [float(x.strip()) for x in clean_str.split(",")]
        except Exception:
            return None

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
        """Get overall cleanup statistics."""
        # Total videos and storage
        total_result = self.supabase.table("douyin_videos").select(
            "id, storage_size, view_count, last_viewed_at, keep_forever"
        ).eq("user_id", user_id).execute()

        videos = total_result.data
        total_videos = len(videos)
        total_storage = sum(v.get("storage_size", 0) or 0 for v in videos)

        # Count categories
        never_viewed = sum(1 for v in videos if v.get("view_count", 0) == 0)
        keep_forever = sum(1 for v in videos if v.get("keep_forever", False))

        cutoff_30 = (datetime.utcnow() - timedelta(days=30)).isoformat()
        not_viewed_30 = sum(
            1 for v in videos
            if v.get("last_viewed_at") and v["last_viewed_at"] < cutoff_30
        )

        # Estimate reclaimable (never viewed + not viewed 30 days, excluding keep_forever)
        reclaimable = sum(
            v.get("storage_size", 0) or 0
            for v in videos
            if not v.get("keep_forever") and (
                v.get("view_count", 0) == 0 or
                (v.get("last_viewed_at") and v["last_viewed_at"] < cutoff_30)
            )
        )

        return {
            "total_videos": total_videos,
            "total_storage_bytes": total_storage,
            "videos_never_viewed": never_viewed,
            "videos_not_viewed_30_days": not_viewed_30,
            "potential_duplicates": 0,  # Would need separate calculation
            "videos_marked_keep": keep_forever,
            "reclaimable_bytes": reclaimable
        }

    async def mark_keep_forever(self, video_id: int, user_id: str) -> bool:
        """Mark a video to keep forever (exclude from suggestions)."""
        result = self.supabase.table("douyin_videos").update({
            "keep_forever": True
        }).eq("id", video_id).eq("user_id", user_id).execute()

        return len(result.data) > 0

    async def unmark_keep_forever(self, video_id: int, user_id: str) -> bool:
        """Remove keep forever mark from a video."""
        result = self.supabase.table("douyin_videos").update({
            "keep_forever": False
        }).eq("id", video_id).eq("user_id", user_id).execute()

        return len(result.data) > 0
