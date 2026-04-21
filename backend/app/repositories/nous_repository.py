# backend/app/repositories/nous_repository.py

"""
Repository for nous_models table — admin-configured platform AI models.
"""

from typing import Any, Dict, List, Optional

from loguru import logger

from app.db.supabase_client import get_async_supabase_admin


class NousRepository:
    """CRUD for nous_models table."""

    TABLE = "nous_models"

    async def _get_client(self):
        return await get_async_supabase_admin()

    # ------------------------------------------------------------------
    # Public queries (no API keys exposed)
    # ------------------------------------------------------------------

    async def list_enabled(
        self, category: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """List enabled Nous models, optionally filtered by category.

        Returns public fields only (no api_key, app_id, base_url).
        """
        try:
            client = await self._get_client()
            query = (
                client.table(self.TABLE)
                .select(
                    "id, name, display_name, category, pricing_type, pricing_value, sort_order"
                )
                .eq("is_enabled", True)
                .order("sort_order")
            )
            if category:
                query = query.eq("category", category)
            result = await query.execute()
            return result.data or []
        except Exception as e:
            logger.error(f"Failed to list enabled nous models: {e}")
            return []

    async def get_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """Get a Nous model by name (includes all fields for backend use)."""
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE)
                .select("*")
                .eq("name", name)
                .maybe_single()
                .execute()
            )
            return result.data
        except Exception as e:
            logger.error(f"Failed to get nous model '{name}': {e}")
            return None

    # ------------------------------------------------------------------
    # Admin CRUD
    # ------------------------------------------------------------------

    async def list_all(self) -> List[Dict[str, Any]]:
        """List all Nous models (admin, includes disabled)."""
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE).select("*").order("sort_order").execute()
            )
            return result.data or []
        except Exception as e:
            logger.error(f"Failed to list all nous models: {e}")
            return []

    async def create(self, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Create a new Nous model."""
        try:
            client = await self._get_client()
            result = await client.table(self.TABLE).insert(data).execute()
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error(f"Failed to create nous model: {e}")
            return None

    async def update(
        self, model_id: str, data: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Update a Nous model."""
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE)
                .update({**data, "updated_at": "now()"})
                .eq("id", model_id)
                .execute()
            )
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error(f"Failed to update nous model {model_id}: {e}")
            return None

    async def delete(self, model_id: str) -> bool:
        """Delete a Nous model."""
        try:
            client = await self._get_client()
            await client.table(self.TABLE).delete().eq("id", model_id).execute()
            return True
        except Exception as e:
            logger.error(f"Failed to delete nous model {model_id}: {e}")
            return False
