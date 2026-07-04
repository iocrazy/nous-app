"""RoutedAiStore — per-session dual-store dispatch for the Phase 2 strangler.

Routing contract
-----------------
``FEATURE_DIRECT_CONVERSATIONS`` (``off`` | ``shadow`` | ``on``, read
DYNAMICALLY on every call — never cached at construction time, so ops/tests
can flip it without a process restart) decides where a chat session's
storage lives:

* ``off`` (default) — everything goes through ``LegacyAiStore``
  (``ai_sessions`` / ``ai_messages``). The new ``ConversationsAiStore`` is
  never touched.
* ``shadow`` — ``LegacyAiStore`` stays authoritative for every read the
  caller sees. ``create_session`` additionally best-effort mirrors the
  create into ``ConversationsAiStore`` (a throwaway row — its id is
  unrelated to the legacy session's id, nothing ever looks it up) purely
  for read-diff observability, logged under the ``[p2-shadow]`` prefix.
  Mirror failures and diff mismatches are swallowed — they NEVER raise and
  NEVER affect the value returned to the caller.
* ``on`` — new sessions are created directly on ``ConversationsAiStore``.
  Existing legacy sessions keep serving from ``ai_*`` — there is no data
  migration. ``list_sessions`` therefore merges both stores so a user with
  old + new sessions sees all of them, newest first.

Per-session ops (get/rename/delete/bump/messages/append) resolve the
*owner* store for a given ``session_id`` via ``_resolve``, which checks an
instance-local cache first. On a cache miss:

* ``off`` / ``shadow`` — always resolves to legacy, no probe. Both modes
  keep legacy as the ONLY store per-session ops ever touch (``shadow``'s
  mirror rows are throwaway and never looked up by session_id — see
  ``create_session`` below); this also means the new store's own
  dependencies (e.g. the SQLAlchemy engine) don't need to be configured
  at all while the flag is off, which matters for gradual rollout.
* ``on`` — probes ``LegacyAiStore.get_session``; a hit means the session
  is a legacy row, a miss means it must be a ``conversations`` row (the
  only two stores that can ever hold it). This is what gives natural
  dual-serving: an id created before the flag flipped to ``on`` keeps
  routing to legacy, while a fresh ``on``-mode create routes to
  ``conversations``.

The resolution is cached for the lifetime of this ``RoutedAiStore``
instance so a session's ops don't re-probe legacy on every call.

No data migration happens anywhere in this file — ``off``/``shadow``/``on``
only ever change where *new* writes land. Legacy is authoritative for any
row it already owns, in every mode.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from loguru import logger

from app.core.config import settings
from app.services.ai.chat.conversations_ai_store import ConversationsAiStore
from app.services.ai.chat.legacy_ai_store import LegacyAiStore
from app.services.ai.chat.message_store import MessageStore

# Sampling guard for shadow-diff logging, reserved for later volume tuning.
# 1.0 = log every shadow comparison (no sampling yet).
_SHADOW_SAMPLE = 1.0

# Stable subset of fields compared by `_shadow_diff` — deliberately narrow:
# shadow diffs are best-effort observability, not a correctness proof (the
# two rows being compared may not even be the "same" logical entity, e.g.
# the create_session mirror lives at a different id than the legacy row).
_DIFF_FIELDS = ("title", "status", "role", "content")

_VALID_MODES = ("off", "shadow", "on")

_OWNER_LEGACY = "legacy"
_OWNER_CONVERSATIONS = "conversations"


def _normalize_mode() -> str:
    """Read + normalize settings.FEATURE_DIRECT_CONVERSATIONS dynamically.

    Unknown values are treated as 'off' (the safe default) and logged once
    per call — never raise on a bad env value.
    """
    raw = (settings.FEATURE_DIRECT_CONVERSATIONS or "").strip().lower()
    if raw not in _VALID_MODES:
        logger.warning(
            "[p2-shadow] unknown FEATURE_DIRECT_CONVERSATIONS={!r}; treating as 'off'",
            raw,
        )
        return "off"
    return raw


def _first_row(value: Any) -> Optional[Dict[str, Any]]:
    """Normalize a MessageStore result (a row dict, or a list of rows) down
    to "the first row to compare", or None when there's nothing to compare."""
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _shadow_diff(op: str, legacy_result: Any, new_result: Any) -> None:
    """Best-effort key-by-key diff on a stable field subset.

    Never raises. Logs `[p2-shadow] MISMATCH op=... detail=...` when the
    two sides disagree on any of `_DIFF_FIELDS`. Silent (no log at all)
    when either side is empty/None, or when everything matches.
    """
    try:
        if _SHADOW_SAMPLE < 1.0:
            import random

            if random.random() > _SHADOW_SAMPLE:
                return

        legacy_row = _first_row(legacy_result)
        new_row = _first_row(new_result)
        if not legacy_row or not new_row:
            return

        mismatches = [
            f"{key}: legacy={legacy_row.get(key)!r} new={new_row.get(key)!r}"
            for key in _DIFF_FIELDS
            if key in legacy_row
            and key in new_row
            and legacy_row.get(key) != new_row.get(key)
        ]
        if mismatches:
            logger.warning(
                "[p2-shadow] MISMATCH op={} detail={}", op, "; ".join(mismatches)
            )
    except Exception as exc:  # noqa: BLE001 - shadow diffs must never raise
        logger.warning("[p2-shadow] diff_failed op={} err={!r}", op, exc)


