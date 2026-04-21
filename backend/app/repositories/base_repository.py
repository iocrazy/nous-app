"""Base repository with shared CRUD operations."""

from typing import Any, Dict, Optional

from loguru import logger

from app.db.supabase_client import get_async_supabase_admin


class BaseRepository:
    """Generic CRUD operations for Supabase tables."""

    TABLE_NAME: str = ""

    async def _get_client(self):
        return await get_async_supabase_admin()

    async def create(self, data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            client = await self._get_client()
            result = await client.table(self.TABLE_NAME).insert(data).execute()
            if not result.data:
                raise RuntimeError(f"Insert into {self.TABLE_NAME} returned no data")
            logger.info("Created %s record", self.TABLE_NAME)
            return result.data[0]
        except Exception as e:
            logger.error("Failed to create %s: %s", self.TABLE_NAME, e)
            raise

    async def update(self, record_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_NAME)
                .update(data)
                .eq("id", record_id)
                .execute()
            )
            return result.data[0] if result.data else {}
        except Exception as e:
            logger.error("Failed to update %s %s: %s", self.TABLE_NAME, record_id, e)
            raise

    async def get_by_id(self, record_id: str) -> Optional[Dict[str, Any]]:
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_NAME)
                .select("*")
                .eq("id", record_id)
                .execute()
            )
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error("Failed to get %s %s: %s", self.TABLE_NAME, record_id, e)
            return None

    async def soft_delete(self, record_id: str) -> None:
        try:
            client = await self._get_client()
            await (
                client.table(self.TABLE_NAME)
                .update({"status": "deleted"})
                .eq("id", record_id)
                .execute()
            )
            logger.info("Soft-deleted %s %s", self.TABLE_NAME, record_id)
        except Exception as e:
            logger.error(
                "Failed to soft-delete %s %s: %s", self.TABLE_NAME, record_id, e
            )
            raise

    async def hard_delete(self, record_id: str) -> None:
        try:
            client = await self._get_client()
            await client.table(self.TABLE_NAME).delete().eq("id", record_id).execute()
            logger.info("Deleted %s %s", self.TABLE_NAME, record_id)
        except Exception as e:
            logger.error("Failed to delete %s %s: %s", self.TABLE_NAME, record_id, e)
            raise
