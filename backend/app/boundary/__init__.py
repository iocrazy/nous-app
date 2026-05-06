"""Trust boundary layer.

See docs/architecture/boundary-layer.md for the contract.

Every untrusted external value crossing into MediaHub business logic
must pass through a validator in this package first.
"""

from app.boundary.errors import (
    BoundaryError,
    ExternalTextRejectedError,
    MaxBytesExceededError,
    PathTraversalError,
    RegexComplexityError,
    SecretMismatchError,
    URLBlockedError,
)
from app.boundary.external_text import (
    NeutralizedText,
    neutralize_external_text,
)
from app.boundary.log_redact import make_loguru_patcher, redact
from app.boundary.max_bytes import (
    DEFAULT_MAX_BYTES,
    aread_with_cap,
    cap_aiter,
    cap_iter,
    read_with_cap,
)
from app.boundary.path_guard import is_safe_path, safe_resolve
from app.boundary.pinned_dns import PinnedDNSResolver
from app.boundary.safe_http import SafeAsyncClient, safe_async_client
from app.boundary.safe_regex import compile_safe, search_safe, vet_pattern
from app.boundary.secret_compare import compare_secret, require_secret
from app.boundary.ssrf_proxy import SsrfProxy
from app.boundary.types import ValidatedURL
from app.boundary.url_guard import validate_url, validate_url_async

__all__ = [
    "DEFAULT_MAX_BYTES",
    "BoundaryError",
    "ExternalTextRejectedError",
    "MaxBytesExceededError",
    "NeutralizedText",
    "PathTraversalError",
    "PinnedDNSResolver",
    "RegexComplexityError",
    "SafeAsyncClient",
    "SecretMismatchError",
    "SsrfProxy",
    "URLBlockedError",
    "ValidatedURL",
    "aread_with_cap",
    "cap_aiter",
    "cap_iter",
    "compare_secret",
    "compile_safe",
    "is_safe_path",
    "make_loguru_patcher",
    "neutralize_external_text",
    "read_with_cap",
    "redact",
    "require_secret",
    "safe_async_client",
    "safe_resolve",
    "search_safe",
    "validate_url",
    "validate_url_async",
    "vet_pattern",
]
