"""Unified Conversation orchestration + in-app authorization (Phase 1).

Port of chat_service.py onto ConversationRepository (Task 4).

Rename Map applied throughout:
  ChatService             → ConversationService
  ChatRepository          → ConversationRepository
  channel_id              → conversation_id
  channel["team_id"]      → conversation["scope_id"]
  content_type            → type
  reply_to_id             → parent_id
  from_bot_agent_id       → from_agent_id
  _CHANNEL_TURN_CAP       → _CONVERSATION_TURN_CAP
  _channel_agent_semaphores → _conversation_agent_semaphores
  get_chat_service        → get_conversation_service

Forward dependency (Task 6):
  run_conversation_agent_turn is imported from a stub module
  (app.services.chat.conversation_agent_turn) at the top level.
  Task 6 will replace the stub body.  Tests can monkeypatch via:
    monkeypatch.setattr(
        app.services.chat.conversation_agent_turn,
        "run_conversation_agent_turn", fake_fn
    )
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from loguru import logger

from app.repositories.agent_repository import get_agent_repository
from app.repositories.conversation_repository import (
    ConversationRepository,
    get_conversation_repository,
)
from app.services.ai.permissions.agent_chat_caps import agent_chat_caps
from app.services.chat.conversation_agent_turn import run_conversation_agent_turn
from app.services.chat.mention_parser import extract_agent_mentions

# Per-conversation cap on concurrent agent turns (anti-flood).
_CONVERSATION_TURN_CAP: int = 2
_conversation_agent_semaphores: dict[int, asyncio.Semaphore] = {}


def _get_conversation_semaphore(conversation_id: int) -> asyncio.Semaphore:
    if conversation_id not in _conversation_agent_semaphores:
        _conversation_agent_semaphores[conversation_id] = asyncio.Semaphore(
            _CONVERSATION_TURN_CAP
        )
    return _conversation_agent_semaphores[conversation_id]


class ConversationService:
    def __init__(self, repo: Optional[ConversationRepository] = None) -> None:
        self._repo = repo or get_conversation_repository()

    async def create_conversation(
        self,
        *,
        user_id: str,
        scope_id: int,
        type: str,
        name: Optional[str],
        history_mode: str,
        member_ids: list[str],
    ) -> dict[str, Any]:
        if not await self._repo.is_team_member(team_id=scope_id, user_id=user_id):
            raise PermissionError("not a member of this team")
        return await self._repo.create_conversation(
            creator_id=user_id,
            scope_id=scope_id,
            type=type,
            name=name,
            history_mode=history_mode,
            member_ids=member_ids,
        )

    async def list_my_conversations(self, *, user_id: str) -> list[dict[str, Any]]:
        return await self._repo.get_my_conversations(user_id)

    async def add_members(
        self, *, conversation_id: int, user_id: str, user_ids: list[str]
    ) -> int:
        await self._require_member(conversation_id, user_id)
        scope_id = await self._repo.conversation_scope_id(
            conversation_id=conversation_id
        )
        if scope_id is not None:
            for target in user_ids:
                if not await self._repo.is_team_member(
                    team_id=scope_id, user_id=target
                ):
                    raise PermissionError("target user not in this team")
        return await self._repo.add_members(
            conversation_id=conversation_id, user_ids=user_ids
        )

    async def post_message(
        self,
        *,
        conversation_id: int,
        user_id: str,
        type: str,
        body: dict[str, Any],
        parent_id: Optional[int],
    ) -> dict[str, Any]:
        await self._require_member(conversation_id, user_id)
        msg = await self._repo.send_message(
            conversation_id=conversation_id,
            sender_id=user_id,
            sender_type="user",
            type=type,
            body=body,
            parent_id=parent_id,
        )
        if type == "text":
            try:
                raw = body.get("mention_user_ids")
                if isinstance(raw, list) and raw:
                    ids = [str(x) for x in raw if isinstance(x, str)]
                    if ids:
                        members = await self._repo.list_member_ids(conversation_id)
                        targets = [
                            u
                            for u in dict.fromkeys(ids)
                            if u in members and u != user_id
                        ]
                        if targets:
                            await self._repo.increment_mentions(
                                conversation_id=conversation_id, user_ids=targets
                            )
            except Exception as exc:
                logger.warning(
                    f"[post_message] mention fan-out failed: "
                    f"conversation={conversation_id} error={exc!r}"
                )
        return msg

    async def get_messages(
        self,
        *,
        conversation_id: int,
        user_id: str,
        before_seq: Optional[int],
        limit: int,
    ) -> list[dict[str, Any]]:
        await self._require_member(conversation_id, user_id)
        return await self._repo.list_messages(
            conversation_id=conversation_id,
            before_seq=before_seq,
            limit=min(max(limit, 1), 100),
        )

    async def mark_read(
        self, *, conversation_id: int, user_id: str, last_read_seq: int
    ) -> None:
        await self._require_member(conversation_id, user_id)
        await self._repo.mark_read(
            conversation_id=conversation_id,
            user_id=user_id,
            last_read_seq=last_read_seq,
        )

    async def add_agent(
        self,
        *,
        conversation_id: int,
        user_id: str,
        agent_slug: str,
    ) -> dict[str, Any]:
        """PERM-08: caller must be a conversation member; agent must be chat-enabled."""
        await self._require_member(conversation_id, user_id)
        conversation = await self._repo.get_conversation(
            conversation_id=conversation_id
        )
        if conversation is None:
            raise ValueError(f"conversation {conversation_id} not found")
        ar = get_agent_repository()
        agent = await ar.get_by_slug(agent_slug)
        if agent is None:
            raise ValueError(f"agent {agent_slug!r} not found")
        caps = agent_chat_caps(agent)
        if not caps.enabled or not caps.allows_team(conversation["scope_id"]):
            raise PermissionError("agent not enabled for chat in this team")
        await self._repo.add_agent_member(
            conversation_id=conversation_id,
            agent_id=agent["id"],
            added_by=user_id,
        )
        return {"added": True, "agent_id": str(agent["id"])}

    async def edit_message(
        self,
        *,
        conversation_id: int,
        user_id: str,
        message_id: int,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        await self._require_member(conversation_id, user_id)
        row = await self._repo.edit_message(
            conversation_id=conversation_id,
            message_id=message_id,
            sender_id=user_id,
            body=body,
        )
        if row is None:
            raise PermissionError("message not found or not editable")
        return row

    async def delete_message(
        self,
        *,
        conversation_id: int,
        user_id: str,
        message_id: int,
    ) -> dict[str, Any]:
        await self._require_member(conversation_id, user_id)
        row = await self._repo.soft_delete_message(
            conversation_id=conversation_id,
            message_id=message_id,
            sender_id=user_id,
        )
        if row is None:
            raise PermissionError("message not found or not deletable")
        return row

    async def dispatch_summons(
        self,
        *,
        conversation_id: int,
        summoner_user_id: str,
        message: dict[str, Any],
    ) -> list[str]:
        """Background entry-point: parse @-mentions and run one turn per summoned agent.

        Iron anti-loop: returns [] immediately when the message was agent-authored.
        Returns slugs of agents that successfully replied.
        """
        # Anti-loop guard — MUST be the very first check, before any I/O.
        if message.get("from_agent_id") is not None:
            return []

        conversation = await self._repo.get_conversation(
            conversation_id=conversation_id
        )
        if conversation is None:
            logger.warning(
                f"[dispatch_summons] conversation {conversation_id} not found"
            )
            return []

        agent_ids = await self._repo.list_conversation_agent_ids(
            conversation_id=conversation_id
        )
        if not agent_ids:
            return []

        ar = get_agent_repository()
        slug_to_agent: dict[str, Any] = {}
        for aid in agent_ids:
            agent = await ar.get_by_id(aid)
            if agent is None:
                continue
            caps = agent_chat_caps(agent)
            if not caps.enabled or not caps.allows_team(conversation["scope_id"]):
                continue
            slug_to_agent[agent["slug"]] = agent

        body = message.get("body") or {}
        mentioned = extract_agent_mentions(body, set(slug_to_agent))
        if not mentioned:
            return []

        sem = _get_conversation_semaphore(conversation_id)
        replied: list[str] = []
        for slug in mentioned:
            agent = slug_to_agent[slug]
            async with sem:
                try:
                    reply = await run_conversation_agent_turn(
                        agent_slug=slug,
                        summoner_user_id=summoner_user_id,
                        conversation=conversation,
                    )
                except Exception as exc:
                    logger.error(
                        f"[dispatch_summons] agent_turn_failed: "
                        f"conversation={conversation_id} agent={slug} error={exc!r}"
                    )
                    continue
            if reply:
                try:
                    await self._repo.send_message(
                        conversation_id=conversation_id,
                        sender_id=None,
                        sender_type="agent",
                        type="text",
                        body={"text": reply},
                        parent_id=None,
                        from_agent_id=agent["id"],
                    )
                    replied.append(slug)
                except Exception as exc:
                    logger.error(
                        f"[dispatch_summons] reply_write_failed: "
                        f"agent={slug} error={exc!r}"
                    )
        return replied

    async def _require_member(self, conversation_id: int, user_id: str) -> None:
        if not await self._repo.is_member(
            conversation_id=conversation_id, user_id=user_id
        ):
            raise PermissionError("not a member of this conversation")


_svc: Optional[ConversationService] = None


def get_conversation_service() -> ConversationService:
    global _svc
    if _svc is None:
        _svc = ConversationService()
    return _svc
