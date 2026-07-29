"""app.services.ai.error_catalog — raw failures → stable error codes.

Every sample below is the shape a real provider / SDK / engine produces,
not a synthetic phrase, because the whole point of the catalog is that it
survives contact with the strings users actually hit. The DBOS cases pin
the property that made this necessary at all: ``str(exc)`` on
``DBOSMaxStepRetriesExceeded`` carries no provider information, so
classification must walk ``.errors``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.ai.error_catalog import (
    INTERNAL,
    OUTPUT_PARSE,
    PROVIDER_AUTH,
    PROVIDER_BAD_MODEL,
    PROVIDER_RATE_LIMIT,
    PROVIDER_UNREACHABLE,
    TASK_TIMEOUT,
    classify_ai_error,
    record_ai_error_code,
)


class _StatusError(Exception):
    """Stand-in for openai.APIStatusError / httpx.HTTPStatusError."""

    def __init__(self, message: str, status_code: int):
        super().__init__(message)
        self.status_code = status_code


class _FakeMaxRetries(Exception):
    """Same surface as dbos.DBOSMaxStepRetriesExceeded: opaque message,
    real causes in ``.errors``."""

    def __init__(self, step_name: str, max_retries: int, errors: list[Exception]):
        self.errors = errors
        super().__init__(
            f"Step {step_name} has exceeded its maximum of {max_retries} retries"
        )


# ── PROVIDER_AUTH ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "message",
    [
        "Error code: 401 - {'error': {'message': 'Incorrect API key provided: "
        "sk-abc***. You can find your API key at https://platform.openai.com/"
        "account/api-keys.', 'type': 'invalid_request_error', 'param': None, "
        "'code': 'invalid_api_key'}}",
        "AuthenticationError: Error code: 401 - {'error': {'code': "
        "'InvalidApiKey', 'message': 'Incorrect API key provided.'}}",
        "Error code: 403 - {'error': {'message': 'Unauthorized'}}",
    ],
)
def test_auth_messages(message):
    assert classify_ai_error(message) == PROVIDER_AUTH


def test_auth_from_status_code_without_matching_text():
    """A bare 401 with an unhelpful body still classifies — the SDK
    exception's status_code is read before any text matching."""
    assert classify_ai_error(_StatusError("request failed", 401)) == PROVIDER_AUTH


# ── PROVIDER_RATE_LIMIT ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "message",
    [
        "Error code: 429 - {'error': {'message': 'Rate limit reached for "
        "gpt-4o in organization org-x on requests per min (RPM): Limit 500, "
        "Used 500.', 'type': 'requests', 'code': 'rate_limit_exceeded'}}",
        "Error code: 429 - {'error': {'message': 'You exceeded your current "
        "quota, please check your plan and billing details.', 'code': "
        "'insufficient_quota'}}",
    ],
)
def test_rate_limit_messages(message):
    assert classify_ai_error(message) == PROVIDER_RATE_LIMIT


# ── PROVIDER_UNREACHABLE ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "message",
    [
        "APIConnectionError: Connection error. "
        "[Errno 111] Connect call failed ('10.0.0.10', 11434)",
        "httpx.ConnectError: [Errno -2] Name or service not known",
        "Error code: 503 - {'error': {'message': 'Service Unavailable'}}",
        "httpx.ConnectTimeout: timed out",
    ],
)
def test_unreachable_messages(message):
    assert classify_ai_error(message) == PROVIDER_UNREACHABLE


def test_connect_timeout_beats_generic_timeout():
    """A *connect* timeout is a reachability problem, not a task that ran
    too long — the ordering between the two rules is load-bearing."""
    assert (
        classify_ai_error("ConnectTimeout: timeout connecting to api.provider.com")
        == PROVIDER_UNREACHABLE
    )


# ── PROVIDER_BAD_MODEL ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "message",
    [
        "Error code: 404 - {'error': {'message': 'The model `qwen-vl-max-2099` "
        "does not exist or you do not have access to it.', 'type': "
        "'invalid_request_error', 'code': 'model_not_found'}}",
        "NotFoundError: model 'llava:34b' not found, try pulling it first",
        "Error code: 404 - {'error': {'message': \"no active grant for "
        "service 'seedream' on this key\"}}",
    ],
)
def test_bad_model_messages(message):
    assert classify_ai_error(message) == PROVIDER_BAD_MODEL


