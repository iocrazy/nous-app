"""Team Chat orchestration + in-app authorization (PHASE-1 + PHASE-2)."""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from loguru import logger

from app.repositories.agent_repository import get_agent_repository
from app.repositories.chat_repository import ChatRepository, get_chat_repository
from app.services.ai.permissions.agent_chat_caps import agent_chat_caps
from app.services.chat.channel_agent_turn import run_channel_agent_turn
from app.services.chat.mention_parser import extract_agent_mentions

# Per-channel cap on concurrent agent turns (anti-flood).
_CHANNEL_TURN_CAP: int = 2
_channel_agent_semaphores: dict[int, asyncio.Semaphore] = {}


def _get_channel_semaphore(channel_id: int) -> asyncio.Semaphore:
    if channel_id not in _channel_agent_semaphores:
        _channel_agent_semaphores[channel_id] = asyncio.Semaphore(_CHANNEL_TURN_CAP)
    return _channel_agent_semaphores[channel_id]


class ChatService:
    def __init__(self, repo: Optional[ChatRepository] = None) -> None:
        self._repo = repo or get_chat_repository()

    async def create_channel(
        self,
        *,
        user_id: str,
        team_id: int,
        type: str,
        name: Optional[str],
        history_mode: str,
        member_ids: list[str],
    ) -> dict[str, Any]:
        if not await self._repo.is_team_member(team_id=team_id, user_id=user_id):
            raise PermissionError("not a member of this team")
        return await self._repo.create_channel(
            creator_id=user_id,
            team_id=team_id,
            type=type,
            name=name,
            history_mode=history_mode,
            member_ids=member_ids,
        )

    async def list_my_channels(self, *, user_id: str) -> list[dict[str, Any]]:
        return await self._repo.get_my_channels(user_id)

    async def add_members(
        self, *, channel_id: int, user_id: str, user_ids: list[str]
    ) -> int:
        await self._require_member(channel_id, user_id)
        team_id = await self._repo.channel_team_id(channel_id=channel_id)
        if team_id is not None:
            for target in user_ids:
                if not await self._repo.is_team_member(team_id=team_id, user_id=target):
                    raise PermissionError("target user not in this team")
        return await self._repo.add_members(channel_id=channel_id, user_ids=user_ids)

    async def post_message(
        self,
        *,
        channel_id: int,
        user_id: str,
        content_type: str,
        body: dict[str, Any],
        reply_to_id: Optional[int],
    ) -> dict[str, Any]:
        await self._require_member(channel_id, user_id)
        return await self._repo.send_message(
            channel_id=channel_id,
            sender_id=user_id,
            sender_type="user",
            content_type=content_type,
            body=body,
            reply_to_id=reply_to_id,
        )

    async def get_messages(
        self, *, channel_id: int, user_id: str, before_seq: Optional[int], limit: int
    ) -> list[dict[str, Any]]:
        await self._require_member(channel_id, user_id)
        return await self._repo.list_messages(
            channel_id=channel_id, before_seq=before_seq, limit=min(max(limit, 1), 100)
        )

    async def mark_read(
        self, *, channel_id: int, user_id: str, last_read_seq: int
    ) -> None:
        await self._require_member(channel_id, user_id)
        await self._repo.mark_read(
            channel_id=channel_id, user_id=user_id, last_read_seq=last_read_seq
        )

    async def add_agent(
        self,
        *,
        channel_id: int,
        user_id: str,
        agent_slug: str,
    ) -> dict[str, Any]:
        """PERM-08: caller must be a channel member; agent must be chat-enabled for the team."""
        await self._require_member(channel_id, user_id)
        channel = await self._repo.get_channel(channel_id=channel_id)
        if channel is None:
            raise ValueError(f"channel {channel_id} not found")
        ar = get_agent_repository()
        agent = await ar.get_by_slug(agent_slug)
        if agent is None:
            raise ValueError(f"agent {agent_slug!r} not found")
        caps = agent_chat_caps(agent)
        if not caps.enabled or not caps.allows_team(channel["team_id"]):
            raise PermissionError("agent not enabled for chat in this team")
        await self._repo.add_agent_to_channel(
            channel_id=channel_id,
            agent_id=agent["id"],
            added_by=user_id,
        )
        return {"added": True, "agent_id": str(agent["id"])}

    async def dispatch_summons(
        self,
        *,
        channel_id: int,
        summoner_user_id: str,
        message: dict[str, Any],
    ) -> list[str]:
        """Background entry-point: parse @-mentions and run one turn per summoned agent.

        Iron anti-loop: returns [] immediately when the message was bot-authored.
        Returns slugs of agents that successfully replied.
        """
        # Anti-loop guard — MUST be the very first check, before any I/O.
        if message.get("from_bot_agent_id"):
            return []

        channel = await self._repo.get_channel(channel_id=channel_id)
        if channel is None:
            logger.warning(f"[dispatch_summons] channel {channel_id} not found")
            return []

        agent_ids = await self._repo.list_channel_agent_ids(channel_id=channel_id)
        if not agent_ids:
            return []

        ar = get_agent_repository()
        slug_to_agent: dict[str, Any] = {}
        for aid in agent_ids:
            agent = await ar.get_by_id(aid)
            if agent is None:
                continue
            caps = agent_chat_caps(agent)
            if not caps.enabled or not caps.allows_team(channel["team_id"]):
                continue
            slug_to_agent[agent["slug"]] = agent

        body = message.get("body") or {}
        mentioned = extract_agent_mentions(body, set(slug_to_agent))
        if not mentioned:
            return []

        sem = _get_channel_semaphore(channel_id)
        replied: list[str] = []
        for slug in mentioned:
            agent = slug_to_agent[slug]
            async with sem:
                try:
                    reply = await run_channel_agent_turn(
                        agent_slug=slug,
                        summoner_user_id=summoner_user_id,
                        channel=channel,
                    )
                except Exception as exc:
                    logger.error(
                        f"[dispatch_summons] agent_turn_failed: "
                        f"channel={channel_id} agent={slug} error={exc!r}"
                    )
                    continue
            if reply:
                await self._repo.send_message(
                    channel_id=channel_id,
                    sender_id=None,
                    sender_type="agent",
                    content_type="text",
                    body={"text": reply},
                    reply_to_id=None,
                    from_bot_agent_id=agent["id"],
                )
                replied.append(slug)
        return replied

    async def _require_member(self, channel_id: int, user_id: str) -> None:
        if not await self._repo.is_member(channel_id=channel_id, user_id=user_id):
            raise PermissionError("not a member of this channel")


_svc: Optional[ChatService] = None


def get_chat_service() -> ChatService:
    global _svc
    if _svc is None:
        _svc = ChatService()
    return _svc
