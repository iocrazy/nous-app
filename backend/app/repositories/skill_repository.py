"""Skill Repository — data access for skills + skill_files tables.

Extends the legacy CRUD (list_skills/archive via BaseRepository) with the
AI Library Phase 1 surface:
  * slug / id lookups
  * batched `list_by_ids` for the prompt composer
  * `list_accessible` scoped to a user (+ optional project)
  * multi-file CRUD on `skill_files`

Mirrors the async pattern used in `agent_repository.py` and
`nous_repository.py`: every `.execute()` is awaited, reads swallow
exceptions and return None/[], writes log + re-raise.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from uuid import UUID

from loguru import logger

from app.repositories.base_repository import BaseRepository

# Columns returned in list queries (excludes content_md, output_format for performance)
_SUMMARY_COLUMNS = (
    "id, team_id, project_id, created_by, name, description, "
    "category, icon, trigger_keywords, is_public, status, created_at, updated_at"
)

# Upsert conflict target on skill_files (see migration 139 ux_skill_files_path)
_SKILL_FILES_CONFLICT = "skill_id,path"


class SkillRepository(BaseRepository):
    """CRUD + list operations for skills.

    Inherits BaseRepository which provides the shared async
    ``_get_client`` helper and legacy ``create``/``update``/``get_by_id``
    used by the existing skills router.
    """

    TABLE_NAME = "skills"
    TABLE = "skills"  # alias for clarity at call sites
    FILES_TABLE = "skill_files"

    # ------------------------------------------------------------------
    # Legacy operations (kept for the existing skills_router)
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # AI Library Phase 1 — skills table reads
    # ------------------------------------------------------------------

    async def get_by_slug(self, slug: str) -> Optional[Dict[str, Any]]:
        """Fetch a single skill by slug; returns None if not found."""
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE)
                .select("*")
                .eq("slug", slug)
                .maybe_single()
                .execute()
            )
            return result.data if result and result.data else None
        except Exception as e:
            logger.error(f"Failed to get skill by slug '{slug}': {e}")
            return None

    async def get_by_id(  # type: ignore[override]
        self, skill_id: Any
    ) -> Optional[Dict[str, Any]]:
        """Fetch a skill by BIGINT id; returns None if not found.

        Accepts ``int`` (new callers) or ``str`` (legacy router). PostgREST
        will coerce either representation against the BIGINT column.
        """
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE)
                .select("*")
                .eq("id", skill_id)
                .maybe_single()
                .execute()
            )
            return result.data if result and result.data else None
        except Exception as e:
            logger.error(f"Failed to get skill by id {skill_id}: {e}")
            return None

    async def list_by_ids(self, skill_ids: List[int]) -> List[Dict[str, Any]]:
        """Batch fetch skills by a list of BIGINT ids (for the composer).

        Short-circuits on an empty input to avoid emitting an ``IN ()``
        clause that PostgREST rejects.
        """
        if not skill_ids:
            return []
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE)
                .select("*")
                .in_("id", skill_ids)
                .execute()
            )
            return result.data or []
        except Exception as e:
            logger.error(f"Failed to list skills by ids {skill_ids}: {e}")
            return []

    async def list_accessible(
        self,
        user_id: UUID,
        project_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """List active skills accessible to this user.

        A skill is accessible when ``status='active'`` and either:
          * ``is_public = true`` (including system presets), or
          * ``project_id`` matches the supplied project (when given).

        Sorted by ``updated_at DESC`` for a freshest-first UX.
        """
        try:
            client = await self._get_client()
            or_parts = ["is_public.eq.true"]
            if project_id is not None:
                or_parts.append(f"project_id.eq.{project_id}")

            query = (
                client.table(self.TABLE)
                .select("*")
                .eq("status", "active")
                .or_(",".join(or_parts))
                .order("updated_at", desc=True)
            )
            result = await query.execute()
            return result.data or []
        except Exception as e:
            logger.error(
                f"Failed to list accessible skills for user {user_id}: {e}"
            )
            return []

    # ------------------------------------------------------------------
    # AI Library Phase 1 — skill_files CRUD
    # ------------------------------------------------------------------

    async def list_files(self, skill_id: int) -> List[Dict[str, Any]]:
        """List all files for a skill, ordered by sort_order then path."""
        try:
            client = await self._get_client()
            result = (
                await client.table(self.FILES_TABLE)
                .select("*")
                .eq("skill_id", skill_id)
                .order("sort_order")
                .order("path")
                .execute()
            )
            return result.data or []
        except Exception as e:
            logger.error(f"Failed to list files for skill {skill_id}: {e}")
            return []

    async def get_file(
        self, skill_id: int, path: str
    ) -> Optional[Dict[str, Any]]:
        """Fetch a single file row by (skill_id, path); None if missing."""
        try:
            client = await self._get_client()
            result = (
                await client.table(self.FILES_TABLE)
                .select("*")
                .eq("skill_id", skill_id)
                .eq("path", path)
                .maybe_single()
                .execute()
            )
            return result.data if result and result.data else None
        except Exception as e:
            logger.error(
                f"Failed to get file {path!r} for skill {skill_id}: {e}"
            )
            return None

    async def upsert_file(
        self,
        skill_id: int,
        *,
        path: str,
        content: Optional[str],
        file_type: str,
        binary_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Insert or update a skill file keyed on ``(skill_id, path)``.

        Relies on the unique index ``ux_skill_files_path`` created in
        migration 139; conflict resolution happens Postgres-side.
        Writes raise on error so the caller can surface a 5xx.
        """
        try:
            client = await self._get_client()
            row = {
                "skill_id": skill_id,
                "path": path,
                "content": content,
                "file_type": file_type,
                "binary_url": binary_url,
            }
            result = (
                await client.table(self.FILES_TABLE)
                .upsert(row, on_conflict=_SKILL_FILES_CONFLICT)
                .execute()
            )
            if not result.data:
                raise RuntimeError(
                    f"Upsert skill_files (skill_id={skill_id}, path={path!r}) "
                    f"returned no data"
                )
            return result.data[0]
        except Exception as e:
            logger.error(
                f"Failed to upsert file {path!r} for skill {skill_id}: {e}"
            )
            raise

    async def delete_file(self, skill_id: int, path: str) -> None:
        """Hard-delete a file row by (skill_id, path). Raises on error."""
        try:
            client = await self._get_client()
            await (
                client.table(self.FILES_TABLE)
                .delete()
                .eq("skill_id", skill_id)
                .eq("path", path)
                .execute()
            )
            logger.info(
                "Deleted skill_files row skill_id=%s path=%s", skill_id, path
            )
        except Exception as e:
            logger.error(
                f"Failed to delete file {path!r} for skill {skill_id}: {e}"
            )
            raise

    # ------------------------------------------------------------------
    # Writes on skills table (PATCH-style)
    # ------------------------------------------------------------------

    async def update_fields(
        self, skill_id: int, updates: Dict[str, Any]
    ) -> Dict[str, Any]:
        """PATCH-style update on skills; returns the updated row or {}."""
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE)
                .update(updates)
                .eq("id", skill_id)
                .execute()
            )
            return result.data[0] if result.data else {}
        except Exception as e:
            logger.error(f"Failed to update skill {skill_id}: {e}")
            raise
