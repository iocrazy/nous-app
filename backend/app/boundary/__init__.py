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
from app.boundary.pinned_dns import PinnedDNSResolver
from app.boundary.safe_http import SafeAsyncClient, safe_async_client
from app.boundary.secret_compare import compare_secret, require_secret
from app.boundary.ssrf_proxy import SsrfProxy
from app.boundary.types import ValidatedURL
from app.boundary.url_guard import validate_url, validate_url_async

__all__ = [
    "BoundaryError",
    "ExternalTextRejectedError",
    "PinnedDNSResolver",
    "SafeAsyncClient",
    "SecretMismatchError",
    "SsrfProxy",
    "URLBlockedError",
    "ValidatedURL",
    "compare_secret",
    "require_secret",
    "safe_async_client",
    "validate_url",
    "validate_url_async",
]
