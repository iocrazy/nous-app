# app/services/projects_service.py

"""
Projects Service

Business logic for the MediaTrack project system: project CRUD,
file uploads with metadata extraction (ffprobe), and video linking.
"""

import asyncio
import json
import mimetypes
import uuid as _uuid
from pathlib import Path
from typing import Optional

from loguru import logger

from app.agent_framework.process_lifecycle import safe_popen_kwargs
from app.core.config import settings
from app.core.file_utils import (
    MAX_UPLOAD_SIZE,
    sanitize_filename,
    sniff_mime,
    stream_upload_to_disk,
)
from app.repositories.projects_repository import get_projects_repository
from app.services.infra.dbos_orchestrator import start_workflow_routed
from app.services.infra.unified_task_manager import get_task_manager

# Card enrichment defaults when a project has no stage/members/history rows
# (or the batch lookups failed) — the frontend renders the base card.
_EMPTY_ENRICHMENT = {
    "current_stage": None,
    "members_preview": None,
    "latest_activity": None,
}

# Stage suggestion resolver: storyboard is the only data-aware + one-click
# stage; every other SOP stage is data-aware + navigation only.
_STORYBOARD_STAGE = "storyboard"

# Tab each non-storyboard stage's nav CTA targets.
_STAGE_NAV_TAB = {
    "planning": "scripts",
    "script": "scripts",
    "generation": "output",
    "review": "files",
    "delivery": "output",
}


