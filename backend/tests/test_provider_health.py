"""Tests for user-side BYOK provider connection-test persistence.

Covers:
- ``validate_provider_key`` accept / reject rules.
- ``POST /ai/provider-health`` rejects bad keys with 422.
- ``persist_provider_health`` writes the correct jsonb_set SQL + binds on
  success AND failure, truncates detail to 300 chars, and NEVER raises when
  the DB layer fails (best-effort telemetry).
- ``POST /ai/test-connection`` persists on success and failure, and still
  returns 200 when persistence blows up.
- ``GET /ai/settings`` surfaces the stored ``provider_health`` map.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.ai import provider_health as ph

# ---------------------------------------------------------------------------
# validate_provider_key
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key", ["openai", "deepseek", "lm_studio", "gpt-4o", "a", "z9"]
)
def test_validate_accepts_valid_slugs(key):
    assert ph.validate_provider_key(key) == key


@pytest.mark.parametrize(
    "key",
    [
        "",  # empty
        "OpenAI",  # uppercase
        "open ai",  # space
        "../etc",  # path traversal chars
        "a" * 41,  # too long
        "prov!",  # punctuation
        "a{b}",  # jsonb-path metacharacters
    ],
)
def test_validate_rejects_bad_keys(key):
    with pytest.raises(ph.InvalidProviderKey):
        ph.validate_provider_key(key)


def test_validate_rejects_non_string():
    with pytest.raises(ph.InvalidProviderKey):
        ph.validate_provider_key(None)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# persist_provider_health — SQL + binds
# ---------------------------------------------------------------------------


def _patch_engine(execute_mock):
    """Patch the db_engine + cache imports used inside persist_provider_health.

    Returns a context-manager stack tuple of patchers already started; caller
    stops them. We patch at the source module so the in-function imports
    resolve to our mocks.
    """
    return patch.multiple(
        "app.db.engine",
        is_configured=MagicMock(return_value=True),
        execute=execute_mock,
    )


@pytest.mark.asyncio
async def test_persist_success_writes_jsonb_set_path():
    captured = {}

    async def _execute(sql, params):
        captured["sql"] = sql
        captured["params"] = params
        return 1

    with _patch_engine(AsyncMock(side_effect=_execute)):
        with patch("app.core.cache.user_settings_cache.invalidate") as inval:
            ok = await ph.persist_provider_health(
                "user-1", "openai", "ok", "5 models available"
            )

    assert ok is True
    inval.assert_called_once_with("user-1")
    assert "jsonb_set" in captured["sql"]
    assert "ai_provider_health" in captured["sql"]
    assert captured["params"]["uid"] == "user-1"
    assert captured["params"]["pk"] == "openai"
    val = json.loads(captured["params"]["val"])
    assert val["status"] == "ok"
    assert val["detail"] == "5 models available"
    assert val["tested_at"].endswith("+00:00") or val["tested_at"].endswith("Z")


@pytest.mark.asyncio
async def test_persist_failure_status_and_truncation():
    captured = {}

    async def _execute(sql, params):
        captured["params"] = params
        return 1

    long_detail = "x" * 500
    with _patch_engine(AsyncMock(side_effect=_execute)):
        with patch("app.core.cache.user_settings_cache.invalidate"):
            await ph.persist_provider_health("u", "doubao", "fail", long_detail)

    val = json.loads(captured["params"]["val"])
    assert val["status"] == "fail"
    assert len(val["detail"]) == 300  # truncated to _MAX_DETAIL_LEN


@pytest.mark.asyncio
async def test_persist_no_row_returns_false():
    with _patch_engine(AsyncMock(return_value=0)):
        with patch("app.core.cache.user_settings_cache.invalidate"):
            ok = await ph.persist_provider_health("nobody", "openai", "ok", "")
    assert ok is False


@pytest.mark.asyncio
async def test_persist_swallows_db_error():
    """A DB failure must NOT propagate — telemetry is best-effort."""
    with _patch_engine(AsyncMock(side_effect=RuntimeError("db down"))):
        with patch("app.core.cache.user_settings_cache.invalidate"):
            ok = await ph.persist_provider_health("u", "openai", "ok", "")
    assert ok is False  # no exception


@pytest.mark.asyncio
async def test_persist_bad_key_swallowed_returns_false():
    """The best-effort hook skips silently on a bad key (no raise)."""
    ok = await ph.persist_provider_health("u", "BAD KEY", "ok", "")
    assert ok is False


@pytest.mark.asyncio
async def test_persist_skips_when_engine_unconfigured():
    with patch("app.db.engine.is_configured", MagicMock(return_value=False)):
        ok = await ph.persist_provider_health("u", "openai", "ok", "")
    assert ok is False


# ---------------------------------------------------------------------------
# POST /ai/provider-health endpoint
# ---------------------------------------------------------------------------


def _fake_auth(user_id="user-1"):
    auth = MagicMock()
    auth.user_id = user_id
    return auth


@pytest.mark.asyncio
async def test_provider_health_endpoint_bad_key_422():
    from fastapi import HTTPException

    from app.api.ai_settings_router import report_provider_health
    from app.schemas.ai import ProviderHealthUpdate

    body = ProviderHealthUpdate(provider_key="../evil", status="ok")
    with pytest.raises(HTTPException) as exc:
        await report_provider_health(body, _fake_auth())
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_provider_health_endpoint_persists_local_probe():
    from app.api.ai_settings_router import report_provider_health
    from app.schemas.ai import ProviderHealthUpdate

    body = ProviderHealthUpdate(
        provider_key="ollama", status="fail", detail="ECONNREFUSED"
    )
    with patch(
        "app.api.ai_settings_router.persist_provider_health",
        new=AsyncMock(return_value=True),
    ) as persist:
        result = await report_provider_health(body, _fake_auth("u9"))

    assert result == {"ok": True}
    persist.assert_awaited_once_with("u9", "ollama", "fail", "ECONNREFUSED")


# ---------------------------------------------------------------------------
# POST /ai/test-connection hook
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_test_connection_persists_on_success():
    from app.api.ai_settings_router import test_ai_connection
    from app.schemas.ai import TestConnectionRequest

    body = TestConnectionRequest(provider_key="openai", api_key="sk-1")
    fake_result = {"success": True, "models": ["a", "b", "c"], "error": None}
    with patch(
        "app.api.ai_settings_router.AIProviderFactory.test_connection",
        new=AsyncMock(return_value=fake_result),
    ):
        with patch(
            "app.api.ai_settings_router.persist_provider_health",
            new=AsyncMock(return_value=True),
        ) as persist:
            resp = await test_ai_connection(body, _fake_auth("u1"))

    assert resp.success is True
    persist.assert_awaited_once_with("u1", "openai", "ok", "3 models available")


@pytest.mark.asyncio
async def test_test_connection_persists_on_failure():
    from app.api.ai_settings_router import test_ai_connection
    from app.schemas.ai import TestConnectionRequest

    body = TestConnectionRequest(provider_key="doubao", api_key="revoked")
    fake_result = {"success": False, "models": None, "error": "401 invalid key"}
    with patch(
        "app.api.ai_settings_router.AIProviderFactory.test_connection",
        new=AsyncMock(return_value=fake_result),
    ):
        with patch(
            "app.api.ai_settings_router.persist_provider_health",
            new=AsyncMock(return_value=True),
        ) as persist:
            resp = await test_ai_connection(body, _fake_auth("u2"))

    assert resp.success is False
    persist.assert_awaited_once_with("u2", "doubao", "fail", "401 invalid key")


@pytest.mark.asyncio
async def test_test_connection_survives_persist_failure():
    """A persist blow-up must not break the connection-test response."""
    from app.api.ai_settings_router import test_ai_connection
    from app.schemas.ai import TestConnectionRequest

    body = TestConnectionRequest(provider_key="openai", api_key="sk-1")
    fake_result = {"success": True, "models": ["a"], "error": None}
    with patch(
        "app.api.ai_settings_router.AIProviderFactory.test_connection",
        new=AsyncMock(return_value=fake_result),
    ):
        with patch(
            "app.api.ai_settings_router.persist_provider_health",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ):
            resp = await test_ai_connection(body, _fake_auth("u3"))

    assert resp.success is True  # endpoint still returns normally


# ---------------------------------------------------------------------------
# GET /ai/settings surfaces provider_health
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_settings_surfaces_provider_health():
    from app.api.ai_settings_router import get_ai_settings

    health = {
        "openai": {
            "status": "ok",
            "detail": "5 models",
            "tested_at": "2026-07-03T00:00:00+00:00",
        },
        "ollama": {
            "status": "fail",
            "detail": "ECONNREFUSED",
            "tested_at": "2026-07-03T00:01:00+00:00",
        },
    }
    stored = {
        "settings_json": {
            "ai_settings": {"ai_providers": {"openai": {"enabled": True}}},
            "ai_provider_health": health,
        }
    }
    with patch("app.api.ai_settings_router.UserSettingsRepository") as repo_cls:
        repo_cls.return_value.get_by_user_id = AsyncMock(return_value=stored)
        resp = await get_ai_settings(_fake_auth("u1"))

    assert resp.provider_health == health


@pytest.mark.asyncio
async def test_get_settings_provider_health_defaults_empty():
    from app.api.ai_settings_router import get_ai_settings

    with patch("app.api.ai_settings_router.UserSettingsRepository") as repo_cls:
        repo_cls.return_value.get_by_user_id = AsyncMock(return_value=None)
        resp = await get_ai_settings(_fake_auth("u1"))

    assert resp.provider_health == {}
