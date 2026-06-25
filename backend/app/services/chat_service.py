"""Team Chat orchestration + in-app authorization (PHASE-1)."""

from __future__ import annotations

from typing import Any, Optional

from app.repositories.chat_repository import ChatRepository, get_chat_repository


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

    async def _require_member(self, channel_id: int, user_id: str) -> None:
        if not await self._repo.is_member(channel_id=channel_id, user_id=user_id):
            raise PermissionError("not a member of this channel")


_svc: Optional[ChatService] = None


def get_chat_service() -> ChatService:
    global _svc
    if _svc is None:
        _svc = ChatService()
    return _svc
