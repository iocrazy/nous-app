"""Trust boundary layer.

See docs/architecture/boundary-layer.md for the contract.

Every untrusted external value crossing into MediaHub business logic
must pass through a validator in this package first.
"""
from app.boundary.errors import (
    BoundaryError,
    ExternalTextRejectedError,
    SecretMismatchError,
    URLBlockedError,
)
from app.boundary.types import ValidatedURL

__all__ = [
    "BoundaryError",
    "ExternalTextRejectedError",
    "SecretMismatchError",
    "URLBlockedError",
    "ValidatedURL",
]
