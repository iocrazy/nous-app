"""Unified Conversation orchestration and in-app authorization."""

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
from app.services.chat.conversation_memory_service import maybe_compact
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
        info = await self._repo.conversation_scope_and_type(
            conversation_id=conversation_id
        )
        # A 1:1 AI thread must stay exactly one human + one agent: /dream's
        # per-pair consolidation joins conversation_members, so a second
        # user row fans out its message counts (P3 Task-2 review ledger).
        if info is not None and info.get("type") == "direct_agent":
            raise PermissionError("cannot add members to a direct agent conversation")
        scope_id = info.get("scope_id") if info is not None else None
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
        generated_media_id: Optional[int] = None
        if type == "image" and body.get("generated_media_id"):
            try:
                generated_media_id = int(body["generated_media_id"])
            except (ValueError, TypeError) as exc:
                raise ValueError(
                    f"invalid generated_media_id: {body['generated_media_id']!r}"
                ) from exc
        msg = await self._repo.send_message(
            conversation_id=conversation_id,
            sender_id=user_id,
            sender_type="user",
            type=type,
            body=body,
            parent_id=parent_id,
        )
        if generated_media_id is not None:
            try:
                await self._repo.add_attachments(
                    message_id=int(msg["id"]),
                    generated_media_ids=[generated_media_id],
                )
            except Exception as exc:
                logger.warning(
                    f"[post_message] add_attachments failed: "
                    f"conversation={conversation_id} error={exc!r}"
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
            for_user_id=user_id,
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

    # ── Group management (roles: owner / admin / member) ─────────────────────
    #
    # Permission matrix (Feishu-style, user-approved 2026-07-04):
    #   invite / add agent          any member
    #   remove regular member/agent admin, owner
    #   remove admin                owner only
    #   grant / revoke admin        owner only
    #   transfer ownership          owner only (old owner → member)
    #   edit name / visibility      admin, owner
    #   leave                       any member except owner (must transfer)
    #   dissolve (archive)          owner only

    _GROUP_TYPES = frozenset({"group", "public"})

    async def _require_group(self, conversation_id: int) -> dict[str, Any]:
        info = await self._repo.conversation_scope_and_type(
            conversation_id=conversation_id
        )
        if info is None:
            raise ValueError(f"conversation {conversation_id} not found")
        if info.get("type") not in self._GROUP_TYPES:
            raise PermissionError("not a group conversation")
        return info

    async def _require_role(
        self, conversation_id: int, user_id: str, allowed: frozenset[str]
    ) -> str:
        role = await self._repo.get_member_role(
            conversation_id=conversation_id, user_id=user_id
        )
        if role is None:
            raise PermissionError("not a member of this conversation")
        if role not in allowed:
            raise PermissionError("insufficient role for this action")
        return role

    async def list_members(
        self, *, conversation_id: int, user_id: str
    ) -> list[dict[str, Any]]:
        await self._require_member(conversation_id, user_id)
        return await self._repo.list_members(conversation_id=conversation_id)

    async def remove_member(
        self, *, conversation_id: int, user_id: str, target_user_id: str
    ) -> dict[str, Any]:
        """Remove a member (admin/owner per matrix) or leave (self-target)."""
        await self._require_group(conversation_id)
        actor_role = await self._repo.get_member_role(
            conversation_id=conversation_id, user_id=user_id
        )
        if actor_role is None:
            raise PermissionError("not a member of this conversation")
        if target_user_id == user_id:
            # Leaving. The owner would strand the group — transfer first.
            if actor_role == "owner":
                raise PermissionError("owner must transfer ownership before leaving")
        else:
            target_role = await self._repo.get_member_role(
                conversation_id=conversation_id, user_id=target_user_id
            )
            if target_role is None:
                raise ValueError("target user is not a member of this conversation")
            if actor_role == "admin":
                if target_role != "member":
                    raise PermissionError("admins can only remove regular members")
            elif actor_role != "owner":
                raise PermissionError("insufficient role to remove members")
        removed = await self._repo.remove_user_member(
            conversation_id=conversation_id, user_id=target_user_id
        )
        return {"removed": removed}

    async def set_member_role(
        self, *, conversation_id: int, user_id: str, target_user_id: str, role: str
    ) -> dict[str, Any]:
        await self._require_group(conversation_id)
        await self._require_role(conversation_id, user_id, frozenset({"owner"}))
        if target_user_id == user_id:
            raise ValueError("cannot change your own role; use transfer-owner")
        target_role = await self._repo.get_member_role(
            conversation_id=conversation_id, user_id=target_user_id
        )
        if target_role is None:
            raise ValueError("target user is not a member of this conversation")
        updated = await self._repo.set_member_role(
            conversation_id=conversation_id, user_id=target_user_id, role=role
        )
        return {"updated": updated, "role": role}

    async def transfer_ownership(
        self, *, conversation_id: int, user_id: str, to_user_id: str
    ) -> dict[str, Any]:
        await self._require_group(conversation_id)
        await self._require_role(conversation_id, user_id, frozenset({"owner"}))
        if to_user_id == user_id:
            raise ValueError("already the owner")
        await self._repo.transfer_owner(
            conversation_id=conversation_id,
            from_user_id=user_id,
            to_user_id=to_user_id,
        )
        return {"transferred": True}

    async def remove_agent(
        self, *, conversation_id: int, user_id: str, agent_id: str
    ) -> dict[str, Any]:
        await self._require_group(conversation_id)
        await self._require_role(conversation_id, user_id, frozenset({"owner", "admin"}))
        removed = await self._repo.remove_agent_member(
            conversation_id=conversation_id, agent_id=agent_id
        )
        return {"removed": removed}

    async def update_conversation(
        self,
        *,
        conversation_id: int,
        user_id: str,
        name: Optional[str] = None,
        type: Optional[str] = None,
    ) -> dict[str, Any]:
        await self._require_group(conversation_id)
        await self._require_role(conversation_id, user_id, frozenset({"owner", "admin"}))
        if name is not None:
            name = name.strip()
            if not name:
                raise ValueError("group name cannot be empty")
        if type is not None and type not in self._GROUP_TYPES:
            raise ValueError("visibility must be 'group' or 'public'")
        row = await self._repo.update_conversation(
            conversation_id=conversation_id, name=name, type=type
        )
        if row is None:
            raise ValueError(f"conversation {conversation_id} not found")
        return row

    async def dissolve_conversation(
        self, *, conversation_id: int, user_id: str
    ) -> dict[str, Any]:
        await self._require_group(conversation_id)
        await self._require_role(conversation_id, user_id, frozenset({"owner"}))
        archived = await self._repo.archive_conversation(
            conversation_id=conversation_id
        )
        return {"archived": archived}

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

        async def _summon_one(slug: str) -> Optional[str]:
            agent = slug_to_agent[slug]
            async with sem:
                try:
                    reply = await run_conversation_agent_turn(
                        agent_slug=slug,
                        summoner_user_id=summoner_user_id,
                        conversation=conversation,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.error(
                        f"[dispatch_summons] agent_turn_failed: "
                        f"conversation={conversation_id} agent={slug} error={exc!r}"
                    )
                    return None
            if not reply:
                return None
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
                return slug
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    f"[dispatch_summons] reply_write_failed: "
                    f"agent={slug} error={exc!r}"
                )
                return None

        results = await asyncio.gather(*(_summon_one(s) for s in mentioned))
        replied = [s for s in results if s]

        # Phase 1.5 — rolling summary upkeep. Never raises; flag-gated inside.
        if replied:
            try:
                await maybe_compact(conversation=conversation)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"[dispatch_summons] compact_failed: {exc!r}")

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
