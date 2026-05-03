"""D4 — active remember() tool: validation + handler."""
from __future__ import annotations

import pytest

from app.services.memory.active_remember import (
    ALLOWED_SCOPES,
    DEFAULT_SCOPE,
    MAX_SUMMARY_LEN,
    MAX_WHEN_LEN,
    MIN_SUMMARY_LEN,
    MIN_WHEN_LEN,
    RememberContext,
    RememberRequest,
    RememberResult,
    RememberValidationError,
    handle_remember,
    validate_remember_args,
)


# ─── validate_remember_args ──────────────────────────────────────────


@pytest.mark.unit
def test_valid_args_normalized():
    req = validate_remember_args(
        summary="user prefers dark mode",
        when_to_use="when discussing UI preferences",
    )
    assert req.summary == "user prefers dark mode"
    assert req.when_to_use == "when discussing UI preferences"
    assert req.scope == DEFAULT_SCOPE
    assert req.extracted_from == "active_call"


@pytest.mark.unit
def test_strips_whitespace():
    req = validate_remember_args(
        summary="  trimmed  ",
        when_to_use="  also trimmed  ",
    )
    assert req.summary == "trimmed"
    assert req.when_to_use == "also trimmed"


@pytest.mark.unit
def test_summary_too_short_rejected():
    with pytest.raises(RememberValidationError, match="too short"):
        validate_remember_args(summary="hi", when_to_use="when discussing X")


@pytest.mark.unit
def test_summary_too_long_rejected():
    with pytest.raises(RememberValidationError, match="too long"):
        validate_remember_args(
            summary="x" * (MAX_SUMMARY_LEN + 1),
            when_to_use="when discussing X",
        )


@pytest.mark.unit
def test_when_to_use_too_short_rejected():
    with pytest.raises(RememberValidationError, match="too short"):
        validate_remember_args(summary="user prefers X", when_to_use="x")


@pytest.mark.unit
def test_when_to_use_too_long_rejected():
    with pytest.raises(RememberValidationError, match="too long"):
        validate_remember_args(
            summary="user prefers X",
            when_to_use="x" * (MAX_WHEN_LEN + 1),
        )


@pytest.mark.unit
def test_invalid_scope_rejected():
    with pytest.raises(RememberValidationError, match="scope"):
        validate_remember_args(
            summary="user prefers X",
            when_to_use="when discussing X",
            scope="not_a_real_scope",
        )


@pytest.mark.unit
def test_all_allowed_scopes_accepted():
    for scope in ALLOWED_SCOPES:
        req = validate_remember_args(
            summary="user prefers X",
            when_to_use="when discussing X",
            scope=scope,
        )
        assert req.scope == scope


@pytest.mark.unit
def test_non_string_inputs_rejected():
    with pytest.raises(RememberValidationError):
        validate_remember_args(summary=123, when_to_use="when X")  # type: ignore[arg-type]
    with pytest.raises(RememberValidationError):
        validate_remember_args(summary="x" * 50, when_to_use=None)  # type: ignore[arg-type]


# ─── handle_remember ──────────────────────────────────────────────────


_CTX = RememberContext(
    agent_id="00000000-0000-0000-0000-000000000001",
    user_id="00000000-0000-0000-0000-000000000002",
    session_id="00000000-0000-0000-0000-000000000003",
)


@pytest.mark.asyncio
async def test_handler_success():
    async def _persistor(request, context):
        return "new-mem-uuid-123"

    result = await handle_remember(
        summary="user prefers Vue",
        when_to_use="when discussing frontend frameworks",
        context=_CTX,
        persistor=_persistor,
    )
    assert isinstance(result, RememberResult)
    assert result.success is True
    assert result.memory_id == "new-mem-uuid-123"
    assert result.error is None


@pytest.mark.asyncio
async def test_handler_validation_failure_returns_error():
    """Validation error → success=False, error populated. NEVER raises."""
    async def _persistor(request, context):
        raise AssertionError("persistor should not be called on validation failure")

    result = await handle_remember(
        summary="hi",  # too short
        when_to_use="when discussing X",
        context=_CTX,
        persistor=_persistor,
    )
    assert result.success is False
    assert "too short" in (result.error or "")


@pytest.mark.asyncio
async def test_handler_persistor_failure_caught():
    async def _broken(request, context):
        raise RuntimeError("DB explosion")

    result = await handle_remember(
        summary="user prefers Vue",
        when_to_use="when discussing frontend frameworks",
        context=_CTX,
        persistor=_broken,
    )
    assert result.success is False
    assert "DB explosion" in (result.error or "")


@pytest.mark.asyncio
async def test_handler_persistor_returns_none_treated_as_failure():
    async def _silent(request, context):
        return None

    result = await handle_remember(
        summary="user prefers Vue",
        when_to_use="when discussing frontend frameworks",
        context=_CTX,
        persistor=_silent,
    )
    assert result.success is False


@pytest.mark.asyncio
async def test_handler_passes_request_through_to_persistor():
    captured = {}

    async def _capture(request, context):
        captured["req"] = request
        captured["ctx"] = context
        return "id-1"

    await handle_remember(
        summary="user prefers Vue",
        when_to_use="when discussing frontend frameworks",
        scope="user_global",
        context=_CTX,
        persistor=_capture,
    )
    assert isinstance(captured["req"], RememberRequest)
    assert captured["req"].scope == "user_global"
    assert captured["req"].extracted_from == "active_call"
    assert captured["ctx"].agent_id == _CTX.agent_id


# ─── Constants sanity ─────────────────────────────────────────────────


@pytest.mark.unit
def test_constants_consistent():
    assert MIN_SUMMARY_LEN < MAX_SUMMARY_LEN
    assert MIN_WHEN_LEN < MAX_WHEN_LEN
    assert DEFAULT_SCOPE in ALLOWED_SCOPES
