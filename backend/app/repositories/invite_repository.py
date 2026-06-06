"""Invite repository for database operations."""

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Union

from loguru import logger

from app.db.supabase_client import get_async_supabase_admin

if TYPE_CHECKING:
    from app.repositories.invite_repository_orm import InviteRepositoryOrm

# Expiry time mapping in milliseconds
EXPIRY_MAP = {
    "30m": 30 * 60 * 1000,
    "1h": 60 * 60 * 1000,
    "6h": 6 * 60 * 60 * 1000,
    "12h": 12 * 60 * 60 * 1000,
    "1d": 24 * 60 * 60 * 1000,
    "7d": 7 * 24 * 60 * 60 * 1000,
}


class InviteRepository:
    """Repository for team invite database operations."""

    async def create_invite(
        self,
        team_id: str,
        created_by: str,
        expires_in: Optional[str] = None,
        max_uses: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Create a new team invite."""
        client = await get_async_supabase_admin()

        # Calculate expiry time
        expires_at = None
        if expires_in and expires_in != "never" and expires_in in EXPIRY_MAP:
            expires_at = (
                datetime.utcnow() + timedelta(milliseconds=EXPIRY_MAP[expires_in])
            ).isoformat()

        result = (
            await client.table("team_invites")
            .insert(
                {
                    "team_id": team_id,
                    "created_by": created_by,
                    "expires_at": expires_at,
                    "max_uses": max_uses,
                }
            )
            .select()
            .single()
            .execute()
        )

        if not result.data:
            raise Exception("Failed to create invite")

        return result.data

    async def get_invites_by_team(self, team_id: str) -> List[Dict[str, Any]]:
        """Get all invites for a team."""
        client = await get_async_supabase_admin()

        result = (
            await client.table("team_invites")
            .select("*")
            .eq("team_id", team_id)
            .order("created_at", desc=True)
            .execute()
        )

        return result.data or []

    async def get_invite_by_id(self, invite_id: str) -> Optional[Dict[str, Any]]:
        """Get an invite by ID."""
        client = await get_async_supabase_admin()

        result = (
            await client.table("team_invites").select("*").eq("id", invite_id).execute()
        )

        return result.data[0] if result.data else None

    async def get_invite_by_code(self, code: str) -> Optional[Dict[str, Any]]:
        """Get an invite by code with team info."""
        client = await get_async_supabase_admin()

        result = (
            await client.table("team_invites")
            .select("*, teams(id, name)")
            .eq("code", code)
            .execute()
        )

        return result.data[0] if result.data else None

    async def delete_invite(self, invite_id: str, user_id: str) -> bool:
        """Delete an invite if user has permission."""
        client = await get_async_supabase_admin()

        # Get invite to check team
        invite = await self.get_invite_by_id(invite_id)
        if not invite:
            return False

        # Check if user is team owner/admin
        membership = (
            await client.table("team_members")
            .select("role")
            .eq("team_id", invite["team_id"])
            .eq("user_id", user_id)
            .execute()
        )

        if not membership.data or membership.data[0]["role"] not in ["owner", "admin"]:
            return False

        await client.table("team_invites").delete().eq("id", invite_id).execute()

        return True

    async def accept_invite(self, code: str, user_id: str) -> Optional[Dict[str, Any]]:
        """Accept an invite and join the team."""
        client = await get_async_supabase_admin()

        # Get invite with team info
        invite = await self.get_invite_by_code(code)

        if not invite:
            raise Exception("Invalid invite code")

        # Check expiration
        if invite.get("expires_at"):
            expires_at = datetime.fromisoformat(
                invite["expires_at"].replace("Z", "+00:00")
            )
            if expires_at < datetime.now(expires_at.tzinfo):
                raise Exception("Invite has expired")

        # Check max uses
        if invite.get("max_uses") and invite["use_count"] >= invite["max_uses"]:
            raise Exception("Invite has reached max uses")

        # Add user to team
        try:
            await client.table("team_members").insert(
                {"team_id": invite["team_id"], "user_id": user_id, "role": "member"}
            ).execute()
        except Exception as e:
            if "23505" in str(e):
                raise Exception("Already a member")
            raise

        # Increment use count
        await client.table("team_invites").update(
            {"use_count": invite["use_count"] + 1}
        ).eq("id", invite["id"]).execute()

        return {
            "team_id": invite["team_id"],
            "team_name": invite.get("teams", {}).get("name", "Unknown Team"),
        }

    async def check_user_can_manage_invites(self, team_id: str, user_id: str) -> bool:
        """Check if user can manage invites for a team."""
        client = await get_async_supabase_admin()

        membership = (
            await client.table("team_members")
            .select("role")
            .eq("team_id", team_id)
            .eq("user_id", user_id)
            .execute()
        )

        if not membership.data:
            return False

        return membership.data[0]["role"] in ["owner", "admin"]


def get_invite_repository() -> Union["InviteRepository", "InviteRepositoryOrm"]:
    """Return the right InviteRepository implementation per env.

    ORM when ``USE_ORM_INVITE`` is set AND the SQLAlchemy engine is configured;
    otherwise the legacy supabase-py REST path. A flag-on but engine-missing
    deploy logs once and falls back to REST (never crashes).
    """
    from app.core.config import settings

    if settings.USE_ORM_INVITE:
        from app.db.engine import is_configured

        if is_configured():
            from app.repositories.invite_repository_orm import InviteRepositoryOrm

            return InviteRepositoryOrm()
        logger.warning(
            "USE_ORM_INVITE=true but SUPAVISOR_DATABASE_URL is empty "
            "— falling back to supabase-py path"
        )
    return InviteRepository()
