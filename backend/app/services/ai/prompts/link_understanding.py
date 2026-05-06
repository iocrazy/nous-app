"""Link understanding — fetch a URL → structured summary.

Sprint 8 capability. When a user pastes a URL into chat ("look at this:
https://..."), the agent currently sees the literal URL string and has
to choose between (a) ignoring it, (b) inventing what's there, or (c)
calling a tool to fetch — but no fetch tool exists today.

This module gives the agent a fetch tool: a single function that goes
from URL → ``LinkSummary`` (title + cleaned body + meta tags +
neutralized excerpt safe to drop into context).

All boundary primitives are wired in:
  - validate_url_async      — SSRF + scheme + DNS rebinding (Sprint 1)
  - safe_async_client       — pinned-DNS HTTP client (Sprint 1)
  - read_with_cap           — byte cap on the response body (Sprint 7)
  - neutralize_external_text — wraps body in unforgeable marker, defangs
    instruction literals (Sprint 1)

The summary is the surface the chat layer / agent skill can inject.
This module does NOT decide HOW to inject — that's the consumer's
choice (system message side note, tool-result, or follow-up turn).

Limitations:
  - HTML only. PDF / image / video bodies return an empty body but
    keep the headers. Caller can branch on content_type.
  - Title comes from <title>; body from a naive tag stripper. We
    deliberately don't pull in BeautifulSoup / readability — heavy dep
    for marginal quality gain at this scope. Upgrade path documented.
  - No JS rendering. Sites that hydrate client-side won't have body
    content. Workaround is to use the existing DrissionPage parser
    (browser-based) for those — different module, different cost.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from loguru import logger

from app.boundary import (
    BoundaryError,
    NeutralizedText,
    aread_with_cap,
    neutralize_external_text,
    safe_async_client,
    validate_url_async,
)


# Tight cap — most useful pages render readable text in well under
# 2 MiB. If you hit a 3 MiB blog post, that's an outlier; tighten the
# cap rather than relax it to keep this surface predictable.
DEFAULT_FETCH_CAP_BYTES = 2 * 1024 * 1024  # 2 MiB

# Soft cap on the body that gets neutralized + injected into context.
# 8 KiB ≈ 2k tokens — fits comfortably in a turn without dominating it.
DEFAULT_BODY_EXCERPT_CHARS = 8 * 1024


# ─── Tag stripping ────────────────────────────────────────────────────


_SCRIPT_OR_STYLE = re.compile(
    r"<(script|style)\b.*?</\1>", re.IGNORECASE | re.DOTALL
)
_TAG = re.compile(r"<[^>]+>")
_WHITESPACE = re.compile(r"\s+")
_TITLE_TAG = re.compile(
    r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL
)
_META_TAG = re.compile(
    r"<meta\b[^>]*>", re.IGNORECASE
)
_META_NAME_CONTENT = re.compile(
    r"""(name|property)\s*=\s*['"]([^'"]+)['"]""", re.IGNORECASE
)
_META_CONTENT_VALUE = re.compile(
    r"""content\s*=\s*['"]([^'"]+)['"]""", re.IGNORECASE
)


def _strip_html(html: str) -> str:
    """Naive but adequate text extraction. Drops <script>/<style>
    bodies entirely, then strips remaining tags + collapses whitespace."""
    no_scripts = _SCRIPT_OR_STYLE.sub(" ", html)
    no_tags = _TAG.sub(" ", no_scripts)
    return _WHITESPACE.sub(" ", no_tags).strip()


def _extract_title(html: str) -> Optional[str]:
    m = _TITLE_TAG.search(html)
    if not m:
        return None
    raw = _WHITESPACE.sub(" ", m.group(1)).strip()
    return raw or None


def _extract_meta(html: str) -> dict[str, str]:
    """Collect <meta name=X content=Y> + <meta property=X content=Y>.
    Last value wins on duplicate keys (common in OpenGraph)."""
    out: dict[str, str] = {}
    for m in _META_TAG.finditer(html):
        tag = m.group(0)
        name_match = _META_NAME_CONTENT.search(tag)
        content_match = _META_CONTENT_VALUE.search(tag)
        if name_match and content_match:
            out[name_match.group(2)] = content_match.group(1)
    return out


# ─── Public surface ───────────────────────────────────────────────────


class LinkUnderstandingError(BoundaryError):
    """Raised when a URL fetch fails for a reason worth surfacing
    to the agent (4xx, 5xx, body-cap exceeded, validate_url rejection).
    Subclass of BoundaryError so existing edge handlers map to HTTP 400."""


@dataclass(frozen=True)
class LinkSummary:
    """Structured view of a fetched URL. Drop ``neutralized.wrapped``
    into the prompt as-is for safe injection."""

    url: str
    status_code: int
    content_type: Optional[str]
    title: Optional[str]
    description: Optional[str]  # convenience: meta og:description / description
    meta: dict[str, str] = field(default_factory=dict)
    body_text: str = ""  # cleaned, NOT neutralized — for caller-side processing
    neutralized: Optional[NeutralizedText] = None  # ready-to-prompt wrapper

    def to_summary_dict(self) -> dict:
        """Compact JSON-friendly form for telemetry / agent_runs metadata."""
        return {
            "url": self.url,
            "status_code": self.status_code,
            "content_type": self.content_type,
            "title": self.title,
            "description": self.description,
            "body_chars": len(self.body_text),
        }


async def understand_link(
    url: str,
    *,
    fetch_cap_bytes: int = DEFAULT_FETCH_CAP_BYTES,
    body_excerpt_chars: int = DEFAULT_BODY_EXCERPT_CHARS,
    timeout_s: float = 15.0,
) -> LinkSummary:
    """Validate, fetch, parse a URL → LinkSummary.

    Returns a populated LinkSummary on success.
    Raises LinkUnderstandingError on validation rejection / HTTP failure
    / oversized body.
    """
    # Boundary: SSRF + DNS-rebind + scheme allowlist
    try:
        validated = await validate_url_async(url)
    except BoundaryError as exc:
        # Re-raise as our own type so the caller has one error class to catch.
        raise LinkUnderstandingError(f"URL rejected at boundary: {exc}") from exc

    try:
        async with safe_async_client(timeout=timeout_s) as client:
            response = await client.get(str(validated))
    except Exception as exc:  # noqa: BLE001 — httpx + boundary errors collapse here
        logger.warning("[link_understanding] fetch failed: %r", exc)
        raise LinkUnderstandingError(f"fetch failed: {type(exc).__name__}") from exc

    if response.status_code >= 400:
        raise LinkUnderstandingError(
            f"upstream returned HTTP {response.status_code}"
        )

    content_type = response.headers.get("content-type")

    # Body cap. read_with_cap reads N+1 to detect overrun deterministically;
    # we already have the response body in httpx, so we apply the cap manually.
    body_bytes = response.content
    if len(body_bytes) > fetch_cap_bytes:
        raise LinkUnderstandingError(
            f"response body exceeded byte cap ({len(body_bytes)} > {fetch_cap_bytes})"
        )

    # Decode. httpx exposes .text but uses chardet — we want strict UTF-8
    # with replacement for invalid bytes (don't trust upstream charset).
    try:
        body = body_bytes.decode("utf-8", errors="replace")
    except (UnicodeDecodeError, LookupError):
        body = ""

    # Non-HTML content_types: skip parsing, return headers only.
    is_html = content_type is not None and "html" in content_type.lower()
    if not is_html:
        return LinkSummary(
            url=str(validated),
            status_code=response.status_code,
            content_type=content_type,
            title=None,
            description=None,
            meta={},
            body_text="",
            neutralized=None,
        )

    title = _extract_title(body)
    meta = _extract_meta(body)
    description = meta.get("description") or meta.get("og:description")
    cleaned = _strip_html(body)

    excerpt = cleaned[:body_excerpt_chars]
    neutralized = neutralize_external_text(excerpt, max_chars=body_excerpt_chars)

    return LinkSummary(
        url=str(validated),
        status_code=response.status_code,
        content_type=content_type,
        title=title,
        description=description,
        meta=meta,
        body_text=cleaned,
        neutralized=neutralized,
    )


__all__ = [
    "DEFAULT_BODY_EXCERPT_CHARS",
    "DEFAULT_FETCH_CAP_BYTES",
    "LinkSummary",
    "LinkUnderstandingError",
    "understand_link",
]
