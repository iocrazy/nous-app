# app/repositories/review_repository.py

"""
Review system data access layer.

Handles CRUD for review_comments, review_annotations, and review_status tables.
"""

from typing import Any, Dict, List, Optional

from app.db.supabase_client import get_async_supabase_admin


class ReviewRepository:
    """Data access for review comments, annotations, and status."""

    async def _get_client(self):
        return await get_async_supabase_admin()

    # ─── Comments ───────────────────────────────────────

    async def create_comment(self, data: Dict[str, Any]) -> Dict[str, Any]:
        client = await self._get_client()
        result = await client.table("review_comments").insert(data).execute()
        return result.data[0]

    async def get_comments_by_resource(
        self,
        resource_id: str,
        version_id: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        client = await self._get_client()
        query = (
            client.table("review_comments")
            .select("*")
            .eq("resource_id", resource_id)
            .is_("parent_id", "null")
            .order("created_at", desc=False)
        )
        if version_id:
            query = query.eq("version_id", version_id)
        if status:
            query = query.eq("status", status)
        result = await query.execute()
        return result.data or []

    async def get_comment_by_id(self, comment_id: str) -> Optional[Dict[str, Any]]:
        client = await self._get_client()
        result = (
            await client.table("review_comments")
            .select("*")
            .eq("id", comment_id)
            .limit(1)
            .execute()
        )
        return result.data[0] if result.data else None

    async def get_replies(self, parent_id: str) -> List[Dict[str, Any]]:
        client = await self._get_client()
        result = (
            await client.table("review_comments")
            .select("*")
            .eq("parent_id", parent_id)
            .order("created_at", desc=False)
            .execute()
        )
        return result.data or []

    async def update_comment(
        self, comment_id: str, data: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        client = await self._get_client()
        data["updated_at"] = "now()"
        result = (
            await client.table("review_comments")
            .update(data)
            .eq("id", comment_id)
            .execute()
        )
        return result.data[0] if result.data else None

    async def delete_comment(self, comment_id: str) -> bool:
        client = await self._get_client()
        result = (
            await client.table("review_comments")
            .delete()
            .eq("id", comment_id)
            .execute()
        )
        return bool(result.data)

    async def get_comment_count(
        self, resource_id: str, version_id: Optional[str] = None
    ) -> int:
        client = await self._get_client()
        query = (
            client.table("review_comments")
            .select("id", count="exact")
            .eq("resource_id", resource_id)
            .is_("parent_id", "null")
        )
        if version_id:
            query = query.eq("version_id", version_id)
        result = await query.execute()
        return result.count or 0

    # ─── Annotations ────────────────────────────────────

    async def create_annotation(self, data: Dict[str, Any]) -> Dict[str, Any]:
        client = await self._get_client()
        result = await client.table("review_annotations").insert(data).execute()
        return result.data[0]

    async def create_annotations_batch(
        self, annotations: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        if not annotations:
            return []
        client = await self._get_client()
        result = await client.table("review_annotations").insert(annotations).execute()
        return result.data or []

    async def get_annotations_by_comment(self, comment_id: str) -> List[Dict[str, Any]]:
        client = await self._get_client()
        result = (
            await client.table("review_annotations")
            .select("*")
            .eq("comment_id", comment_id)
            .order("created_at", desc=False)
            .execute()
        )
        return result.data or []

    async def delete_annotations_by_comment(self, comment_id: str) -> bool:
        client = await self._get_client()
        await (
            client.table("review_annotations")
            .delete()
            .eq("comment_id", comment_id)
            .execute()
        )
        return True

    # ─── Review Status ──────────────────────────────────

    async def upsert_review_status(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Create or update review status for a resource+version+reviewer combo."""
        client = await self._get_client()
        # Check if existing
        query = (
            client.table("review_status")
            .select("*")
            .eq("resource_id", data["resource_id"])
            .eq("reviewer_id", data["reviewer_id"])
        )
        if data.get("version_id"):
            query = query.eq("version_id", data["version_id"])
        else:
            query = query.is_("version_id", "null")
        existing = await query.limit(1).execute()

        if existing.data:
            update = {
                "status": data["status"],
                "comment": data.get("comment"),
                "updated_at": "now()",
            }
            result = (
                await client.table("review_status")
                .update(update)
                .eq("id", existing.data[0]["id"])
                .execute()
            )
            return result.data[0]
        else:
            result = await client.table("review_status").insert(data).execute()
            return result.data[0]

    async def get_review_statuses(
        self, resource_id: str, version_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        client = await self._get_client()
        query = (
            client.table("review_status")
            .select("*")
            .eq("resource_id", resource_id)
            .order("updated_at", desc=True)
        )
        if version_id:
            query = query.eq("version_id", version_id)
        result = await query.execute()
        return result.data or []

    async def get_review_status_by_reviewer(
        self, resource_id: str, reviewer_id: str, version_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        client = await self._get_client()
        query = (
            client.table("review_status")
            .select("*")
            .eq("resource_id", resource_id)
            .eq("reviewer_id", reviewer_id)
        )
        if version_id:
            query = query.eq("version_id", version_id)
        else:
            query = query.is_("version_id", "null")
        result = await query.limit(1).execute()
        return result.data[0] if result.data else None
