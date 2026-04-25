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

    async def list_persistent(self) -> List[Dict[str, Any]]:
        """List agents marked as persistent workers (M3 Delegate targets).

        Returns slug + name + description so the PromptComposer can
        render an `<available_workers>` block. Sorted by slug for
        stable fingerprinting. Empty list when no persistent agents
        exist (Delegate then becomes self-documenting "no workers
        available").
        """
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE)
                .select("id,slug,name,description,model")
                .eq("persistent", True)
                .order("slug")
                .execute()
            )
            return result.data or []
        except Exception as e:
            logger.error(f"Failed to list persistent agents: {e}")
            return []

    async def list_accessible(
        self,
        user_id: UUID,
        team_ids: Optional[List[int]] = None,
        project_ids: Optional[List[int]] = None,
    ) -> List[Dict[str, Any]]:
        """List agents accessible to the user.

        Visible set = union of:
          * ``is_system_preset = true`` (every user sees presets)
          * ``user_id = user_id`` (the user's own agents)
          * ``team_id IN team_ids`` (agents scoped to any of the user's teams)
          * ``project_id IN project_ids`` (agents scoped to user's projects)

        The backend uses the service-role client (RLS bypassed), so this OR
        filter must be enforced here to match migration 138's RLS policy.

        Sorted by ``sort_order`` then ``name``.
        """
        try:
            client = await self._get_client()
            filters = ["is_system_preset.eq.true", f"user_id.eq.{user_id}"]
            if team_ids:
                filters.append(f"team_id.in.({','.join(str(i) for i in team_ids)})")
            if project_ids:
                filters.append(
                    f"project_id.in.({','.join(str(i) for i in project_ids)})"
                )

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

    async def insert(self, fields: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new ai_agents row.

        The caller is responsible for setting ``is_system_preset`` (false for
        user-created agents). Returns the inserted row.
        """
        client = await self._get_client()
        result = await client.table(self.TABLE).insert(fields).execute()
        if not result.data:
            raise RuntimeError("insert returned no data")
        return result.data[0]

    # Fields snapshotted into ai_agent_versions. Narrower than update_fields'
    # accepted fields — only behavioral content, per Phase 2 plan.
    _VERSIONED_AGENT_FIELDS = (
        "identity_md",
        "soul_md",
        "agent_md",
        "model",
        "temperature",
        "max_tokens",
    )

    async def update_fields_versioned(
        self,
        agent_id: UUID,
        updates: Dict[str, Any],
        created_by: Optional[UUID] = None,
        notes: Optional[str] = None,
    ) -> None:
        """Snapshot-then-update: record pre-update behavioral content into
        ai_agent_versions, then apply the patch with bumped current_version.

        No-op if none of the tracked behavioral fields actually differs from
        the current row (silences seed-loader reruns). Raises ValueError if
        the agent does not exist.

        Seed loader should keep using ``update_fields`` (non-versioned) —
        bulk idempotent sync should not pollute version history.

        Note: the snapshot INSERT and live UPDATE are NOT in a single transaction.
        See ``SkillRepository.upsert_file_versioned`` for the same limitation and
        Phase 3 mitigation path.
        """
        client = await self._get_client()
        result = (
            await client.table(self.TABLE)
            .select("*")
            .eq("id", str(agent_id))
            .maybe_single()
            .execute()
        )
        current = result.data if result and result.data else None
        if current is None:
            raise ValueError(f"agent {agent_id} not found")

        # A full no-op (every incoming value equals current) skips entirely —
        # this is what silences seed-loader reruns that repost identical
        # content. If ANY field differs, we do write; snapshots only fire
        # for tracked-field changes so non-behavioral updates (budgets,
        # paused_reason, etc.) don't pollute version history.
        any_changed = any(updates[k] != current.get(k) for k in updates)
        if not any_changed:
            return

        tracked_changed = any(
            k in updates and updates[k] != current.get(k)
            for k in self._VERSIONED_AGENT_FIELDS
        )

        patch: Dict[str, Any] = dict(updates)
        if tracked_changed:
            current_version = int(current.get("current_version") or 1)
            snapshot: Dict[str, Any] = {
                "agent_id": str(agent_id),
                "version_number": current_version,
                "notes": notes,
                "created_by": str(created_by) if created_by else None,
            }
            for field in self._VERSIONED_AGENT_FIELDS:
                snapshot[field] = current.get(field)

            await client.table("ai_agent_versions").insert(snapshot).execute()
            patch["current_version"] = current_version + 1

        await client.table(self.TABLE).update(patch).eq("id", str(agent_id)).execute()
