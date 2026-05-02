"""Boundary error hierarchy.

Routers catch ``BoundaryError`` at the API edge and map to HTTP 400.
Never echo the raw rejected input back to the client.

See docs/architecture/boundary-layer.md for the contract.
"""
from __future__ import annotations


class BoundaryError(Exception):
    """Base for any rejection at the trust boundary."""


class URLBlockedError(BoundaryError):
    """URL failed SSRF / scheme / size policy. Map to HTTP 400."""


class ExternalTextRejectedError(BoundaryError):
    """External text failed size / content policy."""


class SecretMismatchError(BoundaryError):
    """Secret comparison failed. Caller responsible for not leaking
    which side mismatched (do not echo either back to client)."""
