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

Uses the privileged SQLAlchemy engine (bypasses RLS); the service layer
enforces membership. RLS guards the separate frontend path via Supabase JS.

ORM-model style (style batch 3b): statements run on the canonical
read_scope()/write_scope() sessions (app/db/session.py). The SQL bodies are
kept verbatim — atomic seq allocation, the TOCTOU owner-transfer guard and
the joined-gate keyset reads ARE the semantics (documented exceptions per
the convergence doctrine); multi-statement methods run all their statements
inside ONE committing write_scope() transaction, exactly as the old explicit
``engine.begin()`` blocks did. Schema models live in app/models/chat.py.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from sqlalchemy import text

from app.db.session import read_scope, write_scope


def _bigint(v: Any) -> int:
    return int(v)


# ── Session-scope SQL helpers ─────────────────────────────────────────────────


async def _fetch_all(sql: str, params: dict | None = None) -> list[dict[str, Any]]:
    async with read_scope() as session:
        result = await session.execute(text(sql), params or {})
        return [dict(m) for m in result.mappings().all()]


async def _fetch_one(sql: str, params: dict | None = None) -> Optional[dict[str, Any]]:
    async with read_scope() as session:
        row = (await session.execute(text(sql), params or {})).mappings().first()
        return dict(row) if row else None


async def _fetch_val(sql: str, params: dict | None = None) -> Any:
    async with read_scope() as session:
        return (await session.execute(text(sql), params or {})).scalar()


async def _execute(sql: str, params: dict | None = None) -> int:
    """Single-statement write on its own committing transaction (rowcount)."""
    async with write_scope() as session:
        result = await session.execute(text(sql), params or {})
        return result.rowcount or 0


