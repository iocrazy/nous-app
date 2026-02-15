# app/repositories/libraries_repository.py

"""
Libraries Repository

Data access layer for team libraries. Uses async Supabase admin client.
"""

from typing import Any, Dict, List, Optional

from loguru import logger

from app.db.supabase_client import get_async_supabase_admin


class LibrariesRepository:
    """Library data access (async)"""

    TABLE = "libraries"

    def __init__(self):
        self._client = None

    async def _get_client(self):
        if self._client is None:
            self._client = await get_async_supabase_admin()
        return self._client

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

    async def update(
        self, library_id: str, data: Dict[str, Any]
    ) -> Dict[str, Any]:
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
