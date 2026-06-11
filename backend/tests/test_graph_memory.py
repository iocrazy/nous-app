"""Tests for the Graphiti/FalkorDB graph-memory service (Phase 4 M1)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from app.services.ai.memory.graph_memory import (
    GraphFact,
    GraphMemoryConfig,
    GraphMemoryService,
)


class FakeGraphiti:
    """Records calls; stands in for graphiti_core.Graphiti."""

    def __init__(
        self,
        *,
        search_results: list[Any] | None = None,
        raise_on: str | None = None,
    ):
        self.episodes: list[dict] = []
        self.search_calls: list[dict] = []
        self.search_results = search_results or []
        self.raise_on = raise_on

    async def add_episode(self, **kwargs):
        if self.raise_on == "add_episode":
            raise RuntimeError("boom")
        self.episodes.append(kwargs)

    async def search(self, query: str, group_ids=None, num_results=10):
        if self.raise_on == "search":
            raise RuntimeError("boom")
        self.search_calls.append(
            {"query": query, "group_ids": group_ids, "num_results": num_results}
        )
        return self.search_results


class FakeEdge:
    """Mimics graphiti's EntityEdge result shape (fact + timestamps)."""

    def __init__(self, fact: str, valid_at=None):
        self.fact = fact
        self.valid_at = valid_at


def _enabled_config() -> GraphMemoryConfig:
    return GraphMemoryConfig(
        enabled=True,
        falkordb_host="db.test",
        falkordb_port=16379,
        falkordb_database="test_memory",
    )


# ============================================================
# Config
# ============================================================


class TestConfigFromEnv:
    def test_disabled_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("FEATURE_GRAPH_MEMORY", raising=False)
        config = GraphMemoryConfig.from_env()
        assert config.enabled is False

    def test_enabled_with_host(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FEATURE_GRAPH_MEMORY", "true")
        monkeypatch.setenv("FALKORDB_HOST", "192.168.50.9")
        monkeypatch.setenv("FALKORDB_PORT", "16379")
        config = GraphMemoryConfig.from_env()
        assert config.enabled is True
        assert config.falkordb_host == "192.168.50.9"
        assert config.falkordb_port == 16379

    def test_enabled_without_host_is_inoperative(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FEATURE_GRAPH_MEMORY", "1")
        monkeypatch.delenv("FALKORDB_HOST", raising=False)
        config = GraphMemoryConfig.from_env()
        # Flag on but no host → operative() False so callers no-op.
        assert config.enabled is True
        assert config.operative() is False

    def test_bad_port_falls_back_to_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FEATURE_GRAPH_MEMORY", "true")
        monkeypatch.setenv("FALKORDB_HOST", "h")
        monkeypatch.setenv("FALKORDB_PORT", "not-a-number")
        config = GraphMemoryConfig.from_env()
        assert config.falkordb_port == 6379


# ============================================================
# add_chat_episode
# ============================================================


@pytest.mark.asyncio
async def test_disabled_service_never_touches_graphiti() -> None:
    fake = FakeGraphiti()
    service = GraphMemoryService(config=GraphMemoryConfig(enabled=False), graphiti=fake)
    ok = await service.add_chat_episode(group_id="user-1", name="turn", body="hello")
    assert ok is False
    assert fake.episodes == []
    assert await service.search("anything", group_ids=["user-1"]) == []


@pytest.mark.asyncio
async def test_add_episode_passes_through() -> None:
    fake = FakeGraphiti()
    service = GraphMemoryService(config=_enabled_config(), graphiti=fake)
    when = datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc)
    ok = await service.add_chat_episode(
        group_id="user-42",
        name="chat turn 3",
        body="user: I prefer 9:16 vertical exports",
        source_description="mediahub chat",
        reference_time=when,
    )
    assert ok is True
    assert len(fake.episodes) == 1
    episode = fake.episodes[0]
    assert episode["group_id"] == "user-42"
    assert episode["name"] == "chat turn 3"
    assert episode["episode_body"].startswith("user: I prefer")
    assert episode["reference_time"] == when
    assert episode["source_description"] == "mediahub chat"


@pytest.mark.asyncio
async def test_add_episode_failure_is_swallowed() -> None:
    fake = FakeGraphiti(raise_on="add_episode")
    service = GraphMemoryService(config=_enabled_config(), graphiti=fake)
    ok = await service.add_chat_episode(group_id="g", name="n", body="b")
    assert ok is False  # logged, never raises — memory writes must not break callers


# ============================================================
# search
# ============================================================


@pytest.mark.asyncio
async def test_search_maps_edges_to_graph_facts() -> None:
    when = datetime(2026, 6, 1, tzinfo=timezone.utc)
    fake = FakeGraphiti(
        search_results=[FakeEdge("User prefers vertical video", valid_at=when)]
    )
    service = GraphMemoryService(config=_enabled_config(), graphiti=fake)
    facts = await service.search("video preference", group_ids=["user-42"], limit=5)
    assert facts == [GraphFact(fact="User prefers vertical video", valid_at=when)]
    assert fake.search_calls[0]["group_ids"] == ["user-42"]
    assert fake.search_calls[0]["num_results"] == 5


@pytest.mark.asyncio
async def test_search_failure_returns_empty() -> None:
    fake = FakeGraphiti(raise_on="search")
    service = GraphMemoryService(config=_enabled_config(), graphiti=fake)
    assert await service.search("q", group_ids=["g"]) == []


@pytest.mark.asyncio
async def test_inoperative_config_no_ops_without_graphiti() -> None:
    # enabled flag but no host and no injected client → safe no-op,
    # never attempts a lazy connection.
    service = GraphMemoryService(
        config=GraphMemoryConfig(enabled=True, falkordb_host="")
    )
    assert await service.add_chat_episode(group_id="g", name="n", body="b") is False
    assert await service.search("q", group_ids=["g"]) == []


# ============================================================
# Live integration (env-gated; needs a reachable FalkorDB)
# ============================================================


@pytest.mark.asyncio
@pytest.mark.skipif(
    "GRAPH_MEMORY_IT" not in __import__("os").environ,
    reason="set GRAPH_MEMORY_IT=1 (+ FALKORDB_HOST) to run against a live FalkorDB",
)
async def test_live_driver_connects_and_builds_indices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import os

    # Graphiti's default LLM client requires a key at CONSTRUCTION time
    # even though build_indices never calls the LLM. A dummy key keeps
    # this a pure driver-connectivity test. (Production: a missing key
    # is caught in GraphMemoryService._client and degrades to no-op.)
    monkeypatch.setenv("OPENAI_API_KEY", os.environ.get("OPENAI_API_KEY", "it-dummy"))

    from graphiti_core import Graphiti
    from graphiti_core.driver.falkordb_driver import FalkorDriver

    driver = FalkorDriver(
        host=os.environ["FALKORDB_HOST"],
        port=int(os.environ.get("FALKORDB_PORT", "6379")),
        database="it_smoke",
    )
    client = Graphiti(graph_driver=driver)
    # No LLM involved — pure driver/index connectivity.
    await client.build_indices_and_constraints()
    await client.close()
