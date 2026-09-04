"""Chat/issue temp uploads → temp resources on the shared library.

This module decides the scope (team vs personal) for a chat/issue file upload
so that later tasks can create a temp resource on the shared NAS volume
(``${DOWNLOAD_PATH}``).  Both the gateway container (regular chat turns) and
the worker container (issue turns) mount that volume, so storing uploads as
resources makes them readable from both sides.

Task 1 scope: ``resolve_chat_scope`` + ``_get_session_team_id``.
Task 2 scope: ``save_chat_temp_upload``, ``_ensure_chat_uploads_folder``,
              ``_resources_service``, ``_kind_for_mime``.
P1 Task 6:    ``_register_in_generated_inbox`` — the saved resource also gets
              a ``generated_media`` row (``origin_kind='chat_upload'``,
              ``review_state='saved'``, ``promoted_resource_id`` = the
              resource) so chat uploads show up in the Generated inbox. No
              blob copy: the row points at the resource's own file_path.
P6 Task 2:    the folder is identified by ``system_key='chat_uploads'``, never
              by its name. Migration 450 backfills the identity onto folders
              this code never touches; it ships in a SEPARATE, later PR and
              nothing here depends on it having run. See the constants below.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Optional, Tuple

from fastapi import UploadFile
from loguru import logger
from starlette.datastructures import Headers

# Stable identity of the per-scope folder that holds chat/issue uploads.
# A folder's NAME is not its identity: the user can rename it, and we may
# localise it. ``system_key`` is what every lookup keys off; ``is_system`` is
# the companion "protected" switch the API layer enforces (rename / move /
# trash / delete → 409 ``system_folder``), while what goes in and out of the
# folder stays unrestricted. Same paradigm as ``cover_templates`` (mig 441).
CHAT_UPLOADS_SYSTEM_KEY = "chat_uploads"
CHAT_UPLOADS_DISPLAY_NAME = "Chat Uploads"

# What the folder was called before it had an identity. Adopting it is THE
# mechanism here, not a fallback: migration 450 ships in a later PR (it must
# not land while the old name-matching backend is still running, or that
# backend mints a second ``temp`` folder beside the renamed one), so until then
# every scope is adopted lazily, by this code, on its next chat upload.
# Migration 450 then covers whatever is left — the scopes nobody uploaded to.
# Both use the SAME rule, so each is a no-op for what the other already did.
LEGACY_CHAT_UPLOADS_FOLDER_NAME = "temp"

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


def keyed_chat_uploads_criteria():
    """SQLAlchemy criterion: the folder that IS a scope's Chat Uploads folder.

    Deliberately exported: ``backfill_generated_inbox`` joins on the union of
    this and :func:`legacy_chat_uploads_criteria` below, and a second copy of
    either arm would let the two disagree about which rows are chat uploads.
    Not scope-filtered — the caller adds ``scope_id`` (the ensure path) or
    scans every scope (the backfill).
    """
    from app.models import Folders  # noqa: PLC0415

    return Folders.system_key == CHAT_UPLOADS_SYSTEM_KEY


def legacy_chat_uploads_criteria():
    """SQLAlchemy criterion: a not-yet-adopted pre-450 ``temp`` folder.

    ``system_key IS NULL`` is part of the identity, not decoration: a folder
    someone renamed to "temp" AFTER it was keyed for something else must not
    be mistaken for this one.
    """
    from app.models import Folders  # noqa: PLC0415

    return (Folders.system_key.is_(None)) & (
        Folders.name == LEGACY_CHAT_UPLOADS_FOLDER_NAME
    )


def adoptable_chat_uploads_criteria():
    """SQLAlchemy criterion: a legacy ``temp`` folder we may CLAIM.

    Strictly narrower than :func:`legacy_chat_uploads_criteria`, and the
    difference is deliberate — reading a folder is harmless, claiming one is
    not. Adoption sets ``is_system=true``, which the API layer turns into a
    409 on rename / move / trash / delete: claim the wrong folder and the user
    permanently loses control of a directory they made.

    Root-only (``parent_id IS NULL AND library_id IS NULL``) because that is
    what the real folder always looked like, not because it is cautious. The
    pre-450 ``_ensure_temp_folder`` was the ONLY writer of a folder named
    ``temp``, and its insert passed ``name`` / ``scope_id`` / ``created_by``
    and nothing else, so both columns took the DB default. A "temp" folder
    nested under a project — a user's scratch drawer — was therefore never
    ours, however small its id.

    MUST stay identical to migration 450's candidate predicate. If the two
    ever disagree, each claims a different row and the second one violates
    ``ux_folders_scope_system_key`` — during a user's upload.
    """
    from app.models import Folders  # noqa: PLC0415

    return (
        legacy_chat_uploads_criteria()
        & (Folders.parent_id.is_(None))
        & (Folders.library_id.is_(None))
    )


def chat_uploads_folder_criteria():
    """SQLAlchemy criterion: every folder that holds chat uploads.

    The union of the two arms above — the READ side, deliberately wider
    than what :func:`adoptable_chat_uploads_criteria` will claim. Readers
    that reconcile history (the backfill) want both arms, because a scope may
    not be adopted yet at all (migration 450 is a later PR, and this code
    adopts a scope only when someone uploads to it) and because adoption takes
    ONE ``temp`` folder per scope, leaving any others as plain
    user folders: their contents are still chat uploads, and narrowing to the
    keyed arm alone would report "0 temp resources" for them — a wrong answer
    that raises no error. Reading a folder costs nothing; CLAIMING one is
    what needs the narrow rule.
    """
    from sqlalchemy import or_  # noqa: PLC0415

    return or_(keyed_chat_uploads_criteria(), legacy_chat_uploads_criteria())


def _integrity_constraint_name(exc: Exception) -> Optional[str]:
    """The violated constraint's name, across the shapes it actually arrives in.

    Measured against this stack (SQLAlchemy 2 + asyncpg, mig 441 schema), NOT
    assumed: a duplicate keyed folder gives
    ``sqlalchemy.exc.IntegrityError`` whose ``.orig`` is the dialect's own
    ``asyncpg.IntegrityError`` — that wrapper has NEITHER ``constraint_name``
    NOR ``diag``. The name lives one level further down, on
    ``.orig.__cause__`` (``asyncpg.exceptions.UniqueViolationError``). The two
    likelier-looking shapes are tried first anyway because the driver is not
    part of this module's contract and psycopg puts it in both of them.

    Returns ``None`` when no shape carries it — the caller still logs
    ``str(orig)``, which contains the constraint name in prose, so a genuinely
    unexpected violation is never reduced to the word "IntegrityError".
    """
    orig = getattr(exc, "orig", None)
    for candidate in (
        orig,
        getattr(orig, "diag", None),
        getattr(orig, "__cause__", None),
    ):
        name = getattr(candidate, "constraint_name", None)
        if name:
            return str(name)
    return None


async def _find_chat_uploads_folder(scope_id: int) -> Optional[str]:
    """The scope's Chat Uploads folder id (by ``system_key``), or ``None``."""
    from sqlalchemy import select  # noqa: PLC0415

    from app.db.session import read_scope  # noqa: PLC0415
    from app.models import Folders  # noqa: PLC0415

    async with read_scope() as session:
        row = (
            await session.execute(
                select(Folders.id).where(
                    Folders.scope_id == scope_id,
                    keyed_chat_uploads_criteria(),
                    Folders.is_trashed.is_(False),
                )
            )
        ).first()
    return str(row[0]) if row else None


