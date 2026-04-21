# backend/app/services/ai_session_service.py

"""
AISessionService — CRUD operations for AI chat sessions and their messages.

All write/read operations use the Supabase admin client.
Access control: every session belongs to a user_id.  Any operation that
touches a session verifies the requesting user matches the owner and raises
an appropriate HTTP exception on mismatch.
"""

from fastapi import HTTPException, status
from loguru import logger

from app.db.supabase_client import get_async_supabase_admin


class AISessionService:
    """Service for managing AI chat sessions (ai_sessions + ai_messages)."""

    # ------------------------------------------------------------------
    # Session creation
    # ------------------------------------------------------------------

    async def create_session(
        self,
        user_id: str,
        project_id: str | None = None,
        title: str = "New Chat",
        context_type: str | None = None,
        context_id: str | None = None,
        team_id: str | None = None,
    ) -> dict:
        """Create a new chat session and return the created row."""
        supabase = await get_async_supabase_admin()
        row: dict = {
            "user_id": user_id,
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
            logger.error(f"Failed to create session for user {user_id}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create AI session",
            )
        return resp.data[0]

    # ------------------------------------------------------------------
    # Session retrieval
    # ------------------------------------------------------------------

    async def get_session(self, session_id: str, user_id: str) -> dict:
        """Return a session dict, raising 404/403 as appropriate."""
        supabase = await get_async_supabase_admin()
        resp = (
            await supabase.table("ai_sessions")
            .select("*")
            .eq("id", session_id)
            .single()
            .execute()
        )
        session = resp.data
        if not session:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Session not found: {session_id}",
            )
        if session.get("user_id") != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied to this session",
            )
        return session

    # ------------------------------------------------------------------
    # Session listing
    # ------------------------------------------------------------------

    async def list_sessions(
        self,
        user_id: str,
        project_id: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """Return sessions for a user, optionally filtered by project.

        Results are ordered by updated_at descending (most recent first).
        """
        supabase = await get_async_supabase_admin()
        query = (
            supabase.table("ai_sessions")
            .select("*")
            .eq("user_id", user_id)
            .neq("status", "deleted")
            .order("updated_at", desc=True)
            .limit(limit)
        )
        if project_id is not None:
            query = query.eq("project_id", project_id)

        resp = await query.execute()
        return resp.data or []

    # ------------------------------------------------------------------
    # Message retrieval
    # ------------------------------------------------------------------

    async def get_messages(
        self,
        session_id: str,
        user_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        """Return paginated messages for a session.

        Verifies the requesting user owns the session before querying.
        Messages are ordered by created_at ascending (chronological).
        """
        # Verify access (raises 404/403 if not allowed)
        await self.get_session(session_id, user_id)

        supabase = await get_async_supabase_admin()
        resp = (
            await supabase.table("ai_messages")
            .select("*")
            .eq("session_id", session_id)
            .order("created_at", desc=False)
            .range(offset, offset + limit - 1)
            .execute()
        )
        return resp.data or []

    # ------------------------------------------------------------------
    # Session state mutations
    # ------------------------------------------------------------------

    async def archive_session(self, session_id: str, user_id: str) -> dict:
        """Set session status to 'archived'.

        Returns the updated session row.
        """
        await self.get_session(session_id, user_id)  # verify access

        supabase = await get_async_supabase_admin()
        resp = (
            await supabase.table("ai_sessions")
            .update({"status": "archived", "updated_at": "now()"})
            .eq("id", session_id)
            .execute()
        )
        if not resp.data:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to archive session",
            )
        return resp.data[0]

    async def delete_session(self, session_id: str, user_id: str) -> None:
        """Hard-delete a session (cascade deletes messages via FK constraint).

        Raises 404/403 if the session does not belong to the user.
        """
        await self.get_session(session_id, user_id)  # verify access

        supabase = await get_async_supabase_admin()
        resp = (
            await supabase.table("ai_sessions").delete().eq("id", session_id).execute()
        )
        if resp.data is None:
            logger.error(f"Unexpected error deleting session {session_id}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to delete session",
            )

    # ------------------------------------------------------------------
    # Session title update
    # ------------------------------------------------------------------

    async def update_title(self, session_id: str, user_id: str, title: str) -> dict:
        """Update the display title of a session.

        Returns the updated session row.
        """
        await self.get_session(session_id, user_id)  # verify access

        supabase = await get_async_supabase_admin()
        resp = (
            await supabase.table("ai_sessions")
            .update({"title": title, "updated_at": "now()"})
            .eq("id", session_id)
            .execute()
        )
        if not resp.data:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to update session title",
            )
        return resp.data[0]
