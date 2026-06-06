# app/repositories/libraries_repository.py

"""
Libraries Repository

Data access layer for team libraries. Uses async Supabase admin client.

ORM 2.0 migration (Batch L1): ``LibrariesRepository`` is the legacy supabase-py
REST implementation; ``LibrariesRepositoryOrm`` (in
``libraries_repository_orm.py``) is the SQLAlchemy 2.0 ORM successor. Call sites
go through ``get_libraries_repository()`` (bottom of this file) which picks the
ORM subclass when ``USE_ORM_LIBRARIES`` is on AND the engine is configured.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional, Union

from loguru import logger

from app.db.supabase_client import get_async_supabase_admin

if TYPE_CHECKING:
    from app.repositories.libraries_repository_orm import LibrariesRepositoryOrm


class LibrariesRepository:
    """Library data access (async)"""

    TABLE = "libraries"

    def __init__(self):
        pass

    async def _get_client(self):
        """Get async client (loop-aware, safe for Celery workers)."""
        return await get_async_supabase_admin()

    # ------------------------------------------------------------------ #
    # CRUD
    # ------------------------------------------------------------------ #

    async def create(self, data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            client = await self._get_client()
            result = await client.table(self.TABLE).insert(data).execute()
            logger.info(f"Created library: {data.get('name')}")
            return result.data[0] if result.data else {}
        except Exception as e:
            logger.error(f"Failed to create library: {e}")
            raise

    async def get_by_id(self, library_id: str) -> Optional[Dict[str, Any]]:
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE)
                .select("*")
                .eq("id", library_id)
                .execute()
            )
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error(f"Failed to get library {library_id}: {e}")
            raise

    async def list_by_scope(
        self, scope_type: str, scope_id: str
    ) -> List[Dict[str, Any]]:
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE)
                .select("*")
                .eq("scope_type", scope_type)
                .eq("scope_id", scope_id)
                .order("sort_order", desc=False)
                .order("created_at", desc=False)
                .execute()
            )
            return result.data or []
        except Exception as e:
            logger.error(f"Failed to list libraries: {e}")
            raise

    async def update(self, library_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE)
                .update(data)
                .eq("id", library_id)
                .execute()
            )
            return result.data[0] if result.data else {}
        except Exception as e:
            logger.error(f"Failed to update library {library_id}: {e}")
            raise

    async def delete(self, library_id: str) -> bool:
        try:
            client = await self._get_client()
            await client.table(self.TABLE).delete().eq("id", library_id).execute()
            logger.info(f"Deleted library: {library_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete library {library_id}: {e}")
            raise


def get_libraries_repository() -> (
    Union["LibrariesRepository", "LibrariesRepositoryOrm"]
):
    """Return the right LibrariesRepository implementation per env.

    ORM when ``USE_ORM_LIBRARIES`` is set AND the SQLAlchemy engine is
    configured; otherwise the legacy supabase-py REST path. A flag-on but
    engine-missing deploy logs once and falls back to REST (never crashes).
    """
    from app.core.config import settings

    if settings.USE_ORM_LIBRARIES:
        from app.db.engine import is_configured

        if is_configured():
            from app.repositories.libraries_repository_orm import (
                LibrariesRepositoryOrm,
            )

            return LibrariesRepositoryOrm()
        logger.warning(
            "USE_ORM_LIBRARIES=true but SUPAVISOR_DATABASE_URL is empty "
            "— falling back to supabase-py path"
        )
    return LibrariesRepository()
