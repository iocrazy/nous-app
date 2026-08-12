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

# Text lifted off a page gets a longer budget than an error message: it is read
# by a human trying to work out what the platform was showing, and 400 chars of
# a verification screen is one sentence.
MAX_PAGE_TEXT_LEN = 600

# Digit runs at least this long are masked out of page-derived text. Five sits
# below both things that must never be recorded — a 6-digit verification code
# and an 11-digit phone number, the two pieces of the login screen that are
# actually secret — and above the numbers that carry diagnostic meaning
# (a countdown, "2/3", a truncated year).
_MIN_MASKED_DIGITS = 5
_DIGIT_RUN = re.compile(r"\d{%d,}" % _MIN_MASKED_DIGITS)


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


def mask_digit_runs(text: str) -> str:
    """`138****1234` for anything with five or more digits in a row."""
    return _DIGIT_RUN.sub(lambda m: "*" * len(m.group(0)), text)


def scrub_page_text(text: str, *, max_len: int = MAX_PAGE_TEXT_LEN) -> str:
    """`scrub`, plus digit masking, for text copied off a live page.

    Diagnostics captured from a login screen are a different risk from an
    exception string: the page is *the platform's*, and the two numbers it puts
    on screen during a verification step are exactly the two that must not be
    recorded — the code that was texted, and the phone number it went to. Both
    survive `scrub` untouched (it only knows about credentialed URLs), so the
    masking lives here rather than in the caller, where a second capture site
    would eventually forget it.

    Masking runs **before** truncation on purpose: truncating first can cut a
    phone number in half and leave a four-digit tail below the mask threshold.
    """
    if not text:
        return ""
    return scrub(mask_digit_runs(text), max_len=max_len)
