"""Tests for the storyboard character → Graphiti dual-write (Phase 4 M7)."""

from __future__ import annotations

import pytest

from app.services.ai.memory.graph_memory import GraphMemoryConfig, GraphMemoryService
from app.services.storyboard.storyboard_service import _dispatch_character_episode
from app.workflows.write_character_memory import (
    _render_character_body,
    _resolve_canonical_project,
    _write_character_episode,
)


class RecordingService(GraphMemoryService):
    """GraphMemoryService with a recording add_chat_episode."""

    def __init__(self, *, enabled: bool = True, succeed: bool = True):
        super().__init__(config=GraphMemoryConfig(enabled=enabled, falkordb_host="h"))
        # Treat the injected config as already DB-resolved so the real
        # is_enabled()/_ensure_config gate honours it without a settings read.
        self._config_loaded = True
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
# _render_character_body
# ============================================================


class TestRenderCharacterBody:
    def test_name_only(self) -> None:
        assert _render_character_body("Ava", None, None) == (
            "Storyboard character: Ava."
        )

    def test_includes_description_and_traits(self) -> None:
        body = _render_character_body(
            "Ava",
            "A determined chef.",
            {"hair": "short black", "clothing": "white apron"},
        )
        assert body == (
            "Storyboard character: Ava. A determined chef. "
            "Visual traits — hair: short black, clothing: white apron."
        )

    def test_falsy_trait_values_are_dropped(self) -> None:
        body = _render_character_body("Ava", "", {"hair": "", "age": None})
        assert body == "Storyboard character: Ava."

    def test_clips_to_max_chars(self) -> None:
        body = _render_character_body("Ava", "x" * 10000, None)
        assert len(body) == 4000


# ============================================================
# _write_character_episode
# ============================================================


@pytest.mark.asyncio
async def test_writes_episode_under_project_group(service: RecordingService) -> None:
    ok = await _write_character_episode(
        project_id="55",
        character_id="9001",
        name="Ava",
        description="A determined chef.",
        visual_traits={"hair": "short black"},
    )
    assert ok is True
    assert len(service.calls) == 1
    call = service.calls[0]
    assert call["group_id"] == "project-55"
    assert call["name"] == "character-9001"
    assert "determined chef" in call["body"]
    assert call["source_description"] == "storyboard character"


@pytest.mark.asyncio
async def test_disabled_flag_skips_entirely(monkeypatch: pytest.MonkeyPatch) -> None:
    svc = RecordingService(enabled=False)
    monkeypatch.setattr(
        "app.services.ai.memory.graph_memory.get_graph_memory_service",
        lambda: svc,
    )
    ok = await _write_character_episode(
        project_id="55",
        character_id="9001",
        name="Ava",
        description=None,
        visual_traits=None,
    )
    assert ok is False
    assert svc.calls == []


@pytest.mark.asyncio
async def test_blank_name_skips_write(service: RecordingService) -> None:
    ok = await _write_character_episode(
        project_id="55",
        character_id="9001",
        name="   ",
        description="ignored",
        visual_traits=None,
    )
    assert ok is False
    assert service.calls == []


# ============================================================
# _resolve_canonical_project (storyboard_projects.project_id link)
# ============================================================


def _patch_fetch_one(monkeypatch: pytest.MonkeyPatch, result) -> list[dict]:
    calls: list[dict] = []

    async def fake_fetch_one(sql: str, params=None):
        calls.append({"sql": sql, "params": params})
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr("app.db.engine.fetch_one", fake_fetch_one)
    return calls


@pytest.mark.asyncio
async def test_resolve_returns_linked_project(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _patch_fetch_one(monkeypatch, {"project_id": 777})
    assert await _resolve_canonical_project("55") == "777"
    # BIGINT column — the bind param must be an int, not a str.
    assert calls[0]["params"] == {"sid": 55}


@pytest.mark.asyncio
async def test_resolve_unlinked_or_missing_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_fetch_one(monkeypatch, {"project_id": None})
    assert await _resolve_canonical_project("55") is None
    _patch_fetch_one(monkeypatch, None)
    assert await _resolve_canonical_project("55") is None


@pytest.mark.asyncio
async def test_resolve_non_numeric_id_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _patch_fetch_one(monkeypatch, {"project_id": 777})
    assert await _resolve_canonical_project("") is None
    assert await _resolve_canonical_project("not-a-number") is None
    assert calls == []


@pytest.mark.asyncio
async def test_resolve_swallows_db_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_fetch_one(monkeypatch, RuntimeError("db down"))
    assert await _resolve_canonical_project("55") is None


# ============================================================
# _dispatch_character_episode (service-side gating)
# ============================================================


@pytest.fixture
def dispatched(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    calls: list[dict] = []

    async def fake_start(queue: str, **kwargs):
        calls.append({"queue": queue, **kwargs})
        return "wf-id"

    monkeypatch.setattr(
        "app.services.infra.dbos_orchestrator.start_workflow_routed",
        fake_start,
    )
    return calls


@pytest.mark.asyncio
async def test_dispatch_enqueues_routed_workflow(
    service: RecordingService, dispatched: list[dict]
) -> None:
    await _dispatch_character_episode(
        {
            "id": 9001,
            "project_id": 55,
            "name": "Ava",
            "description": "A determined chef.",
            "visual_traits": {"hair": "short black"},
        }
    )
    assert len(dispatched) == 1
    call = dispatched[0]
    assert call["queue"] == "memory_tasks"
    kwargs = call["dbos_workflow_kwargs"]
    assert kwargs["storyboard_project_id"] == "55"
    assert kwargs["character_id"] == "9001"
    assert kwargs["name"] == "Ava"
    assert "short black" in kwargs["visual_traits_json"]


@pytest.mark.asyncio
async def test_dispatch_skips_when_flag_off(
    monkeypatch: pytest.MonkeyPatch, dispatched: list[dict]
) -> None:
    svc = RecordingService(enabled=False)
    monkeypatch.setattr(
        "app.services.ai.memory.graph_memory.get_graph_memory_service",
        lambda: svc,
    )
    await _dispatch_character_episode({"id": 9001, "name": "Ava"})
    assert dispatched == []


@pytest.mark.asyncio
async def test_dispatch_skips_without_character_id(
    service: RecordingService, dispatched: list[dict]
) -> None:
    await _dispatch_character_episode({"name": "Ava"})
    await _dispatch_character_episode({})
    assert dispatched == []


@pytest.mark.asyncio
async def test_dispatch_swallows_enqueue_failure(
    service: RecordingService, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def boom(queue: str, **kwargs):
        raise RuntimeError("queue down")

    monkeypatch.setattr(
        "app.services.infra.dbos_orchestrator.start_workflow_routed",
        boom,
    )
    # Must not raise — the character write already succeeded.
    await _dispatch_character_episode({"id": 9001, "name": "Ava"})
