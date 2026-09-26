"""The hourly probe leaves nous-engine rows alone (spec 2026-09-25 §3.4).

nous-engine loads models on demand and one GPU card carries several 27B
variants, so a scheduled real call per row made the engine swap models every
hour. The later passive readiness read (``GET /models/{id}``) stopped the
loading but still wrote a second, hourly copy of a status the platform view
now reads live from the engine's own list on every request.

So the scheduled path (``allow_costly=False``) sends nothing for an
``actual_provider == 'nous'`` row and records ``not_probed`` with the detail
``live: status comes from nous-engine``; the admin Test button
(``allow_costly=True``) still performs the real call, because a human clicking
Test wants the model loaded and exercised once.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.services.ai.nous_model_health import (
    NOUS_ENGINE_LIVE_DETAIL,
    PROBE_STATUSES,
    probe_nous_model,
    probe_result_status,
)

_BASE = "http://host.docker.internal:8000/v1"


class _Resp:
    def __init__(self, status: int = 200, payload: Any = None, text: str = "") -> None:
        self.status_code = status
        self._payload = payload if payload is not None else {}
        self.text = text

    def json(self) -> Any:
        return self._payload


class _Client:
    """Fake httpx.AsyncClient recording every GET and POST it receives."""

    resp = _Resp()
    gets: list[str] = []
    posts: list[str] = []

    def __init__(self, *a: Any, **k: Any) -> None:
        pass

    async def __aenter__(self) -> "_Client":
        return self

    async def __aexit__(self, *a: Any) -> bool:
        return False

    async def get(self, url: str, headers: Any = None, **k: Any) -> _Resp:
        _Client.gets.append(url)
        _Client.headers = headers
        return _Client.resp

    async def post(
        self, url: str, headers: Any = None, json: Any = None, **k: Any
    ) -> _Resp:
        _Client.posts.append(url)
        return _Client.resp


def _patch(resp: _Resp):
    _Client.resp = resp
    _Client.gets = []
    _Client.posts = []
    return patch("app.services.ai.nous_model_health.httpx.AsyncClient", _Client)


def _row(
    typ: str = "llm", model: str = "qwen3-8-27b-huihui", provider: str = "nous"
) -> dict:
    return {
        "id": 1_900_000_000_000_000_001,
        "name": f"nous-{model}",
        "type": typ,
        "actual_provider": provider,
        "actual_model": model,
        "base_url": _BASE + "/",
        "api_key": "sk-instance",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("typ", ["llm", "embedding", "asr", "image"])
async def test_scheduled_probe_of_nous_row_sends_nothing(typ: str) -> None:
    factory = AsyncMock(return_value={"success": True, "models": []})
    with (
        _patch(_Resp(200, {"id": "qwen3-8-27b-huihui", "object": "model"})),
        patch(
            "app.services.ai.nous_model_health.AIProviderFactory.test_connection",
            factory,
        ),
    ):
        out = await probe_nous_model(_row(typ))
    assert _Client.gets == [] and _Client.posts == []
    factory.assert_not_awaited()
    assert out["ok"] is False
    assert out["not_probed"] is True
    assert (
        out["detail"]
        == NOUS_ENGINE_LIVE_DETAIL
        == ("live: status comes from nous-engine")
    )
    assert out["code"] is None and out["error"] is None
    assert probe_result_status(out) == "not_probed"


@pytest.mark.asyncio
async def test_admin_test_path_still_calls_chat_on_a_nous_row() -> None:
    with _patch(_Resp(200, {"choices": [{"message": {"content": "hi"}}]})):
        out = await probe_nous_model(_row(), allow_costly=True)
    assert _Client.gets == []
    assert _Client.posts == [f"{_BASE}/chat/completions"]
    assert out["ok"] is True
    assert out["detail"] == "chat ok"


@pytest.mark.asyncio
async def test_non_nous_provider_scheduled_probe_is_unchanged() -> None:
    row = _row(model="deepseek-v4-flash", provider="deepseek")
    with _patch(_Resp(200, {"choices": [{"message": {"content": "hi"}}]})):
        out = await probe_nous_model(row)
    assert _Client.gets == []
    assert _Client.posts == [f"{_BASE}/chat/completions"]
    assert out["detail"] == "chat ok"


def test_probe_result_status_maps_idle() -> None:
    assert "idle" in PROBE_STATUSES
    assert probe_result_status({"ok": False, "idle": True}) == "idle"
    # ok still wins, and a plain failure is never read as idle.
    assert probe_result_status({"ok": True, "idle": True}) == "ok"
    assert probe_result_status({"ok": False, "idle": False}) == "fail"


@pytest.mark.asyncio
async def test_scheduled_step_records_nous_rows_as_not_probed() -> None:
    from app.workflows.scheduled_health import probe_nous_models_step

    repo = AsyncMock()
    repo.list_all.return_value = [
        dict(_row(), is_enabled=True),
        dict(_row(model="off"), is_enabled=False),
    ]
    with (
        patch(
            "app.repositories.nous_model_repository.get_nous_model_repository",
            return_value=repo,
        ),
        _patch(_Resp(200)),
    ):
        summary = await probe_nous_models_step()
    assert _Client.gets == [] and _Client.posts == []
    assert summary == {
        "total": 1,
        "ok": 0,
        "failed": 0,
        "idle": 0,
        "not_probed": 1,
    }
    repo.list_all.assert_awaited_once()
    args = repo.record_test_result.await_args.args
    assert args[1] == "not_probed"
    assert args[2] == NOUS_ENGINE_LIVE_DETAIL
    assert args[3] is None
