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

from app.schemas.issue_message import IssueMessage, IssueMessageKind


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
    )
