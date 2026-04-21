# app/repositories/storyboard_repository.py

"""
Storyboard Repository Layer

Data access layer for the Storyboard Workbench module. Covers six domain
entities: projects, nodes, edges, frames, characters, and assets.
All methods are async and use the Supabase admin client.
"""

from typing import Any, Dict, List, Optional

from loguru import logger

from app.db.supabase_client import get_async_supabase_admin


# --------------------------------------------------------------------------- #
# 1. StoryboardProjectRepository
# --------------------------------------------------------------------------- #


class StoryboardProjectRepository:
    """CRUD + list operations for storyboard_projects."""

    TABLE_NAME = "storyboard_projects"

    async def _get_client(self):
        """Get async Supabase admin client."""
        return await get_async_supabase_admin()

    # ------------------------------------------------------------------ #
    # Write operations
    # ------------------------------------------------------------------ #

    async def create(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Insert a new storyboard project.

        Args:
            data: Column values for the new row.

        Returns:
            Created project row dict, or empty dict on failure.
        """
        try:
            client = await self._get_client()
            result = await client.table(self.TABLE_NAME).insert(data).execute()
            logger.info(f"Created storyboard project: {data.get('name')}")
            return result.data[0] if result.data else {}
        except Exception as e:
            logger.error(f"Failed to create storyboard project: {e}")
            raise

    async def update(self, project_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Update an existing storyboard project.

        Args:
            project_id: UUID of the project to update.
            data: Fields to update.

        Returns:
            Updated project row dict.
        """
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_NAME)
                .update(data)
                .eq("id", project_id)
                .execute()
            )
            logger.info(f"Updated storyboard project {project_id}")
            return result.data[0] if result.data else {}
        except Exception as e:
            logger.error(f"Failed to update storyboard project {project_id}: {e}")
            raise

    async def update_viewport(
        self, project_id: str, viewport_json: Dict[str, Any]
    ) -> None:
        """
        Lightweight viewport update — does NOT touch updated_at.

        Intended for high-frequency canvas pan/zoom saves. Bypasses the
        updated_at column so collaborators do not see a spurious "modified"
        timestamp.

        Args:
            project_id: UUID of the project.
            viewport_json: Serialisable viewport state (x, y, zoom, …).
        """
        try:
            client = await self._get_client()
            await (
                client.table(self.TABLE_NAME)
                .update({"viewport_json": viewport_json})
                .eq("id", project_id)
                .execute()
            )
        except Exception as e:
            logger.error(f"Failed to update viewport for project {project_id}: {e}")
            raise

    async def soft_delete(self, project_id: str) -> None:
        """
        Soft-delete a project by setting its status to 'deleted'.

        Args:
            project_id: UUID of the project.
        """
        try:
            client = await self._get_client()
            await (
                client.table(self.TABLE_NAME)
                .update({"status": "deleted"})
                .eq("id", project_id)
                .execute()
            )
            logger.info(f"Soft-deleted storyboard project {project_id}")
        except Exception as e:
            logger.error(f"Failed to soft-delete storyboard project {project_id}: {e}")
            raise

    # ------------------------------------------------------------------ #
    # Read operations
    # ------------------------------------------------------------------ #

    async def get_by_id(self, project_id: str) -> Optional[Dict[str, Any]]:
        """
        Fetch a single storyboard project by UUID.

        Args:
            project_id: UUID of the project.

        Returns:
            Project row dict, or None if not found.
        """
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_NAME)
                .select("*")
                .eq("id", project_id)
                .execute()
            )
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error(f"Failed to get storyboard project {project_id}: {e}")
            return None

    async def list_by_team(
        self,
        team_id: str,
        page: int = 1,
        limit: int = 20,
        search: Optional[str] = None,
        sort_by: str = "updated_at",
        sort_order: str = "desc",
        project_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Paginated list of storyboard projects for a team.

        Args:
            team_id: UUID of the owning team.
            page: 1-based page number.
            limit: Rows per page.
            search: Optional name substring filter (case-insensitive).
            sort_by: Column to sort by (default ``updated_at``).
            sort_order: ``"asc"`` or ``"desc"`` (default ``"desc"``).
            project_id: Optional parent project ID filter.

        Returns:
            Dict with keys ``items`` (list), ``total`` (int),
            ``page`` (int), and ``limit`` (int).
        """
        try:
            client = await self._get_client()
            offset = (page - 1) * limit

            # Count query
            count_query = (
                client.table(self.TABLE_NAME)
                .select("id", count="exact")
                .eq("team_id", team_id)
                .neq("status", "deleted")
            )
            if project_id is not None:
                count_query = count_query.eq("project_id", project_id)
            if search:
                escaped = search.replace("%", r"\%").replace("_", r"\_")
                count_query = count_query.ilike("name", f"%{escaped}%")
            count_result = await count_query.execute()
            total = count_result.count or 0

            # Data query
            desc = sort_order.lower() == "desc"
            data_query = (
                client.table(self.TABLE_NAME)
                .select("*")
                .eq("team_id", team_id)
                .neq("status", "deleted")
                .order(sort_by, desc=desc)
                .range(offset, offset + limit - 1)
            )
            if project_id is not None:
                data_query = data_query.eq("project_id", project_id)
            if search:
                escaped = search.replace("%", r"\%").replace("_", r"\_")
                data_query = data_query.ilike("name", f"%{escaped}%")
            data_result = await data_query.execute()

            return {
                "items": data_result.data or [],
                "total": total,
                "page": page,
                "limit": limit,
            }
        except Exception as e:
            logger.error(f"Failed to list storyboard projects for team {team_id}: {e}")
            return {"items": [], "total": 0, "page": page, "limit": limit}


# --------------------------------------------------------------------------- #
# 2. StoryboardNodeRepository
# --------------------------------------------------------------------------- #


class StoryboardNodeRepository:
    """Bulk upsert and CRUD for storyboard_nodes."""

    TABLE_NAME = "storyboard_nodes"

    async def _get_client(self):
        return await get_async_supabase_admin()

    async def bulk_upsert(
        self, project_id: str, nodes: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Insert or update multiple nodes for a project atomically.

        Each node dict must include its ``id`` for conflict resolution.

        Args:
            project_id: UUID of the owning project (injected into every row).
            nodes: List of node dicts.

        Returns:
            List of upserted node row dicts.
        """
        if not nodes:
            return []
        try:
            client = await self._get_client()
            rows = [{**node, "project_id": project_id} for node in nodes]
            result = (
                await client.table(self.TABLE_NAME)
                .upsert(rows, on_conflict="id")
                .execute()
            )
            logger.info(f"Bulk-upserted {len(rows)} nodes for project {project_id}")
            return result.data or []
        except Exception as e:
            logger.error(f"Failed to bulk-upsert nodes for project {project_id}: {e}")
            raise

    async def update(self, node_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Update a single node.

        Args:
            node_id: UUID of the node.
            data: Fields to update.

        Returns:
            Updated node row dict.
        """
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_NAME)
                .update(data)
                .eq("id", node_id)
                .execute()
            )
            logger.info(f"Updated storyboard node {node_id}")
            return result.data[0] if result.data else {}
        except Exception as e:
            logger.error(f"Failed to update storyboard node {node_id}: {e}")
            raise

    async def delete(self, node_id: str) -> None:
        """
        Hard-delete a single node.

        Args:
            node_id: UUID of the node.
        """
        try:
            client = await self._get_client()
            await client.table(self.TABLE_NAME).delete().eq("id", node_id).execute()
            logger.info(f"Deleted storyboard node {node_id}")
        except Exception as e:
            logger.error(f"Failed to delete storyboard node {node_id}: {e}")
            raise

    async def delete_by_project(self, project_id: str) -> None:
        """
        Delete all nodes belonging to a project.

        Args:
            project_id: UUID of the project.
        """
        try:
            client = await self._get_client()
            await (
                client.table(self.TABLE_NAME)
                .delete()
                .eq("project_id", project_id)
                .execute()
            )
            logger.info(f"Deleted all nodes for project {project_id}")
        except Exception as e:
            logger.error(f"Failed to delete nodes for project {project_id}: {e}")
            raise

    async def get_by_project(self, project_id: str) -> List[Dict[str, Any]]:
        """
        Fetch all nodes for a project.

        Args:
            project_id: UUID of the project.

        Returns:
            List of node row dicts.
        """
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_NAME)
                .select("*")
                .eq("project_id", project_id)
                .execute()
            )
            return result.data or []
        except Exception as e:
            logger.error(f"Failed to get nodes for project {project_id}: {e}")
            return []


# --------------------------------------------------------------------------- #
# 3. StoryboardEdgeRepository
# --------------------------------------------------------------------------- #


class StoryboardEdgeRepository:
    """Bulk upsert and CRUD for storyboard_edges."""

    TABLE_NAME = "storyboard_edges"

    async def _get_client(self):
        return await get_async_supabase_admin()

    async def bulk_upsert(
        self, project_id: str, edges: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Insert or update multiple edges for a project atomically.

        Each edge dict must include its ``id`` for conflict resolution.

        Args:
            project_id: UUID of the owning project (injected into every row).
            edges: List of edge dicts.

        Returns:
            List of upserted edge row dicts.
        """
        if not edges:
            return []
        try:
            client = await self._get_client()
            rows = [{**edge, "project_id": project_id} for edge in edges]
            result = (
                await client.table(self.TABLE_NAME)
                .upsert(rows, on_conflict="id")
                .execute()
            )
            logger.info(f"Bulk-upserted {len(rows)} edges for project {project_id}")
            return result.data or []
        except Exception as e:
            logger.error(f"Failed to bulk-upsert edges for project {project_id}: {e}")
            raise

    async def delete(self, edge_id: str) -> None:
        """
        Hard-delete a single edge.

        Args:
            edge_id: UUID of the edge.
        """
        try:
            client = await self._get_client()
            await client.table(self.TABLE_NAME).delete().eq("id", edge_id).execute()
            logger.info(f"Deleted storyboard edge {edge_id}")
        except Exception as e:
            logger.error(f"Failed to delete storyboard edge {edge_id}: {e}")
            raise

    async def delete_by_project(self, project_id: str) -> None:
        """
        Delete all edges belonging to a project.

        Args:
            project_id: UUID of the project.
        """
        try:
            client = await self._get_client()
            await (
                client.table(self.TABLE_NAME)
                .delete()
                .eq("project_id", project_id)
                .execute()
            )
            logger.info(f"Deleted all edges for project {project_id}")
        except Exception as e:
            logger.error(f"Failed to delete edges for project {project_id}: {e}")
            raise

    async def get_by_project(self, project_id: str) -> List[Dict[str, Any]]:
        """
        Fetch all edges for a project.

        Args:
            project_id: UUID of the project.

        Returns:
            List of edge row dicts.
        """
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_NAME)
                .select("*")
                .eq("project_id", project_id)
                .execute()
            )
            return result.data or []
        except Exception as e:
            logger.error(f"Failed to get edges for project {project_id}: {e}")
            return []


