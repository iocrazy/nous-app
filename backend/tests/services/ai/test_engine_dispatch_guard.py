"""Dispatch guard: a nous-engine row whose service the engine no longer lists
is refused with a typed error before any request is sent (spec 2026-09-25 §3.6).
Unreachable / stale engine → the call goes through."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.services.ai.engine_catalog as ec
from app.core.exceptions import register_exception_handlers
from app.services.ai.providers.ai_provider_helpers import resolve_nous_model

pytestmark = pytest.mark.unit

ENGINE = "http://engine.test/v1"


def _row(**over) -> dict:
    row = {
        "id": 7300000000000000123,
        "name": "nous-qwen3-8b",
        "actual_provider": "nous",
        "actual_model": "qwen3-8b",
        "api_key": "sk-engine",
        "base_url": ENGINE,
        "app_id": None,
        "is_enabled": True,
    }
    row.update(over)
    return row


class _Engine:
    def __init__(self, monkeypatch):
        ec.reset_engine_cache()
        self.answers: list[ec._Read] = []
        self.calls = 0
        self.now = 1000.0
        monkeypatch.setattr(ec, "_fetch", self._fetch)
        monkeypatch.setattr(ec, "_clock", lambda: self.now)

    async def _fetch(self, base_url, api_key):
        self.calls += 1
        return self.answers.pop(0)


def _listed(*ids: str) -> ec._Read:
    return ec._Read(
        services={
            i: ec.EngineService(
                id=i, type="llm", ready=False, context_window=None, capabilities=None
            )
            for i in ids
        }
    )


@pytest.fixture
def engine(monkeypatch):
    e = _Engine(monkeypatch)
    yield e
    ec.reset_engine_cache()


async def _resolve(row: dict):
    repo = MagicMock()
    repo.get_by_name = AsyncMock(return_value=row)
    with (
        patch(
            "app.repositories.nous_model_repository.get_nous_model_repository",
            return_value=repo,
        ),
        patch(
            "app.services.ai.governance.ai_governance.is_nous_allowed",
            AsyncMock(return_value=True),
        ),
    ):
        return await resolve_nous_model(row["name"], "chat")


@pytest.mark.asyncio
async def test_listed_service_resolves_even_when_not_loaded(engine):
    engine.answers = [_listed("qwen3-8b")]
    provider, config, actual = await _resolve(_row())
    assert (provider, actual) == ("nous", "qwen3-8b")
    assert config["base_url"] == ENGINE


@pytest.mark.asyncio
async def test_service_missing_from_a_fresh_list_is_a_typed_refusal(engine):
    engine.answers = [_listed("something-else")]
    with pytest.raises(ec.EngineServiceUnavailableError) as info:
        await _resolve(_row())
    err = info.value
    # Still a RuntimeError: callers that handle "no longer available" keep working.
    assert isinstance(err, RuntimeError)
    assert err.code == "engine_service_unavailable"
    assert err.status_code == 503
    assert err.details == {
        "code": "engine_service_unavailable",
        "model": "nous-qwen3-8b",
    }


@pytest.mark.asyncio
async def test_unreachable_engine_lets_the_call_through(engine):
    engine.answers = [ec._Read(error="ConnectError: refused")]
    provider, _, _ = await _resolve(_row())
    assert provider == "nous"


@pytest.mark.asyncio
async def test_refused_key_lets_the_call_through(engine):
    engine.answers = [ec._Read(error=ec.UNAUTHORIZED_ERROR, unauthorized=True)]
    provider, _, _ = await _resolve(_row())
    assert provider == "nous"


@pytest.mark.asyncio
async def test_stale_snapshot_is_not_used_to_refuse(engine):
    engine.answers = [_listed("other"), ec._Read(error="HTTP 502")]
    with pytest.raises(ec.EngineServiceUnavailableError):
        await _resolve(_row())
    ec._cache.clear()
    engine.now += 60
    provider, _, _ = await _resolve(_row())
    assert provider == "nous"


@pytest.mark.asyncio
async def test_other_providers_never_read_the_engine(engine):
    provider, _, _ = await _resolve(
        _row(name="nous-doubao", actual_provider="ark", actual_model="doubao-x")
    )
    assert provider == "ark"
    assert engine.calls == 0


def test_uncaught_refusal_is_a_typed_503_envelope():
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/dispatch")
    async def _dispatch() -> dict:
        raise ec.EngineServiceUnavailableError("nous-qwen3-8b")

    resp = TestClient(app, raise_server_exceptions=False).get("/dispatch")
    assert resp.status_code == 503
    body = resp.json()
    assert body["success"] is False
    assert body["code"] == "engine_service_unavailable"
    assert body["details"] == {
        "code": "engine_service_unavailable",
        "model": "nous-qwen3-8b",
    }
