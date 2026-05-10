"""Sprint 7 — safe regex (ReDoS guard)."""

from __future__ import annotations

import pytest

from app.boundary.errors import RegexComplexityError
from app.boundary.safe_regex import (
    DEFAULT_MAX_PATTERN_LEN,
    compile_safe,
    search_safe,
    vet_pattern,
)

# ─── vet_pattern (static lint) ────────────────────────────────────────


@pytest.mark.unit
def test_simple_pattern_passes():
    vet_pattern(r"hello\d+")  # no exception


@pytest.mark.unit
def test_oversized_pattern_rejected():
    big = "a" * (DEFAULT_MAX_PATTERN_LEN + 1)
    with pytest.raises(RegexComplexityError, match="length cap"):
        vet_pattern(big)


@pytest.mark.unit
def test_nested_quantifier_rejected():
    """Classic catastrophic-backtracking shape."""
    with pytest.raises(RegexComplexityError, match="backtracking"):
        vet_pattern(r"(a+)+")


@pytest.mark.unit
def test_nested_star_rejected():
    with pytest.raises(RegexComplexityError, match="backtracking"):
        vet_pattern(r"(.*)*")


@pytest.mark.unit
def test_non_string_pattern_rejected():
    with pytest.raises(RegexComplexityError, match="string"):
        vet_pattern(123)  # type: ignore[arg-type]


@pytest.mark.unit
def test_safe_quantifier_outside_group_passes():
    """``a+b*`` — quantifiers but not nested. Common log pattern."""
    vet_pattern(r"a+b*c")


# ─── compile_safe ─────────────────────────────────────────────────────


@pytest.mark.unit
def test_compile_safe_returns_pattern():
    p = compile_safe(r"\d+")
    assert p.search("abc123def").group(0) == "123"


@pytest.mark.unit
def test_compile_safe_rejects_invalid_syntax():
    """Bad regex syntax → RegexComplexityError, not a leaked re.error."""
    with pytest.raises(RegexComplexityError, match="invalid"):
        compile_safe(r"(unclosed")


@pytest.mark.unit
def test_compile_safe_rejects_dangerous_pattern():
    with pytest.raises(RegexComplexityError):
        compile_safe(r"(a+)+")


@pytest.mark.unit
def test_compile_safe_error_does_not_leak_pattern():
    """Pattern may contain caller secrets — error message must be generic."""
    secret_pattern = r"(unclosed_secret_token_abc"
    try:
        compile_safe(secret_pattern)
        pytest.fail("should have raised")
    except RegexComplexityError as exc:
        assert "abc" not in str(exc)


# ─── search_safe (runtime) ────────────────────────────────────────────


@pytest.mark.unit
def test_search_safe_returns_match():
    m = search_safe(r"\d+", "abc123")
    assert m is not None
    assert m.group(0) == "123"


@pytest.mark.unit
def test_search_safe_returns_none_on_no_match():
    assert search_safe(r"\d+", "abc") is None


@pytest.mark.unit
def test_search_safe_rejects_dangerous_pattern():
    """Static lint catches before runtime — no actual match attempt."""
    with pytest.raises(RegexComplexityError):
        search_safe(r"(a+)+", "aaaab")


@pytest.mark.unit
def test_search_safe_rejects_oversized_pattern():
    big = "a" * (DEFAULT_MAX_PATTERN_LEN + 1)
    with pytest.raises(RegexComplexityError, match="length cap"):
        search_safe(big, "anything")
