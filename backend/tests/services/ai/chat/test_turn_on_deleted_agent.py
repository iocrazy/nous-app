"""A turn on a session bound to a soft-deleted agent (mig 501).

Before soft delete the agent row vanished and the turn died with a 404
``agent slug not found`` — AFTER the user message had been persisted, so the
thread gained a message nobody would ever answer. Now the refusal is a typed
409 ``agent_deleted`` raised before anything is written.

Two shapes reach it:
* the slug resolves to nothing live, and the tombstone says deleted;
* the slug was reused by a NEW agent after the delete — the session is still
  bound (``agent_id``) to the deleted one, and must not silently start talking
  to a different agent that happens to share the name.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

_USER = uuid4()
_BOUND = str(uuid4())
_SESSION = "1900000000000000001"
_SLUG = "my-writer"


class _Store:
    def __init__(self) -> None:
        self.appended: List[Dict[str, Any]] = []

    async def get_session(self, *, session_id: Any) -> Dict[str, Any]:
        return {
            "id": _SESSION,
            "user_id": str(_USER),
            "agent_slug": _SLUG,
            "agent_id": _BOUND,
            "team_id": None,
        }

    async def get_messages(self, **_kw: Any) -> List[Dict[str, Any]]:
        return []

    async def append_user_message(self, **kw: Any) -> Dict[str, Any]:
        self.appended.append(kw)
        return {"id": "1", "role": "user", "content": kw.get("content")}

    async def latest_assistant_open_question(self, **_kw: Any):
        return None


def _agent(agent_id: str, *, deleted: bool) -> Dict[str, Any]:
    return {
        "id": agent_id,
        "slug": _SLUG,
        "deleted_at": "2026-09-23T00:00:00+00:00" if deleted else None,
    }


class _Repo:
    def __init__(
        self,
        *,
        live: Optional[Dict[str, Any]],
        tomb: Optional[Dict[str, Any]],
        by_id: Optional[Dict[str, Any]],
    ) -> None:
        self._live, self._tomb, self._by_id = live, tomb, by_id

    async def get_by_slug(
        self, slug: str, *, include_deleted: bool = False, **_kw: Any
    ) -> Optional[Dict[str, Any]]:
        return self._tomb if include_deleted else self._live

    async def get_by_id(self, agent_id: Any, **_kw: Any) -> Optional[Dict[str, Any]]:
        return self._by_id


async def _turn(repo: _Repo) -> tuple[HTTPException, _Store]:
    from app.services.ai.chat.ai_library_chat_service import AILibraryChatService

    store = _Store()
    with patch(
        "app.services.ai.chat.ai_library_chat_service.get_agent_repository",
        return_value=repo,
    ):
        with pytest.raises(HTTPException) as info:
            await AILibraryChatService(store=store).chat(
                _SESSION, user_id=_USER, content="Hello"
            )
    return info.value, store


@pytest.mark.asyncio
async def test_deleted_agent_gives_typed_409_and_persists_nothing() -> None:
    tomb = _agent(_BOUND, deleted=True)
    exc, store = await _turn(_Repo(live=None, tomb=tomb, by_id=tomb))
    assert exc.status_code == 409
    assert exc.detail["code"] == "agent_deleted"
    assert exc.detail["agent_slug"] == _SLUG
    assert exc.detail["message"]
    assert store.appended == [], "refused before the user message is written"


@pytest.mark.asyncio
async def test_slug_reused_by_a_new_agent_still_refuses_the_bound_session() -> None:
    newcomer = _agent(str(uuid4()), deleted=False)
    tomb = _agent(_BOUND, deleted=True)
    exc, store = await _turn(_Repo(live=newcomer, tomb=tomb, by_id=tomb))
    assert exc.status_code == 409
    assert exc.detail["code"] == "agent_deleted"
    assert store.appended == []


@pytest.mark.asyncio
async def test_truly_missing_agent_is_still_404() -> None:
    exc, store = await _turn(_Repo(live=None, tomb=None, by_id=None))
    assert exc.status_code == 404
    assert store.appended == []
