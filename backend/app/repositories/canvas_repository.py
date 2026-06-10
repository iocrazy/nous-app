"""Repository for the ``canvases`` table (Phase 1 Day 2-3).

Pure data access. The optimistic-lock decision (409 vs. apply) lives in
``CanvasService`` so the repo can stay a thin wrapper around supabase-py
queries. We use the service-role client because the canvas RLS already
ran at GET time inside ``ensure_canvas_access``, and we want predictable
behaviour on writes (RLS via supabase-py + service_role on PUTs has
historically been the source of silent NULL-returns).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from loguru import logger

from app.db.supabase_client import get_async_supabase_admin


def _bigint(value: Any) -> int:
    """Coerce a snowflake string ID to an int for asyncpg/supabase bindings."""
    if isinstance(value, int):
        return value
    return int(str(value))


class CanvasRepository:
    """CRUD for the ``canvases`` table (migration 280)."""

    TABLE = "canvases"

    async def _client(self):
        return await get_async_supabase_admin()

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def get_by_id(self, canvas_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a single canvas; returns ``None`` if not found."""
        try:
            client = await self._client()
            result = (
                await client.table(self.TABLE)
                .select("*")
                .eq("id", _bigint(canvas_id))
                .maybe_single()
                .execute()
            )
            return result.data if result and result.data else None
        except Exception as e:
            logger.error(f"canvas get_by_id({canvas_id}) failed: {e}")
            return None

    async def list_for_project(self, project_id: str) -> List[Dict[str, Any]]:
        """All canvases belonging to a project, newest-edited first."""
        try:
            client = await self._client()
            result = (
                await client.table(self.TABLE)
                .select("*")
                .eq("project_id", _bigint(project_id))
                .order("updated_at", desc=True)
                .execute()
            )
            return list(result.data or [])
        except Exception as e:
            logger.error(f"canvas list_for_project({project_id}) failed: {e}")
            return []

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    async def create(
        self,
        *,
        project_id: str,
        name: str,
        kind: str,
        created_by: Optional[str],
        viewport_json: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Insert a new canvas; defaults flow from the column DEFAULTs
        when fields are omitted."""
        payload: Dict[str, Any] = {
            "project_id": _bigint(project_id),
            "name": name,
            "kind": kind,
        }
        if created_by is not None:
            payload["created_by"] = created_by
        if viewport_json is not None:
            payload["viewport_json"] = viewport_json
        try:
            client = await self._client()
            result = await client.table(self.TABLE).insert(payload).execute()
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error(f"canvas create for project {project_id} failed: {e}")
            return None

    async def update_with_lock(
        self,
        canvas_id: str,
        *,
        expected_base_updated_at: str,
        fields: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Apply an optimistic-locked update.

        Returns the new row when ``base_updated_at`` matches and the row
        was updated; returns ``None`` when the token didn't match (the
        caller treats that as a conflict).

        The new ``base_updated_at`` is stamped at the SQL layer to the
        same value as ``updated_at`` so the client's next PUT can echo
        either one without ambiguity.
        """
        if not fields:
            # No mutations requested → just verify the lock and return
            # the row unchanged.
            current = await self.get_by_id(canvas_id)
            if current is None:
                return None
            if str(current.get("base_updated_at")) != str(expected_base_updated_at):
                return None
            return current

        payload = {**fields, "base_updated_at": "__now__"}
        # supabase-py can't send raw `now()` — we have to read+compare+update
        # in two steps. That's safe because the eq() guard on
        # base_updated_at makes the UPDATE itself atomic at the SQL layer:
        # PostgreSQL will only update rows that still match the token.
        try:
            client = await self._client()
            # We let the DB trigger bump updated_at; we set base_updated_at
            # by reading it back from the result row.
            mutable = {k: v for k, v in payload.items() if k != "base_updated_at"}
            # Use a sub-query-style sentinel by sending the lock as eq().
            result = (
                await client.table(self.TABLE)
                .update(mutable)
                .eq("id", _bigint(canvas_id))
                .eq("base_updated_at", expected_base_updated_at)
                .execute()
            )
            if not result.data:
                return None
            # Bump base_updated_at to the now-fresh updated_at so the
            # client's next PUT can use it.
            updated_row = result.data[0]
            new_token = updated_row.get("updated_at")
            if new_token:
                bump = (
                    await client.table(self.TABLE)
                    .update({"base_updated_at": new_token})
                    .eq("id", _bigint(canvas_id))
                    .execute()
                )
                if bump.data:
                    return bump.data[0]
            return updated_row
        except Exception as e:
            logger.error(f"canvas update_with_lock({canvas_id}) failed: {e}")
            return None

    async def delete(self, canvas_id: str) -> bool:
        try:
            client = await self._client()
            result = (
                await client.table(self.TABLE)
                .delete()
                .eq("id", _bigint(canvas_id))
                .execute()
            )
            return bool(result.data)
        except Exception as e:
            logger.error(f"canvas delete({canvas_id}) failed: {e}")
            return False

    # ------------------------------------------------------------------
    # Helpers used by access checks
    # ------------------------------------------------------------------

    async def get_project_id(self, canvas_id: str) -> Optional[str]:
        """Cheap project lookup for membership checks (skips the JSONB)."""
        try:
            client = await self._client()
            result = (
                await client.table(self.TABLE)
                .select("project_id")
                .eq("id", _bigint(canvas_id))
                .maybe_single()
                .execute()
            )
            if not result or not result.data:
                return None
            return str(result.data.get("project_id"))
        except Exception as e:
            logger.error(f"canvas get_project_id({canvas_id}) failed: {e}")
            return None
