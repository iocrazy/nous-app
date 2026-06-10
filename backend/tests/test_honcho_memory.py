"""Tests for the Honcho user-model client + workflow dual-write (Phase 4 M5)."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.services.ai.memory.honcho_memory import (
    HonchoMemoryConfig,
    HonchoMemoryService,
)
from app.workflows.write_memory import _write_honcho_turn


def _service(
    handler, *, enabled: bool = True, workspace: str = "mediahub"
) -> HonchoMemoryService:
    config = HonchoMemoryConfig(
        enabled=enabled, base_url="http://honcho.test", workspace_id=workspace
    )
    client = httpx.AsyncClient(
        base_url="http://honcho.test", transport=httpx.MockTransport(handler)
    )
    return HonchoMemoryService(config=config, client=client)


class Recorder:
    """MockTransport handler that records requests and returns 200."""

    def __init__(self, *, fail_path: str | None = None, status: int = 200):
        self.requests: list[tuple[str, Any]] = []
        self.fail_path = fail_path
        self.status = status

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        self.requests.append((request.url.path, body))
        if self.fail_path and request.url.path.endswith(self.fail_path):
            return httpx.Response(500, text="boom")
        return httpx.Response(self.status, json={"ok": True})


# ============================================================
# Config
# ============================================================


class TestConfig:
    def test_disabled_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("FEATURE_HONCHO_MEMORY", raising=False)
        assert HonchoMemoryConfig.from_env().enabled is False

    def test_enabled_needs_base_url_to_be_operative(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FEATURE_HONCHO_MEMORY", "true")
        monkeypatch.delenv("HONCHO_BASE_URL", raising=False)
        config = HonchoMemoryConfig.from_env()
        assert config.enabled is True
        assert config.operative() is False

    def test_base_url_trailing_slash_stripped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FEATURE_HONCHO_MEMORY", "1")
        monkeypatch.setenv("HONCHO_BASE_URL", "http://h:18000/")
        assert HonchoMemoryConfig.from_env().base_url == "http://h:18000"


# ============================================================
# add_chat_turn
# ============================================================


@pytest.mark.asyncio
async def test_add_chat_turn_creates_namespace_then_posts_messages() -> None:
    recorder = Recorder()
    service = _service(recorder)
    ok = await service.add_chat_turn(
        user_id="42",
        agent_id="7",
        session_id="999",
        user_message="I make cooking videos",
        assistant_message="Noted — fast-paced cooking content.",
    )
    assert ok is True
    paths = [p for p, _ in recorder.requests]
    assert paths == [
        "/v3/workspaces",
        "/v3/workspaces/mediahub/peers",
        "/v3/workspaces/mediahub/peers",
        "/v3/workspaces/mediahub/sessions",
        "/v3/workspaces/mediahub/sessions/session-999/messages",
    ]
    # Peer ids follow the user-{id} / agent-{id} mapping.
    peer_bodies = [b for p, b in recorder.requests if p.endswith("/peers")]
    assert {b["id"] for b in peer_bodies} == {"user-42", "agent-7"}
    final = recorder.requests[-1][1]
    assert final["messages"][0] == {
        "peer_id": "user-42",
        "content": "I make cooking videos",
    }
    assert final["messages"][1]["peer_id"] == "agent-7"


@pytest.mark.asyncio
async def test_second_turn_skips_namespace_round_trips() -> None:
    recorder = Recorder()
    service = _service(recorder)
    for _ in range(2):
        await service.add_chat_turn(
            user_id="42",
            agent_id="7",
            session_id="999",
            user_message="hi",
            assistant_message="hello",
        )
    message_posts = [p for p, _ in recorder.requests if p.endswith("/messages")]
    assert len(message_posts) == 2
    # 5 calls first turn + 1 (messages only) second turn.
    assert len(recorder.requests) == 6


@pytest.mark.asyncio
async def test_disabled_service_makes_no_calls() -> None:
    recorder = Recorder()
    service = _service(recorder, enabled=False)
    ok = await service.add_chat_turn(
        user_id="1",
        agent_id="2",
        session_id="3",
        user_message="x",
        assistant_message="y",
    )
    assert ok is False
    assert recorder.requests == []


@pytest.mark.asyncio
async def test_empty_turn_skips_write() -> None:
    recorder = Recorder()
    service = _service(recorder)
    ok = await service.add_chat_turn(
        user_id="1",
        agent_id="2",
        session_id="3",
        user_message="  ",
        assistant_message="",
    )
    assert ok is False
    assert recorder.requests == []


@pytest.mark.asyncio
async def test_server_error_is_swallowed() -> None:
    recorder = Recorder(fail_path="/messages")
    service = _service(recorder)
    ok = await service.add_chat_turn(
        user_id="1",
        agent_id="2",
        session_id="3",
        user_message="x",
        assistant_message="y",
    )
    assert ok is False  # logged, never raises


# ============================================================
# get_user_context (dialectic)
# ============================================================


@pytest.mark.asyncio
async def test_dialectic_returns_content() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v3/workspaces/mediahub/peers/user-42/chat"
        return httpx.Response(200, json={"content": "Prefers fast cuts."})

    service = _service(handler)
    answer = await service.get_user_context(user_id="42", query="editing style?")
    assert answer == "Prefers fast cuts."


@pytest.mark.asyncio
async def test_dialectic_failure_returns_none() -> None:
    service = _service(Recorder(status=500))
    assert await service.get_user_context(user_id="42", query="anything") is None


# ============================================================
# Workflow dual-write helper
# ============================================================


class RecordingService(HonchoMemoryService):
    def __init__(self, *, enabled: bool = True):
        super().__init__(
            config=HonchoMemoryConfig(enabled=enabled, base_url="http://h")
        )
        self.turns: list[dict] = []

    async def add_chat_turn(self, **kwargs) -> bool:  # type: ignore[override]
        self.turns.append(kwargs)
        return True


@pytest.mark.asyncio
async def test_workflow_helper_posts_latest_pair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc = RecordingService()
    monkeypatch.setattr(
        "app.services.ai.memory.honcho_memory.get_honcho_memory_service",
        lambda: svc,
    )
    ok = await _write_honcho_turn(
        user_id="42",
        agent_id="7",
        session_id="999",
        user_msgs=["older", "newest question"],
        asst_msgs=["older answer", "newest answer"],
    )
    assert ok is True
    assert svc.turns == [
        {
            "user_id": "42",
            "agent_id": "7",
            "session_id": "999",
            "user_message": "newest question",
            "assistant_message": "newest answer",
        }
    ]


@pytest.mark.asyncio
async def test_workflow_helper_disabled_flag_skips(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc = RecordingService(enabled=False)
    monkeypatch.setattr(
        "app.services.ai.memory.honcho_memory.get_honcho_memory_service",
        lambda: svc,
    )
    ok = await _write_honcho_turn(
        user_id="42",
        agent_id="7",
        session_id="999",
        user_msgs=["hi"],
        asst_msgs=["yo"],
    )
    assert ok is False
    assert svc.turns == []


# ============================================================
# Live integration (env-gated; needs a reachable Honcho)
# ============================================================


@pytest.mark.asyncio
@pytest.mark.skipif(
    "HONCHO_MEMORY_IT" not in __import__("os").environ,
    reason="set HONCHO_MEMORY_IT=1 (+ HONCHO_BASE_URL) to run against a live Honcho",
)
async def test_live_add_chat_turn() -> None:
    import os

    service = HonchoMemoryService(
        config=HonchoMemoryConfig(
            enabled=True,
            base_url=os.environ["HONCHO_BASE_URL"].rstrip("/"),
            workspace_id="mediahub-it",
        )
    )
    ok = await service.add_chat_turn(
        user_id="it-user",
        agent_id="it-agent",
        session_id="it-session",
        user_message="Integration smoke: I like minimal thumbnails.",
        assistant_message="Acknowledged: minimal thumbnail preference.",
    )
    assert ok is True
