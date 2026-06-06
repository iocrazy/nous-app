"""Script Repository Layer — data access for script_projects and script_chapters.

ORM 2.0 migration (Batch L1b): the four ``Script*Repository`` classes are the
legacy supabase-py REST implementations; their ``Script*RepositoryOrm``
successors (in ``script_repository_orm.py``) run on SQLAlchemy 2.0. Call sites
go through the ``get_script_*_repository()`` factories (bottom of this file)
which pick the ORM subclass when ``USE_ORM_SCRIPTS`` is on AND the engine is
configured.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional, Union

from loguru import logger

from app.repositories.base_repository import BaseRepository

if TYPE_CHECKING:
    from app.repositories.script_repository_orm import (
        ScriptAssetRepositoryOrm,
        ScriptChapterRepositoryOrm,
        ScriptProjectRepositoryOrm,
        ScriptStoryboardLinkRepositoryOrm,
    )


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
            logger.info(f"Bulk-upserted {len(rows)} chapters for script {script_id}")
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
            logger.error(f"Failed to get chapters for script {script_id}: {e}")
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
            logger.error(f"Failed to list assets for script {script_id}: {e}")
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
                "Failed to list links for storyboard %s: %s", storyboard_project_id, e
            )
            return []


# ─── SQLAlchemy ORM migration factories (Batch L1b) ────────────────────
#
# Each factory returns the ORM subclass when USE_ORM_SCRIPTS is set AND the
# SQLAlchemy engine is configured; otherwise the legacy supabase-py REST path.
# A flag-on but engine-missing deploy logs once and falls back (never crashes).
# The four ORM subclasses are drop-ins (each IS-A its REST counterpart).


def _orm_scripts_enabled() -> bool:
    from app.core.config import settings

    if not settings.USE_ORM_SCRIPTS:
        return False
    from app.db.engine import is_configured

    if is_configured():
        return True
    logger.warning(
        "USE_ORM_SCRIPTS=true but SUPAVISOR_DATABASE_URL is empty "
        "— falling back to supabase-py path"
    )
    return False


def get_script_project_repository() -> (
    Union["ScriptProjectRepository", "ScriptProjectRepositoryOrm"]
):
    """Return the right ScriptProjectRepository per env (ORM or REST)."""
    if _orm_scripts_enabled():
        from app.repositories.script_repository_orm import ScriptProjectRepositoryOrm

        return ScriptProjectRepositoryOrm()
    return ScriptProjectRepository()


def get_script_chapter_repository() -> (
    Union["ScriptChapterRepository", "ScriptChapterRepositoryOrm"]
):
    """Return the right ScriptChapterRepository per env (ORM or REST)."""
    if _orm_scripts_enabled():
        from app.repositories.script_repository_orm import ScriptChapterRepositoryOrm

        return ScriptChapterRepositoryOrm()
    return ScriptChapterRepository()


def get_script_asset_repository() -> (
    Union["ScriptAssetRepository", "ScriptAssetRepositoryOrm"]
):
    """Return the right ScriptAssetRepository per env (ORM or REST)."""
    if _orm_scripts_enabled():
        from app.repositories.script_repository_orm import ScriptAssetRepositoryOrm

        return ScriptAssetRepositoryOrm()
    return ScriptAssetRepository()


def get_script_storyboard_link_repository() -> (
    Union["ScriptStoryboardLinkRepository", "ScriptStoryboardLinkRepositoryOrm"]
):
    """Return the right ScriptStoryboardLinkRepository per env (ORM or REST)."""
    if _orm_scripts_enabled():
        from app.repositories.script_repository_orm import (
            ScriptStoryboardLinkRepositoryOrm,
        )

        return ScriptStoryboardLinkRepositoryOrm()
    return ScriptStoryboardLinkRepository()
