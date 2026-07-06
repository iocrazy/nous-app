"""ConversationsAiStore — MessageStore implementation over the canonical
``conversations`` / ``conversation_members`` / ``conversation_ai_meta``
tables (migration 327 + 332), a.k.a. the Unified Conversations schema.

This was the strangler-fig replacement for the legacy Supabase-backed
``ai_sessions`` / ``ai_messages`` store (Conversations Phase 3, Task 6
retired that store and the dual-store router that once sat in front of
it — this is now the sole ``MessageStore`` implementation). Task 3 shipped
the SESSION half (``create_session`` / ``list_sessions`` / ``get_session`` /
``rename_session`` / ``soft_delete_session`` / ``bump_counters``); Task 4
ships the MESSAGE half (``get_messages`` / ``append_user_message`` /
``append_assistant_message``), reusing ``ConversationRepository.send_message``
for the writes (atomic seq allocation) and a direct ``public.messages``
SELECT for reads.

Physical → legacy mapping
--------------------------
An AI Library "session" is folded onto a ``conversations`` row of
``type='direct_agent'``, decorated with a 1:1 ``conversation_ai_meta``
sidecar row (agent binding + token/message counters + client grouping
hints that the shared conversations schema deliberately doesn't carry).
Membership is two ``conversation_members`` rows: the owning user
(``member_type='user'``, ``role='owner'``) and the bound agent
(``member_type='agent'``).

  ai_sessions.id            → conversations.id
  ai_sessions.user_id       → conversation_members.user_id (member_type='user')
  ai_sessions.agent_id      → conversation_ai_meta.agent_id (+ conversation_members agent row)
  ai_sessions.agent_slug    → conversation_ai_meta.agent_slug
  ai_sessions.title         → conversations.title
  ai_sessions.status        → derived: archived_at IS NULL → 'active' (no other
                               legacy status is reachable through this store —
                               archived rows are simply invisible, see get_session)
  ai_sessions.total_tokens  → conversation_ai_meta.total_tokens
  ai_sessions.message_count → conversation_ai_meta.message_count
  ai_sessions.project_id    → conversations.project_id
  ai_sessions.team_id       → conversations.scope_id
  ai_sessions.context_type  → conversation_ai_meta.context_type
  ai_sessions.context_id    → conversation_ai_meta.context_id
  ai_sessions.created_at    → conversations.created_at
  ai_sessions.updated_at    → conversation_ai_meta.updated_at

Scope resolution — when the caller doesn't pass ``team_id`` explicitly
(personal chat, the common case), we resolve the caller's personal team
via ``_resolve_personal_team_id`` (same helper ``generated_media_router``
and friends use for scope defaulting). It raises ``ValueError`` when no
personal team exists for the user; that propagates unchanged — the
router layer already maps bare ``ValueError`` → 400.

id shapes returned — a deliberate parity choice (see report): this store
does NOT stringify ``conversations.id`` / ``scope_id`` / ``project_id`` —
they're handed back as native Python ints (matching what PostgREST used
to serialize BIGINT columns to, and what Python ints preserve precision
for) — so the SessionOut schema's ``coerce_numbers_to_str`` already does
the int→str conversion for the router response, and this store just
needs to hand back that same "raw" shape. UUID columns (``user_id`` /
``agent_id``) DO need an explicit ``str(...)`` here though: asyncpg hands
back native ``uuid.UUID`` objects, and the row-shape contract
(``message_store.py``) requires plain strings for those fields. This
mirrors the ``str(r["user_id"])`` pattern already used in
``conversation_repository.list_member_ids`` / ``list_conversation_agent_ids``.

Transactionality — ``create_session`` does the conversations INSERT +
both conversation_members INSERTs + the conversation_ai_meta INSERT in
ONE ``eng.begin()`` transaction (mirroring
``ConversationRepository.create_conversation``), so a partial session
(e.g. conversation row with no agent membership) can never be observed.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from sqlalchemy import text

from app.db import engine as db_engine
from app.services.library.resources_service import _resolve_personal_team_id

# public.messages.sender_type -> legacy ai_messages.role
_ROLE_MAP = {"agent": "assistant", "user": "user", "system": "system"}

# Decoration keys folded into body["meta"] by append_assistant_message that
# must be stripped back out on read to reconstruct the caller's original
# `metadata` dict byte-for-byte (see get_messages / _to_legacy_message_shape).
_META_DECORATION_KEYS = ("agent_id", "prompt_tokens", "completion_tokens")


def _bigint(v: Any) -> int:
    return int(v)


class ConversationsAiStore:
    """MessageStore backed by the canonical conversations schema (session half)."""

    store_kind = "conversations"

    # ------------------------------------------------------------------
    # Row mapping
    # ------------------------------------------------------------------

    @staticmethod
    def _to_legacy_shape(row: Dict[str, Any]) -> Dict[str, Any]:
        """Map a joined conversations/conversation_ai_meta/conversation_members
        row (keys: id, scope_id, project_id, title, created_at, agent_slug,
        agent_id, total_tokens, message_count, context_type, context_id,
        updated_at, user_id) into the legacy ai_sessions row shape."""
        agent_id = row.get("agent_id")
        return {
            "id": row["id"],
            "user_id": str(row["user_id"]) if row.get("user_id") is not None else None,
            "agent_id": str(agent_id) if agent_id is not None else None,
            "agent_slug": row.get("agent_slug"),
            "title": row.get("title"),
            "status": "active",
            "total_tokens": row.get("total_tokens"),
            "message_count": row.get("message_count"),
            "project_id": row.get("project_id"),
            "team_id": row.get("scope_id"),
            "context_type": row.get("context_type"),
            "context_id": row.get("context_id"),
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
            # Task 6: dispatch marker — lets AILibraryChatService.chat tell a
            # conversations-backed session apart from a legacy one (so it can
            # route agent_runs.conversation_id vs .session_id correctly).
            "store_kind": ConversationsAiStore.store_kind,
        }

    _JOIN_SELECT = """
        SELECT c.id, c.scope_id, c.project_id, c.title, c.created_at,
               m.agent_slug, m.agent_id, m.total_tokens, m.message_count,
               m.context_type, m.context_id, m.updated_at,
               cm.user_id
          FROM public.conversations c
          JOIN public.conversation_ai_meta m ON m.conversation_id = c.id
          JOIN public.conversation_members cm ON cm.conversation_id = c.id
           AND cm.member_type = 'user'
    """

    async def _fetch_by_id(self, session_id: int) -> Optional[Dict[str, Any]]:
        row = await db_engine.fetch_one(
            self._JOIN_SELECT
            + " WHERE c.id = :cid AND c.archived_at IS NULL"
            + " LIMIT 1",
            {"cid": _bigint(session_id)},
        )
        return self._to_legacy_shape(row) if row else None

    # ------------------------------------------------------------------
    # Sessions
    # ------------------------------------------------------------------

    async def create_session(
        self,
        *,
        user_id: str,
        agent_slug: str,
        agent_id: str,
        title: str,
        project_id: Optional[int],
        team_id: Optional[int],
        context_type: Optional[str],
        context_id: Optional[str],
    ) -> Dict[str, Any]:
        """Insert a conversations row (type='direct_agent') + user/agent
        membership + the conversation_ai_meta sidecar, all in one txn.

        Scope resolution: explicit ``team_id`` wins; otherwise resolves the
        caller's personal team. ``_resolve_personal_team_id`` raises
        ``ValueError`` when unresolvable — that propagates untouched (the
        router maps bare ValueError → 400).
        """
        scope_id = (
            _bigint(team_id)
            if team_id is not None
            else _bigint(await _resolve_personal_team_id(user_id))
        )

        eng = db_engine.get_engine()
        async with eng.begin() as conn:
            conv_row = (
                (
                    await conn.execute(
                        text(
                            """
                            INSERT INTO public.conversations
                              (type, scope_id, project_id, title, created_by)
                            VALUES ('direct_agent', :scope_id, :project_id, :title, :created_by)
                            RETURNING id, scope_id, project_id, title, created_at
                            """
                        ),
                        {
                            "scope_id": scope_id,
                            "project_id": (
                                _bigint(project_id) if project_id is not None else None
                            ),
                            "title": title,
                            "created_by": user_id,
                        },
                    )
                )
                .mappings()
                .one()
            )
            cid = conv_row["id"]

            await conn.execute(
                text(
                    """
                    INSERT INTO public.conversation_members
                      (conversation_id, member_type, user_id, role)
                    VALUES (:cid, 'user', :uid, 'owner')
                    ON CONFLICT DO NOTHING
                    """
                ),
                {"cid": cid, "uid": user_id},
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO public.conversation_members
                      (conversation_id, member_type, agent_id, added_by)
                    VALUES (:cid, 'agent', :agent_id, :added_by)
                    ON CONFLICT DO NOTHING
                    """
                ),
                {"cid": cid, "agent_id": agent_id, "added_by": user_id},
            )
            meta_row = (
                (
                    await conn.execute(
                        text(
                            """
                            INSERT INTO public.conversation_ai_meta
                              (conversation_id, agent_slug, agent_id, context_type, context_id)
                            VALUES (:cid, :agent_slug, :agent_id, :context_type, :context_id)
                            RETURNING total_tokens, message_count, updated_at
                            """
                        ),
                        {
                            "cid": cid,
                            "agent_slug": agent_slug,
                            "agent_id": agent_id,
                            "context_type": context_type,
                            "context_id": context_id,
                        },
                    )
                )
                .mappings()
                .one()
            )

        return {
            "id": conv_row["id"],
            "user_id": str(user_id),
            "agent_id": str(agent_id) if agent_id is not None else None,
            "agent_slug": agent_slug,
            "title": conv_row["title"],
            "status": "active",
            "total_tokens": meta_row["total_tokens"],
            "message_count": meta_row["message_count"],
            "project_id": conv_row["project_id"],
            "team_id": conv_row["scope_id"],
            "context_type": context_type,
            "context_id": context_id,
            "created_at": conv_row["created_at"],
            "updated_at": meta_row["updated_at"],
            # Task 6: dispatch marker — see _to_legacy_shape.
            "store_kind": self.store_kind,
        }

    async def list_sessions(
        self,
        *,
        user_id: str,
        agent_slug: Optional[str],
        project_id: Optional[int],
        limit: int,
        search: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Return the caller's non-archived direct_agent sessions, newest
        conversation_ai_meta.updated_at first.

        ``search`` is a case-insensitive title substring filter applied in
        SQL (ILIKE) — the whole history is searchable, not just one page.
        """
        sql = (
            self._JOIN_SELECT
            + " AND cm.user_id = :uid"
            + " WHERE c.type = 'direct_agent' AND c.archived_at IS NULL"
        )
        params: Dict[str, Any] = {"uid": user_id, "limit": limit}
        if agent_slug is not None:
            sql += " AND m.agent_slug = :agent_slug"
            params["agent_slug"] = agent_slug
        if project_id is not None:
            sql += " AND c.project_id = :project_id"
            params["project_id"] = _bigint(project_id)
        if search:
            # Escape LIKE metacharacters so user input matches literally
            # (backslash is Postgres' default ILIKE escape char).
            escaped = (
                search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            )
            sql += " AND c.title ILIKE :search"
            params["search"] = f"%{escaped}%"
        sql += " ORDER BY m.updated_at DESC LIMIT :limit"

        rows = await db_engine.fetch_all(sql, params)
        return [self._to_legacy_shape(r) for r in rows]

    async def get_session(self, *, session_id: int) -> Optional[Dict[str, Any]]:
        """Fetch a single session row (joined), or None.

        Archived conversations (``archived_at IS NOT NULL``) are treated
        as invisible — same as legacy's ``status='deleted'`` filtering —
        so the service's ``get_session`` 404s instead of leaking a
        soft-deleted row. Ownership stays with the service.

        NOTE (deliberate stricter-than-legacy behavior): the retired
        Supabase-backed store's direct GET did not filter by status, so it
        would still return soft-deleted sessions if looked up directly by
        id; this store is stricter by design and returns None for archived
        rows. This is safe because the UI never re-GETs a session it just
        deleted.
        """
        return await self._fetch_by_id(session_id)

    async def rename_session(self, *, session_id: int, title: str) -> Dict[str, Any]:
        """Update the conversation's title, touch the meta sidecar's
        updated_at, and return the refreshed legacy-shaped row."""
        cid = _bigint(session_id)
        eng = db_engine.get_engine()
        async with eng.begin() as conn:
            await conn.execute(
                text("UPDATE public.conversations SET title = :title WHERE id = :cid"),
                {"title": title, "cid": cid},
            )
            await conn.execute(
                text(
                    "UPDATE public.conversation_ai_meta"
                    " SET updated_at = now()"
                    " WHERE conversation_id = :cid"
                ),
                {"cid": cid},
            )
        return await self._fetch_by_id(cid)

    async def soft_delete_session(self, *, session_id: int) -> None:
        """Archive the conversation (``archived_at = now()``)."""
        await db_engine.execute(
            "UPDATE public.conversations SET archived_at = now() WHERE id = :cid",
            {"cid": _bigint(session_id)},
        )

    async def bump_counters(
        self, *, session_id: int, add_tokens: int, add_messages: int
    ) -> None:
        """Write total_tokens / message_count on conversation_ai_meta.

        NOTE on semantics: despite the parameter names (inherited from the
        Protocol), the service computes and passes ABSOLUTE values
        (``prior + turn``), not deltas — see
        ``AILibraryChatService.chat`` (turn_tokens/prior_total math right
        before this call). An atomic ``total_tokens = total_tokens + :t``
        SQL increment would be a strictly safer primitive, but this store
        writes the given absolutes as-is per the Protocol's
        write-what-you're-given contract.
        """
        await db_engine.execute(
            """
            UPDATE public.conversation_ai_meta
               SET total_tokens = :total_tokens,
                   message_count = :message_count,
                   updated_at = now()
             WHERE conversation_id = :cid
            """,
            {
                "total_tokens": add_tokens,
                "message_count": add_messages,
                "cid": _bigint(session_id),
            },
        )

    # ------------------------------------------------------------------
    # Messages (Task 4)
    # ------------------------------------------------------------------
    #
    # Physical -> legacy mapping (ai_messages row keys, see message_store.py
    # Protocol docstring): id, session_id, role, content, agent_id,
    # prompt_tokens, completion_tokens, metadata_json, created_at.
    #
    #   ai_messages.id                 -> messages.id          (native, not stringified —
    #                                      same id-shape convention as the session half)
    #   ai_messages.session_id         -> messages.conversation_id (native)
    #   ai_messages.role               <- sender_type ('agent'->'assistant', else passthrough)
    #   ai_messages.content            <- body['text']
    #   ai_messages.agent_id           <- body['meta']['agent_id']  (decoration)
    #   ai_messages.prompt_tokens      <- body['meta']['prompt_tokens']  (decoration)
    #   ai_messages.completion_tokens  <- body['meta']['completion_tokens']  (decoration)
    #   ai_messages.metadata_json      <- body['meta'] MINUS the 3 decoration keys above —
    #                                      this reconstructs the caller's original
    #                                      `metadata` dict passed to append_assistant_message
    #                                      byte-for-byte (run_id / tool_calls / awaiting_approval
    #                                      MUST survive the round trip).
    #
    # User messages never carry a 'meta' key, so decoration fields are
    # always None/{} for role='user' rows — user messages never carry
    # agent_id/tokens/metadata_json.

    @staticmethod
    def _to_legacy_message_shape(row: Dict[str, Any]) -> Dict[str, Any]:
        """Map a public.messages row (keys: id, conversation_id, seq,
        sender_type, sender_id, from_agent_id, type, body, created_at) into
        the legacy ai_messages row shape.

        ``id`` / ``session_id`` are returned native (not stringified) —
        deliberate, matching the session-half id-shape convention documented
        at the top of this module: the schema layer's own
        coercion/validators (e.g. ``_COERCE_IDS`` / Pydantic) are what
        stringify BIGINT ids for the API response, so don't "fix" it here.
        """
        body = row.get("body") or {}
        # asyncpg/SQLAlchemy normally hands JSONB back as a decoded dict
        # (proven for `body` in Phase 1.5's conversation_memory_service /
        # conversation_agent_turn readers) — but defend against a driver
        # returning the raw JSON text.
        if isinstance(body, str):
            body = json.loads(body)
        meta = body.get("meta") or {}
        metadata_json = {
            k: v for k, v in meta.items() if k not in _META_DECORATION_KEYS
        }
        # NOTE (empty-metadata asymmetry): on append, a caller passing an
        # empty metadata dict gets it echoed back as `{}` (see
        # append_assistant_message); on reload here, an empty (or
        # decoration-only) `meta` reconstructs to `None` via the
        # `metadata_json or None` below. Nothing distinguishes "caller
        # passed {}" from "caller passed nothing" after the round trip.
        sender_type = row.get("sender_type")
        return {
            "id": row["id"],
            "session_id": row.get("conversation_id"),
            # The `sender_type` fallback (returning it unmapped) is
            # unreachable in practice — `messages.sender_type` has a CHECK
            # constraint limiting it to the three keys in _ROLE_MAP — but is
            # kept as belt-and-braces so a schema/enum drift degrades to a
            # passthrough value instead of a KeyError.
            "role": _ROLE_MAP.get(sender_type, sender_type),
            "content": body.get("text", ""),
            "agent_id": meta.get("agent_id"),
            "prompt_tokens": meta.get("prompt_tokens"),
            "completion_tokens": meta.get("completion_tokens"),
            "metadata_json": metadata_json or None,
            "created_at": row.get("created_at"),
        }

    async def get_messages(
        self, *, session_id: int, limit: int = 200
    ) -> List[Dict[str, Any]]:
        """Chronological (seq ASC), non-deleted messages for a conversation."""
        rows = await db_engine.fetch_all(
            """
            SELECT id, conversation_id, seq, sender_type, sender_id,
                   from_agent_id, type, body, created_at
              FROM public.messages
             WHERE conversation_id = :cid AND deleted_at IS NULL
             ORDER BY seq ASC
             LIMIT :limit
            """,
            {"cid": _bigint(session_id), "limit": limit},
        )
        return [self._to_legacy_message_shape(r) for r in rows]

    async def append_user_message(
        self, *, session_id: int, user_id: str, content: str
    ) -> Dict[str, Any]:
        """Insert a user-role message via the shared ConversationRepository
        (atomic seq allocation + read-cursor advance)."""
        from app.repositories.conversation_repository import (
            get_conversation_repository,
        )

        row = await get_conversation_repository().send_message(
            conversation_id=_bigint(session_id),
            sender_id=user_id,
            sender_type="user",
            type="text",
            body={"text": content},
            parent_id=None,
        )
        return {
            "id": row["id"],
            "session_id": row.get("conversation_id"),
            "role": "user",
            "content": content,
            "agent_id": None,
            "prompt_tokens": None,
            "completion_tokens": None,
            "metadata_json": None,
            "created_at": row.get("created_at"),
        }

    async def append_assistant_message(
        self,
        *,
        session_id: int,
        agent_id: Optional[str],
        content: str,
        prompt_tokens: int,
        completion_tokens: int,
        metadata: dict,
    ) -> Dict[str, Any]:
        """Insert an agent-role message. Usage + caller metadata fold into
        ``body['meta']`` (the ``messages`` table has no dedicated columns for
        them); ``get_messages`` unpacks that sidecar back into the legacy
        agent_id/prompt_tokens/completion_tokens/metadata_json fields.

        ``metadata`` may NOT reuse the reserved decoration keys
        (``agent_id`` / ``prompt_tokens`` / ``completion_tokens``) — those
        are the Protocol boundary's own fields, and a caller key of the same
        name would silently overwrite them in ``meta`` below (dict unpacking
        order) and then get stripped back out on read by
        ``_to_legacy_message_shape``, losing the caller's real value with no
        error. Reject it up front instead.
        """
        from app.repositories.conversation_repository import (
            get_conversation_repository,
        )

        reserved = set(_META_DECORATION_KEYS) & set((metadata or {}).keys())
        if reserved:
            raise ValueError(
                f"metadata keys {sorted(reserved)} are reserved for message decoration"
            )

        agent_id_str = str(agent_id) if agent_id is not None else None
        meta = {
            "agent_id": agent_id_str,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            **(metadata or {}),
        }
        row = await get_conversation_repository().send_message(
            conversation_id=_bigint(session_id),
            sender_id=None,
            sender_type="agent",
            type="text",
            body={"text": content, "meta": meta},
            parent_id=None,
            from_agent_id=agent_id_str,
        )
        return {
            "id": row["id"],
            "session_id": row.get("conversation_id"),
            "role": "assistant",
            "content": content,
            "agent_id": agent_id_str,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "metadata_json": metadata,
            "created_at": row.get("created_at"),
        }
