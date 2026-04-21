"""Repository for ai_agents + agent_skills tables (AI Library Phase 1)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from uuid import UUID

from loguru import logger

from app.db.supabase_client import get_async_supabase_admin


class AgentRepository:
    """Data access for ai_agents + agent_skills tables.

    Uses the service-role (admin) client because access control is
    enforced at the route layer via user-scoped clients. See
    migration 138_ai_library_phase1.sql for schema details.
    """

    TABLE = "ai_agents"
    BINDING_TABLE = "agent_skills"

    async def _get_client(self):
        return await get_async_supabase_admin()

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def get_by_slug(self, slug: str) -> Optional[Dict[str, Any]]:
        """Fetch a single agent by slug; returns None if not found."""
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE)
                .select("*")
                .eq("slug", slug)
                .maybe_single()
                .execute()
            )
            return result.data if result and result.data else None
        except Exception as e:
            logger.error(f"Failed to get agent by slug '{slug}': {e}")
            return None

    async def get_by_id(self, agent_id: UUID) -> Optional[Dict[str, Any]]:
        """Fetch an agent by UUID; returns None if not found."""
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE)
                .select("*")
                .eq("id", str(agent_id))
                .maybe_single()
                .execute()
            )
            return result.data if result and result.data else None
        except Exception as e:
            logger.error(f"Failed to get agent by id {agent_id}: {e}")
            return None

    async def list_accessible(
        self,
        user_id: UUID,
        project_id: Optional[int] = None,
        team_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """List agents accessible to the user.

        Returns system presets + user-owned + project/team scoped agents,
        ordered by sort_order then name.
        """
        try:
            client = await self._get_client()
            filters = ["is_system_preset.eq.true", f"user_id.eq.{user_id}"]
            if project_id is not None:
                filters.append(f"project_id.eq.{project_id}")
            if team_id is not None:
                filters.append(f"team_id.eq.{team_id}")

            query = (
                client.table(self.TABLE)
                .select("*")
                .or_(",".join(filters))
                .order("sort_order")
                .order("name")
            )
            result = await query.execute()
            return result.data or []
        except Exception as e:
            logger.error(f"Failed to list accessible agents for user {user_id}: {e}")
            return []

    async def get_skill_ids(self, agent_id: UUID) -> List[int]:
        """Return the ordered list of enabled skill IDs bound to an agent.

        skill_id is BIGINT per migration 139 (see agent_skills.skill_id FK).
        """
        try:
            client = await self._get_client()
            result = (
                await client.table(self.BINDING_TABLE)
                .select("skill_id")
                .eq("agent_id", str(agent_id))
                .eq("enabled", True)
                .order("sort_order")
                .execute()
            )
            return [int(row["skill_id"]) for row in (result.data or [])]
        except Exception as e:
            logger.error(f"Failed to get skill ids for agent {agent_id}: {e}")
            return []

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    async def update_skill_bindings(self, agent_id: UUID, skill_ids: List[int]) -> None:
        """Replace all skill bindings for an agent (delete existing + insert new).

        Preserves requested order via sort_order.
        """
        try:
            client = await self._get_client()
            await (
                client.table(self.BINDING_TABLE)
                .delete()
                .eq("agent_id", str(agent_id))
                .execute()
            )

            if skill_ids:
                rows = [
                    {
                        "agent_id": str(agent_id),
                        "skill_id": sid,
                        "sort_order": i,
                        "enabled": True,
                    }
                    for i, sid in enumerate(skill_ids)
                ]
                await client.table(self.BINDING_TABLE).insert(rows).execute()

            logger.info(
                "Updated skill bindings for agent %s (%d skills)",
                agent_id,
                len(skill_ids),
            )
        except Exception as e:
            logger.error(f"Failed to update skill bindings for agent {agent_id}: {e}")
            raise

    async def update_fields(
        self, agent_id: UUID, updates: Dict[str, Any]
    ) -> Dict[str, Any]:
        """PATCH-style update on ai_agents; returns the updated row or {}."""
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE)
                .update(updates)
                .eq("id", str(agent_id))
                .execute()
            )
            return result.data[0] if result.data else {}
        except Exception as e:
            logger.error(f"Failed to update agent {agent_id}: {e}")
            raise
