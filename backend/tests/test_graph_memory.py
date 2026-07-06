"""Tests for the Graphiti/FalkorDB graph-memory service (Phase 4 M1)."""

from __future__ import annotations

import asyncio
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
async def test_search_targets_each_group_graph(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """graphiti's FalkorDB driver stores each group_id in its own graph
    (database). client.search only queries the driver's current graph, so the
    real (non-injected) path must build a client per group graph and merge —
    regression: recall always hit the empty default graph and returned nothing.
    """
    built: list[str] = []

    class PerGraphClient:
        def __init__(self, db: str) -> None:
            self.db = db

        async def search(self, query, group_ids=None, num_results=10):
            return [FakeEdge(f"fact::{self.db}")]

    service = GraphMemoryService(config=_enabled_config())  # not injected
    service._config_loaded = True  # skip the DB settings load

    def fake_build(*, database: str):
        built.append(database)
        return PerGraphClient(database)

    monkeypatch.setattr(service, "_build_client", fake_build)
    facts = await service.search("q", group_ids=["user-1", "project-2"], limit=10)
    # one client built per group graph, in order
    assert built == ["user-1", "project-2"]
    # facts merged across both group graphs
    assert {f.fact for f in facts} == {"fact::user-1", "fact::project-2"}


@pytest.mark.asyncio
async def test_search_dedups_and_caps_to_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Merged cross-graph results are deduped by fact text and capped to limit."""

    class DupClient:
        async def search(self, query, group_ids=None, num_results=10):
            return [FakeEdge("same"), FakeEdge("a"), FakeEdge("b"), FakeEdge("same")]

    service = GraphMemoryService(config=_enabled_config())
    service._config_loaded = True
    monkeypatch.setattr(service, "_build_client", lambda *, database: DupClient())
    facts = await service.search("q", group_ids=["user-1"], limit=2)
    assert [f.fact for f in facts] == ["same", "a"]


@pytest.mark.asyncio
async def test_is_enabled_loads_db_config_over_env_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """is_enabled() must consult the admin-panel (system_settings) config, not
    the cheap env default. A service whose env default is disabled but whose
    DB config is enabled must report True — the bug was that callers read
    .config.enabled directly and never triggered the DB load."""
    service = GraphMemoryService(config=GraphMemoryConfig(enabled=False))

    async def fake_from_settings(**_kw):
        return _enabled_config()  # admin panel turned graph memory ON in the DB

    monkeypatch.setattr(GraphMemoryConfig, "from_settings", fake_from_settings)
    assert service.config.enabled is False  # before: cheap env default
    assert await service.is_enabled() is True  # after: DB-sourced
    assert service.config.enabled is True  # config swapped in place, once


@pytest.mark.asyncio
async def test_is_enabled_stays_false_when_db_also_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = GraphMemoryService(config=GraphMemoryConfig(enabled=False))

    async def fake_from_settings(**_kw):
        return GraphMemoryConfig(enabled=False)

    monkeypatch.setattr(GraphMemoryConfig, "from_settings", fake_from_settings)
    assert await service.is_enabled() is False


@pytest.mark.asyncio
async def test_search_times_out_and_degrades_to_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A FalkorDB that accepts the connection but never responds must not stall
    the chat turn: search() wraps the call in a timeout and degrades to []."""
    import app.services.ai.memory.graph_memory as gm

    class HangingGraphiti:
        async def search(self, query, group_ids=None, num_results=10):
            await asyncio.sleep(60)  # never returns within the timeout

    monkeypatch.setattr(gm, "GRAPH_SEARCH_TIMEOUT_S", 0.05)
    service = GraphMemoryService(config=_enabled_config(), graphiti=HangingGraphiti())
    facts = await service.search("q", group_ids=["user-1"], limit=5)
    assert facts == []  # timed out, logged, degraded — never raised


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


# ============================================================
# _build_driver — loop-freeze防御 (2026-07-06 P0 follow-up)
# ============================================================


def test_build_driver_runs_off_loop_with_socket_timeouts(monkeypatch):
    """Pin the two loop-freeze defenses (2026-07-06 P0):

    1. The injected FalkorDB client carries socket timeouts — graphiti's
       default is a sync redis.Redis with NO timeout, so a hung server
       blocks its calling thread forever.
    2. Construction happens on a throwaway thread, NOT the caller's
       thread — on a thread there is no running event loop, so
       FalkorDriver.__init__'s ``loop.create_task(build_indices...)``
       branch (which schedules sync-redis work onto the loop) falls
       through to its RuntimeError/pass path.
    """
    import threading as _threading

    captured: dict = {}

    class _FakeFalkorDB:
        def __init__(self, **kwargs):
            captured["falkor_kwargs"] = kwargs

    class _FakeDriver:
        def __init__(self, *, falkor_db, database):
            captured["falkor_db"] = falkor_db
            captured["database"] = database
            captured["thread"] = _threading.current_thread()

    import falkordb as falkor_mod
    import graphiti_core.driver.falkordb_driver as fd_mod

    monkeypatch.setattr(fd_mod, "FalkorDriver", _FakeDriver)
    monkeypatch.setattr(falkor_mod, "FalkorDB", _FakeFalkorDB)

    service = GraphMemoryService(config=_enabled_config())
    driver = service._build_driver(database="test_memory")

    assert isinstance(driver, _FakeDriver)
    assert captured["database"] == "test_memory"
    kw = captured["falkor_kwargs"]
    assert kw["host"] == "db.test"
    assert kw["port"] == 16379
    assert kw["socket_connect_timeout"] == service._FALKOR_CONNECT_TIMEOUT_S
    assert kw["socket_timeout"] == service._FALKOR_SOCKET_TIMEOUT_S
    # Built on the throwaway thread, not the caller's.
    assert captured["thread"] is not _threading.main_thread()
    assert captured["thread"].name == "falkor-driver-build"


def test_build_driver_hung_construction_times_out(monkeypatch):
    """A construction that hangs (server half-up) must return None within
    the build cap instead of hanging the caller."""
    import time as _time

    class _HangingFalkorDB:
        def __init__(self, **kwargs):
            _time.sleep(60)

    import falkordb as falkor_mod

    monkeypatch.setattr(falkor_mod, "FalkorDB", _HangingFalkorDB)

    service = GraphMemoryService(config=_enabled_config())
    monkeypatch.setattr(service, "_FALKOR_BUILD_TIMEOUT_S", 0.5, raising=False)
    t0 = _time.monotonic()
    assert service._build_driver(database="test_memory") is None
    assert _time.monotonic() - t0 < 5.0
