"""Tests for the Graphiti dual-write in write_memory (Phase 4 M2)."""

from __future__ import annotations

import pytest

from app.services.ai.memory.graph_memory import GraphMemoryConfig, GraphMemoryService
from app.workflows.write_memory import (
    _build_turn_episode,
    _write_graph_episode,
)


class RecordingService(GraphMemoryService):
    """GraphMemoryService with a recording add_chat_episode."""

    def __init__(self, *, enabled: bool = True, succeed: bool = True):
        super().__init__(config=GraphMemoryConfig(enabled=enabled, falkordb_host="h"))
        self.calls: list[dict] = []
        self._succeed = succeed

    async def add_chat_episode(self, **kwargs) -> bool:  # type: ignore[override]
        self.calls.append(kwargs)
        return self._succeed


@pytest.fixture
def service(monkeypatch: pytest.MonkeyPatch) -> RecordingService:
    svc = RecordingService()
    monkeypatch.setattr(
        "app.services.ai.memory.graph_memory.get_graph_memory_service",
        lambda: svc,
    )
    return svc


# ============================================================
# _build_turn_episode
# ============================================================


class TestBuildTurnEpisode:
    def test_renders_only_the_latest_user_message(self) -> None:
        # Latest user message only — assistant reply is excluded (it would
        # pollute the graph with "the assistant said X" facts), and older
        # turns are dropped (the per-turn episode carries just the new turn).
        body = _build_turn_episode(
            ["older question", "what export size?"],
            ["older answer", "use 9:16 vertical"],
        )
        assert body == "user: what export size?"
        assert "assistant" not in body
        assert "older" not in body

    def test_excludes_assistant_text(self) -> None:
        # assistant reply never reaches the episode
        assert _build_turn_episode(["hi"], ["hello there"]) == "user: hi"

    def test_handles_one_sided_turns(self) -> None:
        assert _build_turn_episode(["hi"], []) == "user: hi"
        # assistant-only turn → nothing to ingest (no user fact)
        assert _build_turn_episode([], ["hello"]) == ""
        assert _build_turn_episode([], []) == ""
        # blank user message → empty
        assert _build_turn_episode(["   "], ["reply"]) == ""

    def test_clips_to_max_chars(self) -> None:
        body = _build_turn_episode(["x" * 10000], [], max_chars=100)
        assert len(body) == 100


# ============================================================
# _write_graph_episode
# ============================================================


@pytest.mark.asyncio
async def test_writes_episode_with_user_group(service: RecordingService) -> None:
    ok = await _write_graph_episode(
        user_id="42",
        session_id="777",
        run_id="888",
        iteration=3,
        user_msgs=["what export size?"],
        asst_msgs=["use 9:16 vertical"],
    )
    assert ok is True
    assert len(service.calls) == 1
    call = service.calls[0]
    assert call["group_id"] == "user-42"
    assert call["name"] == "chat-777-888"
    # user message is ingested; the assistant reply is excluded (noise)
    assert "what export size?" in call["body"]
    assert "9:16 vertical" not in call["body"]


@pytest.mark.asyncio
async def test_episode_name_falls_back_to_iteration(
    service: RecordingService,
) -> None:
    await _write_graph_episode(
        user_id="42",
        session_id="777",
        run_id=None,
        iteration=5,
        user_msgs=["hi"],
        asst_msgs=[],
    )
    assert service.calls[0]["name"] == "chat-777-5"


@pytest.mark.asyncio
async def test_disabled_flag_skips_entirely(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc = RecordingService(enabled=False)
    monkeypatch.setattr(
        "app.services.ai.memory.graph_memory.get_graph_memory_service",
        lambda: svc,
    )
    ok = await _write_graph_episode(
        user_id="42",
        session_id="777",
        run_id="888",
        iteration=0,
        user_msgs=["hi"],
        asst_msgs=["yo"],
    )
    assert ok is False
    assert svc.calls == []


@pytest.mark.asyncio
async def test_empty_turn_skips_write(service: RecordingService) -> None:
    ok = await _write_graph_episode(
        user_id="42",
        session_id="777",
        run_id="888",
        iteration=0,
        user_msgs=[],
        asst_msgs=[],
    )
    assert ok is False
    assert service.calls == []
