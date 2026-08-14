# backend/tests/test_mediahub_probe_reason_code.py

"""F1 — the probe must classify a failure into a CLOSED enum, not free text.

#1838 made the red light visible; it did not make it actionable. On 2026-08-14
two failing models needed opposite responses from the user — ``nous-qwen3-llm``
was a ``ReadTimeout`` (a local engine loading, usually nothing to do) while
``mediahub-doubao-seed-2-0-pro`` was ``HTTP 429: SetLimitExceeded`` (quota, go
fix it) — and the UI said the same thing about both.

The raw reason cannot simply be forwarded: it routinely embeds the upstream
host, the private ``base_url`` and the upstream model id, and the public
``GET /api/v1/ai/mediahub-models`` is served to every user. A closed enum is
the one shape that carries the actionable part and cannot carry a secret,
because it is not built from the message at all — it is derived from the
exception TYPE and the HTTP status the probe already holds.

These tests are the table from the design doc, one row at a time.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.services.ai.mediahub_model_health import (
    classify_probe_failure,
    probe_mediahub_model,
)

# --------------------------------------------------------------------------
# The judgement table (design §1), asserted directly on the pure function.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "exc",
    [
        httpx.ReadTimeout(""),
        httpx.ConnectTimeout(""),
        httpx.WriteTimeout(""),
        httpx.PoolTimeout(""),
    ],
)
def test_every_httpx_timeout_subclass_is_timeout(exc: Exception) -> None:
    """All four timeout flavours, not just the ReadTimeout we happened to see:
    they are one user-facing situation ("it did not answer in time")."""
    assert classify_probe_failure(status_code=None, exc=exc) == "timeout"


@pytest.mark.parametrize(
    "exc",
    [
        httpx.ConnectError("All connection attempts failed"),
        httpx.UnsupportedProtocol("Request URL is missing an 'http://'"),
        httpx.ReadError(""),
        httpx.RemoteProtocolError("server disconnected"),
        httpx.ProxyError("bad proxy"),
    ],
)
def test_other_transport_errors_are_unreachable(exc: Exception) -> None:
    """Everything else below the HTTP layer means "we never got an answer".

    ``UnsupportedProtocol`` is in here on purpose: it is what a CLI-backed
    model with no ``base_url`` produces (the probe builds ``/chat/completions``
    onto an empty string), and its message is the one that carried a URL
    fragment into the DB on 2026-08-14.
    """
    assert classify_probe_failure(status_code=None, exc=exc) == "unreachable"


@pytest.mark.parametrize("status", [401, 403])
def test_auth_statuses(status: int) -> None:
    assert classify_probe_failure(status_code=status, exc=None) == "auth"


def test_429_is_rate_limit() -> None:
    """The 2026-08-14 doubao row — the one failure a user can actually act on."""
    assert classify_probe_failure(status_code=429, exc=None) == "rate_limit"


def test_404_is_model_not_found() -> None:
    assert classify_probe_failure(status_code=404, exc=None) == "model_not_found"


@pytest.mark.parametrize("status", [400, 402, 422, 500, 502, 503])
def test_other_error_statuses_are_upstream_error(status: int) -> None:
    assert classify_probe_failure(status_code=status, exc=None) == "upstream_error"


def test_bad_response_is_its_own_code() -> None:
    """HTTP 200 with a body the probe cannot read as a completion/vector.

    Distinct from ``upstream_error`` because the actionable part differs: the
    endpoint answered, so credentials and quota are fine — the model or the
    protocol is wrong.
    """
    assert classify_probe_failure(status_code=200, exc=None, bad_response=True) == (
        "bad_response"
    )


def test_unknown_exception_is_other() -> None:
    assert classify_probe_failure(status_code=None, exc=ValueError("boom")) == "other"


def test_no_signal_at_all_is_other() -> None:
    """The asr branch's shape: a failure with neither a status nor an exception."""
    assert classify_probe_failure(status_code=None, exc=None) == "other"


def test_exception_outranks_a_stale_status() -> None:
    """Defensive ordering: an exception means the request did not complete, so
    any status handed in alongside it cannot describe this attempt."""
    assert (
        classify_probe_failure(status_code=200, exc=httpx.ReadTimeout("")) == "timeout"
    )


def test_classifier_never_invents_a_code_outside_the_enum() -> None:
    """The whole security argument rests on the return being a closed set."""
    from app.services.ai.mediahub_model_health import PROBE_FAILURE_CODES

    samples = [
        classify_probe_failure(status_code=None, exc=httpx.ReadTimeout("")),
        classify_probe_failure(status_code=None, exc=httpx.ConnectError("")),
        classify_probe_failure(status_code=401, exc=None),
        classify_probe_failure(status_code=429, exc=None),
        classify_probe_failure(status_code=404, exc=None),
        classify_probe_failure(status_code=418, exc=None),
        classify_probe_failure(status_code=200, exc=None, bad_response=True),
        classify_probe_failure(status_code=None, exc=None),
    ]
    assert set(samples) <= set(PROBE_FAILURE_CODES)
    assert set(samples) == set(
        PROBE_FAILURE_CODES
    ), "the table should exercise every code in the enum"


