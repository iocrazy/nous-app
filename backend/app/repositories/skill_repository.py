"""Skill Repository — data access for skills table."""

from typing import Any, Dict, List, Optional

from loguru import logger

from app.repositories.base_repository import BaseRepository


# Columns returned in list queries (excludes content_md, output_format for performance)
_SUMMARY_COLUMNS = (
    "id, team_id, project_id, created_by, name, description, "
    "category, icon, trigger_keywords, is_public, status, created_at, updated_at"
)


class SkillRepository(BaseRepository):
    """CRUD + list operations for skills."""

    TABLE_NAME = "skills"

    async def archive(self, skill_id: str) -> None:
        """Soft-delete by setting status to 'archived'."""
        await self.update(skill_id, {"status": "archived"})
        logger.info("Archived skill %s", skill_id)

    async def list_skills(
        self,
        team_id: Optional[str] = None,
        project_id: Optional[str] = None,
        category: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """List active skills: team's own + system presets + public.

        Returns summary (no content_md) for performance.
        When project_id is given, includes project-specific skills.
        """
        try:
            client = await self._get_client()

            # Build OR filter: team's own OR public OR system presets (team_id is null)
            or_parts = ["team_id.is.null"]
            if team_id:
                or_parts.append(f"team_id.eq.{team_id}")
                or_parts.append("is_public.eq.true")

            query = (
                client.table(self.TABLE_NAME)
                .select(_SUMMARY_COLUMNS)
                .or_(",".join(or_parts))
                .eq("status", "active")
            )

            if project_id:
                # Include both project-specific and global (project_id is null)
                query = query.or_(f"project_id.eq.{project_id},project_id.is.null")
            else:
                query = query.is_("project_id", "null")

            if category:
                query = query.eq("category", category)

            query = query.order("created_at", desc=True)
            result = await query.execute()
            return result.data or []
        except Exception as e:
            logger.error("Failed to list skills: %s", e)
            return []
