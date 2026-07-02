"""Data access for Unified Conversations (Phase 1).

Port of chat_repository.py onto the canonical conversations / conversation_members /
messages / message_attachments tables (migration 327).

Rename Map (chat_repository → conversation_repository):
  channels             → conversations
  channel_messages     → messages
  channel_members      → conversation_members
  team_id              → scope_id
  last_message_seq     → last_seq
  content_type         → type
  reply_to_id          → parent_id
  from_bot_agent_id    → from_agent_id
  is_archived=false    → archived_at IS NULL

Extra deltas vs a pure rename (brief §Deltas):
  1. Membership inserts are dual-FK: (member_type='user', user_id) or
     (member_type='agent', agent_id).  Every user-membership query adds
     `member_type='user' AND` alongside the user_id predicate.
  2. add_agent_member → conversation_members instead of agent_channels.
  3. send_message column renames (see above); advances sender read cursor
     only when sender_type='user'.
  4. get_my_conversations uses archived_at IS NULL and last_seq.
  5. add_attachments: one INSERT per generated_media_id with incrementing ord.

name / title bridge:
  The `conversations` table column is `title`.  The public interface (and the
  dict keys returned to callers) use `name` — matching the service/schema layer.
  Bridge is applied at the SQL boundary only:
    - INSERT uses column `title`, bind param `:name`.
    - Every RETURNING / SELECT aliases `title AS name`.

asyncpg hazards preserved:
  - _bigint() coercion on every BIGINT bind.
  - CAST(:before AS bigint) in list_messages so asyncpg never sees an
    untyped NULL and raises AmbiguousParameterError.
  - roles TEXT[] → role TEXT (scalar); creator gets role='owner'.

Uses the privileged db_engine (bypasses RLS); the service layer enforces
membership. RLS guards the separate frontend path via Supabase JS.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from sqlalchemy import text

from app.db import engine as db_engine


def _bigint(v: Any) -> int:
    return int(v)


class ConversationRepository:
    # ── Conversation lifecycle ────────────────────────────────────────────────

    async def create_conversation(
        self,
        *,
        creator_id: str,
        scope_id: int,
        type: str,
        name: Optional[str],
        history_mode: str,
        member_ids: list[str],
    ) -> dict[str, Any]:
        """Insert a new conversation + the creator (owner) and all member_ids.

        `name` is the public interface key; the DB column is `title`.
        RETURNING aliases `title AS name` so the returned dict preserves the key.
        """
        eng = db_engine.get_engine()
        async with eng.begin() as conn:
            row = (
                (
                    await conn.execute(
                        text(
                            """
                            INSERT INTO public.conversations
                              (scope_id, type, history_mode, title, created_by)
                            VALUES (:scope_id, :type, :history_mode, :name, :creator)
                            RETURNING id, scope_id, type, history_mode,
                                      title AS name, topic, last_seq, created_at
                            """
                        ),
                        {
                            "scope_id": _bigint(scope_id),
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
                role = "owner" if uid == creator_id else "member"
                await conn.execute(
                    text(
                        """
                        INSERT INTO public.conversation_members
                          (conversation_id, member_type, user_id, role)
                        VALUES (:cid, 'user', :uid, :role)
                        ON CONFLICT DO NOTHING
                        """
                    ),
                    {"cid": cid, "uid": uid, "role": role},
                )
            return dict(row)

    async def add_members(self, *, conversation_id: int, user_ids: list[str]) -> int:
        if not user_ids:
            return 0
        eng = db_engine.get_engine()
        async with eng.begin() as conn:
            n = 0
            for uid in user_ids:
                res = await conn.execute(
                    text(
                        """
                        INSERT INTO public.conversation_members
                          (conversation_id, member_type, user_id)
                        VALUES (:cid, 'user', :uid)
                        ON CONFLICT DO NOTHING
                        """
                    ),
                    {"cid": _bigint(conversation_id), "uid": uid},
                )
                n += res.rowcount or 0
            return n

    # ── Membership queries ────────────────────────────────────────────────────

    async def is_member(self, *, conversation_id: int, user_id: str) -> bool:
        v = await db_engine.fetch_val(
            """
            SELECT EXISTS (
              SELECT 1 FROM public.conversation_members
              WHERE conversation_id = :cid
                AND member_type = 'user'
                AND user_id = :uid
            )
            """,
            {"cid": _bigint(conversation_id), "uid": user_id},
        )
        return bool(v)

    async def is_team_member(self, *, team_id: int, user_id: str) -> bool:
        result = await db_engine.fetch_val(
            "SELECT EXISTS(SELECT 1 FROM team_members WHERE team_id = :tid AND user_id = :uid)",
            {"tid": _bigint(team_id), "uid": user_id},
        )
        return bool(result)

    async def conversation_scope_id(self, *, conversation_id: int) -> int | None:
        return await db_engine.fetch_val(
            "SELECT scope_id FROM conversations WHERE id = :cid",
            {"cid": _bigint(conversation_id)},
        )

    async def get_my_conversations(self, user_id: str) -> list[dict[str, Any]]:
        """Return conversations visible to *user_id* (open, not archived), with unread."""
        rows = await db_engine.fetch_all(
            """
            SELECT c.id, c.scope_id, c.type, c.history_mode,
                   c.title AS name, c.topic,
                   c.last_seq, c.created_at,
                   GREATEST(c.last_seq - cm.last_read_seq, 0) AS unread,
                   cm.mention_count AS mention_count
              FROM public.conversation_members cm
              JOIN public.conversations c ON c.id = cm.conversation_id
             WHERE cm.member_type = 'user'
               AND cm.user_id = :uid
               AND c.archived_at IS NULL
               AND cm.open = true
             ORDER BY c.last_seq DESC
            """,
            {"uid": user_id},
        )
        return [dict(r) for r in rows]

    async def list_member_ids(self, conversation_id: int) -> list[str]:
        """Return the user_id of every user member of *conversation_id*."""
        rows = await db_engine.fetch_all(
            """
            SELECT user_id
              FROM conversation_members
             WHERE conversation_id = :cid
               AND member_type = 'user'
            """,
            {"cid": _bigint(conversation_id)},
        )
        return [str(r["user_id"]) for r in rows]

    # ── Message operations ────────────────────────────────────────────────────

    async def send_message(
        self,
        *,
        conversation_id: int,
        sender_id: Optional[str],
        sender_type: str,
        type: str,
        body: dict[str, Any],
        parent_id: Optional[int],
        from_agent_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Allocate a monotonic seq, INSERT message, advance sender read cursor.

        The seq allocation (UPDATE conversations SET last_seq+1 RETURNING) and
        the message INSERT run in the same transaction for strict ordering.
        """
        eng = db_engine.get_engine()
        async with eng.begin() as conn:
            seq = (
                await conn.execute(
                    text(
                        """
                        UPDATE public.conversations
                           SET last_seq = last_seq + 1
                         WHERE id = :cid
                         RETURNING last_seq
                        """
                    ),
                    {"cid": _bigint(conversation_id)},
                )
            ).scalar_one()
            row = (
                (
                    await conn.execute(
                        text(
                            """
                            INSERT INTO public.messages
                              (conversation_id, seq, sender_id, sender_type,
                               type, body, parent_id, from_agent_id)
                            VALUES
                              (:cid, :seq, :sender, :stype,
                               :type, CAST(:body AS jsonb), :parent, :agent)
                            RETURNING id, conversation_id, seq, sender_id, sender_type,
                                      type, body, parent_id, from_agent_id,
                                      edited_at, deleted_at, created_at
                            """
                        ),
                        {
                            "cid": _bigint(conversation_id),
                            "seq": seq,
                            "sender": sender_id,
                            "stype": sender_type,
                            "type": type,
                            "body": json.dumps(body),
                            "parent": parent_id,
                            "agent": from_agent_id,
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
                        "UPDATE public.conversation_members"
                        " SET last_read_seq = :seq"
                        " WHERE conversation_id = :cid"
                        "   AND member_type = 'user'"
                        "   AND user_id = :sender"
                    ),
                    {
                        "seq": seq,
                        "cid": _bigint(conversation_id),
                        "sender": sender_id,
                    },
                )
            return dict(row)

    async def list_messages(
        self,
        *,
        conversation_id: int,
        before_seq: Optional[int],
        limit: int,
    ) -> list[dict[str, Any]]:
        """Return messages in descending seq order with optional keyset cutoff.

        CAST(:before AS bigint) is required to prevent asyncpg AmbiguousParameterError
        when before_seq is None (the driver cannot infer the type of a NULL literal
        from a plain :before bind).
        """
        rows = await db_engine.fetch_all(
            """
            SELECT id, conversation_id, seq, sender_id, sender_type,
                   type, body, parent_id, edited_at, deleted_at, created_at
              FROM public.messages
             WHERE conversation_id = :cid
               AND deleted_at IS NULL
               AND (CAST(:before AS bigint) IS NULL OR seq < CAST(:before AS bigint))
             ORDER BY seq DESC
             LIMIT :limit
            """,
            {
                "cid": _bigint(conversation_id),
                "before": before_seq,
                "limit": limit,
            },
        )
        return [dict(r) for r in rows]

    async def mark_read(
        self, *, conversation_id: int, user_id: str, last_read_seq: int
    ) -> None:
        await db_engine.execute(
            """
            UPDATE public.conversation_members
               SET last_read_seq = LEAST(
                     GREATEST(last_read_seq, :seq),
                     (SELECT last_seq FROM public.conversations WHERE id = :cid)
                   ),
                   mention_count = 0
             WHERE conversation_id = :cid
               AND member_type = 'user'
               AND user_id = :uid
            """,
            {
                "cid": _bigint(conversation_id),
                "uid": user_id,
                "seq": last_read_seq,
            },
        )

    # ── Agent membership ──────────────────────────────────────────────────────

    async def add_agent_member(
        self, *, conversation_id: int, agent_id: str, added_by: str
    ) -> None:
        """Insert (conversation_id, member_type='agent', agent_id); idempotent.

        Relies on the `uq_conversation_members` unique index:
          (conversation_id, member_type, COALESCE(user_id, agent_id))
        """
        await db_engine.execute(
            """
            INSERT INTO public.conversation_members
              (conversation_id, member_type, agent_id, added_by)
            VALUES (:cid, 'agent', :agent_id, :added_by)
            ON CONFLICT DO NOTHING
            """,
            {
                "cid": _bigint(conversation_id),
                "agent_id": agent_id,
                "added_by": added_by,
            },
        )

    async def is_agent_member(self, *, conversation_id: int, agent_id: str) -> bool:
        v = await db_engine.fetch_val(
            """
            SELECT EXISTS (
              SELECT 1 FROM public.conversation_members
              WHERE conversation_id = :cid
                AND member_type = 'agent'
                AND agent_id = :agent_id
            )
            """,
            {"cid": _bigint(conversation_id), "agent_id": agent_id},
        )
        return bool(v)

    async def list_conversation_agent_ids(self, *, conversation_id: int) -> list[str]:
        rows = await db_engine.fetch_all(
            """
            SELECT agent_id
              FROM public.conversation_members
             WHERE conversation_id = :cid
               AND member_type = 'agent'
            """,
            {"cid": _bigint(conversation_id)},
        )
        return [str(r["agent_id"]) for r in rows]

    # ── Turn context ──────────────────────────────────────────────────────────

    async def recent_messages(
        self, *, conversation_id: int, limit: int = 20
    ) -> list[dict[str, Any]]:
        """Return the newest *limit* non-deleted messages in ascending seq order.

        Fetches DESC then reverses so the caller sees messages oldest-first
        (chronological), as expected by the agent turn context builder.
        """
        rows = await db_engine.fetch_all(
            """
            SELECT id, conversation_id, seq, sender_id, sender_type,
                   type, body, parent_id, from_agent_id, created_at
              FROM public.messages
             WHERE conversation_id = :cid AND deleted_at IS NULL
             ORDER BY seq DESC
             LIMIT :limit
            """,
            {"cid": _bigint(conversation_id), "limit": limit},
        )
        return [dict(r) for r in reversed(rows)]

    async def messages_in_range(
        self,
        *,
        conversation_id: int,
        from_seq: int,
        to_seq: int,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Ascending non-deleted messages with from_seq <= seq <= to_seq.

        Compaction feed (Phase 1.5): the caller summarizes this span and
        advances conversation_memory.last_seq_summarized to to_seq.
        """
        rows = await db_engine.fetch_all(
            """
            SELECT seq, sender_type, type, body, created_at
              FROM public.messages
             WHERE conversation_id = :cid AND seq >= :from_seq AND seq <= :to_seq
               AND deleted_at IS NULL
             ORDER BY seq ASC
             LIMIT :limit
            """,
            {
                "cid": _bigint(conversation_id),
                "from_seq": _bigint(from_seq),
                "to_seq": _bigint(to_seq),
                "limit": limit,
            },
        )
        return [dict(r) for r in rows]

    # ── Mention fanout ────────────────────────────────────────────────────────

    async def increment_mentions(
        self, *, conversation_id: int, user_ids: list[str]
    ) -> None:
        """Bump mention_count by 1 for only the @-mentioned conversation members.

        Uses per-user UPDATE statements in a transaction to avoid asyncpg
        array-bind risks (DataError in some Supavisor environments with
        empty-array handling). Each mentioned member is updated once.
        """
        if not user_ids:
            return
        eng = db_engine.get_engine()
        async with eng.begin() as conn:
            for uid in user_ids:
                await conn.execute(
                    text(
                        """
                        UPDATE public.conversation_members
                           SET mention_count = mention_count + 1
                         WHERE conversation_id = :cid
                           AND member_type = 'user'
                           AND user_id = :uid
                        """
                    ),
                    {"cid": _bigint(conversation_id), "uid": uid},
                )

    # ── Edit / soft-delete ────────────────────────────────────────────────────

    async def edit_message(
        self,
        conversation_id: int,
        message_id: int,
        sender_id: str,
        body: dict[str, Any],
    ) -> Optional[dict[str, Any]]:
        """UPDATE body + edited_at on a user's own text message.

        Ownership gate: sender_id = :sender_id AND sender_type = 'user'
        State guards:   type = 'text' AND deleted_at IS NULL
        Returns the updated row dict, or None when no row matched the gate.
        """
        row = await db_engine.execute_returning_one(
            """
            UPDATE public.messages
               SET body = CAST(:body AS jsonb), edited_at = now()
             WHERE id = :mid AND conversation_id = :cid
               AND sender_id = :sender_id AND sender_type = 'user'
               AND type = 'text' AND deleted_at IS NULL
            RETURNING id, conversation_id, seq, sender_id, sender_type,
                      type, body, parent_id, from_agent_id,
                      edited_at, deleted_at, created_at
            """,
            {
                "body": json.dumps(body),
                "mid": _bigint(message_id),
                "cid": _bigint(conversation_id),
                "sender_id": sender_id,
            },
        )
        return dict(row) if row else None

    async def soft_delete_message(
        self,
        conversation_id: int,
        message_id: int,
        sender_id: str,
    ) -> Optional[dict[str, Any]]:
        """SET deleted_at = now() on a user's own message (any type).

        Ownership gate: sender_id = :sender_id AND sender_type = 'user'
        State guard:    deleted_at IS NULL  (idempotent no-op if already deleted)
        Returns the updated row dict, or None when no row matched the gate.
        """
        row = await db_engine.execute_returning_one(
            """
            UPDATE public.messages
               SET deleted_at = now()
             WHERE id = :mid AND conversation_id = :cid
               AND sender_id = :sender_id AND sender_type = 'user'
               AND deleted_at IS NULL
            RETURNING id, conversation_id, seq, sender_id, sender_type,
                      type, body, parent_id, from_agent_id,
                      edited_at, deleted_at, created_at
            """,
            {
                "mid": _bigint(message_id),
                "cid": _bigint(conversation_id),
                "sender_id": sender_id,
            },
        )
        return dict(row) if row else None

    # ── Conversation lookup ───────────────────────────────────────────────────

    async def get_conversation(
        self, *, conversation_id: int
    ) -> Optional[dict[str, Any]]:
        """Return id, scope_id, type, history_mode, last_seq or None."""
        return await db_engine.fetch_one(
            """
            SELECT id, scope_id, type, history_mode, last_seq
              FROM public.conversations
             WHERE id = :cid
            """,
            {"cid": _bigint(conversation_id)},
        )

    # ── Attachments ───────────────────────────────────────────────────────────

    async def add_attachments(
        self, *, message_id: int, generated_media_ids: list[int]
    ) -> None:
        """Insert one message_attachments row per generated_media_id.

        ord starts at 0 and increments for each id in the list. All inserts
        run in a single transaction. ON CONFLICT DO NOTHING makes this
        idempotent when called twice for the same message.
        """
        if not generated_media_ids:
            return
        eng = db_engine.get_engine()
        async with eng.begin() as conn:
            for ord_val, gid in enumerate(generated_media_ids):
                await conn.execute(
                    text(
                        """
                        INSERT INTO public.message_attachments
                          (message_id, generated_media_id, ord)
                        VALUES (:mid, :gid, :ord)
                        ON CONFLICT DO NOTHING
                        """
                    ),
                    {
                        "mid": _bigint(message_id),
                        "gid": _bigint(gid),
                        "ord": ord_val,
                    },
                )


# ── Singleton factory ─────────────────────────────────────────────────────────

_repo: Optional[ConversationRepository] = None


def get_conversation_repository() -> ConversationRepository:
    global _repo
    if _repo is None:
        _repo = ConversationRepository()
    return _repo
