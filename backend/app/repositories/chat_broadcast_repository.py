"""Read-only broadcast queries + per-channel watermark for Agent Broadcast.

Provides data access for the DBOS broadcast scanner:
  - Candidate conversations (non-archived, non-1:1 conversations that have ≥1
    agent member in conversation_members) — repointed in Phase 3 W0 from the
    legacy agent_channels ⋈ channels tables (mig 333 drops those tables).
  - Team member user_ids
  - Completed workflow counts since a watermark timestamp (READ ONLY on task_tracking)
  - Watermark get/set in system_settings

ORM session scopes (read_scope/write_scope); the two aggregate/reporting
reads keep their SQL bodies (array_agg / COUNT DISTINCT / dynamic time
clause are clearer as SQL — documented exceptions per the convergence
doctrine), the watermark get/set run on the ``SystemSettings`` model.

Phase 3 W0 note (candidate query only):
  list_broadcast_candidate_channels() now reads conversations/conversation_members
  instead of channels/agent_channels. The returned dict keys ("channel_id",
  "team_id", "agent_ids") are intentionally UNCHANGED from the legacy shape —
  "channel_id" now carries a conversations.id and "team_id" a
  conversations.scope_id — so the broadcast service loop and its watermark/
  dedup handling stay untouched.

Security note (SEC-AGENT-05):
  completed_workflow_counts_since() returns ONLY aggregate counts + task_kind enum.
  It NEVER selects subtitle/metadata/title or any resource content columns — the
  broadcast body is structurally incapable of leaking user-scope private data.

task_tracking discipline (CLAUDE.md route C):
  This module ONLY reads task_tracking (status / completed_at / user_id / task_kind).
  It MUST NOT write or PATCH any task_tracking column.

Team scoping:
  completed_workflow_counts_since JOINs team_members (by team_id) rather than
  binding a user_id array — avoids Supavisor/asyncpg uuid[] bind fragility and
  needs no separate member-id fetch. team_members PK is (team_id, user_id)
  (migration 051) so the JOIN matches each task at most once; COUNT uses
  COUNT(DISTINCT dbos_workflow_id) as a constraint-independent safeguard.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from loguru import logger
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.session import read_scope, write_scope
from app.models import SystemSettings


def _bigint(v: Any) -> int:
    return int(v)


class ChatBroadcastRepository:
    """Read-only broadcast queries + per-channel watermark."""

    async def list_broadcast_candidate_channels(self) -> list[dict]:
        """Return non-archived group/public conversations with ≥1 agent member.

        Each entry: {"channel_id": int, "team_id": int, "agent_ids": list[str]}
        ("channel_id" holds a conversations.id, "team_id" a conversations.scope_id
        — field names retained from the pre-Phase-3 shape, see module docstring).

        Excludes type='direct_agent' (1:1 agent DMs): broadcast only targets
        shared team conversations, matching the pre-Phase-3 semantics where
        agent_channels/channels had no 1:1 concept at all. SQL body kept
        (array_agg + GROUP BY reporting read)."""
        async with read_scope() as session:
            result = await session.execute(
                text(
                    """
                    SELECT c.id AS channel_id,
                           c.scope_id AS team_id,
                           array_agg(cm.agent_id::text) AS agent_ids
                      FROM public.conversation_members cm
                      JOIN public.conversations c ON c.id = cm.conversation_id
                     WHERE cm.member_type = 'agent'
                       AND c.archived_at IS NULL
                       AND c.type <> 'direct_agent'
                     GROUP BY c.id, c.scope_id
                    """
                )
            )
            rows = result.mappings().all()
        out = []
        for row in rows:
            agent_ids_raw = row.get("agent_ids") or []
            out.append(
                {
                    "channel_id": int(row["channel_id"]),
                    "team_id": int(row["team_id"]),
                    "agent_ids": [str(a) for a in agent_ids_raw],
                }
            )
        return out

    async def completed_workflow_counts_since(
        self,
        team_id: int,
        since: Optional[datetime],
    ) -> dict:
        """Aggregate completed workflow counts for a team's members since a timestamp.

        Returns: {"total": int, "by_kind": {task_kind: count}, "max_completed_at": datetime|None}

        Reads task_tracking READ ONLY — never writes any task_tracking column.
        Scopes to team membership by JOINing team_members on the task owner —
        no user_id array-bind (avoids Supavisor/asyncpg uuid[] bind fragility);
        a team with no members simply yields zero rows. SQL body kept
        (COUNT DISTINCT / MAX aggregates + the optional time clause)."""
        params: dict[str, Any] = {"tid": _bigint(team_id)}
        time_clause = ""
        if since is not None:
            time_clause = "AND tt.completed_at > :since"
            params["since"] = since

        async with read_scope() as session:
            result = await session.execute(
                text(
                    f"""
                    SELECT tt.task_kind AS task_kind,
                           COUNT(DISTINCT tt.dbos_workflow_id) AS cnt,
                           MAX(tt.completed_at) AS max_completed_at
                      FROM public.task_tracking tt
                      JOIN public.team_members tm ON tm.user_id = tt.user_id
                     WHERE tm.team_id = :tid
                       AND tt.status = 'completed'
                       AND tt.task_kind = 'workflow'
                       {time_clause}
                     GROUP BY tt.task_kind
                    """
                ),
                params,
            )
            rows = result.mappings().all()

        total = 0
        by_kind: dict[str, int] = {}
        max_completed_at: Optional[datetime] = None

        for row in rows:
            kind = str(row["task_kind"])
            cnt = int(row["cnt"])
            total += cnt
            by_kind[kind] = cnt
            mc = row.get("max_completed_at")
            if mc is not None:
                if isinstance(mc, str):
                    mc = datetime.fromisoformat(mc)
                # Normalize to UTC-aware so comparisons against the (UTC-aware)
                # watermark from get_watermark never raise naive/aware TypeError.
                if mc.tzinfo is None:
                    mc = mc.replace(tzinfo=timezone.utc)
                if max_completed_at is None or mc > max_completed_at:
                    max_completed_at = mc

        return {
            "total": total,
            "by_kind": by_kind,
            "max_completed_at": max_completed_at,
        }

    async def get_watermark(self, channel_id: int) -> Optional[datetime]:
        """Read per-channel broadcast watermark from system_settings.

        Key: broadcast_watermark_channel_{channel_id}
        Returns a UTC-aware datetime or None when the key is absent.
        """
        key = f"broadcast_watermark_channel_{_bigint(channel_id)}"
        async with read_scope() as session:
            raw = (
                await session.execute(
                    select(SystemSettings.value).where(SystemSettings.key == key)
                )
            ).scalar()
        if raw is None:
            return None

        # The watermark is stored as a JSON string value inside the JSONB
        # column — the ORM hands it back as a Python str.
        ts_str = str(raw)

        # A corrupted/unparseable watermark must not crash the scanner loop —
        # treat it like a missing watermark (first-run: skip + reset next pass).
        try:
            dt = datetime.fromisoformat(ts_str)
        except ValueError:
            logger.warning(
                f"[broadcast] unparseable watermark for channel {channel_id}: "
                f"{ts_str!r} — treating as absent"
            )
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt

    async def set_watermark(self, channel_id: int, ts: datetime) -> None:
        """Write per-channel broadcast watermark to system_settings.

        Key: broadcast_watermark_channel_{channel_id}
        Value stored as an ISO-8601 JSON string inside the JSONB column
        (identical wire value to the legacy CAST(:value AS jsonb) path — the
        JSONB bind serializes the Python str to the same JSON string).
        Uses INSERT ... ON CONFLICT DO UPDATE for idempotent upsert.
        """
        key = f"broadcast_watermark_channel_{_bigint(channel_id)}"
        stmt = pg_insert(SystemSettings).values(key=key, value=ts.isoformat())
        stmt = stmt.on_conflict_do_update(
            index_elements=["key"],
            set_={"value": stmt.excluded.value, "updated_at": func.now()},
        )
        async with write_scope() as session:
            await session.execute(stmt)


_broadcast_repo: Optional[ChatBroadcastRepository] = None


def get_broadcast_repository() -> ChatBroadcastRepository:
    global _broadcast_repo
    if _broadcast_repo is None:
        _broadcast_repo = ChatBroadcastRepository()
    return _broadcast_repo
