"""Tests for the Graphiti dual-write in write_memory (Phase 4 M2)."""

from __future__ import annotations

import pytest

from app.services.ai.memory.graph_memory import GraphMemoryConfig, GraphMemoryService
from app.workflows.write_memory import _build_turn_episode, _write_graph_episode


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
    def test_renders_only_the_latest_pair(self) -> None:
        body = _build_turn_episode(
            ["older question", "what export size?"],
            ["older answer", "use 9:16 vertical"],
        )
        assert body == "user: what export size?\nassistant: use 9:16 vertical"
        assert "older" not in body

    def test_handles_one_sided_turns(self) -> None:
        assert _build_turn_episode(["hi"], []) == "user: hi"
        assert _build_turn_episode([], ["hello"]) == "assistant: hello"
        assert _build_turn_episode([], []) == ""

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
    assert "9:16 vertical" in call["body"]


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


# ============================================================
# _build_memory_writer — contradiction wiring (regression)
# ============================================================


def test_build_memory_writer_wires_contradiction_classifier() -> None:
    """The production writer MUST carry a contradiction_classifier; without it
    the supersede post-pass is dead and conflicting memories ('prefers Vue' →
    later 'now uses React') accumulate and are recalled together forever."""
    from app.workflows.write_memory import _build_memory_writer

    async def _llm(prompt: str) -> str:
        return "REPLACES"

    writer = _build_memory_writer(_llm)
    assert writer.contradiction_classifier is _llm
