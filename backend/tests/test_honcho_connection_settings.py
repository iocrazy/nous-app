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


# ── Task 2: Admin endpoints ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_honcho_connection_returns_effective_config():
    from unittest.mock import MagicMock

    from app.api.admin.settings_router import get_honcho_connection
    from app.services.ai.memory.honcho_memory import HonchoMemoryConfig

    cfg = HonchoMemoryConfig(enabled=True, base_url="http://h:1", workspace_id="w")
    with patch.object(
        HonchoMemoryConfig, "from_settings", new=AsyncMock(return_value=cfg)
    ):
        resp = await get_honcho_connection(MagicMock())
    assert (
        resp.enabled is True
        and resp.base_url == "http://h:1"
        and resp.workspace_id == "w"
    )


@pytest.mark.asyncio
async def test_put_honcho_connection_upserts_only_provided_fields():
    from unittest.mock import MagicMock

    from app.api.admin.settings_router import put_honcho_connection
    from app.schemas.admin import HonchoConnectionUpdate
    from app.services.ai.memory.honcho_memory import HonchoMemoryConfig

    repo = MagicMock()
    repo.upsert_setting = AsyncMock(return_value={})
    auth = MagicMock()
    auth.user_id = "admin-1"
    cfg = HonchoMemoryConfig(enabled=False, base_url="http://h:2", workspace_id="w2")
    with (
        patch(
            "app.api.admin.settings_router.get_system_settings_repository",
            return_value=repo,
        ),
        patch.object(
            HonchoMemoryConfig, "from_settings", new=AsyncMock(return_value=cfg)
        ),
    ):
        await put_honcho_connection(HonchoConnectionUpdate(base_url="http://h:2"), auth)
    # only base_url provided → only that key upserted
    repo.upsert_setting.assert_awaited_once_with(
        "honcho_base_url", "http://h:2", "admin-1"
    )
