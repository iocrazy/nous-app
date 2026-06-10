"""Tests for the nous-center async-protocol client + run-then-poll
helper (Phase 2 Day 9-10)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, List, Optional
from unittest.mock import AsyncMock

import pytest

from app.services.canvas.nous_center_client import (
    NousCenterError,
    NousRunHandle,
    NousRunStatus,
)
from app.services.canvas.nous_center_runner import (
    NousCenterNotConfigured,
    _extract_output_text,
    run_nous_workflow,
)


class FakeNousClient:
    """In-memory stand-in for NousCenterClient."""

    def __init__(
        self,
        *,
        start_handle: Optional[NousRunHandle] = None,
        start_raises: Optional[Exception] = None,
        status_sequence: Optional[List[NousRunStatus]] = None,
        status_raises_at: Optional[int] = None,
    ):
        self.start_calls: List[dict] = []
        self.get_calls: List[str] = []
        self._start_handle = start_handle
        self._start_raises = start_raises
        self._status_sequence = list(status_sequence or [])
        self._status_raises_at = status_raises_at

    async def start_run(self, **kwargs):
        self.start_calls.append(kwargs)
        if self._start_raises:
            raise self._start_raises
        return self._start_handle or NousRunHandle(
            run_id="run_1", workflow_slug=kwargs["workflow_slug"], status="queued"
        )

    async def get_run(self, run_id: str) -> NousRunStatus:
        idx = len(self.get_calls)
        self.get_calls.append(run_id)
        if self._status_raises_at is not None and idx == self._status_raises_at:
            raise NousCenterError("transient")
        if idx < len(self._status_sequence):
            return self._status_sequence[idx]
        # Keep returning the last known status forever (avoids IndexError).
        return self._status_sequence[-1]


def _status(state: str, **outputs: Any) -> NousRunStatus:
    return NousRunStatus(
        run_id="run_1",
        status=state,
        outputs=outputs,
        error=outputs.pop("error", None) if "error" in outputs else None,
        raw={"status": state, "outputs": outputs},
    )


def _settings(**overrides) -> Any:
    base = {
        "NOUS_CENTER_BASE_URL": "https://nous.example",
        "NOUS_CENTER_TOKEN": "tok",
        "NOUS_CENTER_POLL_MS": 1,
        "NOUS_CENTER_MAX_WAIT_S": 1.0,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


# ============================================================
# _extract_output_text
# ============================================================


class TestExtractOutputText:
    def test_text_field_preferred(self):
        s = NousRunStatus(
            run_id="r",
            status="completed",
            outputs={"text": "hello", "caption": "ignored"},
            error=None,
            raw={},
        )
        assert _extract_output_text(s) == "hello"

    def test_caption_used_when_text_missing(self):
        s = NousRunStatus(
            run_id="r",
            status="completed",
            outputs={"caption": "a cat"},
            error=None,
            raw={},
        )
        assert _extract_output_text(s) == "a cat"

    def test_falls_back_to_json_dump(self):
        s = NousRunStatus(
            run_id="r",
            status="completed",
            outputs={"image_url": "https://x"},
            error=None,
            raw={},
        )
        result = _extract_output_text(s)
        assert "image_url" in result
        assert "https://x" in result

    def test_empty_outputs_returns_empty(self):
        s = NousRunStatus(
            run_id="r", status="completed", outputs={}, error=None, raw={}
        )
        assert _extract_output_text(s) == ""


# ============================================================
# run_nous_workflow — happy path
# ============================================================


@pytest.mark.asyncio
async def test_completes_on_first_poll():
    client = FakeNousClient(
        status_sequence=[_status("completed", text="done")],
    )
    sleep = AsyncMock()
    result = await run_nous_workflow(
        settings=_settings(),
        workflow_slug="storyboard",
        prompt="a robot in the rain",
        client=client,
        sleep=sleep,
    )
    assert result.ok is True
    assert result.text == "done"
    assert result.error is None
    assert client.start_calls[0]["workflow_slug"] == "storyboard"
    assert client.start_calls[0]["inputs"]["prompt"] == "a robot in the rain"


@pytest.mark.asyncio
async def test_polls_through_running_to_completed():
    client = FakeNousClient(
        status_sequence=[
            _status("running"),
            _status("running"),
            _status("completed", caption="painted"),
        ],
    )
    sleep = AsyncMock()
    result = await run_nous_workflow(
        settings=_settings(),
        workflow_slug="storyboard",
        prompt="x",
        client=client,
        sleep=sleep,
    )
    assert result.ok is True
    assert result.text == "painted"
    assert len(client.get_calls) == 3


@pytest.mark.asyncio
async def test_agent_id_appears_in_metadata():
    client = FakeNousClient(
        status_sequence=[_status("completed", text="ok")],
    )
    await run_nous_workflow(
        settings=_settings(),
        workflow_slug="x",
        prompt="y",
        agent_id="abc",
        client=client,
        sleep=AsyncMock(),
    )
    assert client.start_calls[0]["metadata"] == {"agent_id": "abc"}


# ============================================================
# run_nous_workflow — failure paths (all in-band)
# ============================================================


@pytest.mark.asyncio
async def test_failed_status_returns_in_band_failure():
    client = FakeNousClient(
        status_sequence=[
            NousRunStatus(
                run_id="run_1",
                status="failed",
                outputs={},
                error="OOM",
                raw={},
            ),
        ],
    )
    result = await run_nous_workflow(
        settings=_settings(),
        workflow_slug="x",
        prompt="y",
        client=client,
        sleep=AsyncMock(),
    )
    assert result.ok is False
    assert result.error == "OOM"


@pytest.mark.asyncio
async def test_cancelled_status_returns_in_band_failure():
    client = FakeNousClient(
        status_sequence=[
            NousRunStatus(
                run_id="run_1",
                status="cancelled",
                outputs={},
                error=None,
                raw={},
            ),
        ],
    )
    result = await run_nous_workflow(
        settings=_settings(),
        workflow_slug="x",
        prompt="y",
        client=client,
        sleep=AsyncMock(),
    )
    assert result.ok is False
    assert "cancelled" in (result.error or "")


@pytest.mark.asyncio
async def test_start_run_transport_failure_is_in_band():
    client = FakeNousClient(start_raises=NousCenterError("conn refused"))
    result = await run_nous_workflow(
        settings=_settings(),
        workflow_slug="x",
        prompt="y",
        client=client,
        sleep=AsyncMock(),
    )
    assert result.ok is False
    assert "conn refused" in (result.error or "")


@pytest.mark.asyncio
async def test_get_run_transport_failure_is_in_band():
    client = FakeNousClient(
        status_sequence=[_status("running")],
        status_raises_at=0,
    )
    result = await run_nous_workflow(
        settings=_settings(),
        workflow_slug="x",
        prompt="y",
        client=client,
        sleep=AsyncMock(),
    )
    assert result.ok is False
    assert "transient" in (result.error or "")


@pytest.mark.asyncio
async def test_missing_run_id_in_response_is_in_band():
    client = FakeNousClient(
        start_handle=NousRunHandle(run_id="", workflow_slug="x", status="queued"),
    )
    result = await run_nous_workflow(
        settings=_settings(),
        workflow_slug="x",
        prompt="y",
        client=client,
        sleep=AsyncMock(),
    )
    assert result.ok is False
    assert "no run_id" in (result.error or "")


@pytest.mark.asyncio
async def test_timeout_returns_in_band_failure():
    # Always-queued client + tiny max_wait.
    client = FakeNousClient(
        status_sequence=[_status("queued"), _status("queued"), _status("queued")],
    )
    settings = _settings(NOUS_CENTER_MAX_WAIT_S=0.001, NOUS_CENTER_POLL_MS=1)
    result = await run_nous_workflow(
        settings=settings,
        workflow_slug="x",
        prompt="y",
        client=client,
        sleep=AsyncMock(),
    )
    assert result.ok is False
    assert "timed out" in (result.error or "")


# ============================================================
# Misconfiguration
# ============================================================


@pytest.mark.asyncio
async def test_missing_base_url_raises_not_configured():
    settings = SimpleNamespace(NOUS_CENTER_BASE_URL=None, NOUS_CENTER_TOKEN="t")
    with pytest.raises(NousCenterNotConfigured):
        await run_nous_workflow(settings=settings, workflow_slug="x", prompt="y")


@pytest.mark.asyncio
async def test_missing_token_raises_not_configured():
    settings = SimpleNamespace(NOUS_CENTER_BASE_URL="https://x", NOUS_CENTER_TOKEN="")
    with pytest.raises(NousCenterNotConfigured):
        await run_nous_workflow(settings=settings, workflow_slug="x", prompt="y")