# ── OUTPUT_PARSE ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "message",
    [
        "json.decoder.JSONDecodeError: Expecting value: line 1 column 1 (char 0)",
        "RuntimeError: caption agent returned neither an EN nor a ZH prompt",
        "ValueError: Failed to parse structured prompt payload — invalid JSON",
    ],
)
def test_output_parse_messages(message):
    assert classify_ai_error(message) == OUTPUT_PARSE


# ── TASK_TIMEOUT ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "message",
    [
        "asyncio.exceptions.TimeoutError",
        "httpx.ReadTimeout: The read operation timed out",
    ],
)
def test_task_timeout_messages(message):
    assert classify_ai_error(message) == TASK_TIMEOUT


# ── Unknown / no signal ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "   ",
        "RuntimeError: resource 9000000000000000001 not found",
        "sqlalchemy.exc.IntegrityError: duplicate key value violates unique constraint",
    ],
)
def test_unknown_returns_none(value):
    """No confident match writes NO code, so the UI keeps showing the raw
    error instead of a generic catch-all that hides information."""
    assert classify_ai_error(value) is None


def test_internal_is_available_as_an_explicit_fallback():
    """INTERNAL exists for callers that want a catch-all; classification
    itself never invents it."""
    assert INTERNAL == "INTERNAL"
    assert classify_ai_error("something nobody has a rule for") is None


# ── DBOS wrapping ─────────────────────────────────────────────────────


def test_dbos_wrapper_alone_classifies_as_nothing():
    """The exact string users were shown before this catalog existed."""
    exc = _FakeMaxRetries("ai_caption_via_provider", 3, [])
    assert "exceeded its maximum of 3 retries" in str(exc)
    assert classify_ai_error(exc) is None


def test_dbos_wrapper_unwraps_to_the_underlying_provider_error():
    exc = _FakeMaxRetries(
        "ai_caption_via_provider",
        3,
        [
            Exception("Error code: 401 - {'error': {'code': 'invalid_api_key'}}"),
            Exception("Error code: 401 - {'error': {'code': 'invalid_api_key'}}"),
        ],
    )
    assert classify_ai_error(exc) == PROVIDER_AUTH


def test_dbos_wrapper_unwraps_status_code_bearing_inner_error():
    exc = _FakeMaxRetries("ai_caption_via_provider", 3, [_StatusError("nope", 429)])
    assert classify_ai_error(exc) == PROVIDER_RATE_LIMIT


def test_raise_from_chain_is_followed():
    try:
        try:
            raise _StatusError("Error code: 401 - invalid api key", 401)
        except Exception as inner:
            raise RuntimeError("prompt generation produced no result") from inner
    except RuntimeError as outer:
        assert classify_ai_error(outer) == PROVIDER_AUTH


def test_self_referential_chain_terminates():
    a = RuntimeError("a")
    b = RuntimeError("b")
    a.__context__ = b
    b.__context__ = a
    assert classify_ai_error(a) is None


# ── record_ai_error_code ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_record_writes_error_code_into_metadata_only():
    mgr = MagicMock()
    mgr.patch_metadata = AsyncMock(return_value=None)
    with patch(
        "app.services.infra.unified_task_manager.get_task_manager", return_value=mgr
    ):
        code = await record_ai_error_code(
            "wf-1", _FakeMaxRetries("s", 3, [_StatusError("x", 401)])
        )
    assert code == PROVIDER_AUTH
    mgr.patch_metadata.assert_awaited_once_with("wf-1", {"error_code": PROVIDER_AUTH})


@pytest.mark.asyncio
async def test_record_skips_unclassifiable_errors_without_touching_the_row():
    mgr = MagicMock()
    mgr.patch_metadata = AsyncMock(return_value=None)
    with patch(
        "app.services.infra.unified_task_manager.get_task_manager", return_value=mgr
    ):
        assert (
            await record_ai_error_code("wf-1", RuntimeError("no rule for this")) is None
        )
    mgr.patch_metadata.assert_not_awaited()


@pytest.mark.asyncio
async def test_record_swallows_write_failures():
    """It runs on an already-failing path; it must not become a second,
    different failure."""
    mgr = MagicMock()
    mgr.patch_metadata = AsyncMock(side_effect=RuntimeError("db down"))
    with patch(
        "app.services.infra.unified_task_manager.get_task_manager", return_value=mgr
    ):
        assert await record_ai_error_code("wf-1", _StatusError("x", 401)) is None


@pytest.mark.asyncio
async def test_record_without_workflow_id_is_a_noop():
    assert await record_ai_error_code(None, _StatusError("x", 401)) is None
