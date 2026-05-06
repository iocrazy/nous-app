"""Boundary error hierarchy contract."""
from __future__ import annotations

import pytest

from app.boundary.errors import (
    BoundaryError,
    ExternalTextRejectedError,
    SecretMismatchError,
    URLBlockedError,
)


@pytest.mark.unit
def test_url_blocked_is_boundary_error():
    err = URLBlockedError("blocked: 192.168.1.1")
    assert isinstance(err, BoundaryError)
    assert "192.168.1.1" in str(err)


@pytest.mark.unit
def test_external_text_rejected_is_boundary_error():
    err = ExternalTextRejectedError("too large: 200000 chars")
    assert isinstance(err, BoundaryError)


@pytest.mark.unit
def test_secret_mismatch_is_boundary_error():
    err = SecretMismatchError()
    assert isinstance(err, BoundaryError)


@pytest.mark.unit
def test_subclasses_distinct():
    assert URLBlockedError is not ExternalTextRejectedError
    assert SecretMismatchError is not URLBlockedError
    assert SecretMismatchError is not ExternalTextRejectedError
