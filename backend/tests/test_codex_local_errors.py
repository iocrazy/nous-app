"""codex-local failures are typed end to end: non-retryable for the retry
middleware, classified by the error catalog, mapped to a user message."""

from __future__ import annotations

import pytest

from app.core.provider_errors import provider_error_payload, stream_error_data
from app.services.ai import error_catalog
from app.services.ai.llm.llm_retry_middleware import LLMCallError, classify_error
from app.services.codex.errors import CodexLocalError


@pytest.mark.unit
@pytest.mark.parametrize(
    "code,status",
    [
        ("daemon_offline", 424),
        ("timeout", 424),
        ("tools_unsupported", 422),
        ("codex_not_logged_in", 401),
        ("cli_missing", 424),
        ("codex_no_output", 424),
        ("codex_failed", 424),
        ("ref_rejected", 400),
        # 426 Upgrade Required — 4xx like the rest, so an out-of-date daemon
        # can never silently fall through to a paid model.
        ("daemon_outdated", 426),
    ],
)
def test_every_code_is_non_retryable(code, status):
    exc = CodexLocalError(code, "boom")
    assert exc.status_code == status
    assert classify_error(exc) == "non_retryable"
    assert str(exc).startswith(f"[codex-local:{code}]")


@pytest.mark.unit
def test_catalog_classifies_from_message_marker():
    assert (
        error_catalog.classify_ai_error(CodexLocalError("daemon_offline", "x"))
        == error_catalog.LOCAL_DAEMON_OFFLINE
    )
    # Each cause keeps its own code: "install the CLI", "wait and retry" and
    # "start your daemon" are three different user actions, so one shared
    # code would make two of the three messages a false statement.
    assert (
        error_catalog.classify_ai_error(CodexLocalError("cli_missing", "x"))
        == error_catalog.LOCAL_CLI_MISSING
    )
    # timeout reuses the existing catalog code rather than minting a
    # codex-local twin of advice that already exists and is translated.
    assert (
        error_catalog.classify_ai_error(CodexLocalError("timeout", "x"))
        == error_catalog.TASK_TIMEOUT
    )
    assert (
        error_catalog.classify_ai_error(CodexLocalError("ref_rejected", "x"))
        == error_catalog.LOCAL_REF_REJECTED
    )
    assert (
        error_catalog.classify_ai_error(CodexLocalError("tools_unsupported", "x"))
        == error_catalog.LOCAL_TOOLS_UNSUPPORTED
    )
    assert (
        error_catalog.classify_ai_error(CodexLocalError("codex_not_logged_in", "x"))
        == error_catalog.LOCAL_CODEX_NOT_LOGGED_IN
    )
    assert (
        error_catalog.classify_ai_error(CodexLocalError("codex_failed", "x"))
        == error_catalog.LOCAL_CODEX_FAILED
    )
    assert (
        error_catalog.classify_ai_error(CodexLocalError("daemon_outdated", "x"))
        == error_catalog.LOCAL_DAEMON_OUTDATED
    )
    # the marker survives the LLMCallError wrap the middleware applies
    wrapped = LLMCallError("non-retryable: [codex-local:daemon_offline] x")
    assert (
        error_catalog.classify_ai_error(wrapped) == error_catalog.LOCAL_DAEMON_OFFLINE
    )


@pytest.mark.unit
def test_payload_and_stream_data_carry_the_code():
    exc = LLMCallError("non-retryable: [codex-local:daemon_offline] x")
    status, code, message = provider_error_payload(exc)
    assert (status, code) == (424, "local_daemon_offline")
    assert "daemon" in message.lower()
    assert stream_error_data(exc) == {"error": message, "code": "local_daemon_offline"}


@pytest.mark.unit
@pytest.mark.parametrize(
    "code,status,http_code",
    [
        ("daemon_offline", 424, "local_daemon_offline"),
        ("cli_missing", 424, "local_cli_missing"),
        ("timeout", 504, "task_timeout"),
        ("tools_unsupported", 422, "local_tools_unsupported"),
        ("codex_not_logged_in", 401, "local_codex_not_logged_in"),
        ("ref_rejected", 400, "local_ref_rejected"),
        ("codex_no_output", 424, "local_codex_failed"),
        ("codex_failed", 424, "local_codex_failed"),
        ("daemon_outdated", 426, "local_daemon_outdated"),
    ],
)
def test_a_bare_error_keeps_its_code_without_the_middleware_wrap(
    code, status, http_code
):
    """``tools_unsupported`` is raised by the adapter BEFORE any dispatch, so a
    call site that awaits ``adapter.call()`` directly never gets the
    ``LLMCallError`` wrap. The typed face must not depend on that wrap —
    without ``CodexLocalError`` in the isinstance tuple every one of these
    degrades to a 500 ``internal_error``."""
    exc = CodexLocalError(code, "x")
    assert provider_error_payload(exc)[:2] == (status, http_code)
    assert stream_error_data(exc)["code"] == http_code
