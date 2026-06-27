"""Read-only broadcast queries + per-channel watermark for Agent Broadcast.

Provides data access for the DBOS broadcast scanner:
  - Candidate channels (non-archived channels that have ≥1 agent in agent_channels)
  - Team member user_ids
  - Completed workflow counts since a watermark timestamp (READ ONLY on task_tracking)
  - Watermark get/set in system_settings

Security note (SEC-AGENT-05):
  completed_workflow_counts_since() returns ONLY aggregate counts + task_kind enum.
  It NEVER selects subtitle/metadata/title or any resource content columns — the
  broadcast body is structurally incapable of leaking user-scope private data.

task_tracking discipline (CLAUDE.md route C):
  This module ONLY reads task_tracking (status / completed_at / user_id / task_kind).
  It MUST NOT write or PATCH any task_tracking column.

Array-bind choice:
  user_id = ANY(CAST(:uids AS uuid[])) with the uids parameter as a Python list.
  SQLAlchemy + asyncpg handles Python list → PostgreSQL uuid[] via the CAST.
  If this causes DataError in some Supavisor configurations (cf. increment_mentions),
  fall back to an expanded IN clause.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

from loguru import logger

from app.db import engine as db_engine


def _bigint(v: Any) -> int:
    return int(v)


class ChatBroadcastRepository:
    """Read-only broadcast queries + per-channel watermark."""

    async def list_broadcast_candidate_channels(self) -> list[dict]:
        """Return non-archived channels that have ≥1 agent in agent_channels.

        Each entry: {"channel_id": int, "team_id": int, "agent_ids": list[str]}
        """
        rows = await db_engine.fetch_all(
            """
            SELECT ac.channel_id,
                   c.team_id,
                   array_agg(ac.agent_id::text) AS agent_ids
              FROM public.agent_channels ac
              JOIN public.channels c ON c.id = ac.channel_id
             WHERE c.is_archived = false
             GROUP BY ac.channel_id, c.team_id
            """
        )
        result = []
        for row in rows:
            agent_ids_raw = row.get("agent_ids") or []
            result.append(
                {
                    "channel_id": int(row["channel_id"]),
                    "team_id": int(row["team_id"]),
                    "agent_ids": [str(a) for a in agent_ids_raw],
                }
            )
        return result

    async def team_member_ids(self, team_id: int) -> list[str]:
        """Return user_ids (as plain strings) for all members of team_id."""
        rows = await db_engine.fetch_all(
            "SELECT user_id FROM public.team_members WHERE team_id = :tid",
            {"tid": _bigint(team_id)},
        )
        return [str(r["user_id"]) for r in rows]

    async def completed_workflow_counts_since(
        self,
        user_ids: list[str],
        since: Optional[datetime],
    ) -> dict:
        """Aggregate completed workflow counts for the given users since a timestamp.

        Returns: {"total": int, "by_kind": {task_kind: count}, "max_completed_at": datetime|None}

        Reads task_tracking READ ONLY — never writes any task_tracking column.
        Guard: empty user_ids returns zeros without querying.
        """
        if not user_ids:
            return {"total": 0, "by_kind": {}, "max_completed_at": None}

        params: dict[str, Any] = {"uids": user_ids}
        time_clause = ""
        if since is not None:
            time_clause = "AND completed_at > :since"
            params["since"] = since

        rows = await db_engine.fetch_all(
            f"""
            SELECT task_kind,
                   COUNT(*) AS cnt,
                   MAX(completed_at) AS max_completed_at
              FROM public.task_tracking
             WHERE status = 'completed'
               AND task_kind = 'workflow'
               AND user_id = ANY(CAST(:uids AS uuid[]))
               {time_clause}
             GROUP BY task_kind
            """,
            params,
        )

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
        row = await db_engine.fetch_one(
            "SELECT value FROM public.system_settings WHERE key = :key",
            {"key": key},
        )
        if row is None:
            return None

        # asyncpg returns JSONB string values as Python str (already JSON-decoded).
        raw = row["value"]
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
        Value stored as ISO-8601 string inside a JSONB column.
        Uses INSERT ... ON CONFLICT DO UPDATE for idempotent upsert.
        """
        key = f"broadcast_watermark_channel_{_bigint(channel_id)}"
        # Store the ISO string as a JSON-encoded string value (JSONB requires valid JSON).
        value_json = json.dumps(ts.isoformat())
        await db_engine.execute(
            """
            INSERT INTO public.system_settings (key, value)
            VALUES (:key, CAST(:value AS jsonb))
            ON CONFLICT (key) DO UPDATE
              SET value = CAST(:value AS jsonb),
                  updated_at = now()
            """,
            {"key": key, "value": value_json},
        )


_broadcast_repo: Optional[ChatBroadcastRepository] = None


def get_broadcast_repository() -> ChatBroadcastRepository:
    global _broadcast_repo
    if _broadcast_repo is None:
        _broadcast_repo = ChatBroadcastRepository()
    return _broadcast_repo