async def _ensure_chat_uploads_folder(scope_id: str, user_id: str) -> str:
    """Get-or-adopt-or-create the scope's Chat Uploads folder; return its id.

    Three steps, in this order — the order is the whole point:

    1. **Find by ``system_key``.** The name is never matched here.
    2. **Adopt** the oldest live, unkeyed, ROOT-level ``temp`` folder — see
       :func:`adoptable_chat_uploads_criteria` for why root-level is part of
       the identity rather than a safety margin. This is the same candidate
       predicate migration 450 uses (a later PR), so whichever of the two
       reaches a scope first, the other finds nothing left to do there.
       ``MIN(id)`` because folder ids are
       snowflakes: smallest is oldest, i.e. the one chat uploads have actually
       been landing in. Every other ``temp`` folder in the scope is left alone
       as a plain user folder.
    3. **Create** one carrying the key, ``is_system=True`` and the display
       name. Never a bare name.

    *user_id* is required by ``folders.created_by`` (UUID NOT NULL, no default,
    migration 044).

    Concurrency is settled by the database, not by us: the partial unique index
    ``ux_folders_scope_system_key`` allows exactly one live keyed folder per
    scope, so a racing caller's adopt/insert is rejected and we re-read the
    winner instead of minting a second folder. That guard did not exist before
    this change — the name-matching version could and did create duplicates.
    """
    from sqlalchemy import insert as sa_insert  # noqa: PLC0415
    from sqlalchemy import select  # noqa: PLC0415
    from sqlalchemy import update as sa_update  # noqa: PLC0415
    from sqlalchemy.exc import IntegrityError  # noqa: PLC0415

    from app.db.session import write_scope  # noqa: PLC0415
    from app.models import Folders  # noqa: PLC0415

    sid = int(scope_id)

    found = await _find_chat_uploads_folder(sid)
    if found is not None:
        return found

    outcome: Optional[Tuple[str, str]] = None  # (folder_id, "adopted"/"created")
    try:
        async with write_scope() as session:
            legacy_id = (
                (
                    await session.execute(
                        select(Folders.id)
                        .where(
                            Folders.scope_id == sid,
                            adoptable_chat_uploads_criteria(),
                            Folders.is_trashed.is_(False),
                        )
                        .order_by(Folders.id.asc())
                        .limit(1)
                    )
                )
                .scalars()
                .first()
            )

            if legacy_id is not None:
                # The ``system_key IS NULL`` predicate makes the adoption
                # itself the race guard: a concurrent adopter leaves us zero
                # rows rather than a silent second write.
                # ``updated_at`` is not set here — ``update_folders_updated_at``
                # (BEFORE UPDATE) owns that column and overwrites any value.
                adopted = (
                    (
                        await session.execute(
                            sa_update(Folders)
                            .where(
                                Folders.id == legacy_id,
                                Folders.system_key.is_(None),
                            )
                            .values(
                                system_key=CHAT_UPLOADS_SYSTEM_KEY,
                                is_system=True,
                                name=CHAT_UPLOADS_DISPLAY_NAME,
                            )
                            .returning(Folders.id)
                        )
                    )
                    .scalars()
                    .first()
                )
                if adopted is not None:
                    outcome = (str(adopted), "adopted")

            if outcome is None:
                created = (
                    (
                        await session.execute(
                            sa_insert(Folders)
                            .values(
                                name=CHAT_UPLOADS_DISPLAY_NAME,
                                scope_id=sid,
                                created_by=str(user_id),
                                is_system=True,
                                system_key=CHAT_UPLOADS_SYSTEM_KEY,
                                # parent_id / icon / color omitted → DB defaults.
                            )
                            .returning(Folders.id)
                        )
                    )
                    .scalars()
                    .first()
                )
                if created is not None:
                    outcome = (str(created), "created")
    except IntegrityError as e:
        # Only ux_folders_scope_system_key can realistically land here, and it
        # means somebody else already has the folder. The constraint name and
        # the driver's own message are both in the line because that promise
        # is otherwise unkeepable: "IntegrityError" alone reads identically
        # whether we lost a benign race or violated something nobody
        # anticipated, so the log would quietly assert the benign reading.
        logger.info(
            f"[chat_upload] Chat Uploads folder for scope {scope_id} lost a "
            f"race: {e.__class__.__name__} "
            f"constraint={_integrity_constraint_name(e) or 'unknown'} "
            f"({getattr(e, 'orig', None)}); re-reading the winner"
        )
        outcome = None

    if outcome is not None:
        folder_id, how = outcome
        logger.info(
            f"[chat_upload] {how} Chat Uploads folder {folder_id!r} "
            f"for scope {scope_id}"
        )
        return folder_id

    found = await _find_chat_uploads_folder(sid)
    if found is not None:
        logger.info(
            f"[chat_upload] Chat Uploads folder for scope {scope_id} exists "
            f"concurrently; reusing {found!r}"
        )
        return found

    raise RuntimeError(
        f"[chat_upload] could not ensure the Chat Uploads folder for scope "
        f"{scope_id} (no row adopted, created, or found)"
    )


async def save_chat_temp_upload(
    *,
    user_id: str,
    session_id: Optional[str],
    file_bytes: bytes,
    filename: str,
    mime: str,
) -> dict:
    """Persist *file_bytes* as a resource in the scope's Chat Uploads folder.

    Resolves the scope (team vs personal) from *session_id*, ensures the
    ``system_key='chat_uploads'`` folder exists, then delegates the actual file
    write and DB row creation to ``ResourcesService.upload_resource``.

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
    # scope_type is not passed on: folders are keyed by scope_id alone
    # (it is a teams.id snowflake and unique across both kinds of scope).
    folder_id = await _ensure_chat_uploads_folder(scope_id, user_id)

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
        f"[chat_upload] saved chat upload resource {resource['id']!r} "
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
