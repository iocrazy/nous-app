"""Timing-safe secret comparison contract."""
from __future__ import annotations

import pytest

from app.boundary.errors import SecretMismatchError
from app.boundary.secret_compare import compare_secret, require_secret


@pytest.mark.unit
def test_equal_strings_return_true():
    assert compare_secret("sk-abc123def456", "sk-abc123def456") is True


@pytest.mark.unit
def test_unequal_strings_return_false():
    assert compare_secret("sk-abc123def456", "sk-XYZ123def456") is False


@pytest.mark.unit
def test_different_length_returns_false():
    assert compare_secret("short", "muchlongerstring") is False


@pytest.mark.unit
def test_none_actual_returns_false():
    assert compare_secret(None, "anything") is False


@pytest.mark.unit
def test_none_candidate_returns_false():
    assert compare_secret("anything", None) is False


@pytest.mark.unit
def test_both_none_returns_false():
    """None == None could be True semantically, but we treat it as 'no
    comparison made' which is False to fail closed."""
    assert compare_secret(None, None) is False


@pytest.mark.unit
def test_bytes_input_supported():
    assert compare_secret(b"abc", b"abc") is True
    assert compare_secret(b"abc", b"xyz") is False


@pytest.mark.unit
def test_str_bytes_mixed_supported():
    assert compare_secret("abc", b"abc") is True
    assert compare_secret(b"abc", "abc") is True


@pytest.mark.unit
def test_empty_strings_equal():
    assert compare_secret("", "") is True


@pytest.mark.unit
def test_require_secret_raises_on_mismatch():
    with pytest.raises(SecretMismatchError):
        require_secret("a", "b")


@pytest.mark.unit
def test_require_secret_silent_on_match():
    require_secret("a", "a")  # no raise
