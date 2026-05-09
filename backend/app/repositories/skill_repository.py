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
        logger.info(f"Archived skill {skill_id}")

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
            logger.error(f"Failed to list skills: {e}")
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
        team_ids: Optional[List[int]] = None,
        project_ids: Optional[List[int]] = None,
    ) -> List[Dict[str, Any]]:
        """List active skills accessible to this user.

        A skill is accessible when ``status='active'`` and either:
          * ``is_public = true`` (including system presets), or
          * ``created_by = user_id`` (the user's own skills), or
          * ``team_id IN team_ids`` (any of the user's teams), or
          * ``project_id IN project_ids`` (any of the user's projects).

        The legacy ``project_id`` argument is kept for back-compat as a
        single-project shortcut (appends to ``project_ids``). New callers
        should pass the plural forms directly.

        Sorted by ``updated_at DESC`` for a freshest-first UX.
        """
        try:
            client = await self._get_client()
            or_parts = ["is_public.eq.true", f"created_by.eq.{user_id}"]

            merged_project_ids: list[int] = list(project_ids or [])
            if project_id is not None and project_id not in merged_project_ids:
                merged_project_ids.append(project_id)
            if merged_project_ids:
                or_parts.append(
                    f"project_id.in.({','.join(str(i) for i in merged_project_ids)})"
                )
            if team_ids:
                or_parts.append(f"team_id.in.({','.join(str(i) for i in team_ids)})")

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
            logger.error(f"Failed to list accessible skills for user {user_id}: {e}")
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

    async def get_file(self, skill_id: int, path: str) -> Optional[Dict[str, Any]]:
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
            logger.error(f"Failed to get file {path!r} for skill {skill_id}: {e}")
            return None

    async def upsert_file(
        self,
        skill_id: int,
        *,
        path: str,
        content: Optional[str],
        file_type: str,
        binary_url: Optional[str] = None,
        seed_hash: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Insert or update a skill file keyed on ``(skill_id, path)``.

        Relies on the unique index ``ux_skill_files_path`` created in
        migration 139; conflict resolution happens Postgres-side.
        Writes raise on error so the caller can surface a 5xx.

        ``seed_hash`` is optional and only set by the seed loader for
        idempotent re-runs (mig 199). User-driven writes leave it NULL
        so the next loader pass sees "unknown — re-PATCH" and refreshes.
        """
        try:
            client = await self._get_client()
            row: Dict[str, Any] = {
                "skill_id": skill_id,
                "path": path,
                "content": content,
                "file_type": file_type,
                "binary_url": binary_url,
            }
            if seed_hash is not None:
                row["seed_hash"] = seed_hash
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
            logger.error(f"Failed to upsert file {path!r} for skill {skill_id}: {e}")
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
            logger.info(f"Deleted skill_files row skill_id={skill_id} path={path}")
        except Exception as e:
            logger.error(f"Failed to delete file {path!r} for skill {skill_id}: {e}")
            raise

    # ------------------------------------------------------------------
    # Writes on skills table (PATCH-style)
    # ------------------------------------------------------------------

    async def insert(self, fields: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new ``skills`` row.

        The caller is responsible for setting ``is_public`` (false for
        user-created skills, true for system presets) and supplying a slug.
        Returns the inserted row.
        """
        try:
            client = await self._get_client()
            result = await client.table(self.TABLE).insert(fields).execute()
            if not result.data:
                raise RuntimeError("insert skill returned no data")
            return result.data[0]
        except Exception as e:
            logger.error(f"Failed to insert skill: {e}")
            raise

    async def delete(self, skill_id: int) -> None:
        """Hard-delete a ``skills`` row by BIGINT id.

        FK cascades (migration 138) handle the cleanup of ``agent_skills``
        bindings and ``skill_files`` rows for this skill — no manual work
        needed here. Raises on DB error.
        """
        try:
            client = await self._get_client()
            await client.table(self.TABLE).delete().eq("id", skill_id).execute()
            logger.info(f"Deleted skill id={skill_id}")
        except Exception as e:
            logger.error(f"Failed to delete skill {skill_id}: {e}")
            raise

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

    # Fields snapshotted into skill_versions. Narrower than update_fields'
    # accepted fields — only behavioral content, per Phase 2 plan.
    _VERSIONED_SKILL_FIELDS = ("body_md", "frontmatter_json")

    # Fields snapshotted into skill_file_versions on change.
    _VERSIONED_SKILL_FILE_FIELDS = ("path", "content", "file_type", "binary_url")

    async def update_fields_versioned(
        self,
        skill_id: int,
        updates: Dict[str, Any],
        created_by: Optional[UUID] = None,
        notes: Optional[str] = None,
    ) -> None:
        """Snapshot-then-update for skills. See AgentRepository.update_fields_versioned
        for the pattern rationale.

        No-op if none of ``body_md`` / ``frontmatter_json`` actually differs.
        Raises ValueError if the skill does not exist.

        Note: the snapshot INSERT and live UPDATE are NOT in a single transaction.
        See ``upsert_file_versioned`` for the same limitation and Phase 3 mitigation path.
        """
        client = await self._get_client()
        result = (
            await client.table(self.TABLE)
            .select("*")
            .eq("id", skill_id)
            .maybe_single()
            .execute()
        )
        current = result.data if result and result.data else None
        if current is None:
            raise ValueError(f"skill {skill_id} not found")

        tracked_changed = any(
            k in updates and updates[k] != current.get(k)
            for k in self._VERSIONED_SKILL_FIELDS
        )
        if not tracked_changed:
            return

        current_version = int(current.get("current_version") or 1)
        snapshot = {
            "skill_id": skill_id,
            "version_number": current_version,
            "notes": notes,
            "created_by": str(created_by) if created_by else None,
        }
        for field in self._VERSIONED_SKILL_FIELDS:
            snapshot[field] = current.get(field)

        await client.table("skill_versions").insert(snapshot).execute()

        patch = {**updates, "current_version": current_version + 1}
        await client.table(self.TABLE).update(patch).eq("id", skill_id).execute()

    async def upsert_file_versioned(
        self,
        skill_id: int,
        path: str,
        content: Optional[str] = None,
        file_type: Optional[str] = None,
        binary_url: Optional[str] = None,
        created_by: Optional[UUID] = None,
        notes: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create or update a skill file with version capture.

        Three paths:
        - No existing file at (skill_id, path) → INSERT with current_version=1.
          Returns the inserted row.
        - Existing file, at least one of path/content/file_type/binary_url
          differs → INSERT snapshot of old content into skill_file_versions
          with version_number = existing current_version, then UPDATE live row.
          Returns the merged (new) row.
        - Existing file, nothing tracked differs → NO-OP. Returns current row.

        Note: the snapshot INSERT and live UPDATE are NOT in a single transaction.
        A partial failure (INSERT ok, UPDATE fails) leaves an orphaned version row
        that will collide on the next edit via the UNIQUE(skill_file_id, version_number)
        constraint, forcing a 500 until manual cleanup. Phase 3 should move this to
        a Postgres rpc() for atomicity.
        """
        client = await self._get_client()
        result = (
            await client.table("skill_files")
            .select("*")
            .eq("skill_id", skill_id)
            .eq("path", path)
            .maybe_single()
            .execute()
        )
        current = result.data if result and result.data else None

        new_payload: Dict[str, Any] = {
            "path": path,
            "content": content,
            "file_type": file_type,
            "binary_url": binary_url,
        }

        if current is None:
            insert_row = {
                "skill_id": skill_id,
                **new_payload,
                "current_version": 1,
            }
            resp = await client.table("skill_files").insert(insert_row).execute()
            return resp.data[0] if resp.data else insert_row

        tracked_changed = any(
            new_payload[k] != current.get(k) for k in self._VERSIONED_SKILL_FILE_FIELDS
        )
        if not tracked_changed:
            return current

        current_version = int(current.get("current_version") or 1)
        snapshot: Dict[str, Any] = {
            "skill_file_id": current["id"],
            "version_number": current_version,
            "notes": notes,
            "created_by": str(created_by) if created_by else None,
        }
        for field in self._VERSIONED_SKILL_FILE_FIELDS:
            snapshot[field] = current.get(field)

        await client.table("skill_file_versions").insert(snapshot).execute()

        patch = {**new_payload, "current_version": current_version + 1}
        await (
            client.table("skill_files").update(patch).eq("id", current["id"]).execute()
        )
        return {**current, **patch}
