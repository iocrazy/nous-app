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

    async def patch_node_run_results(
        self,
        canvas_id: str,
        results_by_node_id: Dict[str, Dict[str, Any]],
    ) -> bool:
        """Merge run_result/run_status into nodes_json for the given nodes.

        Read-modify-write WITHOUT an optimistic lock. Uses the service-role
        client so the DBOS graph workflow can persist per-node outputs without
        holding the user's lock token across steps.

        Only the keys supplied in ``results_by_node_id`` (e.g. ``run_result``,
        ``run_status``) are written into ``node.data``; all other node fields
        (position, type, other data keys, connections) are left intact.

        base_updated_at advance (Phase 6a realtime closure):
          ``base_updated_at`` is the optimistic-lock token AND the staleness
          guard the frontend's ``applyRemoteUpdate`` uses to drop self-echo /
          stale realtime events (``if row.base_updated_at <= s.baseUpdatedAt
          return``).  A nodes_json write that does NOT advance the token would
          be seen as STALE by every open tab → the persisted run_result would
          never surface via realtime, defeating the purpose of persisting it.
          So, mirroring ``update_with_lock``, we stamp base_updated_at to the
          freshly-bumped updated_at: the UPDATE writing nodes_json fires the
          ``trg_canvases_touch_updated_at`` trigger (bumps updated_at SQL-side
          via now()), then a second UPDATE echoes that new updated_at into
          base_updated_at.  The timestamp source is the DB trigger's now() — it
          is never computed in Python.

        Clobber-safety note (M4a documented limitation):
          The concurrent user PUT path (``update_with_lock``) replaces
          ``nodes_json`` wholesale.  If a user saves the canvas while the
          workflow is mid-run, the user save and the workflow write race.
          Whichever write lands second wins at the row level, so ``run_result``
          fields written by the workflow may be overwritten by a concurrent user
          save, or vice-versa.  For the M4a scope this is acceptable: the
          frontend relies on Phase 6a realtime broadcasts which will deliver the
          most-recently persisted row.  A user with unsaved local edits gets the
          existing 409 conflict path on their next PUT (their base_updated_at no
          longer matches) — correct last-writer-wins behaviour for v1.  M4b may
          address with a PostgreSQL jsonb path-update to make the write truly
          non-destructive.
        """
        row = await self.get_by_id(canvas_id)
        if row is None:
            logger.warning(
                f"canvas patch_node_run_results: canvas {canvas_id!r} not found"
            )
            return False

        nodes_raw = row.get("nodes_json")
        nodes_list: List[Any] = list(nodes_raw) if isinstance(nodes_raw, list) else []

        patched: List[Any] = []
        for node in nodes_list:
            if not isinstance(node, dict):
                patched.append(node)
                continue
            node_id = node.get("id")
            if node_id in results_by_node_id:
                node_data = dict(node.get("data") or {})
                node_data.update(results_by_node_id[node_id])
                patched.append({**node, "data": node_data})
            else:
                patched.append(node)

        try:
            client = await self._client()
            # Step 1: write nodes_json.  This fires trg_canvases_touch_updated_at
            # which bumps updated_at to now() at the SQL layer.
            result = (
                await client.table(self.TABLE)
                .update({"nodes_json": patched})
                .eq("id", _bigint(canvas_id))
                .execute()
            )
            if not result.data:
                return False
            # Step 2: advance base_updated_at to the freshly-bumped updated_at so
            # the realtime event is recognised as NEWER by open tabs (Phase 6a
            # applyRemoteUpdate staleness guard).  Same SQL-side timestamp the
            # user save path echoes — never a Python-computed value.
            new_token = result.data[0].get("updated_at")
            if new_token:
                await client.table(self.TABLE).update(
                    {"base_updated_at": new_token}
                ).eq("id", _bigint(canvas_id)).execute()
            return True
        except Exception as e:
            logger.error(f"canvas patch_node_run_results({canvas_id}) failed: {e}")
            return False

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
