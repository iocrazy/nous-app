"""B9-G — boundary_audit log_block fire-and-forget contract."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.boundary import audit


@pytest.mark.unit
async def test_log_block_runs_in_background(monkeypatch):
    """log_block returns immediately; the actual DB write happens in a
    background task on the running loop."""
    write_called: list[dict] = []

    async def _fake_write(payload: dict) -> None:
        await asyncio.sleep(0)  # yield once to ensure scheduling
        write_called.append(payload)

    monkeypatch.setattr(audit, "_write_row", _fake_write)

    audit.log_block(
        layer=audit.LAYER_VALIDATE,
        reason="private_ip",
        raw_url="http://192.168.50.9/admin",
        resolved_ip="192.168.50.9",
    )

    # Returned immediately. Write happens in the next event-loop iteration.
    assert write_called == []
    await asyncio.sleep(0.01)
    assert len(write_called) == 1
    payload = write_called[0]
    assert payload["layer"] == audit.LAYER_VALIDATE
    assert payload["reason"] == "private_ip"
    assert payload["raw_url"] == "http://192.168.50.9/admin"
    assert payload["resolved_ip"] == "192.168.50.9"


@pytest.mark.unit
async def test_log_block_truncates_long_raw_url(monkeypatch):
    """raw_url longer than 2000 chars is truncated with marker."""
    write_called: list[dict] = []

    async def _fake_write(payload: dict) -> None:
        write_called.append(payload)

    monkeypatch.setattr(audit, "_write_row", _fake_write)

    huge = "https://example.com/" + ("A" * 5000)
    audit.log_block(
        layer=audit.LAYER_PROXY,
        reason="http_blocked",
        raw_url=huge,
    )
    await asyncio.sleep(0.01)

    assert len(write_called) == 1
    truncated = write_called[0]["raw_url"]
    assert truncated.endswith("...[truncated]")
    assert len(truncated) < len(huge)


@pytest.mark.unit
async def test_log_block_swallows_write_failure(monkeypatch, caplog):
    """If the DB write raises, the audit caller does NOT see the error
    (best-effort semantics)."""
    async def _failing_write(payload: dict) -> None:
        raise RuntimeError("fake supabase down")

    monkeypatch.setattr(audit, "_write_row", _failing_write)

    # Background tasks log to loguru, not stdlib logging — no caplog assertion.
    # We just verify the call doesn't propagate the error.
    audit.log_block(layer=audit.LAYER_PROXY, reason="connect_blocked")
    await asyncio.sleep(0.01)
    # If we got here, the failure was swallowed. Pass.


def test_log_block_no_loop_drops_silently():
    """Called from sync context (no running loop), returns silently."""
    # No running loop here — pytest's sync test
    audit.log_block(
        layer=audit.LAYER_VALIDATE,
        reason="private_ip",
        raw_url="http://10.0.0.1/",
    )
    # If we got here without crashing, the contract held.
