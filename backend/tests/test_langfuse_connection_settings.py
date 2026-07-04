"""Env→DB migration wave 2 — Langfuse telemetry config from system_settings
(env fallback), mirroring the Honcho/Graphiti recipe
(test_honcho_connection_settings.py)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.ai.telemetry.langfuse_exporter import (
    LangfuseConfig,
    LangfuseExporter,
)

# ============================================================
# from_settings — DB wins / env fallback / degrade-safe
# ============================================================


@pytest.mark.asyncio
async def test_from_settings_falls_back_to_env_when_keys_absent():
    async def reader(_key):
        return None  # no settings rows

    env = {
        "FEATURE_LANGFUSE": "true",
        "LANGFUSE_HOST": "http://lf:3100/",
        "LANGFUSE_PUBLIC_KEY": "pk-lf-env",
        "LANGFUSE_SECRET_KEY": "sk-lf-env",
    }
    cfg = await LangfuseConfig.from_settings(reader=reader, env=env)
    assert cfg.enabled is True
    assert cfg.host == "http://lf:3100"  # trailing slash stripped
    assert cfg.public_key == "pk-lf-env"
    assert cfg.secret_key == "sk-lf-env"


@pytest.mark.asyncio
async def test_from_settings_db_overrides_env():
    rows = {
        "telemetry.langfuse.enabled": False,  # native JSONB bool
        "telemetry.langfuse.host": "http://new-lf:3100",
        "telemetry.langfuse.public_key": "pk-lf-db",
        "telemetry.langfuse.secret_key": "sk-lf-db",
    }

    async def reader(key):
        return rows.get(key)

    env = {
        "FEATURE_LANGFUSE": "true",
        "LANGFUSE_HOST": "http://old:1",
        "LANGFUSE_PUBLIC_KEY": "pk-lf-old",
        "LANGFUSE_SECRET_KEY": "sk-lf-old",
    }
    cfg = await LangfuseConfig.from_settings(reader=reader, env=env)
    assert cfg.enabled is False  # DB bool False wins over env "true"
    assert cfg.host == "http://new-lf:3100"
    assert cfg.public_key == "pk-lf-db"
    assert cfg.secret_key == "sk-lf-db"


@pytest.mark.asyncio
async def test_from_settings_db_enabled_true_wins_over_env_false():
    async def reader(key):
        return True if key == "telemetry.langfuse.enabled" else None

    env = {"FEATURE_LANGFUSE": "false"}
    cfg = await LangfuseConfig.from_settings(reader=reader, env=env)
    assert cfg.enabled is True


@pytest.mark.asyncio
async def test_from_settings_degrades_to_env_when_reader_raises():
    async def boom(_key):
        raise RuntimeError("db down")

    env = {
        "FEATURE_LANGFUSE": "true",
        "LANGFUSE_HOST": "http://fallback:3100",
        "LANGFUSE_PUBLIC_KEY": "pk-lf-fallback",
        "LANGFUSE_SECRET_KEY": "sk-lf-fallback",
    }
    cfg = await LangfuseConfig.from_settings(reader=boom, env=env)
    assert cfg.enabled is True
    assert cfg.host == "http://fallback:3100"
    assert cfg.public_key == "pk-lf-fallback"
    assert cfg.secret_key == "sk-lf-fallback"


# ============================================================
# _ensure_config — lazy async load vs sync singleton construction
# ============================================================


@pytest.mark.asyncio
async def test_ensure_config_skips_when_client_injected():
    # Tests inject a client + their own config; _ensure_config must NOT swap it.
    sentinel = LangfuseConfig(
        enabled=True, host="http://test", public_key="pk", secret_key="sk"
    )
    exporter = LangfuseExporter(config=sentinel, client=object())  # type: ignore[arg-type]
    with patch.object(
        LangfuseConfig, "from_settings", new=AsyncMock(side_effect=AssertionError)
    ):
        await exporter._ensure_config()  # must not call from_settings
    assert exporter.config is sentinel


@pytest.mark.asyncio
async def test_ensure_config_loads_once():
    exporter = LangfuseExporter()  # no client
    loaded = LangfuseConfig(
        enabled=True, host="http://x", public_key="pk", secret_key="sk"
    )
    with patch.object(
        LangfuseConfig, "from_settings", new=AsyncMock(return_value=loaded)
    ) as m:
        await exporter._ensure_config()
        await exporter._ensure_config()  # second call is a no-op
    assert exporter.config is loaded
    assert m.await_count == 1


@pytest.mark.asyncio
async def test_export_run_loads_settings_config_before_dispatch():
    """The public entrypoint must call _ensure_config so a fresh singleton
    (no injected client) picks up DB config on first real use."""
    loaded = LangfuseConfig(enabled=False, host="", public_key="", secret_key="")
    exporter = LangfuseExporter()
    with patch.object(
        LangfuseConfig, "from_settings", new=AsyncMock(return_value=loaded)
    ) as m:
        ok = await exporter.export_run(
            run_id="1",
            agent_slug="a",
            status="completed",
            trigger="chat",
            user_id="u",
            session_id=None,
            model=None,
            provider=None,
            input_summary=None,
            output_summary=None,
            prompt_tokens=0,
            completion_tokens=0,
            cost_cents=0,
        )
    assert ok is False  # disabled config → inoperative, no call made
    m.assert_awaited_once()


# ============================================================
# reload() — admin config change applies without a process restart
# ============================================================


@pytest.mark.asyncio
async def test_reload_clears_cached_client_and_config_loaded_flag():
    client = AsyncMock()
    exporter = LangfuseExporter(
        config=LangfuseConfig(enabled=True, host="h", public_key="pk", secret_key="sk"),
        client=client,
    )
    exporter._config_loaded = True

    await exporter.reload()

    client.aclose.assert_awaited_once()
    assert exporter.client is None
    assert exporter._config_loaded is False


@pytest.mark.asyncio
async def test_reload_is_safe_with_no_client():
    exporter = LangfuseExporter()
    exporter._config_loaded = True
    await exporter.reload()  # must not raise
    assert exporter.client is None
    assert exporter._config_loaded is False


@pytest.mark.asyncio
async def test_reload_swallows_client_close_failure():
    client = AsyncMock()
    client.aclose.side_effect = RuntimeError("boom")
    exporter = LangfuseExporter(
        config=LangfuseConfig(enabled=True, host="h", public_key="pk", secret_key="sk"),
        client=client,
    )
    await exporter.reload()  # must not raise
    assert exporter.client is None


# ============================================================
# Admin endpoints — GET/PUT langfuse-connection + reload wiring
# ============================================================


@pytest.mark.asyncio
async def test_get_langfuse_connection_returns_masked_config():
    from app.api.admin.settings_router import get_langfuse_connection

    cfg = LangfuseConfig(
        enabled=True, host="http://lf:1", public_key="pk-lf-x", secret_key="sk-lf-x"
    )
    with patch.object(LangfuseConfig, "from_settings", new=AsyncMock(return_value=cfg)):
        resp = await get_langfuse_connection(MagicMock())
    assert resp.enabled is True
    assert resp.host == "http://lf:1"
    assert resp.public_key == "pk-lf-x"
    assert resp.secret_key_set is True
    assert not hasattr(resp, "secret_key")  # raw secret never leaves the server


@pytest.mark.asyncio
async def test_get_langfuse_connection_reports_unset_secret():
    from app.api.admin.settings_router import get_langfuse_connection

    cfg = LangfuseConfig(enabled=False, host="", public_key="", secret_key="")
    with patch.object(LangfuseConfig, "from_settings", new=AsyncMock(return_value=cfg)):
        resp = await get_langfuse_connection(MagicMock())
    assert resp.secret_key_set is False


@pytest.mark.asyncio
async def test_put_langfuse_connection_upserts_only_provided_fields():
    from app.api.admin.settings_router import put_langfuse_connection
    from app.schemas.admin import LangfuseConnectionUpdate

    repo = MagicMock()
    repo.upsert_setting = AsyncMock(return_value={})
    auth = MagicMock()
    auth.user_id = "admin-1"
    cfg = LangfuseConfig(enabled=False, host="http://h:2", public_key="", secret_key="")

    with (
        patch(
            "app.api.admin.settings_router.get_system_settings_repository",
            return_value=repo,
        ),
        patch.object(LangfuseConfig, "from_settings", new=AsyncMock(return_value=cfg)),
        patch(
            "app.api.admin.settings_router.get_langfuse_exporter",
            return_value=MagicMock(reload=AsyncMock()),
        ),
    ):
        await put_langfuse_connection(LangfuseConnectionUpdate(host="http://h:2"), auth)
    # only host provided → only that key upserted
    repo.upsert_setting.assert_awaited_once_with(
        "telemetry.langfuse.host", "http://h:2", "admin-1"
    )


@pytest.mark.asyncio
async def test_put_langfuse_connection_stores_enabled_as_native_bool():
    from app.api.admin.settings_router import put_langfuse_connection
    from app.schemas.admin import LangfuseConnectionUpdate

    repo = MagicMock()
    repo.upsert_setting = AsyncMock(return_value={})
    auth = MagicMock()
    auth.user_id = "admin-1"
    cfg = LangfuseConfig(enabled=True, host="", public_key="", secret_key="")

    with (
        patch(
            "app.api.admin.settings_router.get_system_settings_repository",
            return_value=repo,
        ),
        patch.object(LangfuseConfig, "from_settings", new=AsyncMock(return_value=cfg)),
        patch(
            "app.api.admin.settings_router.get_langfuse_exporter",
            return_value=MagicMock(reload=AsyncMock()),
        ),
    ):
        await put_langfuse_connection(LangfuseConnectionUpdate(enabled=True), auth)
    repo.upsert_setting.assert_awaited_once_with(
        "telemetry.langfuse.enabled", True, "admin-1"
    )
    args, _ = repo.upsert_setting.await_args
    assert args[1] is True  # native bool, not the string "true"


@pytest.mark.asyncio
async def test_put_langfuse_connection_reloads_live_exporter():
    from app.api.admin.settings_router import put_langfuse_connection
    from app.schemas.admin import LangfuseConnectionUpdate

    repo = MagicMock()
    repo.upsert_setting = AsyncMock(return_value={})
    auth = MagicMock()
    auth.user_id = "admin-1"
    cfg = LangfuseConfig(enabled=True, host="h", public_key="pk", secret_key="sk")
    exporter = MagicMock(reload=AsyncMock())

    with (
        patch(
            "app.api.admin.settings_router.get_system_settings_repository",
            return_value=repo,
        ),
        patch.object(LangfuseConfig, "from_settings", new=AsyncMock(return_value=cfg)),
        patch(
            "app.api.admin.settings_router.get_langfuse_exporter",
            return_value=exporter,
        ),
    ):
        await put_langfuse_connection(LangfuseConnectionUpdate(enabled=True), auth)
    exporter.reload.assert_awaited_once()
