"""Timing-safe secret comparison.

Use everywhere a secret-shaped value is compared with ``==`` (API keys,
webhook signatures, JWT HMAC, temporary tokens). Naive ``==`` leaks a
timing oracle: an attacker can probe character-by-character because
string comparison short-circuits on the first mismatch.

Wraps ``hmac.compare_digest`` which compares in constant time relative
to the shorter input.

See docs/architecture/boundary-layer.md.
"""
from __future__ import annotations

import hmac

from app.boundary.errors import SecretMismatchError


def compare_secret(actual: str | bytes | None, candidate: str | bytes | None) -> bool:
    """Return True iff ``actual`` and ``candidate`` are equal.

    Both args may be str or bytes; mixed types compared as bytes via UTF-8.
    None inputs return False without comparison (caller responsibility to
    decide whether None is allowed).

    Constant-time relative to len(min(actual, candidate)) — note that
    length itself can leak via this function, so for length-sensitive
    secrets (rare) the caller must pad inputs to a fixed size first.
    """
    if actual is None or candidate is None:
        return False
    a = actual.encode("utf-8") if isinstance(actual, str) else actual
    b = candidate.encode("utf-8") if isinstance(candidate, str) else candidate
    return hmac.compare_digest(a, b)


def require_secret(actual: str | bytes | None, candidate: str | bytes | None) -> None:
    """Raise SecretMismatchError if compare_secret returns False.

    Convenience for call sites that want exception-flow rather than
    boolean return. Does NOT log either side."""
    if not compare_secret(actual, candidate):
        raise SecretMismatchError("secret mismatch")
