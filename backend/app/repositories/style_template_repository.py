"""Style Template Repository — data access for style_templates."""

from typing import Any, Dict, List, Optional

from loguru import logger

from app.repositories.base_repository import BaseRepository


class StyleTemplateRepository(BaseRepository):
    """CRUD + list operations for style_templates."""

    TABLE_NAME = "style_templates"

    async def delete(self, template_id: str) -> None:
        """Hard-delete a template by ID."""
        await self.hard_delete(template_id)

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
                query = client.table(self.TABLE_NAME).select("*").eq("team_id", team_id)
            else:
                query = client.table(self.TABLE_NAME).select("*").eq("is_public", True)

            if category:
                query = query.eq("category", category)

            query = query.order("created_at", desc=True)
            result = await query.execute()
            return result.data or []
        except Exception as e:
            logger.error("Failed to list style templates: %s", e)
            return []
