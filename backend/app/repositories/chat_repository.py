"""Data access for Team Chat (PHASE-1). Uses the privileged db_engine; the
service layer enforces membership. RLS guards the separate frontend path."""

from __future__ import annotations

import json
from typing import Any, Optional

from sqlalchemy import text

from app.db import engine as db_engine


def _bigint(v: Any) -> int:
    return int(v)


class ChatRepository:
    async def create_channel(
        self,
        *,
        creator_id: str,
        team_id: int,
        type: str,
        name: Optional[str],
        history_mode: str,
        member_ids: list[str],
    ) -> dict[str, Any]:
        eng = db_engine.get_engine()
        async with eng.begin() as conn:
            row = (
                (
                    await conn.execute(
                        text(
                            """
                        INSERT INTO public.channels (team_id, type, history_mode, name, created_by)
                        VALUES (:team_id, :type, :history_mode, :name, :creator)
                        RETURNING id, team_id, type, history_mode, name, topic, last_message_seq, created_at
                        """
                        ),
                        {
                            "team_id": _bigint(team_id),
                            "type": type,
                            "history_mode": history_mode,
                            "name": name,
                            "creator": creator_id,
                        },
                    )
                )
                .mappings()
                .one()
            )
            cid = row["id"]
            members = {creator_id, *member_ids}
            for uid in members:
                await conn.execute(
                    text(
                        """
                        INSERT INTO public.channel_members (channel_id, user_id, roles)
                        VALUES (:cid, :uid, CAST(:roles AS text[]))
                        ON CONFLICT (channel_id, user_id) DO NOTHING
                        """
                    ),
                    {
                        "cid": cid,
                        "uid": uid,
                        "roles": ["owner"] if uid == creator_id else [],
                    },
                )
            return dict(row)

    async def add_members(self, *, channel_id: int, user_ids: list[str]) -> int:
        if not user_ids:
            return 0
        eng = db_engine.get_engine()
        async with eng.begin() as conn:
            n = 0
            for uid in user_ids:
                res = await conn.execute(
                    text(
                        """
                        INSERT INTO public.channel_members (channel_id, user_id)
                        VALUES (:cid, :uid)
                        ON CONFLICT (channel_id, user_id) DO NOTHING
                        """
                    ),
                    {"cid": _bigint(channel_id), "uid": uid},
                )
                n += res.rowcount or 0
            return n

    async def is_member(self, *, channel_id: int, user_id: str) -> bool:
        v = await db_engine.fetch_val(
            """
            SELECT EXISTS (
              SELECT 1 FROM public.channel_members
              WHERE channel_id = :cid AND user_id = :uid
            )
            """,
            {"cid": _bigint(channel_id), "uid": user_id},
        )
        return bool(v)

    async def is_team_member(self, *, team_id: int, user_id: str) -> bool:
        result = await db_engine.fetch_val(
            "SELECT EXISTS(SELECT 1 FROM team_members WHERE team_id = :tid AND user_id = :uid)",
            {"tid": _bigint(team_id), "uid": user_id},
        )
        return bool(result)

    async def channel_team_id(self, *, channel_id: int) -> int | None:
        return await db_engine.fetch_val(
            "SELECT team_id FROM channels WHERE id = :cid",
            {"cid": _bigint(channel_id)},
        )

    async def get_my_channels(self, user_id: str) -> list[dict[str, Any]]:
        rows = await db_engine.fetch_all(
            """
            SELECT c.id, c.team_id, c.type, c.history_mode, c.name, c.topic,
                   c.last_message_seq, c.created_at,
                   GREATEST(c.last_message_seq - cm.last_read_seq, 0) AS unread
              FROM public.channel_members cm
              JOIN public.channels c ON c.id = cm.channel_id
             WHERE cm.user_id = :uid AND c.is_archived = false AND cm.open = true
             ORDER BY c.last_message_seq DESC
            """,
            {"uid": user_id},
        )
        return [dict(r) for r in rows]

    async def send_message(
        self,
        *,
        channel_id: int,
        sender_id: Optional[str],
        sender_type: str,
        content_type: str,
        body: dict[str, Any],
        reply_to_id: Optional[int],
        from_bot_agent_id: Optional[str] = None,
    ) -> dict[str, Any]:
        eng = db_engine.get_engine()
        async with eng.begin() as conn:
            seq = (
                await conn.execute(
                    text(
                        """
                        UPDATE public.channels
                           SET last_message_seq = last_message_seq + 1
                         WHERE id = :cid
                         RETURNING last_message_seq
                        """
                    ),
                    {"cid": _bigint(channel_id)},
                )
            ).scalar_one()
            row = (
                (
                    await conn.execute(
                        text(
                            """
                        INSERT INTO public.channel_messages
                          (channel_id, seq, sender_id, sender_type, content_type, body,
                           reply_to_id, from_bot_agent_id)
                        VALUES
                          (:cid, :seq, :sender, :stype, :ctype, CAST(:body AS jsonb),
                           :reply, :bot)
                        RETURNING id, channel_id, seq, sender_id, sender_type, content_type,
                                  body, reply_to_id, from_bot_agent_id, edited_at, deleted_at, created_at
                        """
                        ),
                        {
                            "cid": _bigint(channel_id),
                            "seq": seq,
                            "sender": sender_id,
                            "stype": sender_type,
                            "ctype": content_type,
                            "body": json.dumps(body),
                            "reply": reply_to_id,
                            "bot": from_bot_agent_id,
                        },
                    )
                )
                .mappings()
                .one()
            )
            # advance sender's read cursor so own messages never show as unread
            if sender_type == "user" and sender_id is not None:
                await conn.execute(
                    text(
                        "UPDATE public.channel_members"
                        " SET last_read_seq = :seq"
                        " WHERE channel_id = :cid AND user_id = :sender"
                    ),
                    {"seq": seq, "cid": _bigint(channel_id), "sender": sender_id},
                )
            return dict(row)

    async def list_messages(
        self,
        *,
        channel_id: int,
        before_seq: Optional[int],
        limit: int,
    ) -> list[dict[str, Any]]:
        rows = await db_engine.fetch_all(
            """
            SELECT id, channel_id, seq, sender_id, sender_type, content_type, body,
                   reply_to_id, edited_at, deleted_at, created_at
              FROM public.channel_messages
             WHERE channel_id = :cid
               AND deleted_at IS NULL
               AND (:before IS NULL OR seq < :before)
             ORDER BY seq DESC
             LIMIT :limit
            """,
            {"cid": _bigint(channel_id), "before": before_seq, "limit": limit},
        )
        return [dict(r) for r in rows]

    async def mark_read(
        self, *, channel_id: int, user_id: str, last_read_seq: int
    ) -> None:
        await db_engine.execute(
            """
            UPDATE public.channel_members
               SET last_read_seq = LEAST(
                     GREATEST(last_read_seq, :seq),
                     (SELECT last_message_seq FROM public.channels WHERE id = :cid)
                   ),
                   mention_count = 0
             WHERE channel_id = :cid AND user_id = :uid
            """,
            {"cid": _bigint(channel_id), "uid": user_id, "seq": last_read_seq},
        )

    # ── Agent membership ────────────────────────────────────────────────────

    async def add_agent_to_channel(
        self, *, channel_id: int, agent_id: str, added_by: str
    ) -> None:
        """Insert (agent_id, channel_id) into agent_channels; idempotent."""
        await db_engine.execute(
            """
            INSERT INTO public.agent_channels (agent_id, channel_id, added_by)
            VALUES (:agent_id, :cid, :added_by)
            ON CONFLICT (agent_id, channel_id) DO NOTHING
            """,
            {
                "agent_id": agent_id,
                "cid": _bigint(channel_id),
                "added_by": added_by,
            },
        )

    async def is_agent_in_channel(self, *, channel_id: int, agent_id: str) -> bool:
        v = await db_engine.fetch_val(
            """
            SELECT EXISTS (
              SELECT 1 FROM public.agent_channels
              WHERE channel_id = :cid AND agent_id = :agent_id
            )
            """,
            {"cid": _bigint(channel_id), "agent_id": agent_id},
        )
        return bool(v)

    async def list_channel_agent_ids(self, *, channel_id: int) -> list[str]:
        rows = await db_engine.fetch_all(
            """
            SELECT agent_id
              FROM public.agent_channels
             WHERE channel_id = :cid
            """,
            {"cid": _bigint(channel_id)},
        )
        return [str(r["agent_id"]) for r in rows]

    # ── Turn context ────────────────────────────────────────────────────────

    async def recent_messages(
        self, *, channel_id: int, limit: int = 20
    ) -> list[dict[str, Any]]:
        """Return the newest *limit* non-deleted messages in ascending seq order.

        Internally fetches DESC then reverses in Python so the caller always
        sees messages oldest-first (chronological), as expected by the agent
        turn context builder.
        """
        rows = await db_engine.fetch_all(
            """
            SELECT id, channel_id, seq, sender_id, sender_type, content_type, body,
                   reply_to_id, from_bot_agent_id, created_at
              FROM public.channel_messages
             WHERE channel_id = :cid AND deleted_at IS NULL
             ORDER BY seq DESC
             LIMIT :limit
            """,
            {"cid": _bigint(channel_id), "limit": limit},
        )
        return [dict(r) for r in reversed(rows)]

    # ── Mention fanout (UNREAD-03) ──────────────────────────────────────────

    async def increment_mentions(self, *, channel_id: int, user_ids: list[str]) -> None:
        """Bump mention_count by 1 for only the @-mentioned channel members.

        Uses per-user UPDATE statements in a transaction to avoid asyncpg array-bind
        risks (DataError in some Supavisor environments with empty-array handling).
        Each mentioned member is updated once; transaction ensures atomicity.
        """
        if not user_ids:
            return
        eng = db_engine.get_engine()
        async with eng.begin() as conn:
            for uid in user_ids:
                await conn.execute(
                    text(
                        """
                        UPDATE public.channel_members
                           SET mention_count = mention_count + 1
                         WHERE channel_id = :cid AND user_id = :uid
                        """
                    ),
                    {"cid": _bigint(channel_id), "uid": uid},
                )

    # ── Edit / soft-delete ──────────────────────────────────────────────────

    async def edit_message(
        self,
        channel_id: int,
        message_id: int,
        sender_id: str,
        body: dict[str, Any],
    ) -> Optional[dict[str, Any]]:
        """UPDATE body + edited_at on a user's own text message.

        Ownership gate: sender_id = :sender_id AND sender_type = 'user'
        State guards:   content_type = 'text' AND deleted_at IS NULL
        Returns the updated row dict, or None when no row matched the gate.
        """
        row = await db_engine.execute_returning_one(
            """
            UPDATE public.channel_messages
               SET body = CAST(:body AS jsonb), edited_at = now()
             WHERE id = :mid AND channel_id = :cid
               AND sender_id = :sender_id AND sender_type = 'user'
               AND content_type = 'text' AND deleted_at IS NULL
            RETURNING id, channel_id, seq, sender_id, sender_type, content_type,
                      body, reply_to_id, from_bot_agent_id, edited_at, deleted_at, created_at
            """,
            {
                "body": json.dumps(body),
                "mid": _bigint(message_id),
                "cid": _bigint(channel_id),
                "sender_id": sender_id,
            },
        )
        return dict(row) if row else None

    async def soft_delete_message(
        self,
        channel_id: int,
        message_id: int,
        sender_id: str,
    ) -> Optional[dict[str, Any]]:
        """SET deleted_at = now() on a user's own message (any content_type).

        Ownership gate: sender_id = :sender_id AND sender_type = 'user'
        State guard:    deleted_at IS NULL  (idempotent no-op if already deleted)
        Returns the updated row dict, or None when no row matched the gate.
        """
        row = await db_engine.execute_returning_one(
            """
            UPDATE public.channel_messages
               SET deleted_at = now()
             WHERE id = :mid AND channel_id = :cid
               AND sender_id = :sender_id AND sender_type = 'user'
               AND deleted_at IS NULL
            RETURNING id, channel_id, seq, sender_id, sender_type, content_type,
                      body, reply_to_id, from_bot_agent_id, edited_at, deleted_at, created_at
            """,
            {
                "mid": _bigint(message_id),
                "cid": _bigint(channel_id),
                "sender_id": sender_id,
            },
        )
        return dict(row) if row else None

    # ── Channel lookup ──────────────────────────────────────────────────────

    async def get_channel(self, *, channel_id: int) -> Optional[dict[str, Any]]:
        """Return id, team_id, type, history_mode, last_message_seq or None."""
        return await db_engine.fetch_one(
            """
            SELECT id, team_id, type, history_mode, last_message_seq
              FROM public.channels
             WHERE id = :cid
            """,
            {"cid": _bigint(channel_id)},
        )


_repo: Optional[ChatRepository] = None


def get_chat_repository() -> ChatRepository:
    global _repo
    if _repo is None:
        _repo = ChatRepository()
    return _repo
