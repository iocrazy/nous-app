# app/services/projects_service.py

"""
Projects Service

Business logic for the MediaTrack project system: project CRUD,
file uploads with metadata extraction (ffprobe), and video linking.
"""

import asyncio
import json
import mimetypes
import os
import shutil
import tempfile
import uuid as _uuid
from datetime import datetime, timezone
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
from app.services.library.media_storage import store_local_file
from app.services.library.resources_service import _resolve_personal_team_id
from app.services.library.storage_errors import object_store_write_failed
from app.services.library.storage_flag import unified_storage_enabled

# Card enrichment defaults when a project has no stage/members/history rows
# (or the batch lookups failed) — the frontend renders the base card.
_EMPTY_ENRICHMENT = {
    "current_stage": None,
    "members_preview": None,
    "latest_activity": None,
    "workflow_badge": None,
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

# Days (not hours) a project may dwell in a given stage before the card flags
# it as stalled (B1 hybrid activity row). "review" is the only special case:
# it is a hand-off waiting on a human, so sitting there is a queue backlog
# worth surfacing early, while the produce-heavy stages legitimately run long.
# To give another stage its own SLA, add `"<stage_slug>": <days>` here — the
# lookup in _merge_activity falls back to _DEFAULT_STALL_DAYS for any slug
# absent from this map, so no other code needs touching.
STAGE_STALL_THRESHOLDS = {"review": 3}
_DEFAULT_STALL_DAYS = 7


def _parse_iso(s):
    """Best-effort ISO-8601 string -> datetime. Returns None on falsy/invalid input."""
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None


def _merge_activity(stage_act, file_act, *, stage_slug, now=None):
    """Pick the newer of the stage/file event; flag stall from current-stage dwell.

    Pure function (Task 12 — B1 hybrid activity row): no I/O, no mutation of
    its inputs. ``now`` is injectable for tests (ISO str); production passes
    None which resolves to ``datetime.now(timezone.utc)``.

    Returns ``{kind: "file"|"stage", actor, at, stalled}`` (plus ``label``
    for stage events), or ``None`` when both inputs are empty.
    """
    now_dt = _parse_iso(now) or datetime.now(timezone.utc)
    stage_at = _parse_iso(stage_act.get("entered_at")) if stage_act else None
    file_at = _parse_iso(file_act.get("created_at")) if file_act else None

    # Stall = dwell time in the *current* stage beyond its threshold.
    stalled = False
    if stage_at is not None:
        threshold = STAGE_STALL_THRESHOLDS.get(stage_slug, _DEFAULT_STALL_DAYS)
        stalled = (now_dt - stage_at).days >= threshold

    use_file = file_at is not None and (stage_at is None or file_at > stage_at)
    if use_file:
        return {
            "kind": "file",
            "actor": file_act["actor"],
            "at": file_act["created_at"],
            "stalled": stalled,
        }
    if stage_act is not None:
        return {
            "kind": "stage",
            "actor": stage_act.get("actor", ""),
            "label": stage_act.get("stage_name", ""),
            "at": stage_act.get("entered_at"),
            "stalled": stalled,
        }
    if file_act is not None:
        return {
            "kind": "file",
            "actor": file_act["actor"],
            "at": file_act["created_at"],
            "stalled": False,
        }
    return None


async def resolve_current_stage(
    project_id: int, user_id: str  # noqa: ARG001 — kept for call-site compat
) -> Optional[dict]:
    """Retired (M2 PR-G1.5): the legacy SOP stage cursor (``current_stage_id``)
    and its forward-only output-derivation were removed end-to-end —
    ``ProjectStagesRepository.get_current`` / ``derive_activity_flags`` /
    ``set_current_stage`` no longer exist. Every project, workflow or not,
    now has no SOP stage to resolve; kept as a thin stub (rather than deleted
    outright) so any lingering caller degrades to ``None`` instead of
    crashing on the now-removed repo methods.
    """
    return None


def _suggestion_from(stage_slug: str | None, progress: dict | None = None) -> dict:
    """Pure decision table: SOP stage slug (+ optional storyboard progress)
    -> suggestion dict. Extracted (PR-8 Task A) so the single-project and the
    batch (``get_project_suggestions``, homepage G7) paths shared exactly one
    kind table instead of two copies drifting apart. The single-project entry
    point (``build_stage_suggestion``) is gone — after M2 PR-G1.5 retired the
    SOP stage cursor it could only ever return the degraded "no stage" dict,
    and nothing called it. This table is kept because it is the reference for
    the ``StageSuggestionResponse`` kind values the frontend still renders.

    ``progress`` is only consulted when ``stage_slug == "storyboard"``; when
    it is ``None`` for a storyboard-stage project (e.g. the batch fetch
    failed) this falls through to the generic navigate branch below, so a
    broken progress query degrades that one row instead of raising.
    """
    if not stage_slug:
        return {"stage_slug": None, "kind": "", "progress": None, "action": None}

    if stage_slug == _STORYBOARD_STAGE and progress is not None:
        p = progress
        if p["script_count"] == 0:
            return {
                "stage_slug": stage_slug,
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
                "stage_slug": stage_slug,
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
                "stage_slug": stage_slug,
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
            "stage_slug": stage_slug,
            "kind": "storyboard_ready",
            "progress": p,
            "action": {
                "type": "navigate",
                "tab": "scripts",
                "label_key": "projects.suggest.ctaReady",
                "count": None,
            },
        }

    tab = _STAGE_NAV_TAB.get(stage_slug, "files")
    return {
        "stage_slug": stage_slug,
        "kind": f"{stage_slug}_nav",
        "progress": None,
        "action": {
            "type": "navigate",
            "tab": tab,
            "label_key": f"projects.suggest.cta_{stage_slug}",
            "count": None,
        },
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
        """Members / activity / workflow-badge card data for the list page (B1).

        Batch queries run concurrently — same no-N+1 contract as file counts.
        Each is best-effort (returns {} on failure), so a broken enrichment
        degrades the cards, never the list. Shapes:

          current_stage:   always ``None`` (M2 PR-G1.5: the legacy SOP stage
            cursor is retired end-to-end — no project has a stage badge
            anymore; the workflow Stage Board owns per-node status now).
          members_preview: {count, members: [{user_id, username}, ...]} | None
          latest_activity: {kind, actor, at, stalled, label?} | None
            merged from the latest stage transition and the latest file add
            (whichever is newer) via ``_merge_activity`` — see Task 12.
          workflow_badge: per-node status badge for workflow projects | None
        """
        from app.repositories.project_stage_nodes_repository import (
            get_project_stage_nodes_repository,
        )
        from app.repositories.project_stages_repository import (
            get_project_stages_repository,
        )

        stages_repo = get_project_stages_repository()
        nodes_repo = get_project_stage_nodes_repository()
        try:
            (
                activity,
                file_activity,
                members,
                workflow_badges,
            ) = await asyncio.gather(
                stages_repo.latest_activity_for_projects(project_ids),
                stages_repo.latest_file_activity_for_projects(project_ids),
                self.repo.get_project_members_preview(project_ids),
                nodes_repo.workflow_badges_for_projects(project_ids),
            )
        except Exception as e:  # noqa: BLE001 — enrichment must not sink the list
            logger.error(f"[projects] card enrichment failed: {e}")
            return {}

        out: dict = {}
        for pid in [str(p) for p in project_ids]:
            out[pid] = {
                "current_stage": None,
                "members_preview": members.get(pid),
                "latest_activity": _merge_activity(
                    activity.get(pid),
                    file_activity.get(pid),
                    stage_slug=None,
                ),
                "workflow_badge": workflow_badges.get(pid),
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
        data = {**data, "owner_id": user_id}
        # Workflow selection (M1 PR-B) is not a projects column — pull it out
        # before the insert and instantiate the nodes after the row exists.
        workflow_template_id = data.pop("workflow_template_id", None)
        workflow_method = data.pop("workflow_method", None)
        # Ideation (M1.5): topic_id IS a projects column — leave it in `data` so
        # the row records its source. Kept here to mark the topic produced after
        # the project exists (best-effort, below).
        topic_id = data.get("topic_id")
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

        # M2 PR-G: the legacy "born on first SOP stage" seed (set_current_stage)
        # was retired end-to-end — current_stage_id is no longer written here.
        # A project born WITH a workflow template still drives its stages from
        # the instantiated node chain (below); a No-workflow project now simply
        # has no stage concept (spec §8).

        # G3 (final UI spec): every project is born with an Episode 1 + an
        # empty script attached, so Episodes/Canvas always have a target.
        # Best-effort, same discipline as the default-stage block above — a
        # hiccup here must never fail project creation. team_id falls back
        # to the creator's own team (script_projects.team_id is NOT NULL
        # regardless of whether the *project* itself belongs to a team —
        # see create_script_project's require_team_id for the same pattern).
        try:
            episode_team_id = project.get("team_id")
            if not episode_team_id:
                from app.core.deps import get_team_id_for_user

                episode_team_id = await get_team_id_for_user(user_id)

            if episode_team_id:
                from app.repositories.episode_repository import (
                    get_episode_repository,
                )
                from app.repositories.script_repository import (
                    get_script_project_repository,
                )

                episode = await get_episode_repository().create(
                    {
                        "project_id": project["id"],
                        "title": "Episode 1",
                        "sort_order": 1,
                    }
                )
                await get_script_project_repository().create(
                    {
                        "project_id": project["id"],
                        "team_id": episode_team_id,
                        "episode_id": episode["id"],
                        "name": "Episode 1",
                        "status": "active",
                        "created_by": user_id,
                    }
                )
        except Exception as e:  # noqa: BLE001 — default episode/script is enrichment
            logger.error(
                f"[projects] default-episode init failed for {project.get('id')}: {e}"
            )

        # Workflow instantiation (M1 PR-B): copy the chosen team template's nodes
        # onto the project, set the current-node cursor, and open the first
        # group's mirror issues. No template → No-workflow no-op. Best-effort —
        # its own guard swallows failures so create never fails on a workflow
        # hiccup (same discipline as the enrichment blocks above).
        from app.services.workflow.instantiation import (
            maybe_instantiate_project_workflow,
        )

        await maybe_instantiate_project_workflow(
            str(project["id"]),
            workflow_template_id,
            method=workflow_method,
            user_id=user_id,
        )

        # Ideation (M1.5): a project born from a topic auto-marks that topic
        # produced. Best-effort — same discipline as the enrichment blocks above,
        # and one topic may spawn many projects (no uniqueness). Scoped to the
        # topic's own team so it can't flip another team's topic.
        if topic_id:
            try:
                from app.repositories.topics_repository import (
                    get_topics_repository,
                )

                topics_repo = get_topics_repository()
                topic_team_id = await topics_repo.get_topic_team_id(str(topic_id))
                if topic_team_id is not None:
                    await topics_repo.mark_produced(str(topic_id), topic_team_id)
            except Exception as e:  # noqa: BLE001 — topic linkage is enrichment
                logger.error(
                    f"[projects] mark-topic-produced failed for "
                    f"{project.get('id')} topic={topic_id}: {e}"
                )

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

    @staticmethod
    async def _resolve_project_scope_id(project: dict) -> int:
        """Scope id (a ``teams.id`` snowflake) for a project's uploads.

        Projects are optionally attached to a team (``team_id`` is nullable —
        a "personal" project has none). When absent, the owner's personal
        team is the scope, mirroring how resource uploads without an
        explicit ``scope_id`` fall back to ``_resolve_personal_team_id``.
        """
        team_id = project.get("team_id")
        if team_id is not None:
            return int(team_id)
        return int(await _resolve_personal_team_id(str(project["owner_id"])))

    async def upload_file(
        self,
        project_id: str,
        user_id: str,
        file,
        notes: Optional[str] = None,
        source_issue_id: Optional[str] = None,
    ) -> dict:
        """
        Upload a file to a project, extracting metadata for videos.

        When ``source_issue_id`` is set (the issue-side Deliverables dropzone,
        M2-W1) the file is back-linked to that issue and, for a workflow-node
        mirror issue, routed into the node's stage folder; a "filed" line is
        dropped on the issue timeline. All of that is best-effort — it never
        blocks the upload.

        Steps:
        1. Validate project exists
        2. Stream to a temp file, then dual-track: FEATURE_UNIFIED_STORAGE on
           content-addresses it into the Supabase Storage `library` bucket
           (a storage failure is a hard, typed ObjectStoreWriteFailed);
           flag off moves it into DOWNLOAD_PATH/mediatrack/{project_id}/
           (legacy, dedup-suffixed).
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

        safe_name = sanitize_filename(file.filename)

        # Stream to a temp file first (mirrors resources_service.
        # upload_resource): the final location depends on the storage
        # track, and the object-store PUT needs a local source file
        # either way.
        tmp_fd, tmp_name = tempfile.mkstemp()
        os.close(tmp_fd)
        tmp_path = Path(tmp_name)
        try:
            file_size, file_hash = await stream_upload_to_disk(
                file, tmp_path, MAX_UPLOAD_SIZE
            )

            # Classify — sniff real content type first so a forged
            # Content-Type cannot mislabel a binary as media.
            mime = (
                sniff_mime(tmp_path)
                or file.content_type
                or mimetypes.guess_type(safe_name)[0]
                or ""
            )
            file_type = self._classify_file_type(mime)

            # Extract video metadata from the local tmp path — the file is
            # still on disk either way (store_local_file only reads it), so
            # no materialize() is needed here.
            metadata = {}
            if file_type == "video":
                metadata = await self._extract_video_metadata(str(tmp_path))

            stored = None
            if await unified_storage_enabled():
                # Scope resolution lives INSIDE the try: without a scope
                # there is no object key, so a personal project whose owner
                # lacks a personal-team row is a storage failure too — typed,
                # never a bogus "Project not found" 404 via the router's
                # generic ValueError handler.
                try:
                    scope_id = await self._resolve_project_scope_id(project)
                    stored = await store_local_file(
                        scope_id=scope_id,
                        source_path=str(tmp_path),
                        mime=mime,
                        filename=safe_name,
                        sha256=file_hash,
                    )
                except Exception as exc:
                    raise object_store_write_failed(
                        exc,
                        where="project_upload_file",
                        project_id=str(project_id),
                        filename=safe_name,
                        mime=mime,
                        size_bytes=file_size,
                    ) from exc

            if stored is not None:
                relative_path = stored.file_path
                final_name = safe_name
            else:
                # Legacy filesystem move — fs fallback only: content
                # addressing has no name collisions, so the duplicate-name
                # "_counter" suffixing only makes sense here.
                save_dir = Path(settings.DOWNLOAD_PATH) / "mediatrack" / project_id
                save_dir.mkdir(parents=True, exist_ok=True)
                target = save_dir / safe_name
                counter = 1
                stem = target.stem
                suffix = target.suffix
                while target.exists():
                    target = save_dir / f"{stem}_{counter}{suffix}"
                    counter += 1
                await asyncio.to_thread(shutil.move, str(tmp_path), str(target))
                final_name = target.name
                relative_path = f"mediatrack/{project_id}/{final_name}"
        finally:
            # No-op if the move succeeded (tmp_path no longer exists); when
            # the object-store write succeeded, this is what cleans up the
            # tmp file (store_local_file only reads it, never deletes it).
            tmp_path.unlink(missing_ok=True)

        # Issue-side deliverable routing (best-effort): a mirror-issue upload
        # lands in the node's stage folder and back-links to the issue.
        deliverable_folder_id: Optional[str] = None
        if source_issue_id:
            from app.services.workflow.deliverable_uploads import (
                resolve_deliverable_folder,
            )

            deliverable_folder_id = await resolve_deliverable_folder(
                project_id, source_issue_id, user_id
            )

        # Create DB record
        file_data = {
            "project_id": project_id,
            "filename": final_name,
            "file_type": file_type,
            "mime_type": mime,
            "file_path": relative_path,
            "file_size_bytes": file_size,
            "uploaded_by": user_id,
            "notes": notes,
            **metadata,
        }
        if source_issue_id:
            file_data["source_issue_id"] = source_issue_id
        if deliverable_folder_id:
            file_data["folder_id"] = deliverable_folder_id
        created_file = await self.repo.create_file(file_data)

        # Create V1 version record
        version_data = {
            "file_id": created_file["id"],
            "version_number": 1,
            "filename": final_name,
            "file_path": relative_path,
            "file_size_bytes": file_size,
            "mime_type": mime,
            "uploaded_by": user_id,
            "notes": notes,
            **metadata,
        }
        await self.repo.create_version(version_data)

        # Best-effort issue timeline line for a deliverable upload.
        if source_issue_id:
            from app.services.workflow.deliverable_uploads import (
                record_deliverable_filed,
            )

            await record_deliverable_filed(
                source_issue_id,
                final_name,
                str(created_file.get("id")),
                user_id=user_id,
            )

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
        3. Stream to a temp file, then dual-track write (mirrors
           upload_file / Task 2.2's upload_new_version pattern)
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

        # Stream to a temp file first (mirrors upload_file): the final
        # location depends on the storage track, and the object-store PUT
        # needs a local source file either way.
        safe_name = sanitize_filename(file.filename)
        tmp_fd, tmp_name = tempfile.mkstemp()
        os.close(tmp_fd)
        tmp_path = Path(tmp_name)
        try:
            file_size, file_hash = await stream_upload_to_disk(
                file, tmp_path, MAX_UPLOAD_SIZE
            )

            # Classify and extract metadata — sniff real content type first.
            mime = (
                sniff_mime(tmp_path)
                or file.content_type
                or mimetypes.guess_type(safe_name)[0]
                or ""
            )
            file_type = self._classify_file_type(mime)
            metadata = {}
            if file_type == "video":
                metadata = await self._extract_video_metadata(str(tmp_path))

            stored = None
            if await unified_storage_enabled():
                # Scope resolution lives INSIDE the try (same contract as
                # upload_file): a scope-resolution failure is a storage-
                # track failure — typed, hard.
                try:
                    scope_id = await self._resolve_project_scope_id(project)
                    stored = await store_local_file(
                        scope_id=scope_id,
                        source_path=str(tmp_path),
                        mime=mime,
                        filename=safe_name,
                        sha256=file_hash,
                    )
                except Exception as exc:
                    raise object_store_write_failed(
                        exc,
                        where="project_upload_new_version",
                        project_id=str(project_id),
                        file_id=str(file_id),
                        filename=safe_name,
                        mime=mime,
                        size_bytes=file_size,
                    ) from exc

            if stored is not None:
                relative_path = stored.file_path
            else:
                # Legacy filesystem move — fs fallback only. Version
                # filenames already carry the "v{n}_" prefix so there is
                # no duplicate-name concern (unlike upload_file's flat dir).
                save_dir = (
                    Path(settings.DOWNLOAD_PATH)
                    / "mediatrack"
                    / project_id
                    / "versions"
                    / file_id
                )
                save_dir.mkdir(parents=True, exist_ok=True)
                target = save_dir / f"v{next_version}_{safe_name}"
                await asyncio.to_thread(shutil.move, str(tmp_path), str(target))
                relative_path = (
                    f"mediatrack/{project_id}/versions/{file_id}/"
                    f"v{next_version}_{safe_name}"
                )
        finally:
            # No-op if the move succeeded (tmp_path no longer exists); when
            # the object-store write succeeded, this is what cleans up the
            # tmp file (store_local_file only reads it, never deletes it).
            tmp_path.unlink(missing_ok=True)

        # Create version record
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
        source_issue_id: Optional[str] = None,
    ) -> list:
        """List files in a project, optionally filtered by folder or source issue.

        A ``source_issue_id`` filter (the issue-side Deliverables list) overrides
        the folder-scoping default and returns every file filed from that issue.
        Each row is enriched with ``source_issue_identifier`` (MH-N) so the Files
        module can render the "from MH-xx" back-link without a per-row fetch.
        """
        project = await self.repo.get_project_by_id(project_id)
        if not project:
            raise ValueError("Project not found")
        files = await self.repo.get_project_files(
            project_id, include_trashed=include_trashed
        )
        if source_issue_id is not None:
            files = [
                f
                for f in files
                if str(f.get("source_issue_id")) == str(source_issue_id)
            ]
        elif not include_trashed:
            if folder_id is not None:
                files = [f for f in files if f.get("folder_id") == folder_id]
            else:
                files = [f for f in files if not f.get("folder_id")]
        return await self._enrich_source_issue(files)

    async def _enrich_source_issue(self, files: list) -> list:
        """Attach ``source_issue_identifier`` (MH-N) to files that carry a
        ``source_issue_id``. Best-effort: an identifier lookup failure leaves the
        field null (the chip just won't render), never sinks the list."""
        ids = [f["source_issue_id"] for f in files if f.get("source_issue_id")]
        if not ids:
            return files
        try:
            from app.repositories.issue_repository import get_issue_repository

            id_map = await get_issue_repository().map_identifiers([int(i) for i in ids])
        except Exception as exc:  # noqa: BLE001 — chip is decoration
            logger.error(f"Failed to map source issue identifiers: {exc}")
            id_map = {}
        return [
            {
                **f,
                # Stringify the snowflake id (JSON number precision) alongside
                # the human identifier the chip actually routes on.
                "source_issue_id": (
                    str(f["source_issue_id"]) if f.get("source_issue_id") else None
                ),
                "source_issue_identifier": (
                    id_map.get(str(f["source_issue_id"]))
                    if f.get("source_issue_id")
                    else None
                ),
            }
            for f in files
        ]

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
    # Repo accessors (B3)
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

    # ------------------------------------------------------------------ #
    # Batch stage suggestions (B3 / G7 — homepage queue data source)
    # ------------------------------------------------------------------ #

    async def get_project_suggestions(
        self, user_id: str, team_id: str | None = None
    ) -> list[dict]:
        """Batch 'one next step' queue rows for the homepage (PR-8 Task A).

        M2 PR-G1.5: the legacy SOP stage cursor is retired end-to-end, so
        every project now degrades to the existing "no stage" suggestion
        (kind="" — the frontend renders nothing); there is no more
        storyboard-stage branch to fan out shot-progress queries for.

        Scope is the SAME visible-projects call the list endpoint uses
        (``repo.get_user_projects``, non-archived) — this never reinvents
        permissions. ``latest_activity``/``stalled`` still come from the
        existing ``_get_card_enrichment`` batch (workflow badge / file
        activity are unaffected by this retirement).
        """
        projects = await self.repo.get_user_projects(
            user_id, team_id=team_id, archived=False
        )
        if not projects:
            return []

        ids = [p["id"] for p in projects]
        enrichment = await self._get_card_enrichment(ids)

        items: list[dict] = []
        for p in projects:
            pid = str(p["id"])
            card = enrichment.get(pid, _EMPTY_ENRICHMENT)
            latest_activity = card.get("latest_activity")
            # 2026-07-28: the queue went silently empty after the SOP stage
            # retirement (every row was kind="" and the frontend filters
            # those out). Suggestions are workflow-driven now: a project with
            # an active workflow node suggests continuing that stage; every
            # other project still gets a generic open row so the queue keeps
            # its original contract of one row per active project.
            badge = card.get("workflow_badge")
            if badge and badge.get("current_node_name"):
                suggestion = {
                    "stage_slug": None,
                    "kind": "workflow_stage",
                    "progress": None,
                    "action": None,
                }
            else:
                suggestion = {
                    "stage_slug": None,
                    "kind": "open_project",
                    "progress": None,
                    "action": None,
                }
            items.append(
                {
                    "project_id": pid,
                    "name": p.get("name"),
                    "stage_slug": suggestion["stage_slug"],
                    "kind": suggestion["kind"],
                    "progress": suggestion["progress"],
                    "action": suggestion["action"],
                    "stalled": bool((latest_activity or {}).get("stalled")),
                    "latest_activity": latest_activity,
                }
            )
        return items

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
                    dbos_workflow_kwargs={
                        "shot_id": shot_id,
                        "user_id": user_id,
                        # 3a: batch auto-storyboard is a human action too —
                        # explicit None, same reading as the /generate route.
                        "run_id": None,
                        "turn": None,
                        "step": None,
                    },
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

    # ------------------------------------------------------------------ #
    # Recent items (Projects "Recent" view)
    # ------------------------------------------------------------------ #

    async def get_recent_items(self, user_id: str, limit: int = 8) -> list[dict]:
        """Recently-edited scripts + canvases across the caller's projects,
        merged and sorted by ``updated_at`` desc, capped at ``limit``.

        Scope mirrors the projects-list endpoint exactly: each repo joins
        ``projects`` and filters ``owner_id == user_id``, so the Recent view
        can never surface work from a project the caller can't see. ``limit``
        is clamped to 1..20; each repo is asked for the clamped ceiling so the
        merge always has enough candidates before the final cap.

        Timestamps are DB-issued UTC ISO strings (``+00:00``), so a lexical
        sort is chronological. Best-effort at the repo layer — a failing repo
        returns ``[]`` and simply contributes nothing to the merge.
        """
        from app.repositories.canvas_repository import CanvasRepository
        from app.repositories.script_repository import (
            get_script_project_repository,
        )

        # Router already clamps via Query(ge=1, le=20); re-clamped here as
        # defense for any direct (non-router) caller of this service method.
        capped = max(1, min(int(limit), 20))
        scripts = await get_script_project_repository().list_recent_for_user(
            user_id, capped
        )
        canvases = await CanvasRepository().list_recent_for_user(user_id, capped)

        merged = [{**s, "kind": "script"} for s in scripts] + [
            {**c, "kind": "canvas"} for c in canvases
        ]
        merged.sort(key=lambda item: item.get("updated_at") or "", reverse=True)
        return merged[:capped]

    # ------------------------------------------------------------------ #
    # Project entities — Characters/Locations ASSETS view (PR-10a, G13)
    # ------------------------------------------------------------------ #

    async def get_project_entities(self, project_id) -> dict:
        """Project-level Characters/Locations roll-up, derived from every
        non-deleted script's scenes. Fetch + pure-derive split: the counting
        logic lives in ``project_entities.derive_project_entities`` so it's
        independently unit-testable without a DB."""
        from app.repositories.script_scene_repository import (
            get_script_scene_repository,
        )
        from app.services.library.project_entities import derive_project_entities

        rows = await get_script_scene_repository().list_scene_rows_for_project(
            project_id
        )
        return derive_project_entities(rows)
