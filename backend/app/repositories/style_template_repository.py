"""Style Template Repository — data access for style_templates.

ORM 2.0 migration (Batch L1): ``StyleTemplateRepository`` is the legacy
supabase-py REST implementation; ``StyleTemplateRepositoryOrm`` (in
``style_template_repository_orm.py``) is the SQLAlchemy 2.0 ORM successor.
Call sites go through ``get_style_template_repository()`` (bottom of this file)
which picks the ORM subclass when ``USE_ORM_STYLE_TEMPLATES`` is on AND the
engine is configured.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional, Union

from loguru import logger

from app.repositories.base_repository import BaseRepository

if TYPE_CHECKING:
    from app.repositories.style_template_repository_orm import (
        StyleTemplateRepositoryOrm,
    )


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
            logger.error(f"Failed to list style templates: {e}")
            return []


def get_style_template_repository() -> (
    Union["StyleTemplateRepository", "StyleTemplateRepositoryOrm"]
):
    """Return the right StyleTemplateRepository implementation per env.

    ORM when ``USE_ORM_STYLE_TEMPLATES`` is set AND the SQLAlchemy engine is
    configured; otherwise the legacy supabase-py REST path. A flag-on but
    engine-missing deploy logs once and falls back to REST (never crashes).
    """
    from app.core.config import settings

    if settings.USE_ORM_STYLE_TEMPLATES:
        from app.db.engine import is_configured

        if is_configured():
            from app.repositories.style_template_repository_orm import (
                StyleTemplateRepositoryOrm,
            )

            return StyleTemplateRepositoryOrm()
        logger.warning(
            "USE_ORM_STYLE_TEMPLATES=true but SUPAVISOR_DATABASE_URL is empty "
            "— falling back to supabase-py path"
        )
    return StyleTemplateRepository()
