"""Hourly probe of local nous-engine rows must not force the engine to load.

nous-engine loads models on demand and one GPU card now carries three 27B
variants. A scheduled ``POST /chat/completions`` per row made the engine swap
models every hour. The engine already exposes a readiness read that never loads
anything: ``GET {base}/models/{id}`` answers 200 when the model is authorized
AND loaded, 503 (``ModelNotReadyError``) when it is authorized but not loaded,
404 when the key has no grant, 401/403 when the key is bad.

So the scheduled path (``allow_costly=False``) reads that endpoint for
``actual_provider == 'nous'`` llm / embedding / asr rows, and the admin Test
button (``allow_costly=True``) still performs the real call, because a human
clicking Test wants the model loaded and exercised once.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.services.ai.nous_model_health import (
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
@pytest.mark.parametrize("typ", ["llm", "embedding", "asr"])
async def test_scheduled_probe_of_nous_row_only_reads_readiness(typ: str) -> None:
    factory = AsyncMock(return_value={"success": True, "models": []})
    with (
        _patch(_Resp(200, {"id": "qwen3-8-27b-huihui", "object": "model"})),
        patch(
            "app.services.ai.nous_model_health.AIProviderFactory.test_connection",
            factory,
        ),
    ):
        out = await probe_nous_model(_row(typ))
    assert _Client.gets == [f"{_BASE}/models/qwen3-8-27b-huihui"]
    assert _Client.posts == []
    factory.assert_not_awaited()
    assert _Client.headers["Authorization"] == "Bearer sk-instance"
    assert out["ok"] is True
    assert out["detail"] == "loaded"
    assert out["code"] is None
    assert probe_result_status(out) == "ok"


@pytest.mark.asyncio
async def test_503_means_authorized_but_not_loaded_and_is_idle() -> None:
    with _patch(
        _Resp(503, {"error": {"message": "model is not loaded"}}, "not loaded")
    ):
        out = await probe_nous_model(_row())
    assert out["ok"] is False
    assert out["idle"] is True
    assert out["detail"] == "authorized, not loaded"
    assert out["code"] is None
    assert out["error"] is None
    assert probe_result_status(out) == "idle"


@pytest.mark.asyncio
async def test_404_is_a_model_not_found_failure() -> None:
    with _patch(_Resp(404, {"error": "not found"}, "not found")):
        out = await probe_nous_model(_row())
    assert out["ok"] is False
    assert not out.get("idle")
    assert out["code"] == "model_not_found"
    assert "404" in out["error"]
    assert probe_result_status(out) == "fail"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403])
async def test_bad_key_is_an_auth_failure(status: int) -> None:
    with _patch(_Resp(status, {}, "unauthorized")):
        out = await probe_nous_model(_row())
    assert out["code"] == "auth"
    assert probe_result_status(out) == "fail"


@pytest.mark.asyncio
async def test_transport_error_on_readiness_read_is_classified() -> None:
    import httpx

    class _Boom(_Client):
        async def get(self, url: str, headers: Any = None, **k: Any) -> _Resp:
            raise httpx.ConnectError("")

    with patch("app.services.ai.nous_model_health.httpx.AsyncClient", _Boom):
        out = await probe_nous_model(_row())
    assert out["code"] == "unreachable"
    assert out["error"].startswith("ConnectError")
    assert probe_result_status(out) == "fail"


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


@pytest.mark.asyncio
async def test_nous_image_row_stays_not_probed_on_the_schedule() -> None:
    with _patch(_Resp(200)):
        out = await probe_nous_model(_row(typ="image", model="studio-upscale"))
    assert _Client.gets == [] and _Client.posts == []
    assert probe_result_status(out) == "not_probed"


def test_probe_result_status_maps_idle() -> None:
    assert "idle" in PROBE_STATUSES
    assert probe_result_status({"ok": False, "idle": True}) == "idle"
    # ok still wins, and a plain failure is never read as idle.
    assert probe_result_status({"ok": True, "idle": True}) == "ok"
    assert probe_result_status({"ok": False, "idle": False}) == "fail"


@pytest.mark.asyncio
async def test_scheduled_step_counts_idle_in_its_own_bucket() -> None:
    from app.workflows.scheduled_health import probe_nous_models_step

    repo = AsyncMock()
    repo.list_all.return_value = [dict(_row(), is_enabled=True)]
    with (
        patch(
            "app.repositories.nous_model_repository.get_nous_model_repository",
            return_value=repo,
        ),
        _patch(_Resp(503, {}, "not loaded")),
    ):
        summary = await probe_nous_models_step()
    assert summary["idle"] == 1
    assert summary["failed"] == 0
    assert summary["ok"] == 0
    args = repo.record_test_result.await_args.args
    assert args[1] == "idle"
    assert args[2] == "authorized, not loaded"
    assert repo.record_test_result.await_args.args[3] is None
