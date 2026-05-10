"""ValidatedURL wrapper contract — must behave as str at runtime,
distinct identity for runtime isinstance guards."""

from __future__ import annotations

import pytest

from app.boundary.types import ValidatedURL


@pytest.mark.unit
def test_validated_url_is_str_at_runtime():
    v = ValidatedURL("https://example.com/video")
    assert isinstance(v, str)
    assert v == "https://example.com/video"
    assert v.startswith("https://")


@pytest.mark.unit
def test_validated_url_distinct_isinstance():
    """Runtime guard mechanism: services use isinstance(url, ValidatedURL)
    to reject raw str without relying on mypy."""
    raw = "https://example.com/video"
    v = ValidatedURL(raw)
    assert isinstance(v, ValidatedURL)
    # raw str fails the runtime isinstance check
    assert not isinstance(raw, ValidatedURL)


@pytest.mark.unit
def test_validated_url_repr_marks_as_validated():
    v = ValidatedURL("https://example.com")
    assert "ValidatedURL" in repr(v)
