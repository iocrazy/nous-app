"""Script Repository Layer — data access for script_projects and script_chapters."""

from typing import Any, Dict, List, Optional

from loguru import logger

from app.db.supabase_client import get_async_supabase_admin


class ScriptProjectRepository:
    """CRUD + list operations for script_projects."""

    TABLE_NAME = "script_projects"

    async def _get_client(self):
        return await get_async_supabase_admin()

    async def create(self, data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            client = await self._get_client()
            result = await client.table(self.TABLE_NAME).insert(data).execute()
            logger.info(f"Created script project: {data.get('name')}")
            return result.data[0] if result.data else {}
        except Exception as e:
            logger.error(f"Failed to create script project: {e}")
            raise

    async def update(self, script_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_NAME)
                .update(data)
                .eq("id", script_id)
                .execute()
            )
            return result.data[0] if result.data else {}
        except Exception as e:
            logger.error(f"Failed to update script project {script_id}: {e}")
            raise

    async def get_by_id(self, script_id: str) -> Optional[Dict[str, Any]]:
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_NAME)
                .select("*")
                .eq("id", script_id)
                .execute()
            )
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error(f"Failed to get script project {script_id}: {e}")
            return None

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
                count_query = count_query.ilike("name", f"%{search}%")
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
                data_query = data_query.ilike("name", f"%{search}%")
            data_result = await data_query.execute()

            return {
                "items": data_result.data or [],
                "total": total,
                "page": page,
                "limit": limit,
            }
        except Exception as e:
            logger.error(f"Failed to list script projects for project {project_id}: {e}")
            return {"items": [], "total": 0, "page": page, "limit": limit}

    async def soft_delete(self, script_id: str) -> None:
        try:
            client = await self._get_client()
            await (
                client.table(self.TABLE_NAME)
                .update({"status": "deleted"})
                .eq("id", script_id)
                .execute()
            )
            logger.info(f"Soft-deleted script project {script_id}")
        except Exception as e:
            logger.error(f"Failed to soft-delete script project {script_id}: {e}")
            raise


class ScriptChapterRepository:
    """CRUD for script_chapters."""

    TABLE_NAME = "script_chapters"

    async def _get_client(self):
        return await get_async_supabase_admin()

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
            logger.info(f"Bulk-upserted {len(rows)} chapters for script {script_id}")
            return result.data or []
        except Exception as e:
            logger.error(f"Failed to bulk-upsert chapters for script {script_id}: {e}")
            raise

    async def create(self, data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            client = await self._get_client()
            result = await client.table(self.TABLE_NAME).insert(data).execute()
            return result.data[0] if result.data else {}
        except Exception as e:
            logger.error(f"Failed to create chapter: {e}")
            raise

    async def update(self, chapter_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_NAME)
                .update(data)
                .eq("id", chapter_id)
                .execute()
            )
            return result.data[0] if result.data else {}
        except Exception as e:
            logger.error(f"Failed to update chapter {chapter_id}: {e}")
            raise

    async def delete(self, chapter_id: str) -> None:
        try:
            client = await self._get_client()
            await (
                client.table(self.TABLE_NAME)
                .delete()
                .eq("id", chapter_id)
                .execute()
            )
            logger.info(f"Deleted chapter {chapter_id}")
        except Exception as e:
            logger.error(f"Failed to delete chapter {chapter_id}: {e}")
            raise

    async def get_by_id(self, chapter_id: str) -> Optional[Dict[str, Any]]:
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_NAME)
                .select("*")
                .eq("id", chapter_id)
                .execute()
            )
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error(f"Failed to get chapter {chapter_id}: {e}")
            return None

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
            logger.error(f"Failed to get chapters for script {script_id}: {e}")
            return []


class ScriptAssetRepository:
    """CRUD for script_assets."""

    TABLE_NAME = "script_assets"

    async def _get_client(self):
        return await get_async_supabase_admin()

    async def create(self, data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            client = await self._get_client()
            result = await client.table(self.TABLE_NAME).insert(data).execute()
            return result.data[0] if result.data else {}
        except Exception as e:
            logger.error(f"Failed to create script asset: {e}")
            raise

    async def update(self, asset_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_NAME)
                .update(data)
                .eq("id", asset_id)
                .execute()
            )
            return result.data[0] if result.data else {}
        except Exception as e:
            logger.error(f"Failed to update script asset {asset_id}: {e}")
            raise

    async def delete(self, asset_id: str) -> None:
        try:
            client = await self._get_client()
            await client.table(self.TABLE_NAME).delete().eq("id", asset_id).execute()
        except Exception as e:
            logger.error(f"Failed to delete script asset {asset_id}: {e}")
            raise

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
            logger.error(f"Failed to list assets for script {script_id}: {e}")
            return []

    async def get_by_id(self, asset_id: str) -> Optional[Dict[str, Any]]:
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_NAME)
                .select("*")
                .eq("id", asset_id)
                .execute()
            )
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error(f"Failed to get script asset {asset_id}: {e}")
            return None


class ScriptStoryboardLinkRepository:
    """CRUD for script_storyboard_links."""

    TABLE_NAME = "script_storyboard_links"

    async def _get_client(self):
        return await get_async_supabase_admin()

    async def create(self, data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            client = await self._get_client()
            result = await client.table(self.TABLE_NAME).insert(data).execute()
            return result.data[0] if result.data else {}
        except Exception as e:
            logger.error(f"Failed to create script-storyboard link: {e}")
            raise

    async def delete(self, link_id: str) -> None:
        try:
            client = await self._get_client()
            await client.table(self.TABLE_NAME).delete().eq("id", link_id).execute()
        except Exception as e:
            logger.error(f"Failed to delete script-storyboard link {link_id}: {e}")
            raise

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
            logger.error(f"Failed to list links for chapter {chapter_id}: {e}")
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
                f"Failed to list links for storyboard {storyboard_project_id}: {e}"
            )
            return []
