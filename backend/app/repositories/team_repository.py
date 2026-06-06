"""Team repository for database operations."""

from typing import TYPE_CHECKING, Any, Dict, List, Optional, Union

from loguru import logger

from app.db.supabase_client import get_async_supabase_admin

if TYPE_CHECKING:
    from app.repositories.team_repository_orm import TeamRepositoryOrm


class TeamRepository:
    """Repository for team database operations."""

    async def get_user_teams(self, user_id: str) -> List[Dict[str, Any]]:
        """Get all teams the user is a member of."""
        client = await get_async_supabase_admin()

        # Get team IDs from memberships
        memberships = (
            await client.table("team_members")
            .select("team_id")
            .eq("user_id", user_id)
            .execute()
        )

        if not memberships.data:
            return []

        team_ids = [m["team_id"] for m in memberships.data]

        # Get team details
        teams = (
            await client.table("teams")
            .select("*")
            .in_("id", team_ids)
            .order("created_at", desc=True)
            .execute()
        )

        return teams.data or []

    async def get_team_by_id(
        self, team_id: str, user_id: str
    ) -> Optional[Dict[str, Any]]:
        """Get a team by ID if user has access."""
        client = await get_async_supabase_admin()

        # Check membership
        membership = (
            await client.table("team_members")
            .select("*")
            .eq("team_id", team_id)
            .eq("user_id", user_id)
            .execute()
        )

        if not membership.data:
            return None

        # Get team
        team = (
            await client.table("teams").select("*").eq("id", team_id).single().execute()
        )

        return team.data

    async def create_team(self, name: str, owner_id: str) -> Dict[str, Any]:
        """Create a new team and add owner as member."""
        client = await get_async_supabase_admin()

        # Create team
        team = (
            await client.table("teams")
            .insert({"name": name, "owner_id": owner_id})
            .select()
            .single()
            .execute()
        )

        if not team.data:
            raise Exception("Failed to create team")

        # Owner membership is added by the add_owner_as_member DB trigger
        # (teams_add_owner_trigger, mig 009 — the canonical mechanism per mig
        # 053/229). We do NOT insert it explicitly: the trigger already ran
        # AFTER INSERT ON teams, so a second explicit insert collides on the
        # team_members PK (23505) → every API team creation 500s. Dropping the
        # redundant insert is the prod-bug fix.

        return team.data

    async def update_team(
        self, team_id: str, user_id: str, **updates
    ) -> Optional[Dict[str, Any]]:
        """Update a team if user is owner."""
        client = await get_async_supabase_admin()

        # Check ownership
        team = (
            await client.table("teams")
            .select("*")
            .eq("id", team_id)
            .eq("owner_id", user_id)
            .execute()
        )

        if not team.data:
            return None

        # Update team
        result = (
            await client.table("teams")
            .update(updates)
            .eq("id", team_id)
            .select()
            .single()
            .execute()
        )

        return result.data

    async def delete_team(self, team_id: str, user_id: str) -> bool:
        """Delete a team if user is owner."""
        client = await get_async_supabase_admin()

        # Check ownership
        team = (
            await client.table("teams")
            .select("id")
            .eq("id", team_id)
            .eq("owner_id", user_id)
            .execute()
        )

        if not team.data:
            return False

        # Delete team (cascade will handle members)
        await client.table("teams").delete().eq("id", team_id).execute()

        return True

    async def get_team_members(
        self, team_id: str, user_id: str
    ) -> List[Dict[str, Any]]:
        """Get team members if user has access."""
        client = await get_async_supabase_admin()

        # Check membership
        membership = (
            await client.table("team_members")
            .select("*")
            .eq("team_id", team_id)
            .eq("user_id", user_id)
            .execute()
        )

        if not membership.data:
            return []

        # Get all members
        members = (
            await client.table("team_members")
            .select("*")
            .eq("team_id", team_id)
            .order("joined_at")
            .execute()
        )

        return members.data or []

    async def add_member(
        self, team_id: str, new_user_id: str, role: str = "member"
    ) -> Optional[Dict[str, Any]]:
        """Add a member to a team."""
        client = await get_async_supabase_admin()

        try:
            result = (
                await client.table("team_members")
                .insert({"team_id": team_id, "user_id": new_user_id, "role": role})
                .select()
                .single()
                .execute()
            )

            return result.data
        except Exception as e:
            if "23505" in str(e):  # Duplicate key
                return None
            raise

    async def update_member_role(
        self, team_id: str, target_user_id: str, role: str, requester_id: str
    ) -> bool:
        """Update a member's role if requester is owner/admin."""
        client = await get_async_supabase_admin()

        # Check if requester is owner or admin
        requester = (
            await client.table("team_members")
            .select("role")
            .eq("team_id", team_id)
            .eq("user_id", requester_id)
            .execute()
        )

        if not requester.data or requester.data[0]["role"] not in ["owner", "admin"]:
            return False

        # Update role
        await client.table("team_members").update({"role": role}).eq(
            "team_id", team_id
        ).eq("user_id", target_user_id).execute()

        return True

    async def remove_member(
        self, team_id: str, target_user_id: str, requester_id: str
    ) -> bool:
        """Remove a member from team."""
        client = await get_async_supabase_admin()

        # Check if requester is owner/admin or removing themselves
        if requester_id != target_user_id:
            requester = (
                await client.table("team_members")
                .select("role")
                .eq("team_id", team_id)
                .eq("user_id", requester_id)
                .execute()
            )

            if not requester.data or requester.data[0]["role"] not in [
                "owner",
                "admin",
            ]:
                return False

        # Don't allow removing the owner
        team = (
            await client.table("teams")
            .select("owner_id")
            .eq("id", team_id)
            .single()
            .execute()
        )
        if team.data and team.data["owner_id"] == target_user_id:
            return False

        # Remove member
        await client.table("team_members").delete().eq("team_id", team_id).eq(
            "user_id", target_user_id
        ).execute()

        return True

    async def get_team_by_invite_code(
        self, invite_code: str
    ) -> Optional[Dict[str, Any]]:
        """Get a team by its invite code."""
        client = await get_async_supabase_admin()

        result = (
            await client.table("teams")
            .select("*")
            .eq("invite_code", invite_code.upper())
            .execute()
        )

        return result.data[0] if result.data else None

    async def join_team_by_code(
        self, invite_code: str, user_id: str
    ) -> Optional[Dict[str, Any]]:
        """Join a team using invite code."""
        team = await self.get_team_by_invite_code(invite_code)

        if not team:
            return None

        # Add user as member
        member = await self.add_member(team["id"], user_id, "member")

        if member is None:
            raise Exception("Already a member of this team")

        return team


def get_team_repository() -> Union["TeamRepository", "TeamRepositoryOrm"]:
    """Return the right TeamRepository implementation per env.

    ORM when ``USE_ORM_TEAM`` is set AND the SQLAlchemy engine is configured;
    otherwise the legacy supabase-py REST path. A flag-on but engine-missing
    deploy logs once and falls back to REST (never crashes).
    """
    from app.core.config import settings

    if settings.USE_ORM_TEAM:
        from app.db.engine import is_configured

        if is_configured():
            from app.repositories.team_repository_orm import TeamRepositoryOrm

            return TeamRepositoryOrm()
        logger.warning(
            "USE_ORM_TEAM=true but SUPAVISOR_DATABASE_URL is empty "
            "— falling back to supabase-py path"
        )
    return TeamRepository()