class ProjectsService:
    """MediaTrack projects business logic"""

    def __init__(self):
        self.repo = get_projects_repository()

    # ------------------------------------------------------------------ #
    # Projects
    # ------------------------------------------------------------------ #

    async def get_projects_with_counts(
        self,
        user_id: str,
        team_id: str | None = None,
        project_type: str | None = None,
        starred: bool | None = None,
        archived: bool | None = False,
    ) -> list:
        """
        Get projects for a user with file counts attached. All filters push
        down to the repo's SQL query (no more in-memory filtering here).

        Args:
            user_id: UUID of the authenticated user.
            team_id: If provided, filter by team. If None, return all.
            project_type: Optional project_type filter.
            starred: Optional is_starred filter.
            archived: False (default) = active only, True = archived only,
                None = both.

        Returns:
            List of project dicts, each with a ``file_count`` key.
        """
        projects = await self.repo.get_user_projects(
            user_id,
            team_id=team_id,
            project_type=project_type,
            starred=starred,
            archived=archived,
        )
        if not projects:
            return []

        ids = [p["id"] for p in projects]
        counts = await self.repo.get_project_file_counts(ids)
        enrichment = await self._get_card_enrichment(ids)
        return [
            {
                **p,
                "file_count": counts.get(str(p["id"]), 0),
                **enrichment.get(str(p["id"]), _EMPTY_ENRICHMENT),
            }
            for p in projects
        ]

    async def _get_card_enrichment(self, project_ids: list) -> dict:
        """Stage / members / activity card data for the list page (B1).

        Three batch queries (stage join, latest history, member preview) run
        concurrently — same no-N+1 contract as file counts. Each is
        best-effort (returns {} on failure), so a broken enrichment degrades
        the cards, never the list. Shapes:

          current_stage:   {slug, name, index, total} | None
            index/total derive from the stage catalog's sort_order ranking —
            the card ring renders index-of-total without knowing sort_order.
          members_preview: {count, members: [{user_id, username}, ...]} | None
          latest_activity: {stage_name, actor, entered_at} | None
        """
        from app.repositories.project_stages_repository import (
            get_project_stages_repository,
        )

        stages_repo = get_project_stages_repository()
        try:
            stage_map, activity, members, catalog = await asyncio.gather(
                stages_repo.stages_for_projects(project_ids),
                stages_repo.latest_activity_for_projects(project_ids),
                self.repo.get_project_members_preview(project_ids),
                stages_repo.list_catalog(),
            )
        except Exception as e:  # noqa: BLE001 — enrichment must not sink the list
            logger.error(f"[projects] card enrichment failed: {e}")
            return {}

        order = [s["sort_order"] for s in catalog]
        total = len(order)

        out: dict = {}
        for pid in [str(p) for p in project_ids]:
            stage = stage_map.get(pid)
            current_stage = None
            if stage is not None:
                try:
                    index = order.index(stage["sort_order"]) + 1
                except ValueError:
                    index = 0
                current_stage = {
                    "slug": stage["slug"],
                    "name": stage["name"],
                    "index": index,
                    "total": total,
                }
            out[pid] = {
                "current_stage": current_stage,
                "members_preview": members.get(pid),
                "latest_activity": activity.get(pid),
            }
        return out

    async def create_project(self, user_id: str, data: dict) -> dict:
        """
        Create a new project owned by the given user.

        Args:
            user_id: UUID of the authenticated user.
            data: Project creation data.

        Returns:
            Created project dict.
        """
        data["owner_id"] = user_id
        project = await self.repo.create_project(data)

        # Generate display code if project belongs to a team
        team_id = project.get("team_id")
        if team_id:
            try:
                from app.services.library.display_code_service import (
                    generate_display_code,
                )

                display_code = await generate_display_code(int(team_id), "P")
                project = await self.repo.update_project(
                    project["id"], {"display_code": display_code}
                )
            except Exception as exc:
                logger.warning(f"Failed to generate display_code: {exc}")

        return project

    async def update_project(self, project_id: str, user_id: str, data: dict) -> dict:
        """
        Update a project.

        Ownership/membership is enforced by the route-level write guard.

        Args:
            project_id: UUID of the project.
            user_id: UUID of the authenticated user.
            data: Fields to update.

        Returns:
            Updated project dict.

        Raises:
            ValueError: If project not found.
        """
        project = await self.repo.get_project_by_id(project_id)
        if not project:
            raise ValueError("Project not found")
        if "archived" in data:
            from datetime import datetime, timezone

            data = {**data}
            data["archived_at"] = (
                datetime.now(timezone.utc) if data.pop("archived") else None
            )
        return await self.repo.update_project(project_id, data)

    async def delete_project(self, project_id: str, user_id: str) -> bool:
        """
        Delete a project after verifying ownership.

        Args:
            project_id: UUID of the project.
            user_id: UUID of the authenticated user.

        Returns:
            True if deleted.

        Raises:
            ValueError: If project not found.
            PermissionError: If user is not the owner.
        """
        project = await self.repo.get_project_by_id(project_id)
        if not project:
            raise ValueError("Project not found")
        if project["owner_id"] != user_id:
            raise PermissionError("Only the project owner can delete this project")
        return await self.repo.delete_project(project_id)

    # ------------------------------------------------------------------ #
    # File uploads
    # ------------------------------------------------------------------ #

    async def upload_file(
        self,
        project_id: str,
        user_id: str,
        file,
        notes: Optional[str] = None,
    ) -> dict:
        """
        Upload a file to a project, extracting metadata for videos.

        Steps:
        1. Validate project exists
        2. Save file to disk under DOWNLOAD_PATH/mediatrack/{project_id}/
        3. Classify file type from MIME
        4. Extract video metadata via ffprobe (if applicable)
        5. Create DB record

        Args:
            project_id: UUID of the project.
            user_id: UUID of the authenticated user.
            file: FastAPI UploadFile instance.
            notes: Optional notes for the file.

        Returns:
            Created file dict.

        Raises:
            ValueError: If project not found.
        """
        project = await self.repo.get_project_by_id(project_id)
        if not project:
            raise ValueError("Project not found")

        # Save to disk
        safe_name = sanitize_filename(file.filename)
        save_dir = Path(settings.DOWNLOAD_PATH) / "mediatrack" / project_id
        save_dir.mkdir(parents=True, exist_ok=True)

        # Handle duplicate filenames
        target = save_dir / safe_name
        counter = 1
        stem = target.stem
        suffix = target.suffix
        while target.exists():
            target = save_dir / f"{stem}_{counter}{suffix}"
            counter += 1

        file_size, _ = await stream_upload_to_disk(file, target, MAX_UPLOAD_SIZE)

        # Classify — sniff real content type first so a forged Content-Type
        # cannot mislabel a binary as media.
        mime = (
            sniff_mime(target)
            or file.content_type
            or mimetypes.guess_type(safe_name)[0]
            or ""
        )
        file_type = self._classify_file_type(mime)

        # Extract video metadata
        metadata = {}
        if file_type == "video":
            metadata = await self._extract_video_metadata(str(target))

        # Create DB record
        relative_path = f"mediatrack/{project_id}/{target.name}"
        file_data = {
            "project_id": project_id,
            "filename": target.name,
            "file_type": file_type,
            "mime_type": mime,
            "file_path": relative_path,
            "file_size_bytes": file_size,
            "uploaded_by": user_id,
            "notes": notes,
            **metadata,
        }
        created_file = await self.repo.create_file(file_data)

        # Create V1 version record
        version_data = {
            "file_id": created_file["id"],
            "version_number": 1,
            "filename": target.name,
            "file_path": relative_path,
            "file_size_bytes": file_size,
            "mime_type": mime,
            "uploaded_by": user_id,
            "notes": notes,
            **metadata,
        }
        await self.repo.create_version(version_data)

        return created_file

    # ------------------------------------------------------------------ #
    # Link media
    # ------------------------------------------------------------------ #

    async def link_media(self, project_id: str, media_id: str, user_id: str) -> dict:
        """
        Link an existing media from the parsed_media table to a project.

        Args:
            project_id: UUID of the project.
            media_id: UUID of the media.
            user_id: UUID of the authenticated user.

        Returns:
            Created file dict.

        Raises:
            ValueError: If project or media not found.
            PermissionError: If the media is owned by a different user.
        """
        project = await self.repo.get_project_by_id(project_id)
        if not project:
            raise ValueError("Project not found")

        media = await self.repo.get_media_metadata(media_id)
        if not media:
            raise ValueError("Media not found")

        # parsed_media has no ownership column (dropped in migration 083);
        # ownership lives on resources.creator_id via resources.media_id.
        # None → allow: orphan/system media without a resource row passes.
        media_creator = await self.repo.get_media_creator(media_id)
        if media_creator and media_creator != str(user_id):
            raise PermissionError("You do not have access to this media item")

        file_data = {
            "project_id": project_id,
            "filename": media.get("title", "Untitled") or "Untitled",
            "file_type": "video",
            "mime_type": "video/mp4",
            "file_path": media.get("download_path"),
            "file_size_bytes": media.get("datasize_bytes"),
            "media_id": media_id,
            "duration_seconds": (
                int(media["duration"]) if media.get("duration") else None
            ),
            "resolution": media.get("resolution"),
            "uploaded_by": user_id,
            "cover_image_path": media.get("cover_download_path"),
        }
        return await self.repo.create_file(file_data)

    # ------------------------------------------------------------------ #
    # File versions
    # ------------------------------------------------------------------ #

    async def upload_new_version(
        self,
        project_id: str,
        file_id: str,
        user_id: str,
        file,
        notes: Optional[str] = None,
    ) -> dict:
        """
        Upload a new version of an existing file.

        Steps:
        1. Validate project + file
        2. Get next version number
        3. Save file to disk
        4. Extract metadata if video
        5. Create version record
        6. Update current_version + metadata on project_files
        """
        project = await self.repo.get_project_by_id(project_id)
        if not project:
            raise ValueError("Project not found")

        # Delegates the ownership check (incl. the int/str project_id
        # coercion — the 5.3 trap) to the single shared gate.
        await self._verify_file_in_project(project_id, file_id)

        # Get next version number
        next_version = await self.repo.get_next_version_number(file_id)

        # Save to disk
        safe_name = sanitize_filename(file.filename)
        save_dir = (
            Path(settings.DOWNLOAD_PATH)
            / "mediatrack"
            / project_id
            / "versions"
            / file_id
        )
        save_dir.mkdir(parents=True, exist_ok=True)

        target = save_dir / f"v{next_version}_{safe_name}"
        file_size, _ = await stream_upload_to_disk(file, target, MAX_UPLOAD_SIZE)

        # Classify and extract metadata — sniff real content type first.
        mime = (
            sniff_mime(target)
            or file.content_type
            or mimetypes.guess_type(safe_name)[0]
            or ""
        )
        file_type = self._classify_file_type(mime)
        metadata = {}
        if file_type == "video":
            metadata = await self._extract_video_metadata(str(target))

        # Create version record
        relative_path = (
            f"mediatrack/{project_id}/versions/{file_id}/v{next_version}_{safe_name}"
        )
        version_data = {
            "file_id": file_id,
            "version_number": next_version,
            "filename": safe_name,
            "file_path": relative_path,
            "file_size_bytes": file_size,
            "mime_type": mime,
            "uploaded_by": user_id,
            "notes": notes,
            **metadata,
        }
        version = await self.repo.create_version(version_data)

        # Update project_files with current version and latest metadata
        update_data = {
            "current_version": next_version,
            "file_path": relative_path,
            "file_size_bytes": file_size,
            "mime_type": mime,
            "filename": safe_name,
            **metadata,
        }
        await self.repo.update_file(file_id, update_data)

        return version

    # ------------------------------------------------------------------ #
    # Review comments
    # ------------------------------------------------------------------ #

    async def add_comment(
        self,
        project_id: str,
        file_id: str,
        author_id: str,
        content: str,
        timestamp_seconds: Optional[float] = None,
        version_id: Optional[str] = None,
        drawing_data: Optional[dict] = None,
    ) -> dict:
        """Add a review comment to a file after verifying project ownership."""
        await self._verify_file_in_project(project_id, file_id)
        comment_data = {
            "file_id": file_id,
            "author_id": author_id,
            "content": content,
        }
        if timestamp_seconds is not None:
            comment_data["timestamp_seconds"] = timestamp_seconds
        if version_id:
            comment_data["version_id"] = version_id
        if drawing_data:
            comment_data["drawing_data"] = drawing_data
        return await self.repo.create_comment(comment_data)

    # ------------------------------------------------------------------ #
    # File read / update / delete
    # ------------------------------------------------------------------ #

    async def _verify_file_in_project(self, project_id: str, file_id: str) -> dict:
        """Verify a file exists and belongs to the project. Returns the file."""
        file_record = await self.repo.get_file_by_id(file_id)
        if not file_record:
            raise ValueError("File not found")
        # project_id on the row is a NATIVE int post-ORM (bigint ids stay
        # native — the 5.3 trap), while the router path param is a str.
        # Coerce both sides or the guard rejects EVERY call.
        if str(file_record.get("project_id")) != str(project_id):
            raise ValueError("File not found in this project")
        return file_record

    async def get_project(self, project_id: str) -> dict:
        """Get a single project with file count."""
        project = await self.repo.get_project_by_id(project_id)
        if not project:
            raise ValueError("Project not found")
        project["file_count"] = await self.repo.get_project_file_count(project_id)
        return project

    async def get_project_files(
        self,
        project_id: str,
        include_trashed: bool = False,
        folder_id: Optional[str] = None,
    ) -> list:
        """List files in a project, optionally filtered by folder."""
        project = await self.repo.get_project_by_id(project_id)
        if not project:
            raise ValueError("Project not found")
        files = await self.repo.get_project_files(
            project_id, include_trashed=include_trashed
        )
        if not include_trashed:
            if folder_id is not None:
                files = [f for f in files if f.get("folder_id") == folder_id]
            else:
                files = [f for f in files if not f.get("folder_id")]
        return files

    async def get_file_info(self, project_id: str, file_id: str) -> dict:
        """Get detailed info for a single file."""
        return await self._verify_file_in_project(project_id, file_id)

    async def update_file(self, project_id: str, file_id: str, data: dict) -> dict:
        """Update file metadata (rename, notes, trash/restore)."""
        await self._verify_file_in_project(project_id, file_id)
        if data.get("is_trashed") is True:
            from datetime import datetime, timezone

            data["trashed_at"] = datetime.now(timezone.utc).isoformat()
        elif data.get("is_trashed") is False:
            data["trashed_at"] = None
        return await self.repo.update_file(file_id, data)

    async def restore_file(self, project_id: str, file_id: str) -> dict:
        """Restore a trashed file."""
        await self._verify_file_in_project(project_id, file_id)
        return await self.repo.update_file(
            file_id, {"is_trashed": False, "trashed_at": None}
        )

    async def move_file(
        self, project_id: str, file_id: str, folder_id: Optional[str]
    ) -> dict:
        """Move a file to a different folder."""
        await self._verify_file_in_project(project_id, file_id)
        return await self.repo.update_file(file_id, {"folder_id": folder_id})

    async def delete_file(self, project_id: str, file_id: str) -> bool:
        """Permanently delete a file record."""
        await self._verify_file_in_project(project_id, file_id)
        return await self.repo.delete_file(file_id)

    async def get_file_versions(self, project_id: str, file_id: str) -> list:
        """List all versions of a file."""
        await self._verify_file_in_project(project_id, file_id)
        return await self.repo.get_file_versions(file_id)

    async def get_file_comments(
        self,
        project_id: str,
        file_id: str,
        version_id: Optional[str] = None,
    ) -> list:
        """List comments on a file."""
        await self._verify_file_in_project(project_id, file_id)
        return await self.repo.get_comments_for_file(file_id, version_id=version_id)

    async def delete_comment(self, comment_id: str, user_id: str) -> bool:
        """Delete a comment. Only the author can delete."""
        comment = await self.repo.get_comment_by_id(comment_id)
        if not comment:
            raise ValueError("Comment not found")
        if comment.get("author_id") != user_id:
            raise PermissionError("Can only delete your own comments")
        return await self.repo.delete_comment(comment_id)

    # ------------------------------------------------------------------ #
    # Folders
    # ------------------------------------------------------------------ #

    async def list_folders(
        self, project_id: str, parent_id: Optional[str] = None
    ) -> list:
        """List folders in a project."""
        return await self.repo.get_folders(project_id, parent_id)

    async def create_folder(
        self,
        project_id: str,
        name: str,
        user_id: str,
        parent_id: Optional[str] = None,
    ) -> dict:
        """Create a new folder in a project."""
        data: dict = {
            "project_id": project_id,
            "name": name,
            "created_by": user_id,
        }
        if parent_id:
            data["parent_id"] = parent_id
        return await self.repo.create_folder(data)

    async def rename_folder(self, project_id: str, folder_id: str, name: str) -> dict:
        """Rename a folder."""
        result = await self.repo.update_folder(folder_id, project_id, {"name": name})
        if not result:
            raise ValueError("Folder not found")
        return result

    async def delete_folder(self, project_id: str, folder_id: str) -> bool:
        """Delete a folder, reparenting its children to the parent folder."""
        folder = await self.repo.get_folder(folder_id, project_id)
        if not folder:
            raise ValueError("Folder not found")
        parent_id = folder.get("parent_id")
        await self.repo.reparent_folder_children(folder_id, parent_id)
        return await self.repo.delete_folder_record(folder_id, project_id)

    # ------------------------------------------------------------------ #
    # Shares
    # ------------------------------------------------------------------ #

    async def list_shares(self, project_id: str) -> list:
        """List all shares for files in a project."""
        return await self.repo.get_shares_by_project(project_id)

    async def create_share(self, project_id: str, data: dict, user_id: str) -> dict:
        """Create a share link for a project file."""
        import secrets
        from datetime import datetime, timedelta, timezone

        file_record = await self.repo.get_file_in_project(data["file_id"], project_id)
        if not file_record:
            raise ValueError("File not found in project")

        share_code = secrets.token_urlsafe(8)[:12]
        share_name = data.get("share_name") or file_record["filename"]

        share_data: dict = {
            "project_file_id": data["file_id"],
            "share_type": data.get("share_type", "link"),
            "shared_by": user_id,
            "share_name": share_name,
            "share_code": share_code,
            "password": data.get("password"),
            "allow_download": data.get("allow_download", True),
            "status": "active",
        }
        if data.get("expires_hours"):
            share_data["expires_at"] = (
                datetime.now(timezone.utc) + timedelta(hours=data["expires_hours"])
            ).isoformat()

        return await self.repo.create_share(share_data)

    # ------------------------------------------------------------------ #
    # Members
    # ------------------------------------------------------------------ #

    async def list_members(self, project_id: str) -> list:
        """List all members of a project, enriched with email."""
        members = await self.repo.get_members(project_id)
        return await self.repo.enrich_members_with_email(members)

    async def add_member(
        self,
        project_id: str,
        user_id: str,
        role: str,
        invited_by: str,
    ) -> dict:
        """Add a member to a project."""
        data = {
            "project_id": project_id,
            "user_id": user_id,
            "role": role,
            "invited_by": invited_by,
        }
        member = await self.repo.create_member(data)
        member["email"] = await self.repo.get_user_email(user_id)
        return member

    async def update_member_role(
        self, project_id: str, member_id: str, role: str
    ) -> dict:
        """Update a member's role."""
        result = await self.repo.update_member(member_id, project_id, {"role": role})
        if not result:
            raise ValueError("Member not found")
        return result

    async def remove_member(self, project_id: str, member_id: str) -> bool:
        """Remove a member from a project."""
        return await self.repo.delete_member(member_id, project_id)

    # ------------------------------------------------------------------ #
    # Collections
    # ------------------------------------------------------------------ #

    async def list_collections(self, project_id: str) -> list:
        """List all collections for a project."""
        return await self.repo.get_collections(project_id)

    async def create_collection(
        self, project_id: str, data: dict, user_id: str
    ) -> dict:
        """Create a collection link for external file uploads."""
        import secrets

        collection_code = secrets.token_urlsafe(8)[:12]
        insert_data: dict = {
            "project_id": project_id,
            "collection_code": collection_code,
            "collection_name": data["collection_name"],
            "max_file_size_mb": data.get("max_file_size_mb", 500),
            "created_by": user_id,
        }
        if data.get("allowed_types"):
            insert_data["allowed_types"] = data["allowed_types"]
        if data.get("deadline"):
            insert_data["deadline"] = data["deadline"]
        return await self.repo.create_collection(insert_data)

    async def delete_collection(self, project_id: str, collection_id: str) -> bool:
        """Delete a collection link."""
        return await self.repo.delete_collection(collection_id, project_id)

    # ------------------------------------------------------------------ #
    # Review status
    # ------------------------------------------------------------------ #

    async def update_review_status(
        self, project_id: str, file_id: str, user_id: str, review_status: Optional[str]
    ) -> dict:
        """Update review status for a file after verifying project ownership."""
        project = await self.repo.get_project_by_id(project_id)
        if not project:
            raise ValueError("Project not found")

        # Delegates the ownership check (incl. the int/str project_id
        # coercion — the 5.3 trap) to the single shared gate.
        await self._verify_file_in_project(project_id, file_id)

        return await self.repo.update_review_status(file_id, review_status)

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _classify_file_type(self, mime: str) -> str:
        """Classify a MIME type into video/image/document."""
        if not mime:
            return "document"
        if mime.startswith("video/"):
            return "video"
        if mime.startswith("image/"):
            return "image"
        return "document"

    async def _extract_video_metadata(self, filepath: str) -> dict:
        """
        Extract video metadata using ffprobe.

        Returns a dict with keys like duration_seconds, resolution, fps,
        video_codec, audio_codec, etc. Returns empty dict on failure.
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                "ffprobe",
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                filepath,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **safe_popen_kwargs(),
            )
            stdout, _ = await proc.communicate()
            if proc.returncode != 0:
                return {}

            info = json.loads(stdout)
            result = {}

            # Parse format-level metadata
            fmt = info.get("format", {})
            duration = fmt.get("duration")
            if duration:
                result["duration_seconds"] = int(float(duration))

            # Parse stream-level metadata
            for stream in info.get("streams", []):
                codec_type = stream.get("codec_type")
                if codec_type == "video":
                    result["video_codec"] = stream.get("codec_name")
                    w = stream.get("width")
                    h = stream.get("height")
                    if w and h:
                        result["resolution"] = f"{w}x{h}"
                    # FPS from r_frame_rate
                    r_frame_rate = stream.get("r_frame_rate", "")
                    if "/" in r_frame_rate:
                        num, den = r_frame_rate.split("/")
                        if int(den) > 0:
                            result["fps"] = round(int(num) / int(den), 2)
                    # Bitrate
                    bit_rate = stream.get("bit_rate")
                    if bit_rate:
                        result["video_bitrate_kbps"] = int(int(bit_rate) / 1000)
                elif codec_type == "audio":
                    result["audio_codec"] = stream.get("codec_name")
                    result["audio_channels"] = stream.get("channels")
                    sample_rate = stream.get("sample_rate")
                    if sample_rate:
                        result["audio_sample_rate"] = int(sample_rate)
                    bit_rate = stream.get("bit_rate")
                    if bit_rate:
                        result["audio_bitrate_kbps"] = int(int(bit_rate) / 1000)

            return result
        except Exception as e:
            logger.warning(f"ffprobe failed for {filepath}: {e}")
            return {}

    # ------------------------------------------------------------------ #
    # Stage suggestion (B3)
    # ------------------------------------------------------------------ #

    def _stages_repo(self):
        """Lazy accessor honoring test overrides (see test_stage_suggestion.py)."""
        override = getattr(self, "_stages_repo_override", None)
        if override is not None:
            return override
        from app.repositories.project_stages_repository import (
            get_project_stages_repository,
        )

        return get_project_stages_repository()

    def _shots_repo(self):
        """Lazy accessor honoring test overrides (see test_stage_suggestion.py)."""
        override = getattr(self, "_shots_repo_override", None)
        if override is not None:
            return override
        from app.repositories.script_shot_repository import (
            get_script_shot_repository,
        )

        return get_script_shot_repository()

    async def build_stage_suggestion(self, project_id) -> dict:
        """Typed 'what's the one next step' for the project's current stage.

        Storyboard stage is data-aware + one-click (generate_missing_frames);
        every other stage is data-aware + navigation. Unknown / no stage →
        kind="" so the frontend renders nothing.
        """
        stage = await self._stages_repo().get_current(project_id)
        slug = (stage or {}).get("slug")
        if not slug:
            return {"stage_slug": None, "kind": "", "progress": None, "action": None}

        if slug == _STORYBOARD_STAGE:
            p = await self._shots_repo().storyboard_progress_for_project(project_id)
            if p["script_count"] == 0:
                return {
                    "stage_slug": slug,
                    "kind": "storyboard_no_script",
                    "progress": p,
                    "action": {
                        "type": "navigate",
                        "tab": "scripts",
                        "label_key": "projects.suggest.ctaScripts",
                        "count": None,
                    },
                }
            if p["total"] == 0:
                return {
                    "stage_slug": slug,
                    "kind": "storyboard_no_shots",
                    "progress": p,
                    "action": {
                        "type": "navigate",
                        "tab": "scripts",
                        "label_key": "projects.suggest.ctaBreakdown",
                        "count": None,
                    },
                }
            if p["empty"] > 0:
                return {
                    "stage_slug": slug,
                    "kind": "storyboard_generate",
                    "progress": p,
                    "action": {
                        "type": "generate_missing_frames",
                        "tab": None,
                        "label_key": "projects.suggest.ctaGenerate",
                        "count": p["empty"],
                    },
                }
            return {
                "stage_slug": slug,
                "kind": "storyboard_ready",
                "progress": p,
                "action": {
                    "type": "navigate",
                    "tab": "scripts",
                    "label_key": "projects.suggest.ctaReady",
                    "count": None,
                },
            }

        tab = _STAGE_NAV_TAB.get(slug, "files")
        return {
            "stage_slug": slug,
            "kind": f"{slug}_nav",
            "progress": None,
            "action": {
                "type": "navigate",
                "tab": tab,
                "label_key": f"projects.suggest.cta_{slug}",
                "count": None,
            },
        }

    # ------------------------------------------------------------------ #
    # Batch generate-missing-frames (B3 one-click action)
    # ------------------------------------------------------------------ #

    async def generate_missing_frames(
        self, project_id: int | str, user_id: str
    ) -> dict:
        """Dispatch one shot-generate workflow per empty shot in the project.

        Mirrors the single-shot /generate endpoint per shot so each generation
        gets its OWN task_tracking row driven by the DBOS lifecycle trigger
        (route-C discipline). Per-shot dispatch failure rolls that shot back to
        'empty', marks its task failed, and the loop continues (partial success
        is fine). No artificial parent row — N generations = N tracked tasks,
        consistent with clicking generate on each shot individually.
        """
        from app.workflows.script_shot_generate import script_shot_generate_workflow

        shots_repo = self._shots_repo()
        empty_ids = await shots_repo.list_empty_shot_ids_for_project(project_id)

        mgr = get_task_manager()
        task_ids: list[str] = []
        for shot_id in empty_ids:
            wf_id = str(_uuid.uuid4())
            task_id = await mgr.create(
                user_id=user_id,
                task_type="shot_generate",  # ≤20 chars (task_tracking.task_type VARCHAR(20))
                title="Generate shot image",
                dbos_workflow_id=wf_id,
            )
            try:
                await shots_repo.update_status(shot_id, "generating")
                await start_workflow_routed(
                    "script_shot_generate",
                    dbos_workflow_callable=script_shot_generate_workflow,
                    dbos_workflow_kwargs={"shot_id": shot_id, "user_id": user_id},
                    workflow_id=wf_id,
                )
                task_ids.append(task_id)
            except Exception as exc:  # noqa: BLE001 — skip this shot, keep the batch
                logger.error(
                    f"[projects] generate-missing shot {shot_id} failed: {exc}"
                )
                try:
                    await shots_repo.update_status(shot_id, "empty")
                except Exception as rollback_exc:  # noqa: BLE001
                    logger.error(
                        f"[projects] generate-missing shot {shot_id} rollback "
                        f"failed: {rollback_exc}"
                    )
                try:
                    await mgr.fail(task_id, f"dispatch failed: {exc}")
                except Exception as fail_exc:  # noqa: BLE001
                    logger.error(
                        f"[projects] generate-missing shot {shot_id} fail() "
                        f"itself failed: {fail_exc}"
                    )

        return {"dispatched_count": len(task_ids), "task_ids": task_ids}
