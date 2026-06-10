"""Tests for the raw nous-center HTTP client (httpx-level).

Uses respx (already a backend test dep — see frontend canvas tests for
the matching fetch pattern)."""

from __future__ import annotations

import httpx
import pytest
import respx

from app.services.canvas.nous_center_client import (
    NousCenterClient,
    NousCenterError,
)

BASE = "https://nous.test"


def make_client() -> NousCenterClient:
    return NousCenterClient(base_url=BASE, token="tok")


class TestStartRun:
    @pytest.mark.asyncio
    @respx.mock
    async def test_returns_handle_on_200(self):
        respx.post(f"{BASE}/runs").mock(
            return_value=httpx.Response(
                200,
                json={
                    "run_id": "run_42",
                    "workflow_slug": "story",
                    "status": "queued",
                    "estimated_completion_seconds": 5,
                },
            )
        )
        client = make_client()
        handle = await client.start_run(workflow_slug="story", inputs={"prompt": "x"})
        assert handle.run_id == "run_42"
        assert handle.status == "queued"
        assert handle.estimated_completion_seconds == 5

    @pytest.mark.asyncio
    @respx.mock
    async def test_sends_authorization_and_idempotency_headers(self):
        route = respx.post(f"{BASE}/runs").mock(
            return_value=httpx.Response(
                200, json={"run_id": "r", "workflow_slug": "x", "status": "queued"}
            )
        )
        client = make_client()
        await client.start_run(
            workflow_slug="x",
            inputs={"prompt": "y"},
            idempotency_key="fixed-key",
        )
        request = route.calls[0].request
        assert request.headers["Authorization"] == "Bearer tok"
        assert request.headers["X-API-Version"] == "v1"
        assert request.headers["Idempotency-Key"] == "fixed-key"

    @pytest.mark.asyncio
    @respx.mock
    async def test_includes_metadata_when_provided(self):
        route = respx.post(f"{BASE}/runs").mock(
            return_value=httpx.Response(
                200, json={"run_id": "r", "workflow_slug": "x", "status": "queued"}
            )
        )
        client = make_client()
        await client.start_run(
            workflow_slug="x",
            inputs={"prompt": "y"},
            metadata={"agent_id": "abc"},
        )
        body = route.calls[0].request.read()
        assert b"agent_id" in body
        assert b"abc" in body

    @pytest.mark.asyncio
    @respx.mock
    async def test_400_raises_nous_center_error_with_status(self):
        respx.post(f"{BASE}/runs").mock(
            return_value=httpx.Response(400, text="bad inputs")
        )
        client = make_client()
        with pytest.raises(NousCenterError) as exc_info:
            await client.start_run(workflow_slug="x", inputs={})
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    @respx.mock
    async def test_transport_failure_raises_nous_center_error(self):
        respx.post(f"{BASE}/runs").mock(side_effect=httpx.ConnectError("nope"))
        client = make_client()
        with pytest.raises(NousCenterError) as exc_info:
            await client.start_run(workflow_slug="x", inputs={})
        assert "transport failed" in str(exc_info.value)


class TestGetRun:
    @pytest.mark.asyncio
    @respx.mock
    async def test_happy_path_returns_status(self):
        respx.get(f"{BASE}/runs/r1").mock(
            return_value=httpx.Response(
                200,
                json={
                    "run_id": "r1",
                    "status": "completed",
                    "outputs": {"text": "the result"},
                    "error": None,
                },
            )
        )
        client = make_client()
        status = await client.get_run("r1")
        assert status.status == "completed"
        assert status.outputs == {"text": "the result"}
        assert status.error is None

    @pytest.mark.asyncio
    @respx.mock
    async def test_404_raises_with_status_code(self):
        respx.get(f"{BASE}/runs/missing").mock(
            return_value=httpx.Response(404, text="not found")
        )
        client = make_client()
        with pytest.raises(NousCenterError) as exc_info:
            await client.get_run("missing")
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_empty_run_id_rejected_locally(self):
        client = make_client()
        with pytest.raises(ValueError):
            await client.get_run("")


class TestConstruction:
    def test_rejects_missing_base_url(self):
        with pytest.raises(ValueError):
            NousCenterClient(base_url="", token="t")

    def test_rejects_missing_token(self):
        with pytest.raises(ValueError):
            NousCenterClient(base_url="https://x", token="")
