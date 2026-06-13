"""Tests for the Langfuse trace exporter (Phase 4.5-6)."""

from __future__ import annotations

import json

import httpx
import pytest

from app.services.ai.telemetry.langfuse_exporter import (
    LangfuseConfig,
    LangfuseExporter,
)


class Recorder:
    def __init__(self, *, status: int = 207):
        self.requests: list[tuple[str, dict]] = []
        self.status = status

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        self.requests.append((request.url.path, body))
        return httpx.Response(self.status, json={"successes": [], "errors": []})


def _exporter(handler, *, enabled: bool = True) -> LangfuseExporter:
    config = LangfuseConfig(
        enabled=enabled,
        host="http://lf.test",
        public_key="pk-lf-x",
        secret_key="sk-lf-x",
    )
    client = httpx.AsyncClient(
        base_url="http://lf.test", transport=httpx.MockTransport(handler)
    )
    return LangfuseExporter(config=config, client=client)


def _run_kwargs(**over) -> dict:
    base = dict(
        run_id="123",
        agent_slug="script_ai",
        status="completed",
        trigger="chat",
        user_id="u-1",
        session_id="888",
        model="qwen3.5-plus",
        provider="qwen",
        input_summary="hello",
        output_summary="world",
        prompt_tokens=100,
        completion_tokens=50,
        cost_cents=2.5,
    )
    base.update(over)
    return base


# ============================================================
# Config
# ============================================================


class TestConfig:
    def test_disabled_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for var in (
            "FEATURE_LANGFUSE",
            "LANGFUSE_HOST",
            "LANGFUSE_PUBLIC_KEY",
            "LANGFUSE_SECRET_KEY",
        ):
            monkeypatch.delenv(var, raising=False)
        assert LangfuseConfig.from_env().operative() is False

    def test_flag_without_keys_is_inoperative(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FEATURE_LANGFUSE", "true")
        monkeypatch.setenv("LANGFUSE_HOST", "http://x:3100/")
        monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
        monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
        config = LangfuseConfig.from_env()
        assert config.enabled is True
        assert config.operative() is False
        assert config.host == "http://x:3100"  # trailing slash stripped


# ============================================================
# export_run
# ============================================================


@pytest.mark.asyncio
async def test_exports_trace_and_generation() -> None:
    recorder = Recorder()
    ok = await _exporter(recorder).export_run(**_run_kwargs())
    assert ok is True
    path, body = recorder.requests[0]
    assert path == "/api/public/ingestion"
    types = [e["type"] for e in body["batch"]]
    assert types == ["trace-create", "generation-create"]
    trace = body["batch"][0]["body"]
    assert trace["id"] == "run-123"
    assert trace["name"] == "script_ai"
    assert trace["sessionId"] == "888"
    assert trace["tags"] == ["chat", "completed"]
    gen = body["batch"][1]["body"]
    assert gen["traceId"] == "run-123"
    assert gen["usage"] == {"input": 100, "output": 50, "totalCost": 0.025}
    assert gen["level"] == "DEFAULT"


@pytest.mark.asyncio
async def test_failed_run_marks_error_level() -> None:
    recorder = Recorder()
    await _exporter(recorder).export_run(
        **_run_kwargs(status="failed", error_message="boom")
    )
    _, body = recorder.requests[0]
    gen = body["batch"][1]["body"]
    assert gen["level"] == "ERROR"
    assert gen["statusMessage"] == "boom"
    assert body["batch"][0]["body"]["metadata"]["error"] == "boom"


@pytest.mark.asyncio
async def test_disabled_exporter_makes_no_calls() -> None:
    recorder = Recorder()
    ok = await _exporter(recorder, enabled=False).export_run(**_run_kwargs())
    assert ok is False
    assert recorder.requests == []


@pytest.mark.asyncio
async def test_server_error_is_swallowed() -> None:
    recorder = Recorder(status=500)
    ok = await _exporter(recorder).export_run(**_run_kwargs())
    assert ok is False  # logged, never raises


@pytest.mark.asyncio
async def test_transport_error_is_swallowed() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    ok = await _exporter(boom).export_run(**_run_kwargs())
    assert ok is False
