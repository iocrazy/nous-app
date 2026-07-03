"""MessageStore protocol — the strangler seam for AILibraryChatService storage.

``AILibraryChatService`` today reads/writes the ``ai_sessions`` /
``ai_messages`` tables directly. This Protocol carves those nine storage
operations out into a swappable interface so a future store (Task 3/4,
backed by the shared ``conversations`` / ``messages`` schema) can be
substituted without touching the service's business logic (ownership
checks, error mapping, hooks, counters math).

``LegacyAiStore`` (``legacy_ai_store.py``) is the first — and today only
— implementation: it is a byte-for-byte extraction of the existing
Supabase calls, so swapping it in for the inline code is a zero
behavior-change refactor.

Row-shape contract
-------------------
Every method returns dicts shaped EXACTLY like today's ``ai_sessions`` /
``ai_messages`` rows — the service and router serializers must not need
to change when a new store is plugged in:

``ai_sessions`` row keys::

    id, user_id, agent_slug, title, status, total_tokens, message_count,
    project_id, team_id, context_type, context_id, created_at, updated_at

``ai_messages`` row keys::

    id, session_id, role, content, agent_id, prompt_tokens,
    completion_tokens, metadata_json, created_at

A store implementation that backs onto a different physical schema
(e.g. the shared ``conversations`` / ``messages`` tables) MUST adapt its
native rows into this shape before returning them.

Ownership (``session.user_id == caller.user_id``) is enforced by the
SERVICE, not the store — ``get_session`` here returns the row (or
``None``) with no authorization check.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol


class MessageStore(Protocol):
    """Storage seam for AI Library chat sessions + messages."""

    async def create_session(
        self,
        *,
        user_id: str,
        agent_slug: str,
        agent_id: str,
        title: str,
        project_id: Optional[int],
        team_id: Optional[int],
        context_type: Optional[str],
        context_id: Optional[str],
    ) -> Dict[str, Any]:
        """Insert a new session row. Returns the inserted row."""
        ...

    async def list_sessions(
        self,
        *,
        user_id: str,
        agent_slug: Optional[str],
        project_id: Optional[int],
        limit: int,
    ) -> List[Dict[str, Any]]:
        """Return the caller's non-deleted sessions, newest-updated first."""
        ...

    async def get_session(self, *, session_id: int) -> Optional[Dict[str, Any]]:
        """Fetch a single session row by id, or None. Ownership stays in the service."""
        ...

    async def rename_session(self, *, session_id: int, title: str) -> Dict[str, Any]:
        """Update a session's title. Returns the updated row."""
        ...

    async def soft_delete_session(self, *, session_id: int) -> None:
        """Mark a session ``status='deleted'``."""
        ...

    async def bump_counters(
        self, *, session_id: int, add_tokens: int, add_messages: int
    ) -> None:
        """Update a session's total_tokens / message_count.

        The caller (service) computes the values to write exactly as it
        does today; this method persists them without re-fetching.
        """
        ...

    async def get_messages(
        self, *, session_id: int, limit: int = 200
    ) -> List[Dict[str, Any]]:
        """Return a session's messages, chronological, capped at ``limit``."""
        ...

    async def append_user_message(
        self, *, session_id: int, user_id: str, content: str
    ) -> Dict[str, Any]:
        """Insert a user-role message. Returns the inserted row."""
        ...

    async def append_assistant_message(
        self,
        *,
        session_id: int,
        agent_id: Optional[str],
        content: str,
        prompt_tokens: int,
        completion_tokens: int,
        metadata: dict,
    ) -> Dict[str, Any]:
        """Insert an assistant-role message. Returns the inserted row."""
        ...
