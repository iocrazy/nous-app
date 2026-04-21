# app/services/storyboard_service.py

"""
Storyboard Core Service

Business logic for the Storyboard Workbench: project CRUD, canvas sync,
and character management. Delegates data access to the repository layer.
"""

import asyncio
import hashlib
import os
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from loguru import logger

from app.repositories.storyboard_repository import (
    StoryboardAssetRepository,
    StoryboardCharacterRepository,
    StoryboardEdgeRepository,
    StoryboardFrameRepository,
    StoryboardNodeRepository,
    StoryboardProjectRepository,
)
from app.schemas.storyboard import CanvasSyncRequest

# NAS directory sub-structure created for every new storyboard project
_PROJECT_SUBDIRS = [
    "frames",
    "thumbnails",
    "characters",
    "videos",
    "exports",
    "temp",
    "splits",
]


class StoryboardService:
    """Orchestrates storyboard business logic across all sub-repositories."""

    def __init__(self) -> None:
        self.project_repo = StoryboardProjectRepository()
        self.node_repo = StoryboardNodeRepository()
        self.edge_repo = StoryboardEdgeRepository()
        self.frame_repo = StoryboardFrameRepository()
        self.character_repo = StoryboardCharacterRepository()
        self.asset_repo = StoryboardAssetRepository()

    # ------------------------------------------------------------------ #
    # Authorization
    # ------------------------------------------------------------------ #

    async def verify_project_access(self, project_id: str, user_id: str) -> None:
        """
        Verify that *user_id* belongs to the team that owns *project_id*.

        Raises:
            HTTPException(404): If the project does not exist.
            HTTPException(403): If the user is not a member of the project's team.
        """
        project = await self.project_repo.get_by_id(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")

        team_id = project.get("team_id")
        if not team_id:
            raise HTTPException(
                status_code=403, detail="Project has no team association"
            )

        from app.db.supabase_client import get_async_supabase_admin

        client = await get_async_supabase_admin()
        result = (
            await client.table("team_members")
            .select("team_id")
            .eq("team_id", team_id)
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
        if not result.data:
            raise HTTPException(
                status_code=403,
                detail="You do not have access to this project",
            )

    # ------------------------------------------------------------------ #
    # Project operations
    # ------------------------------------------------------------------ #

    async def create_project(
        self,
        team_id: str,
        user_id: str,
        name: str,
        description: Optional[str] = None,
        project_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Create a storyboard project and provision its NAS directory tree.

        Args:
            team_id: UUID of the owning team.
            user_id: UUID of the creating user.
            name: Human-readable project name.
            description: Optional project description.
            project_id: Optional parent project ID.

        Returns:
            Created project row dict.
        """
        try:
            project_data: Dict[str, Any] = {
                "team_id": team_id,
                "created_by": user_id,
                "name": name,
            }
            if description is not None:
                project_data["description"] = description
            if project_id is not None:
                project_data["project_id"] = project_id

            project = await self.project_repo.create(project_data)
            project_id = project.get("id", "")

            if project_id:
                self._ensure_nas_directories(team_id, project_id)

            logger.info(
                "Created storyboard project %s for team %s", project_id, team_id
            )
            return project
        except Exception as exc:
            logger.error("Failed to create storyboard project: %s", exc)
            raise

    def _ensure_nas_directories(self, team_id: str, project_id: str) -> None:
        """
        Create the NAS directory tree for a project if it does not exist.

        Args:
            team_id: UUID of the team (used as top-level folder).
            project_id: UUID of the project.
        """
        nas_base = os.environ.get("NAS_BASE_PATH", "/app/downloads")
        project_root = os.path.join(
            nas_base, "teams", str(team_id), "storyboard", str(project_id)
        )
        try:
            os.makedirs(project_root, exist_ok=True)
            for subdir in _PROJECT_SUBDIRS:
                os.makedirs(os.path.join(project_root, subdir), exist_ok=True)
            logger.info("Provisioned NAS directories at %s", project_root)
        except OSError as exc:
            logger.error(
                "Failed to create NAS directories for project %s: %s",
                project_id,
                exc,
            )

    async def get_project_full(self, project_id: str) -> Dict[str, Any]:
        """
        Fetch project details together with all canvas entities in parallel.

        Args:
            project_id: UUID of the project.

        Returns:
            Dict with keys: project, nodes, edges, frames, characters.
        """
        try:
            project, nodes, edges, frames, characters = await asyncio.gather(
                self.project_repo.get_by_id(project_id),
                self.node_repo.get_by_project(project_id),
                self.edge_repo.get_by_project(project_id),
                self.frame_repo.get_by_project(project_id),
                self.character_repo.list_by_project(project_id),
            )
            return {
                "project": project,
                "nodes": nodes,
                "edges": edges,
                "frames": frames,
                "characters": characters,
            }
        except Exception as exc:
            logger.error(
                "Failed to fetch full project data for %s: %s", project_id, exc
            )
            raise

    async def list_projects(
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
        Paginated list of active storyboard projects for a team.

        Deleted projects (status='deleted') are excluded by the repository.

        Args:
            team_id: UUID of the team.
            page: 1-based page number.
            limit: Rows per page.
            search: Optional name substring filter.
            sort_by: Column to sort by.
            sort_order: 'asc' or 'desc'.
            project_id: Optional parent project ID filter.

        Returns:
            Dict with keys: items, total, page, limit.
        """
        try:
            return await self.project_repo.list_by_team(
                team_id=team_id,
                page=page,
                limit=limit,
                search=search,
                sort_by=sort_by,
                sort_order=sort_order,
                project_id=project_id,
            )
        except Exception as exc:
            logger.error("Failed to list projects for team %s: %s", team_id, exc)
            raise

    async def update_project(
        self, project_id: str, data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Update a storyboard project's metadata.

        Args:
            project_id: UUID of the project.
            data: Fields to update.

        Returns:
            Updated project row dict.
        """
        try:
            return await self.project_repo.update(project_id, data)
        except Exception as exc:
            logger.error("Failed to update storyboard project %s: %s", project_id, exc)
            raise

    async def soft_delete_project(self, project_id: str) -> None:
        """
        Soft-delete a project by marking its status as 'deleted'.

        Args:
            project_id: UUID of the project.
        """
        try:
            await self.project_repo.soft_delete(project_id)
        except Exception as exc:
            logger.error(
                "Failed to soft-delete storyboard project %s: %s",
                project_id,
                exc,
            )
            raise

    async def update_viewport(
        self, project_id: str, viewport_json: Dict[str, Any]
    ) -> None:
        """
        Persist the canvas viewport state without touching updated_at.

        Args:
            project_id: UUID of the project.
            viewport_json: Serialisable viewport state (x, y, zoom, …).
        """
        try:
            await self.project_repo.update_viewport(project_id, viewport_json)
        except Exception as exc:
            logger.error(
                "Failed to update viewport for project %s: %s",
                project_id,
                exc,
            )
            raise

    # ------------------------------------------------------------------ #
    # Canvas sync
    # ------------------------------------------------------------------ #

    async def sync_canvas(
        self, project_id: str, sync_request: CanvasSyncRequest
    ) -> Dict[str, Any]:
        """
        Apply an incremental canvas sync: upsert/delete nodes and edges.

        Processing order:
          1. added_nodes   → bulk upsert
          2. updated_nodes → individual updates
          3. deleted_node_ids → individual deletes
          4. added_edges   → bulk upsert
          5. deleted_edge_ids → individual deletes

        Args:
            project_id: UUID of the canvas project.
            sync_request: Validated sync payload.

        Returns:
            Summary dict with counts for each operation.
        """
        added_nodes_count = 0
        updated_nodes_count = 0
        deleted_nodes_count = 0
        added_edges_count = 0
        deleted_edges_count = 0

        try:
            # --- nodes: add ---
            if sync_request.added_nodes:
                added_node_dicts: List[Dict[str, Any]] = [
                    node.model_dump() for node in sync_request.added_nodes
                ]
                await self.node_repo.bulk_upsert(project_id, added_node_dicts)
                added_nodes_count = len(added_node_dicts)

            # --- nodes: update ---
            if sync_request.updated_nodes:
                update_tasks = [
                    self.node_repo.update(node["id"], node)
                    for node in sync_request.updated_nodes
                    if "id" in node
                ]
                await asyncio.gather(*update_tasks)
                updated_nodes_count = len(update_tasks)

            # --- nodes: delete ---
            if sync_request.deleted_node_ids:
                delete_node_tasks = [
                    self.node_repo.delete(node_id)
                    for node_id in sync_request.deleted_node_ids
                ]
                await asyncio.gather(*delete_node_tasks)
                deleted_nodes_count = len(delete_node_tasks)

            # --- edges: add ---
            if sync_request.added_edges:
                await self.edge_repo.bulk_upsert(project_id, sync_request.added_edges)
                added_edges_count = len(sync_request.added_edges)

            # --- edges: delete ---
            if sync_request.deleted_edge_ids:
                delete_edge_tasks = [
                    self.edge_repo.delete(edge_id)
                    for edge_id in sync_request.deleted_edge_ids
                ]
                await asyncio.gather(*delete_edge_tasks)
                deleted_edges_count = len(delete_edge_tasks)

            logger.info(
                "Canvas sync for project %s: +%d nodes, ~%d nodes, -%d nodes, "
                "+%d edges, -%d edges",
                project_id,
                added_nodes_count,
                updated_nodes_count,
                deleted_nodes_count,
                added_edges_count,
                deleted_edges_count,
            )

            return {
                "added_nodes_count": added_nodes_count,
                "updated_nodes_count": updated_nodes_count,
                "deleted_nodes_count": deleted_nodes_count,
                "added_edges_count": added_edges_count,
                "deleted_edges_count": deleted_edges_count,
            }
        except Exception as exc:
            logger.error("Canvas sync failed for project %s: %s", project_id, exc)
            raise

    # ------------------------------------------------------------------ #
    # Character operations
    # ------------------------------------------------------------------ #

    async def create_character(
        self,
        project_id: str,
        name: str,
        description: Optional[str] = None,
        visual_traits: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Create a character entry for a storyboard project.

        Args:
            project_id: UUID of the owning project.
            name: Character display name.
            description: Optional narrative description.
            visual_traits: Optional dict of visual attributes (age, hair, etc.).

        Returns:
            Created character row dict.
        """
        try:
            character_data: Dict[str, Any] = {
                "project_id": project_id,
                "name": name,
            }
            if description is not None:
                character_data["description"] = description
            if visual_traits is not None:
                character_data["visual_traits"] = visual_traits

            character = await self.character_repo.create(character_data)
            logger.info("Created character '%s' for project %s", name, project_id)
            return character
        except Exception as exc:
            logger.error(
                "Failed to create character for project %s: %s",
                project_id,
                exc,
            )
            raise

    async def update_character(
        self, character_id: str, data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Update a character's attributes.

        Args:
            character_id: UUID of the character.
            data: Fields to update.

        Returns:
            Updated character row dict.
        """
        try:
            return await self.character_repo.update(character_id, data)
        except Exception as exc:
            logger.error("Failed to update character %s: %s", character_id, exc)
            raise

    async def delete_character(self, character_id: str) -> None:
        """
        Hard-delete a character.

        Args:
            character_id: UUID of the character.
        """
        try:
            await self.character_repo.delete(character_id)
        except Exception as exc:
            logger.error("Failed to delete character %s: %s", character_id, exc)
            raise

    async def get_character_prompt_fragment(self, character_id: str) -> str:
        """
        Build an AI prompt fragment that describes a character's visual traits.

        Trait keys used (all optional): age, body_type, hair, clothing.
        Example output: "a 30s athletic person with long brown hair wearing a red jacket"

        Args:
            character_id: UUID of the character.

        Returns:
            Prompt fragment string describing the character's appearance.

        Raises:
            ValueError: If the character cannot be found.
        """
        try:
            character = await self.character_repo.get_by_id(character_id)
        except Exception as exc:
            logger.error(
                "Failed to fetch character %s for prompt: %s",
                character_id,
                exc,
            )
            raise

        if not character:
            raise ValueError(f"Character {character_id} not found")

        visual_traits: Dict[str, Any] = character.get("visual_traits") or {}

        age = visual_traits.get("age", "")
        body_type = visual_traits.get("body_type", "")
        hair = visual_traits.get("hair", "")
        clothing = visual_traits.get("clothing", "")

        parts: List[str] = ["a"]
        if age:
            parts.append(str(age))
        if body_type:
            parts.append(str(body_type))
        parts.append("person")
        if hair:
            parts.append(f"with {hair}")
        if clothing:
            parts.append(f"wearing {clothing}")

        return " ".join(parts)

    # ------------------------------------------------------------------ #
    # Image upload
    # ------------------------------------------------------------------ #

    async def upload_image(
        self,
        project_id: str,
        file_bytes: bytes,
        filename: str,
        content_type: str,
        node_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Save an uploaded image to NAS with deduplication and preview generation.

        Steps:
          1. Compute SHA-256 hash of file bytes
          2. Check storyboard_assets for existing hash (dedup)
          3. If new: save original + generate 512px preview thumbnail
          4. Insert asset record into storyboard_assets
          5. Return asset metadata including URLs

        Args:
            project_id: UUID of the storyboard project.
            file_bytes: Raw file content.
            filename: Original filename (used to derive extension).
            content_type: MIME type of the file.
            node_id: Optional node ID (stored in metadata).

        Returns:
            Dict with image_url, preview_url, asset_id, width, height.
        """
        # 1. Compute hash
        file_hash = hashlib.sha256(file_bytes).hexdigest()

        # 2. Dedup check
        existing = await self.asset_repo.find_by_hash(project_id, file_hash)
        if existing:
            logger.info(
                "Dedup hit for hash %s in project %s, returning existing asset %s",
                file_hash[:12],
                project_id,
                existing["id"],
            )
            return self._build_upload_response(existing)

        # 3. Resolve project to get team_id for path
        project = await self.project_repo.get_by_id(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")

        team_id = str(project.get("team_id", "unknown"))
        nas_base = os.environ.get("NAS_BASE_PATH", "/app/downloads")

        # Derive extension from filename
        ext = Path(filename).suffix.lower()
        if not ext:
            ext = _mime_to_ext(content_type)

        # Build relative paths (stored in DB, relative to NAS_BASE_PATH)
        rel_dir = f"teams/{team_id}/storyboard/{str(project_id)}/images"
        rel_preview_dir = f"teams/{team_id}/storyboard/{str(project_id)}/previews"
        rel_image_path = f"{rel_dir}/{file_hash}{ext}"
        rel_preview_path = f"{rel_preview_dir}/{file_hash}.jpg"

        abs_image_path = Path(nas_base) / rel_image_path
        abs_preview_path = Path(nas_base) / rel_preview_path

        # Create directories
        abs_image_path.parent.mkdir(parents=True, exist_ok=True)
        abs_preview_path.parent.mkdir(parents=True, exist_ok=True)

        # Save original
        abs_image_path.write_bytes(file_bytes)

        # Get dimensions and generate preview
        width, height = 0, 0
        try:
            from PIL import Image

            img = Image.open(BytesIO(file_bytes))
            width, height = img.size

            # Generate preview thumbnail (max 512px on longest side)
            img_rgb = img.convert("RGB")
            scale = min(512 / max(width, 1), 512 / max(height, 1), 1.0)
            if scale < 1.0:
                new_w = max(1, int(width * scale))
                new_h = max(1, int(height * scale))
                img_rgb = img_rgb.resize((new_w, new_h), Image.LANCZOS)
            img_rgb.save(str(abs_preview_path), format="JPEG", quality=85)
        except Exception as exc:
            logger.warning(
                "Failed to process image / generate preview for %s: %s",
                filename,
                exc,
            )
            # Still proceed — preview just won't be available
            rel_preview_path = ""

        # 4. Insert asset record
        metadata: Dict[str, Any] = {"original_filename": filename}
        if node_id:
            metadata["node_id"] = node_id

        asset_data: Dict[str, Any] = {
            "project_id": project_id,
            "file_path": rel_image_path,
            "file_hash": file_hash,
            "file_size": len(file_bytes),
            "mime_type": content_type,
            "width": width,
            "height": height,
            "preview_path": rel_preview_path if rel_preview_path else None,
            "metadata_json": metadata,
            "source_type": "uploaded",
        }

        asset = await self.asset_repo.create(asset_data)
        logger.info(
            "Uploaded image asset %s for project %s (hash=%s, %dx%d)",
            asset.get("id"),
            project_id,
            file_hash[:12],
            width,
            height,
        )

        return self._build_upload_response(asset)

    def _build_upload_response(self, asset: Dict[str, Any]) -> Dict[str, Any]:
        """Build the upload response dict from an asset record."""
        asset_id = str(asset["id"])
        return {
            "asset_id": asset_id,
            "image_url": f"/api/v1/storyboard/assets/{asset_id}/file",
            "preview_url": (
                f"/api/v1/storyboard/assets/{asset_id}/file?preview=true"
                if asset.get("preview_path")
                else f"/api/v1/storyboard/assets/{asset_id}/file"
            ),
            "width": asset.get("width", 0),
            "height": asset.get("height", 0),
            "file_hash": asset.get("file_hash", ""),
        }

    async def split_image_asset(
        self,
        project_id: str,
        asset_id: str,
        rows: int,
        cols: int,
        node_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Split an uploaded image asset into a grid of frames.

        For each cell in the grid:
          1. Crop and save as a separate image + preview
          2. Create a storyboard_asset record
          3. Create a storyboard_frame record

        Args:
            project_id: UUID of the storyboard project.
            asset_id: UUID of the source asset to split.
            rows: Number of rows in the grid.
            cols: Number of columns in the grid.
            node_id: Optional UUID of the canvas node to attach frames to.

        Returns:
            Dict with keys: frames (list of frame records), source_asset_id,
            rows, cols.
        """
        from app.services.storyboard_image_service import StoryboardImageService

        # Validate grid dimensions
        if rows < 1 or rows > 10:
            raise HTTPException(status_code=422, detail="rows must be between 1 and 10")
        if cols < 1 or cols > 10:
            raise HTTPException(status_code=422, detail="cols must be between 1 and 10")

        # 1. Get source asset
        source_asset = await self.get_asset(asset_id)
        if not source_asset:
            raise HTTPException(status_code=404, detail="Source asset not found")

        if str(source_asset.get("project_id")) != str(project_id):
            raise HTTPException(
                status_code=403, detail="Asset does not belong to this project"
            )

        # 2. Resolve paths
        project = await self.project_repo.get_by_id(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")

        team_id = str(project.get("team_id", "unknown"))
        nas_base = os.environ.get("NAS_BASE_PATH", "/app/downloads")

        source_file_path = Path(nas_base) / source_asset["file_path"]
        if not source_file_path.exists():
            raise HTTPException(
                status_code=404, detail="Source image file not found on disk"
            )

        # Compute a hash prefix for output filenames
        source_hash = source_asset.get("file_hash", "unknown")[:12]

        # Output directories (relative to NAS_BASE_PATH)
        rel_split_dir = f"teams/{team_id}/storyboard/{project_id}/splits"
        rel_preview_dir = f"teams/{team_id}/storyboard/{project_id}/previews"
        abs_split_dir = Path(nas_base) / rel_split_dir
        abs_preview_dir = Path(nas_base) / rel_preview_dir

        # 3. Split the image
        image_service = StoryboardImageService()
        try:
            cells = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: image_service.split_image_to_grid(
                    image_path=str(source_file_path),
                    rows=rows,
                    cols=cols,
                    output_dir=str(abs_split_dir),
                    preview_dir=str(abs_preview_dir),
                    file_prefix=source_hash,
                ),
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        except Exception as exc:
            logger.error(
                "split_image_asset: image processing failed for asset %s: %s",
                asset_id,
                exc,
            )
            raise HTTPException(
                status_code=500, detail=f"Image splitting failed: {exc}"
            )

        # 4. Create asset + frame records for each cell
        created_frames: List[Dict[str, Any]] = []

        for cell in cells:
            # Compute relative paths (stored in DB)
            cell_abs_path = Path(cell["file_path"])
            cell_rel_path = str(cell_abs_path.relative_to(nas_base))
            preview_abs_path = Path(cell["preview_path"])
            preview_rel_path = str(preview_abs_path.relative_to(nas_base))

            # Compute hash for the cell image
            cell_hash = hashlib.sha256(cell_abs_path.read_bytes()).hexdigest()

            # Create asset record
            cell_asset_data: Dict[str, Any] = {
                "project_id": project_id,
                "file_path": cell_rel_path,
                "file_hash": cell_hash,
                "file_size": cell_abs_path.stat().st_size,
                "mime_type": "image/png",
                "width": cell["width"],
                "height": cell["height"],
                "preview_path": preview_rel_path,
                "metadata_json": {
                    "source_asset_id": asset_id,
                    "split_row": cell["row"],
                    "split_col": cell["col"],
                    "grid_rows": rows,
                    "grid_cols": cols,
                },
                "source_type": "split",
            }
            cell_asset = await self.asset_repo.create(cell_asset_data)
            cell_asset_id = str(cell_asset.get("id", ""))

            # Build image URLs
            image_url = f"/api/v1/storyboard/assets/{cell_asset_id}/file"
            preview_url = f"/api/v1/storyboard/assets/{cell_asset_id}/file?preview=true"

            # Create frame record
            frame_data: Dict[str, Any] = {
                "project_id": project_id,
                "frame_index": cell["index"],
                "image_url": image_url,
                "thumbnail_url": preview_url,
                "sort_order": cell["index"],
                "note": "",
                "duration_seconds": 2.0,
                "transition_type": "cut",
            }
            if node_id:
                frame_data["node_id"] = node_id

            frame = await self.frame_repo.create(frame_data)

            created_frames.append(
                {
                    **frame,
                    "asset_id": cell_asset_id,
                    "image_url": image_url,
                    "preview_url": preview_url,
                    "width": cell["width"],
                    "height": cell["height"],
                    "row": cell["row"],
                    "col": cell["col"],
                }
            )

        logger.info(
            "split_image_asset: created %d frames from asset %s in project %s (%dx%d grid)",
            len(created_frames),
            asset_id,
            project_id,
            rows,
            cols,
        )

        return {
            "frames": created_frames,
            "source_asset_id": asset_id,
            "rows": rows,
            "cols": cols,
        }

    async def get_asset(self, asset_id: str) -> Optional[Dict[str, Any]]:
        """
        Fetch a single asset by ID.

        Args:
            asset_id: Snowflake ID of the asset.

        Returns:
            Asset row dict, or None if not found.
        """
        try:
            client = await self.asset_repo._get_client()
            result = (
                await client.table("storyboard_assets")
                .select("*")
                .eq("id", asset_id)
                .limit(1)
                .execute()
            )
            return result.data[0] if result.data else None
        except Exception as exc:
            logger.error("Failed to get asset %s: %s", asset_id, exc)
            return None


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

_MIME_EXT_MAP = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
    "image/tiff": ".tiff",
}


def _mime_to_ext(mime_type: str) -> str:
    """Convert a MIME type to a file extension, defaulting to .png."""
    return _MIME_EXT_MAP.get(mime_type, ".png")
