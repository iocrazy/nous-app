"""Platform-level config sourcing for graph memory (Phase 4 — frontend-settable).

GraphMemoryConfig.from_settings() resolves each field from system_settings (DB,
admin-set) with env fallback, so the Graphiti gate + extractor/embedder provider
can be configured from an admin UI instead of editing prod compose env (which
Watchtower doesn't reload). DB value > env > default, per field.
"""

from __future__ import annotations

import pytest

from app.services.ai.memory.graph_memory import (
    DEFAULT_DATABASE,
    DEFAULT_FALKORDB_PORT,
    GraphMemoryConfig,
)


def _reader(values: dict):
    async def read(key: str):
        return values.get(key)

    return read


@pytest.mark.asyncio
async def test_db_values_take_precedence_over_env():
    cfg = await GraphMemoryConfig.from_settings(
        reader=_reader(
            {
                "graph_memory_enabled": "true",
                "graph_falkordb_host": "10.0.0.9",
                "graph_falkordb_port": "16379",
                "graph_extractor_api_key": "ms-db",
                "graph_extractor_base_url": "https://db.example/v1",
                "graph_extractor_model": "Qwen/Qwen-db",
            }
        ),
        env={
            "FEATURE_GRAPH_MEMORY": "false",
            "FALKORDB_HOST": "env-host",
            "OPENAI_API_KEY": "ms-env",
        },
    )
    assert cfg.enabled is True
    assert cfg.falkordb_host == "10.0.0.9"
    assert cfg.falkordb_port == 16379
    assert cfg.extractor_api_key == "ms-db"
    assert cfg.extractor_base_url == "https://db.example/v1"
    assert cfg.extractor_model == "Qwen/Qwen-db"


@pytest.mark.asyncio
async def test_env_fallback_when_db_absent():
    cfg = await GraphMemoryConfig.from_settings(
        reader=_reader({}),
        env={
            "FEATURE_GRAPH_MEMORY": "1",
            "FALKORDB_HOST": "env-host",
            "FALKORDB_PORT": "6399",
            "OPENAI_API_KEY": "ms-env",
            "OPENAI_BASE_URL": "https://env.example/v1",
        },
    )
    assert cfg.enabled is True
    assert cfg.falkordb_host == "env-host"
    assert cfg.falkordb_port == 6399
    assert cfg.extractor_api_key == "ms-env"
    assert cfg.extractor_base_url == "https://env.example/v1"


@pytest.mark.asyncio
async def test_defaults_when_both_absent():
    cfg = await GraphMemoryConfig.from_settings(reader=_reader({}), env={})
    assert cfg.enabled is False
    assert cfg.falkordb_host == ""
    assert cfg.falkordb_port == DEFAULT_FALKORDB_PORT
    assert cfg.falkordb_database == DEFAULT_DATABASE
    assert cfg.extractor_api_key == ""
    assert cfg.extractor_model == ""
    assert cfg.operative() is False  # disabled + no host


@pytest.mark.asyncio
async def test_operative_needs_enabled_and_host():
    on_no_host = await GraphMemoryConfig.from_settings(
        reader=_reader({"graph_memory_enabled": "true"}), env={}
    )
    assert on_no_host.operative() is False
    on_with_host = await GraphMemoryConfig.from_settings(
        reader=_reader({"graph_memory_enabled": "true", "graph_falkordb_host": "h"}),
        env={},
    )
    assert on_with_host.operative() is True


@pytest.mark.asyncio
async def test_bad_db_port_falls_back_to_default():
    cfg = await GraphMemoryConfig.from_settings(
        reader=_reader({"graph_falkordb_port": "not-a-number"}), env={}
    )
    assert cfg.falkordb_port == DEFAULT_FALKORDB_PORT


@pytest.mark.asyncio
async def test_reader_exception_degrades_to_env(monkeypatch):
    async def boom(key: str):
        raise RuntimeError("db down")

    cfg = await GraphMemoryConfig.from_settings(
        reader=boom, env={"FEATURE_GRAPH_MEMORY": "true", "FALKORDB_HOST": "env-host"}
    )
    # A broken settings table must not sink memory config — env still applies.
    assert cfg.enabled is True
    assert cfg.falkordb_host == "env-host"


def test_from_env_still_works_for_default_factory():
    # Backward-compat: the dataclass default_factory path stays env-only.
    cfg = GraphMemoryConfig.from_env()
    assert isinstance(cfg, GraphMemoryConfig)
