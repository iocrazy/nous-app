"""Script Repository Layer — data access for script_projects and script_chapters."""

from typing import Any, Dict, List, Optional

from loguru import logger

from app.repositories.base_repository import BaseRepository


class ScriptProjectRepository(BaseRepository):
    """CRUD + list operations for script_projects."""

    TABLE_NAME = "script_projects"

    async def list_by_project(
        self,
        project_id: int,
        page: int = 1,
        limit: int = 20,
        search: Optional[str] = None,
    ) -> Dict[str, Any]:
        try:
            client = await self._get_client()
            offset = (page - 1) * limit

            count_query = (
                client.table(self.TABLE_NAME)
                .select("id", count="exact")
                .eq("project_id", project_id)
                .neq("status", "deleted")
            )
            if search:
                escaped = search.replace("%", r"\%").replace("_", r"\_")
                count_query = count_query.ilike("name", f"%{escaped}%")
            count_result = await count_query.execute()
            total = count_result.count or 0

            data_query = (
                client.table(self.TABLE_NAME)
                .select("*")
                .eq("project_id", project_id)
                .neq("status", "deleted")
                .order("updated_at", desc=True)
                .range(offset, offset + limit - 1)
            )
            if search:
                escaped = search.replace("%", r"\%").replace("_", r"\_")
                data_query = data_query.ilike("name", f"%{escaped}%")
            data_result = await data_query.execute()

            return {
                "items": data_result.data or [],
                "total": total,
                "page": page,
                "limit": limit,
            }
        except Exception as e:
            logger.error(
                "Failed to list script projects for project %s: %s", project_id, e
            )
            return {"items": [], "total": 0, "page": page, "limit": limit}


class ScriptChapterRepository(BaseRepository):
    """CRUD for script_chapters."""

    TABLE_NAME = "script_chapters"

    async def bulk_upsert(
        self, script_id: str, chapters: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        if not chapters:
            return []
        try:
            client = await self._get_client()
            rows = [{**ch, "script_id": script_id} for ch in chapters]
            result = (
                await client.table(self.TABLE_NAME)
                .upsert(rows, on_conflict="id")
                .execute()
            )
            logger.info("Bulk-upserted %d chapters for script %s", len(rows), script_id)
            return result.data or []
        except Exception as e:
            logger.error(
                "Failed to bulk-upsert chapters for script %s: %s", script_id, e
            )
            raise

    async def delete(self, chapter_id: str) -> None:
        """Hard-delete a chapter by ID."""
        await self.hard_delete(chapter_id)

    async def get_by_script(self, script_id: str) -> List[Dict[str, Any]]:
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_NAME)
                .select("*")
                .eq("script_id", script_id)
                .order("sort_order")
                .execute()
            )
            return result.data or []
        except Exception as e:
            logger.error("Failed to get chapters for script %s: %s", script_id, e)
            return []


class ScriptAssetRepository(BaseRepository):
    """CRUD for script_assets."""

    TABLE_NAME = "script_assets"

    async def delete(self, asset_id: str) -> None:
        """Hard-delete an asset by ID."""
        await self.hard_delete(asset_id)

    async def list_by_script(
        self, script_id: str, asset_type: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        try:
            client = await self._get_client()
            query = (
                client.table(self.TABLE_NAME)
                .select("*")
                .eq("script_id", script_id)
                .order("sort_order")
            )
            if asset_type:
                query = query.eq("asset_type", asset_type)
            result = await query.execute()
            return result.data or []
        except Exception as e:
            logger.error("Failed to list assets for script %s: %s", script_id, e)
            return []


class ScriptStoryboardLinkRepository(BaseRepository):
    """CRUD for script_storyboard_links."""

    TABLE_NAME = "script_storyboard_links"

    async def delete(self, link_id: str) -> None:
        """Hard-delete a link by ID."""
        await self.hard_delete(link_id)

    async def list_by_chapter(self, chapter_id: str) -> List[Dict[str, Any]]:
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_NAME)
                .select("*")
                .eq("chapter_id", chapter_id)
                .order("created_at")
                .execute()
            )
            return result.data or []
        except Exception as e:
            logger.error("Failed to list links for chapter %s: %s", chapter_id, e)
            return []

    async def list_by_storyboard(
        self, storyboard_project_id: str
    ) -> List[Dict[str, Any]]:
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_NAME)
                .select("*")
                .eq("storyboard_project_id", storyboard_project_id)
                .order("created_at")
                .execute()
            )
            return result.data or []
        except Exception as e:
            logger.error(
                "Failed to list links for storyboard %s: %s", storyboard_project_id, e
            )
            return []
