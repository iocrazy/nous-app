"""Phase 2a Task 4: ``answer_to`` rides the existing chat endpoints (no new
endpoint) — both the buffered and the SSE variant hand it to the service."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest

from app.schemas.ai_library_chat import ChatRequest

pytestmark = pytest.mark.unit

_R = "app.api.ai_library_router"
AUTH = SimpleNamespace(user_id="11111111-1111-1111-1111-111111111111")


def test_chat_request_accepts_answer_to():
    assert ChatRequest(content="Twist", answer_to="q:1:2").answer_to == "q:1:2"
    assert ChatRequest(content="x").answer_to is None


async def test_buffered_endpoint_threads_answer_to_into_the_service():
    import importlib

    r = importlib.import_module(_R)
    with patch(f"{_R}.AILibraryChatService") as svc_cls:
        svc_cls.return_value.chat = AsyncMock(
            return_value={
                "assistant_message": {"id": 1, "content": "ok"},
                "usage": {},
                "run_id": None,
            }
        )
        await r.send_chat_message(
            "55", ChatRequest(content="Twist", answer_to="q:1:2"), AUTH
        )
    assert svc_cls.return_value.chat.await_args.kwargs["answer_to"] == "q:1:2"


async def test_chat_stream_threads_answer_to_into_chat():
    from app.services.ai.chat.ai_library_chat_service import AILibraryChatService

    svc = AILibraryChatService.__new__(AILibraryChatService)
    svc.chat = AsyncMock(return_value={"assistant_message": {"id": 1, "content": "ok"}})
    events = [
        e
        async for e in svc.chat_stream(
            "55", user_id=UUID(AUTH.user_id), content="Twist", answer_to="q:1:2"
        )
    ]
    assert events[-1]["type"] == "done"
    assert svc.chat.await_args.kwargs["answer_to"] == "q:1:2"
