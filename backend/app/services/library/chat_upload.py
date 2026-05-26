"""Chat/issue temp uploads → temp resources on the shared library.

This module decides the scope (team vs personal) for a chat/issue file upload
so that later tasks can create a temp resource on the shared NAS volume
(``${DOWNLOAD_PATH}``).  Both the gateway container (regular chat turns) and
the worker container (issue turns) mount that volume, so storing uploads as
resources makes them readable from both sides.

Task 1 scope: ``resolve_chat_scope`` + ``_get_session_team_id``.
``save_chat_temp_upload`` and ``_ensure_temp_folder`` are added in Task 2.
"""

from __future__ import annotations

from typing import Optional, Tuple

from loguru import logger

# Name of the auto-created folder that holds transient chat uploads.
# Task 2 will get-or-create this folder per scope before saving a resource.
TEMP_FOLDER_NAME = "temp"


async def _get_session_team_id(session_id: str) -> Optional[int]:
    """Return the ``team_id`` for *session_id*, or ``None`` if not team-scoped.

    Uses the SQLAlchemy async engine (``app.db.engine``) which is the
    canonical async DB-read pattern in this codebase (see write_memory.py,
    workflow_health_sweeper.py, etc.).  The import is deferred inside the
    function so it follows the same lazy-import convention used across all
    DBOS step and workflow modules.
    """
    from app.db import engine as db_engine  # deferred — matches codebase convention

    row = await db_engine.fetch_one(
        "SELECT team_id FROM public.ai_sessions WHERE id = :id",
        {"id": session_id},
    )
    if row is None:
        logger.debug(f"[chat_upload] session {session_id!r} not found")
        return None
    team_id = row.get("team_id")
    return int(team_id) if team_id is not None else None


async def resolve_chat_scope(
    *,
    session_id: Optional[str],
    user_id: str,
) -> Tuple[str, str]:
    """Decide the resource scope for a chat/issue file upload.

    Returns a ``(scope_type, scope_id)`` tuple:

    * ``("team", str(team_id))`` — when *session_id* resolves to a
      team-scoped session (``ai_sessions.team_id IS NOT NULL``).
    * ``("personal", str(user_id))`` — for personal sessions or when no
      session context is available.

    The returned tuple is intentionally immutable (a plain tuple) so callers
    can pass it directly to ``ResourcesService.upload_resource``.
    """
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
    return ("personal", str(user_id))
