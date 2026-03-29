"""Style Template Repository — data access for style_templates."""

from typing import Any, Dict, List, Optional

from loguru import logger

from app.db.supabase_client import get_async_supabase_admin


class StyleTemplateRepository:
    """CRUD + list operations for style_templates."""

    TABLE_NAME = "style_templates"

    async def _get_client(self):
        return await get_async_supabase_admin()

    async def create(self, data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            client = await self._get_client()
            result = await client.table(self.TABLE_NAME).insert(data).execute()
            logger.info(f"Created style template: {data.get('name')}")
            return result.data[0] if result.data else {}
        except Exception as e:
            logger.error(f"Failed to create style template: {e}")
            raise

    async def update(
        self, template_id: str, data: Dict[str, Any]
    ) -> Dict[str, Any]:
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_NAME)
                .update(data)
                .eq("id", template_id)
                .execute()
            )
            return result.data[0] if result.data else {}
        except Exception as e:
            logger.error(f"Failed to update style template {template_id}: {e}")
            raise

    async def delete(self, template_id: str) -> None:
        try:
            client = await self._get_client()
            await (
                client.table(self.TABLE_NAME)
                .delete()
                .eq("id", template_id)
                .execute()
            )
            logger.info(f"Deleted style template {template_id}")
        except Exception as e:
            logger.error(f"Failed to delete style template {template_id}: {e}")
            raise

    async def get_by_id(self, template_id: str) -> Optional[Dict[str, Any]]:
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_NAME)
                .select("*")
                .eq("id", template_id)
                .execute()
            )
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error(f"Failed to get style template {template_id}: {e}")
            return None

    async def list_templates(
        self,
        team_id: Optional[str] = None,
        category: Optional[str] = None,
        include_public: bool = True,
    ) -> List[Dict[str, Any]]:
        """List templates filtered by team, category, and public visibility.

        When team_id is provided, returns both the team's templates and
        public templates. When team_id is absent, returns only public ones.
        """
        try:
            client = await self._get_client()

            if team_id and include_public:
                # Supabase PostgREST: or filter for team's own + public
                query = (
                    client.table(self.TABLE_NAME)
                    .select("*")
                    .or_(f"team_id.eq.{team_id},is_public.eq.true")
                )
            elif team_id:
                query = (
                    client.table(self.TABLE_NAME)
                    .select("*")
                    .eq("team_id", team_id)
                )
            else:
                query = (
                    client.table(self.TABLE_NAME)
                    .select("*")
                    .eq("is_public", True)
                )

            if category:
                query = query.eq("category", category)

            query = query.order("created_at", desc=True)
            result = await query.execute()
            return result.data or []
        except Exception as e:
            logger.error(f"Failed to list style templates: {e}")
            return []