def test_a_code_never_carries_free_text() -> None:
    """A message full of secrets must not survive into the code — the enum is
    the reason this feature is safe to show a user at all."""
    leaky = httpx.ConnectError(
        "Connection refused to http://10.0.0.10:9997/v1 (key sk-abcdef)"
    )
    code = classify_probe_failure(status_code=None, exc=leaky)
    assert code == "unreachable"
    assert "10.0.0.10" not in code and "sk-" not in code


# --------------------------------------------------------------------------
# The probe wires the code through every failure exit (and never on success).
# --------------------------------------------------------------------------


class _RaisingClient:
    _exc: Exception = RuntimeError("unset")

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, headers=None, json=None):
        raise _RaisingClient._exc


class _RespondingClient:
    """Returns a preset status + JSON body instead of raising."""

    _status: int = 200
    _body: dict = {}

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, headers=None, json=None):
        return httpx.Response(
            _RespondingClient._status,
            json=_RespondingClient._body,
            request=httpx.Request("POST", "https://example.test/probe"),
        )


def _patch_raising(exc: Exception):
    _RaisingClient._exc = exc
    return patch(
        "app.services.ai.mediahub_model_health.httpx.AsyncClient", _RaisingClient
    )


def _patch_responding(status: int, body: dict):
    _RespondingClient._status = status
    _RespondingClient._body = body
    return patch(
        "app.services.ai.mediahub_model_health.httpx.AsyncClient", _RespondingClient
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

_ASR_ROW = {
    "type": "asr",
    "actual_provider": "volcengine",
    "actual_model": "bigmodel",
    "base_url": "https://openspeech.bytedance.com",
    "api_key": "k",
    "app_id": "a",
}


@pytest.mark.asyncio
async def test_transport_failure_carries_a_code() -> None:
    with _patch_raising(httpx.ReadTimeout("")):
        out = await probe_mediahub_model(_LLM_ROW)
    assert out["ok"] is False
    assert out["code"] == "timeout"


@pytest.mark.asyncio
async def test_chat_http_error_carries_the_status_code() -> None:
    """The 2026-08-14 doubao case end to end."""
    with _patch_responding(429, {"error": "SetLimitExceeded"}):
        out = await probe_mediahub_model(_LLM_ROW)
    assert out["ok"] is False
    assert out["code"] == "rate_limit"


@pytest.mark.asyncio
async def test_embedding_http_error_carries_the_status_code() -> None:
    with _patch_responding(401, {"error": "bad key"}):
        out = await probe_mediahub_model(_EMBEDDING_ROW)
    assert out["ok"] is False
    assert out["code"] == "auth"


@pytest.mark.asyncio
async def test_chat_200_without_choices_is_bad_response() -> None:
    with _patch_responding(200, {"id": "x"}):
        out = await probe_mediahub_model(_LLM_ROW)
    assert out["ok"] is False
    assert out["code"] == "bad_response"


@pytest.mark.asyncio
async def test_embedding_200_without_a_vector_is_bad_response() -> None:
    with _patch_responding(200, {"data": [{"index": 0}]}):
        out = await probe_mediahub_model(_EMBEDDING_ROW)
    assert out["ok"] is False
    assert out["code"] == "bad_response"


@pytest.mark.asyncio
async def test_asr_failure_is_always_other() -> None:
    """asr is the ONE branch that must not be classified.

    It goes through ``AIProviderFactory.test_connection``, which returns free
    text and neither a status code nor an exception object. Guessing a code by
    substring-matching that text would be exactly the "read the message" move
    this design exists to avoid — a message that says "timeout" may be an
    upstream body being quoted, and the classifier would then be a text parser
    with a security-relevant output. Structured codes from the provider layer
    are a separate, larger change (design §3, backlog).
    """
    with patch(
        "app.services.ai.mediahub_model_health.AIProviderFactory.test_connection",
        AsyncMock(return_value={"success": False, "error": "HTTP 429 rate limited"}),
    ):
        out = await probe_mediahub_model(_ASR_ROW)
    assert out["ok"] is False
    assert out["code"] == "other", "asr must not be classified by substring"


@pytest.mark.asyncio
async def test_success_records_no_code() -> None:
    """A green result must CLEAR the code, not leave the last failure's behind —
    the column is written on every probe, so ``None`` here is what erases it."""
    with _patch_responding(200, {"choices": [{"message": {"content": "pong"}}]}):
        out = await probe_mediahub_model(_LLM_ROW)
    assert out["ok"] is True
    assert out["code"] is None


@pytest.mark.asyncio
async def test_asr_success_records_no_code() -> None:
    with patch(
        "app.services.ai.mediahub_model_health.AIProviderFactory.test_connection",
        AsyncMock(return_value={"success": True}),
    ):
        out = await probe_mediahub_model(_ASR_ROW)
    assert out["ok"] is True
    assert out["code"] is None
