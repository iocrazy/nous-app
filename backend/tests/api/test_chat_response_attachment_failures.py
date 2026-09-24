"""The buffered chat route must deliver ``attachment_failures`` over HTTP.

The route has always put the key in its return dict, but ``ChatResponse`` did
not declare it, so ``response_model`` filtered it out before it reached the
wire. Calling the handler function directly (as ``test_chat_answer_to`` does)
cannot see that: the filtering happens in FastAPI's serialization, so this
test goes through a real ``TestClient`` request.

The service return value below is the real shape built by
``AILibraryChatService.chat``: one ``{index, kind, reason}`` entry per failed
attachment, sorted by ``index``, with reason codes the service really emits.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.ai_library_router import router
from app.core.cache import module_gate_cache
from app.core.deps import get_auth

pytestmark = pytest.mark.unit

_R = "app.api.ai_library_router"
_USER_ID = "11111111-1111-1111-1111-111111111111"

_FAILURES = [
    {"index": 0, "kind": "asset_ref", "reason": "asset_no_primary_image"},
    {"index": 1, "kind": "image", "reason": "model_no_vision"},
    {"index": 5, "kind": "asset_ref", "reason": "attachment_limit_exceeded"},
]


def _service_result(failures: list[dict]) -> dict:
    return {
        "assistant_message": {
            "id": 7318251094211584001,  # Snowflake BIGINT, as the store returns it
            "session_id": 7318251094211584000,
            "role": "assistant",
            "content": "I could only read part of what you attached.",
        },
        "usage": {"prompt_tokens": 12, "completion_tokens": 9},
        "run_id": "7318251094211584002",
        "tool_calls": [],
        "attachment_failures": failures,
    }


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    async def _grant():
        return SimpleNamespace(user_id=_USER_ID, email="user@example.com")

    app.dependency_overrides[get_auth] = _grant
    return TestClient(app)


def _post_chat(client: TestClient, failures: list[dict]):
    with (
        patch.object(
            module_gate_cache,
            "get_or_load",
            AsyncMock(return_value=SimpleNamespace(enabled=True)),
        ),
        patch(f"{_R}.AILibraryChatService") as svc_cls,
    ):
        svc_cls.return_value.chat = AsyncMock(return_value=_service_result(failures))
        return client.post(
            "/api/v1/ai-library/sessions/55/chat",
            json={"content": "Describe these"},
        )


def test_buffered_chat_response_carries_attachment_failures(client):
    resp = _post_chat(client, _FAILURES)

    assert resp.status_code == 200, resp.text
    assert resp.json()["attachment_failures"] == _FAILURES


def test_buffered_chat_response_has_empty_failures_when_all_delivered(client):
    resp = _post_chat(client, [])

    assert resp.status_code == 200, resp.text
    assert resp.json()["attachment_failures"] == []
