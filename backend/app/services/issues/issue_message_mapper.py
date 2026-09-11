"""Pure mapper: a raw ai_messages-shaped row → the IssueMessage UI shape.
Shared by the messages GET endpoint and the WS chat-stream publisher.

Row source (Conversations Phase 3, Task 6+): ``row`` is the legacy
``ai_messages``-shaped dict produced by ``ConversationsAiStore``
(``conversations_ai_store.py::_to_legacy_message_shape``), backed by the
canonical ``public.messages`` table. ``row["id"]`` / ``row["session_id"]``
are native BIGINT snowflake ids there (NOT UUIDs, unlike the retired
Supabase-backed store's real ``ai_messages.id`` UUID PK) — see
``IssueMessage.id: str`` in ``schemas/issue_message.py`` for the matching
schema-side widening."""

from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

from loguru import logger

from app.schemas.issue_message import IssueMessage, IssueMessageKind


def _display_attachments(
    raw: Any, *, message_id: Any
) -> Optional[list[dict[str, Any]]]:
    """The row's stored display attachments, or ``None``.

    ``ConversationsAiStore`` already hands back ``body['attachments'] or None``,
    so the common cases are a list of small dicts or nothing at all. Anything
    else can only come from a row written outside that contract; it is dropped
    with a warning rather than raising, because a single malformed legacy row
    must not make an entire issue's thread unreadable. The warning is what
    keeps that from being a silent swallow — the drift stays findable.
    """
    if not raw:
        return None
    if not isinstance(raw, list):
        logger.warning(
            f"[issue_message] message {message_id}: attachments is "
            f"{type(raw).__name__}, not a list — dropped"
        )
        return None
    kept = [a for a in raw if isinstance(a, dict)]
    if len(kept) != len(raw):
        logger.warning(
            f"[issue_message] message {message_id}: dropped "
            f"{len(raw) - len(kept)} non-dict attachment(s)"
        )
    return kept or None


def map_ai_message_to_issue_message(
    row: dict[str, Any],
    *,
    issue_id: int,
    session_user_id: Optional[UUID],
) -> IssueMessage:
    """Map a single ai_messages row to the IssueMessage UI shape.

    Mapping rules (Spec-1a Task 5):
      role='user'      → kind='comment',       author_user_id=session's user
      role='assistant' → kind='agent_run',     author_agent_id=ai_message.agent_id,
                                               agent_run_id=metadata_json.run_id
      role='system'    → kind='system_status', from_status / to_status from
                                               metadata_json when kind=='status'
    """
    role: str = row.get("role", "")
    meta: dict[str, Any] = row.get("metadata_json") or {}
    raw_agent_id = row.get("agent_id")

    if role == "user":
        kind = IssueMessageKind.COMMENT
        author_user_id: Optional[UUID] = session_user_id
        author_agent_id: Optional[UUID] = None
        agent_run_id: Optional[str] = None
        from_status: Optional[str] = None
        to_status: Optional[str] = None
    elif role == "assistant":
        kind = IssueMessageKind.AGENT_RUN
        author_user_id = None
        author_agent_id = UUID(str(raw_agent_id)) if raw_agent_id else None
        # agent_runs.id is BIGINT Snowflake (mig 232) — keep as numeric
        # string; wrapping in UUID() raises ValueError on a bigint.
        raw_run_id = meta.get("run_id")
        agent_run_id = str(raw_run_id) if raw_run_id else None
        from_status = None
        to_status = None
    else:
        # role='system' — treat as system_status; extract from/to if present
        kind = IssueMessageKind.SYSTEM_STATUS
        author_user_id = None
        author_agent_id = None
        agent_run_id = None
        if meta.get("kind") == "status":
            from_status = meta.get("from") or None
            to_status = meta.get("to") or None
        else:
            from_status = None
            to_status = None

    return IssueMessage(
        # row["id"] is a BIGINT snowflake under ConversationsAiStore (see
        # module docstring) — never a UUID, so no UUID() parse here.
        id=str(row["id"]),
        issue_id=issue_id,
        kind=kind,
        author_user_id=author_user_id,
        author_agent_id=author_agent_id,
        body=row.get("content"),
        meta=meta,
        agent_run_id=agent_run_id,
        from_status=from_status,
        to_status=to_status,
        created_at=row["created_at"],
        # 三期 3a Task 8a: the citation chip (and every other stored chip) has
        # to survive a reload. The store persists these on user-role messages;
        # before this they were written and never read back.
        attachments=_display_attachments(
            row.get("attachments"), message_id=row.get("id")
        ),
    )
