"""log_redact — auto-mask secret-shaped tokens at log write time.

Defense against accidental secret leakage via loguru. Patterns require a
KNOWN prefix (sk-, Bearer, JWT eyJ…, KEY=…) and a minimum length so
random IDs (UUIDs, Snowflake BIGINTs, commit hashes) don't get wrongly
masked.
"""
from __future__ import annotations

import pytest

from app.boundary.log_redact import (
    make_loguru_patcher,
    redact,
)


# ============================================================================
# Positive — known secret shapes ARE redacted
# ============================================================================

@pytest.mark.unit
@pytest.mark.parametrize(
    "raw,must_contain,must_not_contain",
    [
        # sk- API key (Anthropic / OpenAI / Doubao convention)
        ("api key sk-1234567890abcdefghij used", "sk-***",
         "sk-1234567890abcdefghij"),
        # Bearer token
        ("Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.payload12345.sig123abc",
         "Bearer ***",
         "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.payload12345.sig123abc"),
        # Authorization header
        ("Authorization: Bearer abc123def456ghi789jklmnop",
         "Authorization: Bearer ***",
         "abc123def456ghi789jklmnop"),
        # query string token
        ("token=ya29.a0AfH6SMBxxxxxxxxxxxxxxxx&other=value",
         "token=***",
         "ya29.a0AfH6SMBxxxxxxxxxxxxxxxx"),
        # KEY=value env style (uppercase prefix that ends with KEY/TOKEN/SECRET)
        ("DOUBAO_API_KEY=sk-doubao-very-secret-key-xxxxxxxxxxxx",
         "DOUBAO_API_KEY=***",
         "sk-doubao-very-secret-key-xxxxxxxxxxxx"),
        # JWT bare in body (3-segment dot-separated)
        ("Token leaked in log: eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
         ".eyJzdWIiOiIxMjM0NTY3ODkwIn0"
         ".SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c — investigate",
         "***JWT***",
         "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"),
    ],
)
def test_redacts_known_secret_shapes(raw, must_contain, must_not_contain):
    result = redact(raw)
    assert must_contain in result, (
        f"expected {must_contain!r} in {result!r}"
    )
    assert must_not_contain not in result, (
        f"original secret leaked: {must_not_contain!r} found in {result!r}"
    )


# ============================================================================
# Negative — false-positive guards (E13)
# ============================================================================

@pytest.mark.unit
def test_uuid_not_redacted():
    """UUIDs must NOT be redacted (no known secret prefix)."""
    raw = "user uuid: 550e8400-e29b-41d4-a716-446655440000 logged in"
    assert redact(raw) == raw


@pytest.mark.unit
def test_snowflake_bigint_not_redacted():
    """Snowflake BIGINTs (mediahub uses these everywhere as IDs) must
    NOT be redacted."""
    raw = "resource_id=1234567890123456789 was deleted"
    assert redact(raw) == raw


@pytest.mark.unit
def test_commit_hash_not_redacted():
    """Git commit SHAs (40 hex chars) — random looking but not secret."""
    raw = "deploy commit 3d626f4072e1aabbcc1234deadbeef0011223344 succeeded"
    assert redact(raw) == raw


@pytest.mark.unit
def test_short_alphanumeric_not_redacted():
    """Random short identifiers (under min threshold) — too noisy
    to redact. Must pass through."""
    raw = "video id: abc123 platform: yt"
    assert redact(raw) == raw


@pytest.mark.unit
def test_normal_log_text_untouched():
    raw = "user clicked download for video xyz at 12:34:56"
    assert redact(raw) == raw


@pytest.mark.unit
def test_version_number_not_redacted():
    """version.module.commit pattern (looks like JWT 3-segment but
    short) — needs min length per segment to qualify as JWT."""
    raw = "version 1.2.3 module foo.bar commit abc.def.ghi shipped"
    assert redact(raw) == raw


# ============================================================================
# Edge cases
# ============================================================================

@pytest.mark.unit
def test_empty_string_untouched():
    assert redact("") == ""


@pytest.mark.unit
def test_non_string_passthrough():
    assert redact(None) is None  # type: ignore[arg-type]
    assert redact(42) == 42  # type: ignore[arg-type]
    assert redact({"key": "value"}) == {"key": "value"}  # type: ignore[arg-type]


# ============================================================================
# Loguru patcher — message + extra walk (E6)
# ============================================================================

@pytest.mark.unit
def test_patcher_redacts_record_message():
    patcher = make_loguru_patcher()
    secret_key = "sk-abc12345678ghi90jklmnopqrstuvwx"  # 31 chars after sk-
    record = {
        "message": f"API key {secret_key} was logged",
        "extra": {},
    }
    patcher(record)
    assert "sk-***" in record["message"]
    assert secret_key not in record["message"]


@pytest.mark.unit
def test_patcher_redacts_record_extra_strings():
    """E6: logger.bind(token='Bearer xxx').info('event') puts the
    token in record['extra'], not record['message']. The patcher MUST
    walk extra and redact string values too — else bind context leaks."""
    patcher = make_loguru_patcher()
    record = {
        "message": "request processed",
        "extra": {
            "user_token": "Bearer abc123def456ghi789jklmnop",
            "request_id": "req-12345",  # short, normal id — not redacted
            "depth": 3,  # non-string — passthrough
        },
    }
    patcher(record)
    assert "Bearer ***" in record["extra"]["user_token"]
    assert "abc123def456ghi789jklmnop" not in record["extra"]["user_token"]
    assert record["extra"]["request_id"] == "req-12345"  # untouched
    assert record["extra"]["depth"] == 3  # untouched


@pytest.mark.unit
def test_patcher_handles_nested_extra_dict_shallowly():
    """For now, extra walking is one level (no recursion into nested
    dicts). Document the boundary so a contributor doesn't add a
    secret in a nested dict and assume it's protected."""
    patcher = make_loguru_patcher()
    record = {
        "message": "x",
        "extra": {
            "outer": "sk-must-be-redacted-12345678",
            "nested": {"inner_token": "Bearer should-not-yet-be-redacted-12345"},
        },
    }
    patcher(record)
    assert "sk-***" in record["extra"]["outer"]
    # Nested dict NOT walked (current contract)
    assert "Bearer should-not-yet-be-redacted-12345" in str(record["extra"]["nested"])


@pytest.mark.unit
def test_patcher_no_extra_no_crash():
    patcher = make_loguru_patcher()
    record = {"message": "no extra here"}
    patcher(record)  # must not raise
    assert record["message"] == "no extra here"