class RoutedAiStore:
    """MessageStore that dispatches per-session between LegacyAiStore and
    ConversationsAiStore, gated by FEATURE_DIRECT_CONVERSATIONS.

    See module docstring for the full routing contract.
    """

    store_kind = "routed"

    def __init__(
        self,
        legacy: Optional[MessageStore] = None,
        new: Optional[MessageStore] = None,
    ) -> None:
        self._legacy: MessageStore = legacy or LegacyAiStore()
        self._new: MessageStore = new or ConversationsAiStore()
        # Instance-local — NOT a module global. A fresh RoutedAiStore (one
        # per service/request in current wiring) starts with an empty cache;
        # nothing here leaks across instances or processes.
        self._owner_cache: Dict[int, str] = {}

    # ------------------------------------------------------------------
    # Owner resolution
    # ------------------------------------------------------------------

    def _store_for(self, owner: str) -> MessageStore:
        return self._legacy if owner == _OWNER_LEGACY else self._new

    async def _resolve(self, session_id: int) -> str:
        """Resolve + cache which store owns `session_id`.

        Cache hit → return immediately, no probe. Cache miss:
          * mode != 'on' → always "legacy", no probe at all (off/shadow
            per-session ops never touch the new store — see module
            docstring).
          * mode == 'on' → probe LegacyAiStore.get_session; a hit means
            "legacy", a miss means the session must live on the
            conversations store (the only two stores a RoutedAiStore ever
            knows about).
        """
        cached = self._owner_cache.get(session_id)
        if cached is not None:
            return cached

        if _normalize_mode() != "on":
            self._owner_cache[session_id] = _OWNER_LEGACY
            return _OWNER_LEGACY

        row = await self._legacy.get_session(session_id=session_id)
        owner = _OWNER_LEGACY if row is not None else _OWNER_CONVERSATIONS
        self._owner_cache[session_id] = owner
        return owner

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
        kwargs: Dict[str, Any] = {
            "user_id": user_id,
            "agent_slug": agent_slug,
            "agent_id": agent_id,
            "title": title,
            "project_id": project_id,
            "team_id": team_id,
            "context_type": context_type,
            "context_id": context_id,
        }
        mode = _normalize_mode()

        if mode == "on":
            row = await self._new.create_session(**kwargs)
            if row is not None and row.get("id") is not None:
                self._owner_cache[row["id"]] = _OWNER_CONVERSATIONS
            return row

        # off / shadow: legacy is authoritative for the returned result.
        row = await self._legacy.create_session(**kwargs)
        if row is not None and row.get("id") is not None:
            self._owner_cache[row["id"]] = _OWNER_LEGACY

        if mode == "shadow":
            try:
                mirror = await self._new.create_session(**kwargs)
            except Exception as exc:  # noqa: BLE001 - mirror is best-effort
                logger.warning("[p2-shadow] mirror_create_failed: {!r}", exc)
            else:
                _shadow_diff("create_session", row, mirror)

        return row

    async def list_sessions(
        self,
        *,
        user_id: str,
        agent_slug: Optional[str],
        project_id: Optional[int],
        limit: int,
    ) -> List[Dict[str, Any]]:
        kwargs: Dict[str, Any] = {
            "user_id": user_id,
            "agent_slug": agent_slug,
            "project_id": project_id,
            "limit": limit,
        }
        mode = _normalize_mode()

        legacy_rows = await self._legacy.list_sessions(**kwargs)

        if mode == "off":
            return legacy_rows

        if mode == "shadow":
            try:
                new_rows = await self._new.list_sessions(**kwargs)
            except Exception as exc:  # noqa: BLE001 - shadow list is best-effort
                logger.warning("[p2-shadow] mirror_list_failed: {!r}", exc)
            else:
                logger.info(
                    "[p2-shadow] op=list_sessions legacy_count={} new_count={}",
                    len(legacy_rows),
                    len(new_rows),
                )
                _shadow_diff("list_sessions", legacy_rows, new_rows)
            return legacy_rows

        # mode == "on": users with old + new sessions must see both.
        new_rows = await self._new.list_sessions(**kwargs)
        merged = list(legacy_rows) + list(new_rows)
        merged.sort(key=lambda row: row.get("updated_at") or "", reverse=True)
        return merged[:limit]

    async def get_session(self, *, session_id: int) -> Optional[Dict[str, Any]]:
        cached = self._owner_cache.get(session_id)
        if cached is not None:
            return await self._store_for(cached).get_session(session_id=session_id)

        if _normalize_mode() != "on":
            # off / shadow: legacy is the only store per-session ops touch.
            self._owner_cache[session_id] = _OWNER_LEGACY
            return await self._legacy.get_session(session_id=session_id)

        # Cache miss in 'on' mode: the legacy probe result IS the answer
        # when it hits, so don't fetch it twice.
        row = await self._legacy.get_session(session_id=session_id)
        if row is not None:
            self._owner_cache[session_id] = _OWNER_LEGACY
            return row

        self._owner_cache[session_id] = _OWNER_CONVERSATIONS
        return await self._new.get_session(session_id=session_id)

    async def rename_session(self, *, session_id: int, title: str) -> Dict[str, Any]:
        owner = await self._resolve(session_id)
        return await self._store_for(owner).rename_session(
            session_id=session_id, title=title
        )

    async def soft_delete_session(self, *, session_id: int) -> None:
        owner = await self._resolve(session_id)
        await self._store_for(owner).soft_delete_session(session_id=session_id)

    async def bump_counters(
        self, *, session_id: int, add_tokens: int, add_messages: int
    ) -> None:
        owner = await self._resolve(session_id)
        await self._store_for(owner).bump_counters(
            session_id=session_id, add_tokens=add_tokens, add_messages=add_messages
        )

    # ------------------------------------------------------------------
    # Messages
    # ------------------------------------------------------------------

    async def get_messages(
        self, *, session_id: int, limit: int = 200
    ) -> List[Dict[str, Any]]:
        owner = await self._resolve(session_id)
        return await self._store_for(owner).get_messages(
            session_id=session_id, limit=limit
        )

    async def append_user_message(
        self, *, session_id: int, user_id: str, content: str
    ) -> Dict[str, Any]:
        owner = await self._resolve(session_id)
        return await self._store_for(owner).append_user_message(
            session_id=session_id, user_id=user_id, content=content
        )

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
        owner = await self._resolve(session_id)
        return await self._store_for(owner).append_assistant_message(
            session_id=session_id,
            agent_id=agent_id,
            content=content,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            metadata=metadata,
        )
