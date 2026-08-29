"""Chat/issue temp uploads → temp resources on the shared library.

This module decides the scope (team vs personal) for a chat/issue file upload
so that later tasks can create a temp resource on the shared NAS volume
(``${DOWNLOAD_PATH}``).  Both the gateway container (regular chat turns) and
the worker container (issue turns) mount that volume, so storing uploads as
resources makes them readable from both sides.

Task 1 scope: ``resolve_chat_scope`` + ``_get_session_team_id``.
Task 2 scope: ``save_chat_temp_upload``, ``_ensure_temp_folder``,
              ``_resources_service``, ``_kind_for_mime``.
P1 Task 6:    ``_register_in_generated_inbox`` — the saved resource also gets
              a ``generated_media`` row (``origin_kind='chat_upload'``,
              ``review_state='saved'``, ``promoted_resource_id`` = the
              resource) so chat uploads show up in the Generated inbox. No
              blob copy: the row points at the resource's own file_path.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Optional, Tuple

from fastapi import UploadFile
from loguru import logger
from starlette.datastructures import Headers

# Name of the auto-created folder that holds transient chat uploads.
# get-or-create per scope before saving a resource.
TEMP_FOLDER_NAME = "temp"

# Extension sets for kind inference (fallback when MIME is not recognised).
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff", ".tif"}
_VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v", ".flv"}
_PDF_EXTS = {".pdf"}


async def _get_session_team_id(session_id: str) -> Optional[int]:
    """Return the ``team_id`` for *session_id*, or ``None`` if not team-scoped.

    Uses the ORM ``read_scope()`` session (``app.db.session``), the
    canonical async DB-read pattern in this codebase (Phase B4). The import
    is deferred inside the function so it follows the same lazy-import
    convention used across all DBOS step and workflow modules.

    Conversations-only (Conversations Phase 3, Task 6 collapsed the
    compatibility layer — the legacy ``ai_sessions`` fallback this used to
    try after a conversations miss is gone; the legacy table itself is
    dropped in Wave 2).

    The conversations column is ``scope_id`` (returned as ``team_id``
    below), NOT a literal ``team_id`` column — that name only existed on
    the legacy ``ai_sessions`` table. A ``conversations`` row for a
    ``direct_agent`` session ALWAYS has a non-NULL ``scope_id`` (Phase 2
    assigns the user's personal-team scope at create time), so a hit here
    always lands on the "team-scoped" branch of ``resolve_chat_scope``
    below — that is CORRECT: the personal-team snowflake IS the team_id
    for a personal conversation, not a signal that the session is
    legacy-personal.
    """
    from sqlalchemy import select

    from app.db.session import read_scope  # deferred — matches codebase convention
    from app.models import Conversations

    sid = int(session_id)
    # conversations.id is a BIGINT snowflake carried as str (mig 232) —
    # asyncpg rejects str binds on int8.
    async with read_scope() as session:
        row = (
            await session.execute(
                select(Conversations.scope_id).where(Conversations.id == sid)
            )
        ).first()
    if row is None:
        logger.debug(f"[chat_upload] session {session_id!r} not found")
        return None
    team_id = row[0]
    return int(team_id) if team_id is not None else None


async def resolve_chat_scope(
    *,
    session_id: Optional[str],
    user_id: str,
) -> Tuple[str, str]:
    """Decide the resource scope for a chat/issue file upload.

    Returns a ``(scope_type, scope_id)`` tuple:

    * ``("team", str(team_id))`` — when *session_id* resolves to a
      team-scoped session (``conversations.scope_id IS NOT NULL``).
    * ``("personal", str(personal_team_id))`` — for personal sessions or
      when no session context is available.

    ``scope_id`` is always a ``teams.id`` snowflake (bigint), never the user
    UUID: it feeds ``folders.scope_id`` / ``resource_items.scope_id`` which
    became bigint in PR-E 4c-3, so the personal branch resolves the user's
    personal team rather than returning the raw UUID (which fails 22P02).

    The returned tuple is intentionally immutable (a plain tuple) so callers
    can pass it directly to ``ResourcesService.upload_resource``.
    """
    from app.services.library.resources_service import (  # noqa: PLC0415
        _resolve_personal_team_id,
    )

    if session_id:
        try:
            team_id = await _get_session_team_id(session_id)
        except Exception:
            logger.exception(
                f"[chat_upload] failed to fetch team_id for session {session_id!r}; "
                "falling back to personal scope"
            )
            team_id = None
        if team_id is not None:
            return ("team", str(team_id))
    return ("personal", await _resolve_personal_team_id(user_id))


# ------------------------------------------------------------------ #
# Task 2 helpers — module-level so tests can monkeypatch them
# ------------------------------------------------------------------ #


def _resources_service():
    """Return a ``ResourcesService`` instance (deferred import, monkeypatchable)."""
    from app.services.library.resources_service import ResourcesService  # noqa: PLC0415

    return ResourcesService()


def _kind_for_mime(mime: str, filename: str) -> str:
    """Infer upload kind from MIME type, with extension as fallback.

    Returns one of ``"image"``, ``"video"``, or ``"pdf"``.  Defaults to
    ``"pdf"`` for unrecognised types so callers always receive a valid string.
    """
    mime_lower = (mime or "").lower()
    if mime_lower.startswith("image/"):
        return "image"
    if mime_lower.startswith("video/"):
        return "video"
    if mime_lower == "application/pdf":
        return "pdf"

    # Extension-based fallback for ambiguous MIME values like
    # "application/octet-stream" which some clients send for every binary.
    ext = Path(filename).suffix.lower()
    if ext in _IMAGE_EXTS:
        return "image"
    if ext in _VIDEO_EXTS:
        return "video"
    if ext in _PDF_EXTS:
        return "pdf"

    return "pdf"


def media_kind_for_mime(mime: Optional[str]) -> str:
    """``generated_media.media_kind`` for a chat upload.

    Deliberately NOT ``_kind_for_mime`` above (which answers the API's
    ``kind`` and folds everything unknown into "pdf"): the inbox renders by
    media_kind, and a docx must not claim to be a PDF preview. Three values
    only — image / video / file.
    """
    m = (mime or "").lower()
    if m.startswith("image/"):
        return "image"
    if m.startswith("video/"):
        return "video"
    return "file"


def _conversation_id_from_session(session_id: Optional[str]) -> Optional[int]:
    """The conversation this upload belongs to, when the session names one.

    ``session_id`` is a conversation snowflake for chat turns, but issue turns
    pass a non-numeric handle — those get NULL rather than a fabricated id.
    """
    if not session_id:
        return None
    try:
        return int(session_id)
    except (TypeError, ValueError):
        return None


async def _register_in_generated_inbox(
    *,
    resource: dict,
    scope_id: str,
    user_id: str,
    session_id: Optional[str],
    mime: str,
) -> None:
    """Best-effort: give the saved resource a row in the Generated inbox.

    This is the ONE place in this module where a swallowed error is correct:
    the bytes are on disk and the resource row exists, so the upload has
    already succeeded from the caller's point of view — failing it here would
    lose a file the user is waiting on to fix a row that
    ``backfill_generated_inbox`` re-creates on its next run. It is logged with
    the resource id (never ``pass``) so the gap is reconcilable.
    """
    from app.repositories.generated_media_repository import (  # noqa: PLC0415
        GeneratedMediaRepository,
    )

    try:
        await GeneratedMediaRepository().insert_registered_resource(
            scope_id=int(scope_id),
            creator_id=str(user_id),
            resource_id=int(resource["id"]),
            file_path=resource["file_path"],
            mime=mime or None,
            media_kind=media_kind_for_mime(mime),
            conversation_id=_conversation_id_from_session(session_id),
            origin_kind="chat_upload",
        )
    except Exception as e:
        logger.error(
            f"[chat_upload] inbox registration failed for resource "
            f"{resource['id']!r} (scope {scope_id}); backfill_generated_inbox "
            f"will pick it up: {e!r}"
        )


async def _ensure_temp_folder(scope_type: str, scope_id: str, user_id: str) -> str:
    """Get-or-create the reserved ``temp`` folder for *scope_type*/*scope_id*.

    Lists existing (non-trashed) folders for the scope and returns the id of
    the one named ``TEMP_FOLDER_NAME``.  Creates it via
    ``ResourcesRepository.create_folder`` if it does not exist yet.

    *user_id* is required because the ``folders`` table has
    ``created_by UUID NOT NULL`` (migration 044) with no default — the create
    path fails with a NOT NULL violation without it.

    Returns the folder id as a string.
    """
    from app.repositories.resources_repository import (  # noqa: PLC0415
        ResourcesRepository,
    )

    repo = ResourcesRepository()
    # NOTE: ResourcesRepository.get_folders swallows DB errors and returns []
    # (existing repo pattern). An empty list may therefore mask a DB failure
    # rather than meaning "no temp folder yet" — in that case we fall through
    # to create_folder, whose guard below surfaces a clear error.
    folders = await repo.get_folders(scope_type, scope_id)
    for folder in folders:
        if folder.get("name") == TEMP_FOLDER_NAME:
            return str(folder["id"])

    # Folder does not exist yet — create it.  ``created_by`` is mandatory
    # (NOT NULL, no DB default); optional fields (icon, color, parent_id)
    # are omitted and default to NULL.
    created = await repo.create_folder(
        {
            "name": TEMP_FOLDER_NAME,
            # PR-E 4b: scope_type no longer written (nullable post mig 240).
            "scope_id": scope_id,
            "created_by": str(user_id),
            # parent_id, icon, color intentionally omitted → DB defaults (NULL)
        }
    )
    # create_folder returns {} when result.data is empty, which can mean
    # either (a) RLS blocked the insert OR (b) a concurrent caller created
    # the temp folder a moment ago and our INSERT lost the race. Disambiguate
    # by re-listing folders one more time before declaring failure.
    if "id" not in created:
        folders = await repo.get_folders(scope_type, scope_id)
        for folder in folders:
            if folder.get("name") == TEMP_FOLDER_NAME:
                logger.info(
                    f"[chat_upload] temp folder created concurrently for scope "
                    f"{scope_type}/{scope_id}; reusing {folder['id']!r}"
                )
                return str(folder["id"])
        raise RuntimeError(
            f"[chat_upload] create_folder returned no id for scope "
            f"{scope_type}/{scope_id}"
        )
    folder_id = str(created["id"])
    logger.info(
        f"[chat_upload] created temp folder {folder_id!r} "
        f"for scope {scope_type}/{scope_id}"
    )
    return folder_id


async def save_chat_temp_upload(
    *,
    user_id: str,
    session_id: Optional[str],
    file_bytes: bytes,
    filename: str,
    mime: str,
) -> dict:
    """Persist *file_bytes* as a resource in the scope's ``temp`` folder.

    Resolves the scope (team vs personal) from *session_id*, ensures the
    ``temp`` folder exists, then delegates the actual file write and DB row
    creation to ``ResourcesService.upload_resource``.

    Returns a normalised dict:
    ```python
    {
        "resource_id": str,
        "file_path": str,   # relative path inside DOWNLOAD_PATH
        "kind": "image" | "video" | "pdf",
        "mime": str,
        "filename": str,
        "size_bytes": int,
    }
    ```

    Raises propagated exceptions from ``ResourcesService`` or
    ``ResourcesRepository`` so the caller can decide how to handle failures.
    """
    size_bytes = len(file_bytes)
    scope_type, scope_id = await resolve_chat_scope(
        session_id=session_id, user_id=user_id
    )
    folder_id = await _ensure_temp_folder(scope_type, scope_id, user_id)

    # Wrap the raw bytes in a FastAPI UploadFile so upload_resource can stream
    # it to disk via the shared stream_upload_to_disk utility.  UploadFile
    # accepts a BinaryIO; its .read() is an async coroutine backed by anyio.
    upload_file = UploadFile(
        file=io.BytesIO(file_bytes),
        size=size_bytes,
        filename=filename,
        headers=Headers({"content-type": mime}),
    )

    svc = _resources_service()
    try:
        resource = await svc.upload_resource(
            user_id=str(user_id),
            file=upload_file,
            scope_type=scope_type,
            scope_id=scope_id,
            folder_id=folder_id,
        )
    finally:
        # Release the underlying BytesIO regardless of success/failure.
        await upload_file.close()

    # upload_resource returns {} when its repo insert yields no data. Fail fast
    # with the actual payload instead of a downstream KeyError.
    if "id" not in resource or "file_path" not in resource:
        raise RuntimeError(
            f"[chat_upload] upload_resource returned incomplete resource dict: "
            f"{resource!r}"
        )

    logger.info(
        f"[chat_upload] saved temp resource {resource['id']!r} "
        f"({size_bytes} bytes) in scope {scope_type}/{scope_id}"
    )

    await _register_in_generated_inbox(
        resource=resource,
        scope_id=scope_id,
        user_id=user_id,
        session_id=session_id,
        mime=mime,
    )

    return {
        "resource_id": str(resource["id"]),
        "file_path": resource["file_path"],
        "kind": _kind_for_mime(mime, filename),
        "mime": mime,
        "filename": filename,
        "size_bytes": size_bytes,
    }