async def _execute_returning_one(
    sql: str, params: dict | None = None
) -> Optional[dict[str, Any]]:
    """Single write with RETURNING on a committing transaction — the #498
    silent-rollback class (UPDATE…RETURNING on a non-committing connect())
    is structurally impossible here."""
    async with write_scope() as session:
        row = (await session.execute(text(sql), params or {})).mappings().first()
        return dict(row) if row else None


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
        async with write_scope() as session:
            row = (
                (
                    await session.execute(
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
                await session.execute(
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
        async with write_scope() as session:
            n = 0
            for uid in user_ids:
                res = await session.execute(
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
        v = await _fetch_val(
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
        result = await _fetch_val(
            "SELECT EXISTS(SELECT 1 FROM team_members WHERE team_id = :tid AND user_id = :uid)",
            {"tid": _bigint(team_id), "uid": user_id},
        )
        return bool(result)

    async def conversation_scope_id(self, *, conversation_id: int) -> int | None:
        return await _fetch_val(
            "SELECT scope_id FROM conversations WHERE id = :cid",
            {"cid": _bigint(conversation_id)},
        )

    async def conversation_scope_and_type(
        self, *, conversation_id: int
    ) -> dict[str, Any] | None:
        return await _fetch_one(
            "SELECT scope_id, type, archived_at FROM conversations WHERE id = :cid",
            {"cid": _bigint(conversation_id)},
        )

    async def get_my_conversations(self, user_id: str) -> list[dict[str, Any]]:
        """Return conversations visible to *user_id* (open, not archived), with unread."""
        rows = await _fetch_all(
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
               -- direct_agent = 1:1 AI chat threads (Phase 2); they have
               -- their own surface and must not appear in the team-chat sidebar.
               AND c.type <> 'direct_agent'
               AND cm.open = true
             ORDER BY c.last_seq DESC
            """,
            {"uid": user_id},
        )
        return [dict(r) for r in rows]

    async def list_member_ids(self, conversation_id: int) -> list[str]:
        """Return the user_id of every user member of *conversation_id*."""
        rows = await _fetch_all(
            """
            SELECT user_id
              FROM conversation_members
             WHERE conversation_id = :cid
               AND member_type = 'user'
            """,
            {"cid": _bigint(conversation_id)},
        )
        return [str(r["user_id"]) for r in rows]

    # ── Group management (roles) ──────────────────────────────────────────────

    async def list_members(self, *, conversation_id: int) -> list[dict[str, Any]]:
        """All members (users + agents) with role, enriched with display names.

        User names come from user_profiles.username via a service-role read —
        RLS only exposes the caller's own profile row to the client, so the
        frontend cannot resolve other members' names itself.
        """
        rows = await _fetch_all(
            """
            SELECT cm.member_type, cm.user_id, cm.agent_id, cm.role,
                   cm.joined_at,
                   up.username AS profile_name,
                   au.email AS email,
                   a.name AS agent_name, a.slug AS agent_slug
              FROM public.conversation_members cm
              LEFT JOIN public.user_profiles up
                     ON cm.member_type = 'user' AND up.id = cm.user_id
              LEFT JOIN auth.users au
                     ON cm.member_type = 'user' AND au.id = cm.user_id
              LEFT JOIN public.ai_agents a
                     ON cm.member_type = 'agent' AND a.id = cm.agent_id
             WHERE cm.conversation_id = :cid
             ORDER BY cm.joined_at ASC
            """,
            {"cid": _bigint(conversation_id)},
        )
        return [
            {
                "member_type": r["member_type"],
                "user_id": r["user_id"],
                "agent_id": r["agent_id"],
                "role": r["role"],
                "joined_at": r["joined_at"],
                "name": r["profile_name"] or r["agent_name"],
                "email": r["email"],
                "agent_slug": r["agent_slug"],
            }
            for r in rows
        ]

    async def get_member_role(
        self, *, conversation_id: int, user_id: str
    ) -> Optional[str]:
        v = await _fetch_val(
            """
            SELECT role FROM public.conversation_members
             WHERE conversation_id = :cid
               AND member_type = 'user'
               AND user_id = :uid
            """,
            {"cid": _bigint(conversation_id), "uid": user_id},
        )
        return None if v is None else str(v)

    async def remove_user_member(self, *, conversation_id: int, user_id: str) -> bool:
        # role <> 'owner' guard: the service pre-checks the target's role, but
        # a concurrent transfer-owner can promote the target between check and
        # delete (TOCTOU) — deleting the owner then leaves the group permanently
        # ownerless. 0 rows → the service surfaces a conflict error.
        n = await _execute(
            """
            DELETE FROM public.conversation_members
             WHERE conversation_id = :cid
               AND member_type = 'user'
               AND user_id = :uid
               AND role <> 'owner'
            """,
            {"cid": _bigint(conversation_id), "uid": user_id},
        )
        return bool(n)

    async def remove_agent_member(self, *, conversation_id: int, agent_id: str) -> bool:
        n = await _execute(
            """
            DELETE FROM public.conversation_members
             WHERE conversation_id = :cid
               AND member_type = 'agent'
               AND agent_id = :agent_id
            """,
            {"cid": _bigint(conversation_id), "agent_id": agent_id},
        )
        return bool(n)

    async def set_member_role(
        self, *, conversation_id: int, user_id: str, role: str
    ) -> bool:
        n = await _execute(
            """
            UPDATE public.conversation_members
               SET role = :role
             WHERE conversation_id = :cid
               AND member_type = 'user'
               AND user_id = :uid
            """,
            {"cid": _bigint(conversation_id), "uid": user_id, "role": role},
        )
        return bool(n)

    async def transfer_owner(
        self, *, conversation_id: int, from_user_id: str, to_user_id: str
    ) -> None:
        """Atomically demote *from_user_id* to member and promote *to_user_id*.

        Both UPDATEs run in one transaction. Demote runs FIRST with a
        `role='owner'` guard: two concurrent transfers both promoting first
        would each demote the old owner unconditionally and leave TWO owners.
        With the guard, the second transaction's demote hits 0 rows (the row
        lock serializes them) and rolls back. A missing target likewise rolls
        the demote back, so the group is never left ownerless.
        """
        async with write_scope() as session:
            demoted = await session.execute(
                text(
                    """
                    UPDATE public.conversation_members
                       SET role = 'member'
                     WHERE conversation_id = :cid
                       AND member_type = 'user'
                       AND user_id = :from_uid
                       AND role = 'owner'
                    """
                ),
                {"cid": _bigint(conversation_id), "from_uid": from_user_id},
            )
            if not demoted.rowcount:
                raise ValueError("caller is no longer the owner of this conversation")
            promoted = await session.execute(
                text(
                    """
                    UPDATE public.conversation_members
                       SET role = 'owner'
                     WHERE conversation_id = :cid
                       AND member_type = 'user'
                       AND user_id = :to_uid
                    """
                ),
                {"cid": _bigint(conversation_id), "to_uid": to_user_id},
            )
            if not promoted.rowcount:
                raise ValueError("target user is not a member of this conversation")

    async def update_conversation(
        self,
        *,
        conversation_id: int,
        name: Optional[str] = None,
        type: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        """Update title and/or type; None means leave unchanged."""
        # execute_returning_one, not fetch_one: fetch_one runs on connect()
        # and never commits, so the UPDATE would silently roll back.
        row = await _execute_returning_one(
            """
            UPDATE public.conversations
               SET title = COALESCE(:name, title),
                   type  = COALESCE(:type, type)
             WHERE id = :cid
               AND archived_at IS NULL
            RETURNING id, scope_id, type, history_mode,
                      title AS name, topic, last_seq, created_at
            """,
            {"cid": _bigint(conversation_id), "name": name, "type": type},
        )
        return dict(row) if row else None

    async def archive_conversation(self, *, conversation_id: int) -> bool:
        n = await _execute(
            """
            UPDATE public.conversations
               SET archived_at = now()
             WHERE id = :cid
               AND archived_at IS NULL
            """,
            {"cid": _bigint(conversation_id)},
        )
        return bool(n)

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
        async with write_scope() as session:
            # archived_at guard: a dissolved group must stop accepting
            # messages; without it "deleted" groups keep chatting forever.
            seq = (
                await session.execute(
                    text(
                        """
                        UPDATE public.conversations
                           SET last_seq = last_seq + 1
                         WHERE id = :cid
                           AND archived_at IS NULL
                         RETURNING last_seq
                        """
                    ),
                    {"cid": _bigint(conversation_id)},
                )
            ).scalar()
            if seq is None:
                raise ValueError("conversation not found or archived")
            row = (
                (
                    await session.execute(
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
                await session.execute(
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
        for_user_id: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Return messages in descending seq order with optional keyset cutoff.

        CAST(:before AS bigint) is required to prevent asyncpg AmbiguousParameterError
        when before_seq is None (the driver cannot infer the type of a NULL literal
        from a plain :before bind).

        ``for_user_id`` (Phase-1 final-review carryover): when given, apply the
        same ``history_mode='joined'`` cutoff as the ``messages_select`` RLS
        policy in migration 328 — a joined-mode member only sees messages
        created at/after their own ``conversation_members.joined_at``.
        ``shared``-mode conversations (and every call that omits
        ``for_user_id``, e.g. the agent-turn context builders) are unaffected.
        """
        if for_user_id is None:
            rows = await _fetch_all(
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

        rows = await _fetch_all(
            """
            SELECT m.id, m.conversation_id, m.seq, m.sender_id, m.sender_type,
                   m.type, m.body, m.parent_id, m.edited_at, m.deleted_at, m.created_at
              FROM public.messages m
              JOIN public.conversations c ON c.id = m.conversation_id
             WHERE m.conversation_id = :cid
               AND m.deleted_at IS NULL
               AND (CAST(:before AS bigint) IS NULL OR m.seq < CAST(:before AS bigint))
               AND (
                     c.history_mode = 'shared'
                     OR m.created_at >= (
                          SELECT cm.joined_at
                            FROM public.conversation_members cm
                           WHERE cm.conversation_id = m.conversation_id
                             AND cm.member_type = 'user'
                             AND cm.user_id = :for_uid
                        )
                   )
             ORDER BY m.seq DESC
             LIMIT :limit
            """,
            {
                "cid": _bigint(conversation_id),
                "before": before_seq,
                "limit": limit,
                "for_uid": for_user_id,
            },
        )
        return [dict(r) for r in rows]

    async def mark_read(
        self, *, conversation_id: int, user_id: str, last_read_seq: int
    ) -> None:
        await _execute(
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
        await _execute(
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
        v = await _fetch_val(
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
        rows = await _fetch_all(
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
        rows = await _fetch_all(
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
        rows = await _fetch_all(
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
        async with write_scope() as session:
            for uid in user_ids:
                await session.execute(
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
        row = await _execute_returning_one(
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
        row = await _execute_returning_one(
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
        return await _fetch_one(
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
        async with write_scope() as session:
            for ord_val, gid in enumerate(generated_media_ids):
                await session.execute(
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
