"""LegacyAiStore — MessageStore implementation over ai_sessions / ai_messages.

Byte-for-byte extraction of the Supabase calls that used to live inline in
``AILibraryChatService``. Every column literal, filter chain (``.eq`` /
``.neq`` / ``.order`` / ``.limit``), and the client-acquisition pattern are
unchanged — this file MOVES code, it does not change behavior.

Client acquisition — a deliberate note on WHY this isn't a bare module-level
``get_async_supabase_admin()`` call: the existing test suite patches
``app.services.ai.chat.ai_library_chat_service.get_async_supabase_admin``
(not ``app.db.supabase_client`` or this module), and some of those tests
construct ``AILibraryChatService()`` *before* entering the ``patch(...)``
context manager. A constructor-time capture of the function reference would
freeze on the unpatched original in that case. So by default (no explicit
``get_client`` override) this store resolves the client lazily, at call
time, through the ``ai_library_chat_service`` module's current attribute —
picking up whatever is bound there (patched or real) at the moment each
store method actually runs, exactly matching what the inline code used to
do. Passing an explicit ``get_client`` (e.g. in unit tests for this file)
skips that indirection entirely.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, List, Optional

_ClientGetter = Callable[[], Awaitable[Any]]


class LegacyAiStore:
    """MessageStore backed directly by the legacy ai_sessions/ai_messages tables."""

    store_kind = "legacy"

    def __init__(self, get_client: Optional[_ClientGetter] = None) -> None:
        self._get_client_override = get_client

    async def _client(self) -> Any:
        if self._get_client_override is not None:
            return await self._get_client_override()
        # Lazy import + module-attribute lookup (not `from ... import`) so
        # this always reflects the CURRENT value of
        # ai_library_chat_service.get_async_supabase_admin, including any
        # test patch applied after this LegacyAiStore instance was built.
        from app.services.ai.chat import ai_library_chat_service as _svc_mod

        return await _svc_mod.get_async_supabase_admin()

    # ------------------------------------------------------------------
    # Sessions (S1-S6)
    # ------------------------------------------------------------------

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
        """S1: insert an ai_sessions row."""
        supabase = await self._client()
        row: Dict[str, Any] = {
            "user_id": str(user_id),
            "agent_id": agent_id,
            "agent_slug": agent_slug,
            "title": title,
            "status": "active",
            "total_tokens": 0,
            "message_count": 0,
        }
        if project_id is not None:
            row["project_id"] = project_id
        if team_id is not None:
            row["team_id"] = team_id
        if context_type is not None:
            row["context_type"] = context_type
        if context_id is not None:
            row["context_id"] = context_id

        resp = await supabase.table("ai_sessions").insert(row).execute()
        if not resp.data:
            return None
        # Task 6: stamp store_kind so downstream dispatch (RunRecorder's
        # session_id vs conversation_id choice) can tell a legacy row from
        # a conversations-backed row without a second lookup. Not a real
        # ai_sessions column — added post-fetch, never sent to Supabase.
        return {**resp.data[0], "store_kind": self.store_kind}

    async def list_sessions(
        self,
        *,
        user_id: str,
        agent_slug: Optional[str],
        project_id: Optional[int],
        limit: int,
    ) -> List[Dict[str, Any]]:
        """S2: select non-deleted sessions, newest-updated first."""
        supabase = await self._client()
        query = (
            supabase.table("ai_sessions")
            .select("*")
            .eq("user_id", str(user_id))
            .neq("status", "deleted")
            .order("updated_at", desc=True)
            .limit(limit)
        )
        if agent_slug is not None:
            query = query.eq("agent_slug", agent_slug)
        if project_id is not None:
            query = query.eq("project_id", project_id)

        resp = await query.execute()
        return resp.data or []

    async def get_session(self, *, session_id: int) -> Optional[Dict[str, Any]]:
        """S3: select a single session row by id. Ownership stays in the service."""
        supabase = await self._client()
        resp = (
            await supabase.table("ai_sessions")
            .select("*")
            .eq("id", str(session_id))
            .maybe_single()
            .execute()
        )
        if not (resp and resp.data):
            return None
        # Task 6: same store_kind stamp as create_session (see comment there).
        return {**resp.data, "store_kind": self.store_kind}

    async def rename_session(self, *, session_id: int, title: str) -> Dict[str, Any]:
        """S4: update a session's title."""
        supabase = await self._client()
        resp = (
            await supabase.table("ai_sessions")
            .update({"title": title})
            .eq("id", str(session_id))
            .execute()
        )
        return resp.data[0] if resp.data else None

    async def soft_delete_session(self, *, session_id: int) -> None:
        """S5: mark a session status='deleted'."""
        supabase = await self._client()
        await (
            supabase.table("ai_sessions")
            .update({"status": "deleted"})
            .eq("id", str(session_id))
            .execute()
        )

    async def bump_counters(
        self, *, session_id: int, add_tokens: int, add_messages: int
    ) -> None:
        """S6: update total_tokens / message_count.

        The service computes the values to write (today: ``prior + turn``,
        off the in-memory session snapshot loaded at turn start) exactly as
        it did before extraction; this method writes them as-is with no
        re-fetch and no re-computation.
        """
        supabase = await self._client()
        await (
            supabase.table("ai_sessions")
            .update(
                {
                    "total_tokens": add_tokens,
                    "message_count": add_messages,
                }
            )
            .eq("id", str(session_id))
            .execute()
        )

    # ------------------------------------------------------------------
    # Messages (M1-M3)
    # ------------------------------------------------------------------

    async def get_messages(
        self, *, session_id: int, limit: int = 200
    ) -> List[Dict[str, Any]]:
        """M1: select messages for a session, chronological, capped."""
        supabase = await self._client()
        resp = (
            await supabase.table("ai_messages")
            .select("*")
            .eq("session_id", str(session_id))
            .order("created_at", desc=False)
            .limit(limit)
            .execute()
        )
        return resp.data or []

    async def append_user_message(
        self, *, session_id: int, user_id: str, content: str
    ) -> Dict[str, Any]:
        """M2: insert a user-role message. Writes ONLY session_id/role/content
        (no tokens, no agent_id, no metadata) — ``user_id`` is accepted for
        Protocol parity with future stores but is not a legacy column."""
        supabase = await self._client()
        resp = (
            await supabase.table("ai_messages")
            .insert(
                {
                    "session_id": str(session_id),
                    "role": "user",
                    "content": content,
                }
            )
            .execute()
        )
        return resp.data[0] if resp.data else None

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
        """M3: insert an assistant-role message with usage + metadata_json."""
        supabase = await self._client()
        resp = (
            await supabase.table("ai_messages")
            .insert(
                {
                    "session_id": str(session_id),
                    "role": "assistant",
                    "content": content,
                    "agent_id": str(agent_id),
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "metadata_json": metadata,
                }
            )
            .execute()
        )
        return resp.data[0] if resp.data else None
