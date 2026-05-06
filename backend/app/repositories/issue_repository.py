"""Issue repository — data access for the issues table (PR-D1).

CRUD + atomic identifier allocation via the issue_next_identifier RPC.

Atomic-create contract:
    - issue_next_identifier() must be called in the SAME transaction as the
      INSERT (otherwise concurrent callers can take the same identifier and
      the second INSERT will fail the unique index).
    - The current implementation runs both calls in one PostgREST round-trip
      via the supabase RPC + insert in sequence. Under heavy contention this
      pattern still leaves a small window — for production we plan to wrap
      both in a SECURITY DEFINER stored procedure (deferred to PR-D2).
"""

from __future__ import annotations

from typing import Any, Optional

from loguru import logger

from app.db.supabase_client import get_async_supabase_admin


class IssueRepository:
    TABLE_NAME = "issues"

    async def _client(self):
        return await get_async_supabase_admin()

    async def atomic_create(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Allocate (issue_number, identifier) and INSERT atomically.

        payload should NOT contain `id`, `issue_number`, `identifier`,
        `created_at`, or `updated_at` — the DB sets those.

        Uses the `issue_create_atomic(payload jsonb)` stored procedure
        (migration 173) which performs the counter UPDATE + INSERT in a
        single PG transaction. If INSERT fails (RLS / CHECK / FK), the
        counter rolls back too — no MH-N gap.
        """
        # UUIDs (and other non-JSON-native types) need to be string-coerced
        # before being sent through PostgREST RPC payload.
        from uuid import UUID

        sanitized: dict[str, Any] = {}
        for k, v in payload.items():
            sanitized[k] = str(v) if isinstance(v, UUID) else v

        client = await self._client()
        result = await client.rpc(
            "issue_create_atomic",
            {"payload": sanitized},
        ).execute()
        if not result.data:
            raise RuntimeError("issue_create_atomic returned empty result")
        # Postgres function returns issues row; supabase-py wraps it as either
        # the row dict directly OR a single-element list, depending on version.
        row = (
            result.data
            if isinstance(result.data, dict)
            else (
                result.data[0]
                if isinstance(result.data, list) and result.data
                else None
            )
        )
        if not row:
            raise RuntimeError("issue_create_atomic returned malformed result")
        logger.info("Created issue %s (id=%s)", row.get("identifier"), row.get("id"))
        return row

    async def get_by_id(self, issue_id: int) -> Optional[dict[str, Any]]:
        client = await self._client()
        result = (
            await client.table(self.TABLE_NAME)
            .select("*")
            .eq("id", issue_id)
            .limit(1)
            .execute()
        )
        return result.data[0] if result.data else None

    async def get_by_identifier(self, identifier: str) -> Optional[dict[str, Any]]:
        client = await self._client()
        result = (
            await client.table(self.TABLE_NAME)
            .select("*")
            .eq("identifier", identifier)
            .limit(1)
            .execute()
        )
        return result.data[0] if result.data else None

    async def update(self, issue_id: int, patch: dict[str, Any]) -> dict[str, Any]:
        if not patch:
            existing = await self.get_by_id(issue_id)
            if not existing:
                raise ValueError(f"issue id={issue_id} not found")
            return existing
        client = await self._client()
        result = (
            await client.table(self.TABLE_NAME)
            .update(patch)
            .eq("id", issue_id)
            .execute()
        )
        if not result.data:
            raise ValueError(f"issue id={issue_id} not found or update no-op")
        return result.data[0]

    async def list_for_user(
        self,
        user_id: str,
        *,
        status: Optional[str] = None,
        project_id: Optional[int] = None,
        team_id: Optional[int] = None,
        include_hidden: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        """List issues visible to user_id. Uses the RLS SELECT policy by virtue
        of going through service_role + explicit creator/assignee filter.

        Returns (items, total).
        """
        # Defensive: validate user_id is a real UUID before interpolating into
        # the PostgREST `or_` filter expression. PostgREST or-syntax uses commas,
        # parens, and dots as separators — a malformed user_id (or one from an
        # untrusted source in the future) could otherwise extend the filter.
        from uuid import UUID

        UUID(user_id)  # raises ValueError on bad input

        client = await self._client()
        # We always filter on creator/assignee/team-membership at the app layer
        # since we use service_role (which bypasses RLS). Mirror the policy logic.
        # For simplicity here: only own + assignee. Team / project visibility
        # is folded in via separate queries / RPC in PR-D2.
        builder = (
            client.table(self.TABLE_NAME)
            .select("*", count="exact")
            .or_(f"created_by_user_id.eq.{user_id},assignee_user_id.eq.{user_id}")
            .order("created_at", desc=True)
            .range(offset, offset + limit - 1)
        )
        if status:
            builder = builder.eq("status", status)
        if project_id:
            builder = builder.eq("project_id", project_id)
        if team_id:
            builder = builder.eq("team_id", team_id)
        if not include_hidden:
            builder = builder.is_("hidden_at", "null")

        result = await builder.execute()
        return result.data or [], result.count or 0

    async def soft_delete(self, issue_id: int) -> dict[str, Any]:
        """User-facing delete — sets hidden_at, keeps row for audit / undo."""
        from datetime import datetime, timezone

        return await self.update(
            issue_id, {"hidden_at": datetime.now(timezone.utc).isoformat()}
        )

    async def transition_status(
        self,
        issue_id: int,
        new_status: str,
        *,
        dbos_workflow_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Status-transition setter. Includes lifecycle timestamp side-effects
        (started_at / completed_at / cancelled_at) per design doc Protocol 5."""
        from datetime import datetime, timezone

        patch: dict[str, Any] = {"status": new_status}
        now = datetime.now(timezone.utc).isoformat()

        if new_status == "in_progress":
            patch["started_at"] = now
        elif new_status == "done":
            patch["completed_at"] = now
        elif new_status == "cancelled":
            patch["cancelled_at"] = now

        if dbos_workflow_id is not None:
            patch["dbos_workflow_id"] = dbos_workflow_id

        return await self.update(issue_id, patch)


issue_repository = IssueRepository()
