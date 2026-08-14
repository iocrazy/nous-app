# backend/tests/test_mediahub_model_health_diagnosable.py

"""F1 — a failed platform-model probe must always be diagnosable after the fact.

Production incident (2026-08-14): ``mediahub-deepseek-v4-flash`` was marked
``fail`` with an EMPTY ``last_test_detail``, while a sibling model on the same
base_url/credentials probed OK 20 seconds earlier — and the model turned out to
be fully usable end-to-end. The reason was lost because httpx's timeout
exceptions carry an empty ``str()``: the old fallback ``str(e)[:200]`` rendered
``ReadTimeout`` as ``""``, so "model is genuinely broken" and "the probe
misfired" became indistinguishable in the DB.

These tests pin the two properties that make that impossible to repeat:
the failure reason is never empty, and it names the exception type.
"""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest

from app.services.ai.mediahub_model_health import probe_mediahub_model


class _RaisingClient:
    """httpx.AsyncClient stand-in whose ``post`` raises a preset exception,
    recording the constructor kwargs so the timeout budget can be asserted."""

    _exc: Exception = RuntimeError("unset")
    kwargs: dict = {}

    def __init__(self, *a, **k):
        _RaisingClient.kwargs = k

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, headers=None, json=None):
        raise _RaisingClient._exc


def _patch_raising(exc: Exception):
    _RaisingClient._exc = exc
    return patch(
        "app.services.ai.mediahub_model_health.httpx.AsyncClient", _RaisingClient
    )


_LLM_ROW = {
    "type": "llm",
    "actual_model": "deepseek-v4-flash",
    "base_url": "https://api.deepseek.com/v1",
    "api_key": "k",
}

_EMBEDDING_ROW = {
    "type": "embedding",
    "actual_model": "text-embedding-3-small",
    "base_url": "https://api.openai.com/v1",
    "api_key": "k",
}


@pytest.mark.parametrize(
    "exc",
    [
        httpx.ReadTimeout(""),
        httpx.ConnectTimeout(""),
        httpx.ReadError(""),
    ],
)
@pytest.mark.asyncio
async def test_empty_str_exception_still_yields_a_reason(exc: Exception) -> None:
    """The exact production shape: an exception whose ``str()`` is empty.

    Asserting ``str(exc) == ""`` first makes the test self-evidently about that
    property — if a future httpx gives these messages, the test says so instead
    of silently passing for the wrong reason.
    """
    assert str(exc) == "", "precondition: these httpx errors stringify to empty"

    with _patch_raising(exc):
        out = await probe_mediahub_model(_LLM_ROW)

    assert out["ok"] is False
    assert out["error"], "a failed probe must never record an empty reason"
    assert type(exc).__name__ in out["error"]


@pytest.mark.asyncio
async def test_exception_with_a_message_keeps_it() -> None:
    """A real message is preserved (and still qualified by the type name)."""
    with _patch_raising(httpx.ConnectError("All connection attempts failed")):
        out = await probe_mediahub_model(_LLM_ROW)

    assert out["ok"] is False
    assert "ConnectError" in out["error"]
    assert "All connection attempts failed" in out["error"]


@pytest.mark.asyncio
async def test_reason_is_truncated() -> None:
    """Reason still fits the 200-char ``last_test_detail`` budget."""
    with _patch_raising(RuntimeError("x" * 500)):
        out = await probe_mediahub_model(_LLM_ROW)

    assert len(out["error"]) <= 200


@pytest.mark.parametrize("row", [_LLM_ROW, _EMBEDDING_ROW])
@pytest.mark.asyncio
async def test_probe_allows_60s(row: dict) -> None:
    """20s was the prime suspect for the 2026-08-14 misjudgement (cold starts on
    a self-hosted engine routinely exceed it). A misjudged model costs far more
    than 40 extra seconds on an hourly ping."""
    with _patch_raising(httpx.ReadTimeout("")):
        await probe_mediahub_model(row)

    assert _RaisingClient.kwargs.get("timeout") == 60.0
