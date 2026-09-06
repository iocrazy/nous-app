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

from app.services.library.resources_service import _resolve_personal_team_id

# public.messages.sender_type -> legacy ai_messages.role
_ROLE_MAP = {"agent": "assistant", "user": "user", "system": "system"}

# Decoration keys folded into body["meta"] by append_assistant_message that
# must be stripped back out on read to reconstruct the caller's original
# `metadata` dict byte-for-byte (see get_messages / _to_legacy_message_shape).
_META_DECORATION_KEYS = ("agent_id", "prompt_tokens", "completion_tokens")

# The only attachment keys persisted under body["attachments"] on a user-role
# message — display metadata for re-rendering the bubble on history reload.
# Deliberately excludes `data_url`: the vision pipeline resolves bytes from the
# request separately, and inlining them here would bloat every history read.
# See ConversationsAiStore.display_attachments for the shared reducer.
# `asset_id` (P5) joins the list for the same reason `resource_id` is on it: an
# `asset_ref` bubble re-rendered from history has nothing to point at without
# it, and the reference kinds carry no bytes to fall back on — the chip would
# degrade to a bare name with no link.
#
# `loadout_id` is REACHABLE as of v2. It was whitelisted ahead of time so that
# the day a picker sent a real loadout the value would persist without a second
# migration of this tuple; the staged-chip loadout menu
# (`frontend/components/chat/StagedAssetLoadoutMenu.tsx`) is that day, and both
# entry points can now carry one. The reducer still drops None
# (`if a.get(k) is not None`), so BOTH shapes are real and a fixture set that
# covers only one under-tests the persisted bubble:
#
#   * key absent  — the asset wears its default loadout (the common case, and
#     what every pre-v2 row looks like);
#   * key present — the author picked a specific outfit for that turn.
#
# A reader of a persisted row must therefore treat a missing `loadout_id` as
# "default loadout", never as "this build cannot emit one".
_DISPLAY_ATTACHMENT_KEYS = (
    "kind",
    "resource_id",
    "asset_id",
    "loadout_id",
    "mime",
    "alt_text",
    "name",
)


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

    @staticmethod
    def _joined_query():
        """ORM equivalent of the legacy ``_JOIN_SELECT`` string (Phase B4):
        conversations JOIN conversation_ai_meta JOIN conversation_members
        (member_type='user'). Callers append their own WHERE/ORDER
        BY/LIMIT on top of the returned Select."""
        from sqlalchemy import select

        from app.models import ConversationAiMeta, ConversationMembers, Conversations

        return (
            select(
                Conversations.id,
                Conversations.scope_id,
                Conversations.project_id,
                Conversations.title,
                Conversations.created_at,
                ConversationAiMeta.agent_slug,
                ConversationAiMeta.agent_id,
                ConversationAiMeta.total_tokens,
                ConversationAiMeta.message_count,
                ConversationAiMeta.context_type,
                ConversationAiMeta.context_id,
                ConversationAiMeta.updated_at,
                ConversationMembers.user_id,
            )
            .select_from(Conversations)
            .join(
                ConversationAiMeta,
                ConversationAiMeta.conversation_id == Conversations.id,
            )
            .join(
                ConversationMembers,
                (ConversationMembers.conversation_id == Conversations.id)
                & (ConversationMembers.member_type == "user"),
            )
        )

    async def _fetch_by_id(self, session_id: int) -> Optional[Dict[str, Any]]:
        from app.db.session import read_scope
        from app.models import Conversations

        cid = _bigint(session_id)
        async with read_scope() as session:
            row = (
                (
                    await session.execute(
                        self._joined_query()
                        .where(
                            Conversations.id == cid,
                            Conversations.archived_at.is_(None),
                        )
                        .limit(1)
                    )
                )
                .mappings()
                .first()
            )
        return self._to_legacy_shape(dict(row)) if row else None

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
        from sqlalchemy import insert
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        from app.db.session import write_scope
        from app.models import ConversationAiMeta, ConversationMembers, Conversations

        scope_id = (
            _bigint(team_id)
            if team_id is not None
            else _bigint(await _resolve_personal_team_id(user_id))
        )

        async with write_scope() as session:
            conv_row = (
                (
                    await session.execute(
                        insert(Conversations)
                        .values(
                            type="direct_agent",
                            scope_id=scope_id,
                            project_id=(
                                _bigint(project_id) if project_id is not None else None
                            ),
                            title=title,
                            created_by=user_id,
                        )
                        .returning(
                            Conversations.id,
                            Conversations.scope_id,
                            Conversations.project_id,
                            Conversations.title,
                            Conversations.created_at,
                        )
                    )
                )
                .mappings()
                .one()
            )
            cid = conv_row["id"]

            # Bare ON CONFLICT DO NOTHING (no index_elements) — matches the
            # legacy raw SQL: conversation_members has no real PK, only the
            # expression index uq_conversation_members(conversation_id,
            # member_type, COALESCE(user_id, agent_id)), which SQLAlchemy
            # cannot name by column list.
            await session.execute(
                pg_insert(ConversationMembers)
                .values(
                    conversation_id=cid,
                    member_type="user",
                    user_id=user_id,
                    role="owner",
                )
                .on_conflict_do_nothing()
            )
            await session.execute(
                pg_insert(ConversationMembers)
                .values(
                    conversation_id=cid,
                    member_type="agent",
                    agent_id=agent_id,
                    added_by=user_id,
                )
                .on_conflict_do_nothing()
            )
            meta_row = (
                (
                    await session.execute(
                        insert(ConversationAiMeta)
                        .values(
                            conversation_id=cid,
                            agent_slug=agent_slug,
                            agent_id=agent_id,
                            context_type=context_type,
                            context_id=context_id,
                        )
                        .returning(
                            ConversationAiMeta.total_tokens,
                            ConversationAiMeta.message_count,
                            ConversationAiMeta.updated_at,
                        )
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
        from app.db.session import read_scope
        from app.models import ConversationAiMeta, ConversationMembers, Conversations

        stmt = self._joined_query().where(
            Conversations.type == "direct_agent",
            Conversations.archived_at.is_(None),
            ConversationMembers.user_id == user_id,
        )
        if agent_slug is not None:
            stmt = stmt.where(ConversationAiMeta.agent_slug == agent_slug)
        if project_id is not None:
            stmt = stmt.where(Conversations.project_id == _bigint(project_id))
        if search:
            # Escape LIKE metacharacters so user input matches literally
            # (backslash is Postgres' default ILIKE escape char — ilike()
            # with no explicit `escape=` kwarg emits no ESCAPE clause, which
            # matches the legacy raw SQL exactly).
            escaped = (
                search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            )
            stmt = stmt.where(Conversations.title.ilike(f"%{escaped}%"))
        stmt = stmt.order_by(ConversationAiMeta.updated_at.desc()).limit(limit)

        async with read_scope() as session:
            rows = (await session.execute(stmt)).mappings().all()
        return [self._to_legacy_shape(dict(r)) for r in rows]

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
        from sqlalchemy import func, update

        from app.db.session import write_scope
        from app.models import ConversationAiMeta, Conversations

        cid = _bigint(session_id)
        async with write_scope() as session:
            await session.execute(
                update(Conversations).where(Conversations.id == cid).values(title=title)
            )
            await session.execute(
                update(ConversationAiMeta)
                .where(ConversationAiMeta.conversation_id == cid)
                .values(updated_at=func.now())
            )
        return await self._fetch_by_id(cid)

    async def soft_delete_session(self, *, session_id: int) -> None:
        """Archive the conversation (``archived_at = now()``)."""
        from sqlalchemy import func, update

        from app.db.session import write_scope
        from app.models import Conversations

        async with write_scope() as session:
            await session.execute(
                update(Conversations)
                .where(Conversations.id == _bigint(session_id))
                .values(archived_at=func.now())
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
        from sqlalchemy import func, update

        from app.db.session import write_scope
        from app.models import ConversationAiMeta

        async with write_scope() as session:
            await session.execute(
                update(ConversationAiMeta)
                .where(ConversationAiMeta.conversation_id == _bigint(session_id))
                .values(
                    total_tokens=add_tokens,
                    message_count=add_messages,
                    updated_at=func.now(),
                )
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
            # Display metadata written by append_user_message; absent (None)
            # for assistant rows and pre-feature user rows.
            "attachments": body.get("attachments") or None,
            "created_at": row.get("created_at"),
        }

    async def get_messages(
        self, *, session_id: int, limit: int = 200
    ) -> List[Dict[str, Any]]:
        """Chronological (seq ASC), non-deleted messages for a conversation."""
        from sqlalchemy import select

        from app.db.session import read_scope
        from app.models import Messages

        async with read_scope() as session:
            rows = (
                (
                    await session.execute(
                        select(
                            Messages.id,
                            Messages.conversation_id,
                            Messages.seq,
                            Messages.sender_type,
                            Messages.sender_id,
                            Messages.from_agent_id,
                            Messages.type,
                            Messages.body,
                            Messages.created_at,
                        )
                        .where(
                            Messages.conversation_id == _bigint(session_id),
                            Messages.deleted_at.is_(None),
                        )
                        .order_by(Messages.seq.asc())
                        .limit(limit)
                    )
                )
                .mappings()
                .all()
            )
        return [self._to_legacy_message_shape(dict(r)) for r in rows]

    @staticmethod
    def display_attachments(
        attachments: Optional[List[Dict[str, Any]]],
    ) -> Optional[List[Dict[str, Any]]]:
        """Reduce attachment dicts to the display metadata stored on a message.

        The stored shape is a contract, not a convenience: history reloads
        re-render user bubbles from it, so every writer of a user-role message
        must produce the SAME keys. Anything not listed here — most importantly
        `data_url` bytes — must never reach the store.

        Shared by run_session_turn (the agent-turn writer) and the issue
        note path (the suppressed-comment writer) so the two cannot drift into
        storing different shapes for the same kind of row.
        """
        if not attachments:
            return None
        reduced = [
            {k: a.get(k) for k in _DISPLAY_ATTACHMENT_KEYS if a.get(k) is not None}
            for a in attachments
        ]
        return reduced or None

    async def append_user_message(
        self,
        *,
        session_id: int,
        user_id: str,
        content: str,
        attachments: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Insert a user-role message via the shared ConversationRepository
        (atomic seq allocation + read-cursor advance).

        ``attachments`` is display metadata only (kind / resource_id / mime /
        alt_text) — small dicts, never file bytes or data URLs. Stored under
        ``body['attachments']`` so history reloads can re-render the image
        chips in the user bubble; the vision pipeline resolves the actual
        bytes separately from the request's attachments.
        """
        from app.repositories.conversation_repository import (
            get_conversation_repository,
        )

        body: Dict[str, Any] = {"text": content}
        if attachments:
            body["attachments"] = attachments
        row = await get_conversation_repository().send_message(
            conversation_id=_bigint(session_id),
            sender_id=user_id,
            sender_type="user",
            type="text",
            body=body,
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
            "attachments": attachments or None,
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
