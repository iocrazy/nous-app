"""Phase 2b — Honcho connection config from system_settings (env fallback)."""

from unittest.mock import AsyncMock, patch

import pytest

from app.services.ai.memory.honcho_memory import (
    HonchoMemoryConfig,
    HonchoMemoryService,
)


@pytest.mark.asyncio
async def test_from_settings_falls_back_to_env_when_keys_absent():
    async def reader(_key):
        return None  # no settings rows

    env = {
        "FEATURE_HONCHO_MEMORY": "true",
        "HONCHO_BASE_URL": "http://honcho:18000/",
        "HONCHO_WORKSPACE_ID": "mediahub",
    }
    cfg = await HonchoMemoryConfig.from_settings(reader=reader, env=env)
    assert cfg.enabled is True
    assert cfg.base_url == "http://honcho:18000"  # trailing slash stripped
    assert cfg.workspace_id == "mediahub"


@pytest.mark.asyncio
async def test_from_settings_db_overrides_env():
    rows = {
        "honcho_memory_enabled": "false",
        "honcho_base_url": "http://new-honcho:9000",
        "honcho_workspace_id": "team-1",
    }

    async def reader(key):
        return rows.get(key)

    env = {"FEATURE_HONCHO_MEMORY": "true", "HONCHO_BASE_URL": "http://old:1"}
    cfg = await HonchoMemoryConfig.from_settings(reader=reader, env=env)
    assert cfg.enabled is False  # DB "false" wins over env "true"
    assert cfg.base_url == "http://new-honcho:9000"
    assert cfg.workspace_id == "team-1"


@pytest.mark.asyncio
async def test_ensure_config_skips_when_client_injected():
    # Tests inject a client + their own config; _ensure_config must NOT swap it.
    sentinel = HonchoMemoryConfig(
        enabled=True, base_url="http://test", workspace_id="w"
    )
    svc = HonchoMemoryService(config=sentinel, client=object())  # type: ignore[arg-type]
    with patch.object(
        HonchoMemoryConfig, "from_settings", new=AsyncMock(side_effect=AssertionError)
    ):
        await svc._ensure_config()  # must not call from_settings
    assert svc.config is sentinel


@pytest.mark.asyncio
async def test_ensure_config_loads_once():
    svc = HonchoMemoryService()  # no client
    loaded = HonchoMemoryConfig(enabled=True, base_url="http://x", workspace_id="w")
    with patch.object(
        HonchoMemoryConfig, "from_settings", new=AsyncMock(return_value=loaded)
    ) as m:
        await svc._ensure_config()
        await svc._ensure_config()  # second call is a no-op
    assert svc.config is loaded
    assert m.await_count == 1