# --------------------------------------------------------------------------- #
# 4. StoryboardFrameRepository
# --------------------------------------------------------------------------- #


class StoryboardFrameRepository:
    """Bulk upsert, reorder, and read operations for storyboard_frames."""

    TABLE_NAME = "storyboard_frames"

    async def _get_client(self):
        return await get_async_supabase_admin()

    async def create(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Insert a single frame record.

        Args:
            data: Column values for the new row.

        Returns:
            Created frame row dict.
        """
        try:
            client = await self._get_client()
            result = await client.table(self.TABLE_NAME).insert(data).execute()
            logger.info(f"Created storyboard frame: index={data.get('frame_index')}")
            return result.data[0] if result.data else {}
        except Exception as e:
            logger.error(f"Failed to create storyboard frame: {e}")
            raise

    async def bulk_upsert(
        self, node_id: str, frames: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Insert or update multiple frames for a node atomically.

        Each frame dict must include its ``id`` for conflict resolution.

        Args:
            node_id: UUID of the parent node (injected into every row).
            frames: List of frame dicts.

        Returns:
            List of upserted frame row dicts.
        """
        if not frames:
            return []
        try:
            client = await self._get_client()
            rows = [{**frame, "node_id": node_id} for frame in frames]
            result = (
                await client.table(self.TABLE_NAME)
                .upsert(rows, on_conflict="id")
                .execute()
            )
            logger.info(f"Bulk-upserted {len(rows)} frames for node {node_id}")
            return result.data or []
        except Exception as e:
            logger.error(f"Failed to bulk-upsert frames for node {node_id}: {e}")
            raise

    async def update(self, frame_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Update a single frame.

        Args:
            frame_id: UUID of the frame.
            data: Fields to update.

        Returns:
            Updated frame row dict.
        """
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_NAME)
                .update(data)
                .eq("id", frame_id)
                .execute()
            )
            logger.info(f"Updated storyboard frame {frame_id}")
            return result.data[0] if result.data else {}
        except Exception as e:
            logger.error(f"Failed to update storyboard frame {frame_id}: {e}")
            raise

    async def reorder(self, frame_ids: List[str]) -> None:
        """
        Reorder frames by assigning sort_order based on list position.

        The first element gets ``sort_order=0``, the second ``1``, etc.
        Each frame is updated individually to preserve atomicity per row.

        Args:
            frame_ids: Ordered list of frame UUIDs.
        """
        if not frame_ids:
            return
        try:
            client = await self._get_client()
            # Single-round-trip batch reorder via Postgres RPC (migration 122).
            # Fallback to per-row updates if the RPC is unavailable (e.g. prior
            # to migration rollout) so behavior stays correct, just slower.
            try:
                await client.rpc(
                    "rpc_reorder_storyboard_frames",
                    {"p_frame_ids": list(frame_ids)},
                ).execute()
            except Exception as rpc_err:
                logger.warning(
                    f"Batch reorder RPC unavailable, falling back to per-row "
                    f"updates ({len(frame_ids)} frames): {rpc_err}"
                )
                for index, frame_id in enumerate(frame_ids):
                    await (
                        client.table(self.TABLE_NAME)
                        .update({"sort_order": index})
                        .eq("id", frame_id)
                        .execute()
                    )
            logger.info(f"Reordered {len(frame_ids)} storyboard frames")
        except Exception as e:
            logger.error(f"Failed to reorder storyboard frames: {e}")
            raise

    async def get_by_node(self, node_id: str) -> List[Dict[str, Any]]:
        """
        Fetch all frames for a node, ordered by sort_order.

        Args:
            node_id: UUID of the parent node.

        Returns:
            List of frame row dicts.
        """
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_NAME)
                .select("*")
                .eq("node_id", node_id)
                .order("sort_order")
                .execute()
            )
            return result.data or []
        except Exception as e:
            logger.error(f"Failed to get frames for node {node_id}: {e}")
            return []

    async def get_by_project(self, project_id: str) -> List[Dict[str, Any]]:
        """
        Fetch all frames for a project (via joined node), ordered by sort_order.

        Args:
            project_id: UUID of the project.

        Returns:
            List of frame row dicts.
        """
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_NAME)
                .select("*")
                .eq("project_id", project_id)
                .order("sort_order")
                .execute()
            )
            return result.data or []
        except Exception as e:
            logger.error(f"Failed to get frames for project {project_id}: {e}")
            return []


# --------------------------------------------------------------------------- #
# 5. StoryboardCharacterRepository
# --------------------------------------------------------------------------- #


class StoryboardCharacterRepository:
    """CRUD for storyboard_characters."""

    TABLE_NAME = "storyboard_characters"

    async def _get_client(self):
        return await get_async_supabase_admin()

    async def create(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Insert a new character.

        Args:
            data: Column values for the new row.

        Returns:
            Created character row dict.
        """
        try:
            client = await self._get_client()
            result = await client.table(self.TABLE_NAME).insert(data).execute()
            logger.info(f"Created storyboard character: {data.get('name')}")
            return result.data[0] if result.data else {}
        except Exception as e:
            logger.error(f"Failed to create storyboard character: {e}")
            raise

    async def update(self, character_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Update an existing character.

        Args:
            character_id: UUID of the character.
            data: Fields to update.

        Returns:
            Updated character row dict.
        """
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_NAME)
                .update(data)
                .eq("id", character_id)
                .execute()
            )
            logger.info(f"Updated storyboard character {character_id}")
            return result.data[0] if result.data else {}
        except Exception as e:
            logger.error(f"Failed to update storyboard character {character_id}: {e}")
            raise

    async def delete(self, character_id: str) -> None:
        """
        Hard-delete a character.

        Args:
            character_id: UUID of the character.
        """
        try:
            client = await self._get_client()
            await (
                client.table(self.TABLE_NAME).delete().eq("id", character_id).execute()
            )
            logger.info(f"Deleted storyboard character {character_id}")
        except Exception as e:
            logger.error(f"Failed to delete storyboard character {character_id}: {e}")
            raise

    async def get_by_id(self, character_id: str) -> Optional[Dict[str, Any]]:
        """
        Fetch a single character by UUID.

        Args:
            character_id: UUID of the character.

        Returns:
            Character row dict, or None if not found.
        """
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_NAME)
                .select("*")
                .eq("id", character_id)
                .limit(1)
                .execute()
            )
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error(f"Failed to get character {character_id}: {e}")
            return None

    async def list_by_project(self, project_id: str) -> List[Dict[str, Any]]:
        """
        Fetch all characters for a project.

        Args:
            project_id: UUID of the project.

        Returns:
            List of character row dicts.
        """
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_NAME)
                .select("*")
                .eq("project_id", project_id)
                .order("created_at")
                .execute()
            )
            return result.data or []
        except Exception as e:
            logger.error(f"Failed to list characters for project {project_id}: {e}")
            return []


# --------------------------------------------------------------------------- #
# 6. StoryboardAssetRepository
# --------------------------------------------------------------------------- #


class StoryboardAssetRepository:
    """Asset record management for storyboard_assets."""

    TABLE_NAME = "storyboard_assets"

    async def _get_client(self):
        return await get_async_supabase_admin()

    async def create(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Insert a new asset record.

        Args:
            data: Column values for the new row.

        Returns:
            Created asset row dict.
        """
        try:
            client = await self._get_client()
            result = await client.table(self.TABLE_NAME).insert(data).execute()
            logger.info(f"Created storyboard asset: {data.get('filename')}")
            return result.data[0] if result.data else {}
        except Exception as e:
            logger.error(f"Failed to create storyboard asset: {e}")
            raise

    async def find_by_hash(
        self, project_id: str, file_hash: str
    ) -> Optional[Dict[str, Any]]:
        """
        Look up an asset by its content hash within a project.

        Used for deduplication — if a file with the same hash already exists
        in the project, callers can reuse the existing storage URL.

        Args:
            project_id: UUID of the project scope.
            file_hash: Content hash (e.g. SHA-256 hex string).

        Returns:
            Asset row dict if found, otherwise ``None``.
        """
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_NAME)
                .select("*")
                .eq("project_id", project_id)
                .eq("file_hash", file_hash)
                .limit(1)
                .execute()
            )
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error(f"Failed to find asset by hash in project {project_id}: {e}")
            return None

    async def delete(self, asset_id: str) -> None:
        """
        Hard-delete an asset record.

        Args:
            asset_id: UUID of the asset.
        """
        try:
            client = await self._get_client()
            await client.table(self.TABLE_NAME).delete().eq("id", asset_id).execute()
            logger.info(f"Deleted storyboard asset {asset_id}")
        except Exception as e:
            logger.error(f"Failed to delete storyboard asset {asset_id}: {e}")
            raise

    async def list_by_project(self, project_id: str) -> List[Dict[str, Any]]:
        """
        Fetch all assets for a project, newest first.

        Args:
            project_id: UUID of the project.

        Returns:
            List of asset row dicts.
        """
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_NAME)
                .select("*")
                .eq("project_id", project_id)
                .order("created_at", desc=True)
                .execute()
            )
            return result.data or []
        except Exception as e:
            logger.error(f"Failed to list assets for project {project_id}: {e}")
            return []
