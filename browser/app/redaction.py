"""Scrubbing for anything that leaves the process (HTTP bodies, logs).

Discipline (spec 7.6): plaintext session material lives in memory only. The
practical leak is not the storage_state field itself - nobody serialises that
by hand - it is a proxy URL with embedded credentials showing up inside a
Playwright error string, which then gets copied into `message`.
"""

from __future__ import annotations

import re

# scheme://user:password@host  ->  scheme://***:***@host
_CREDENTIALED_URL = re.compile(r"(?P<scheme>[a-zA-Z][a-zA-Z0-9+.\-]*://)[^/\s:@]+:[^/\s@]+@")

MAX_MESSAGE_LEN = 400


def redact_url_credentials(text: str) -> str:
    """Replace `user:pass@` in any URL-ish substring with `***:***@`."""
    return _CREDENTIALED_URL.sub(lambda m: f"{m.group('scheme')}***:***@", text)


def scrub(text: str, *, max_len: int = MAX_MESSAGE_LEN) -> str:
    """Make an arbitrary (often exception-derived) string safe to return.

    Redacts embedded credentials and truncates, so a stack-trace-sized
    Playwright error cannot smuggle page content into an API response.
    """
    if not text:
        return ""
    cleaned = redact_url_credentials(" ".join(text.split()))
    if len(cleaned) > max_len:
        return cleaned[: max_len - 1] + "…"
    return cleaned
