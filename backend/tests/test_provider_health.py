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

Phase B2 Task 1 (2026-08-04): ``persist_provider_health`` moved off
``db_engine.execute`` raw SQL onto the ORM ``write_scope()`` session (nested
``jsonb_set`` expression — see ``tests/test_orm_b2_task1_compile_coverage.py``
for the dedicated operator-equivalence coverage). The success/failure tests
below patch ``app.db.session.write_scope`` with a fake session instead of
``app.db.engine.execute``; the no-row / DB-error / unconfigured-engine tests
were untouched (they only assert the swallow-and-return-False contract, which
still holds because ``write_scope()`` raises the same way a bad ``execute()``
did).
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.dialects import postgresql

from app.db import session as db_session
from app.services.ai import provider_health as ph


def _compile(stmt: Any) -> tuple[str, dict[str, Any]]:
    compiled = stmt.compile(dialect=postgresql.dialect())
    return str(compiled), dict(compiled.params)


class _FakeResult:
    def __init__(self, rowcount: int = 1) -> None:
        self.rowcount = rowcount


class _FakeSession:
    def __init__(self, rowcount: int = 1) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._rowcount = rowcount

    async def execute(self, stmt: Any) -> _FakeResult:
        self.calls.append(_compile(stmt))
        return _FakeResult(self._rowcount)


class _ScopeCM:
    def __init__(self, session: _FakeSession) -> None:
        self._session = session

    async def __aenter__(self) -> _FakeSession:
        return self._session

    async def __aexit__(self, *exc: Any) -> bool:
        return False


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


class _RaisingSession:
    """A fake session whose execute() raises — simulates a DB error mid-write."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def execute(self, stmt: Any) -> Any:
        raise self._exc


@pytest.mark.asyncio
async def test_persist_success_writes_jsonb_set_path():
    with patch("app.db.engine.is_configured", MagicMock(return_value=True)):
        session = _FakeSession(rowcount=1)
        with patch.object(db_session, "write_scope", lambda: _ScopeCM(session)):
            with patch("app.core.cache.user_settings_cache.invalidate") as inval:
                ok = await ph.persist_provider_health(
                    "user-1", "openai", "ok", "5 models available"
                )

    assert ok is True
    inval.assert_called_once_with("user-1")
    sql = "\n".join(s for s, _ in session.calls)
    binds: list[Any] = []
    for _s, params in session.calls:
        binds.extend(params.values())
    assert "jsonb_set" in sql
    assert "ai_provider_health" in binds
    assert "user-1" in binds
    assert "openai" in binds
    val_json = next(v for v in binds if isinstance(v, str) and v.startswith('{"'))
    val = json.loads(val_json)
    assert val["status"] == "ok"
    assert val["detail"] == "5 models available"
    assert val["tested_at"].endswith("+00:00") or val["tested_at"].endswith("Z")


@pytest.mark.asyncio
async def test_persist_failure_status_and_truncation():
    long_detail = "x" * 500
    with patch("app.db.engine.is_configured", MagicMock(return_value=True)):
        session = _FakeSession(rowcount=1)
        with patch.object(db_session, "write_scope", lambda: _ScopeCM(session)):
            with patch("app.core.cache.user_settings_cache.invalidate"):
                await ph.persist_provider_health("u", "doubao", "fail", long_detail)

    binds: list[Any] = []
    for _s, params in session.calls:
        binds.extend(params.values())
    val_json = next(v for v in binds if isinstance(v, str) and v.startswith('{"'))
    val = json.loads(val_json)
    assert val["status"] == "fail"
    assert len(val["detail"]) == 300  # truncated to _MAX_DETAIL_LEN


@pytest.mark.asyncio
async def test_persist_no_row_returns_false():
    with patch("app.db.engine.is_configured", MagicMock(return_value=True)):
        session = _FakeSession(rowcount=0)  # no matching user_settings row
        with patch.object(db_session, "write_scope", lambda: _ScopeCM(session)):
            with patch("app.core.cache.user_settings_cache.invalidate"):
                ok = await ph.persist_provider_health("nobody", "openai", "ok", "")
    assert ok is False


@pytest.mark.asyncio
async def test_persist_swallows_db_error():
    """A DB failure must NOT propagate — telemetry is best-effort."""
    with patch("app.db.engine.is_configured", MagicMock(return_value=True)):
        session = _RaisingSession(RuntimeError("db down"))
        with patch.object(db_session, "write_scope", lambda: _ScopeCM(session)):
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
