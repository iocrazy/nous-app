"""Link-injection middleware — pull URLs out of user messages, fetch +
neutralize via :mod:`link_understanding`, return ready-to-prompt blocks.

Sprint 8.5 wire-up. Sprint 8 landed ``understand_link`` but no caller
wired it. The chat path saw URLs as opaque strings; the agent had to
either ignore them, hallucinate the contents, or fail the turn.

This module adds the *detection + budget + dispatch* layer that sits
between the user's raw message and the chat service:

  1. URL-detect: regex over the latest user turn(s).
  2. De-dup + cap (default 3 URLs per turn — beyond that we return
     a notice, not a flood of fetches).
  3. Concurrent fetch via ``understand_link`` with a per-call timeout.
  4. Convert each successful summary into a system-side context block
     using the boundary's neutralized envelope.
  5. Failures (4xx/5xx, byte cap, boundary reject) get a one-line
     placeholder block — the agent should know "we tried but couldn't
     read this URL" rather than think the URL was empty.

The output is a list of strings the chat composer can drop into its
``request_instructions`` field (one block per URL). The composer
already wraps request_instructions in a section header that's outside
the cache boundary — link injection is naturally per-turn-mutable.

This module is deliberately a primitive (pure functions + dataclass
return). Whether to call it on every turn / only when URL-detected /
behind a feature flag is left to the route caller (chat service).
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Iterable, Optional

from loguru import logger

from app.services.link_understanding import (
    LinkSummary,
    LinkUnderstandingError,
    understand_link,
)


# ─── URL extraction ───────────────────────────────────────────────────


# Conservative URL regex — http(s) only, requires scheme. Markdown
# escaping ([text](url)), bare URLs, and URLs followed by punctuation
# all extract correctly because the trailing-punctuation strip below
# normalises the captured group.
_URL_REGEX = re.compile(
    r"https?://[^\s<>\"'`{}|\\^[\]]+",
    re.IGNORECASE,
)
# Punctuation that's commonly mistaken as part of a URL when it's
# really sentence punctuation. Stripped from the right edge of each
# match so "see https://x.com/foo." doesn't try to fetch
# "https://x.com/foo." and 404 on the period.
_TRAILING_PUNCT = ".,;:!?)]}>'\""


def extract_urls(
    text: str, *, max_urls: int = 3, dedupe: bool = True
) -> list[str]:
    """Extract up to ``max_urls`` URLs from ``text`` in source order.

    De-duplicates by exact URL string when ``dedupe`` is True.
    Trailing sentence punctuation is stripped from each URL.
    """
    if not text:
        return []
    raw = _URL_REGEX.findall(text)
    out: list[str] = []
    seen: set[str] = set()
    for url in raw:
        cleaned = url.rstrip(_TRAILING_PUNCT)
        if not cleaned:
            continue
        if dedupe and cleaned in seen:
            continue
        seen.add(cleaned)
        out.append(cleaned)
        if len(out) >= max_urls:
            break
    return out


def extract_urls_from_messages(
    messages: Iterable[dict],
    *,
    max_urls: int = 3,
    only_role: Optional[str] = "user",
    only_last: bool = True,
) -> list[str]:
    """Walk a chat-style message list and extract URLs.

    Defaults are tuned for "look at the most recent user turn only" —
    the typical chat-service signal. Set ``only_last=False`` to scan
    the whole conversation, or ``only_role=None`` to scan everything.
    """
    msgs = list(messages)
    if only_role is not None:
        msgs = [m for m in msgs if m.get("role") == only_role]
    if only_last and msgs:
        msgs = [msgs[-1]]
    seen: set[str] = set()
    out: list[str] = []
    for m in msgs:
        for url in extract_urls(
            m.get("content") or "",
            max_urls=max_urls,
            dedupe=False,
        ):
            if url in seen:
                continue
            seen.add(url)
            out.append(url)
            if len(out) >= max_urls:
                return out
    return out


# ─── Block rendering ──────────────────────────────────────────────────


_OK_TEMPLATE = """\
[link-summary url={url}]
title: {title}
description: {description}
content: {content}
[/link-summary]
"""

_ERR_TEMPLATE = """\
[link-summary url={url} error=true]
We tried to fetch this URL but failed: {reason}.
The agent should not invent its contents.
[/link-summary]
"""


def render_block(summary: LinkSummary) -> str:
    """Convert a successful LinkSummary into a system-side text block
    ready to drop into the prompt's request_instructions section.

    The neutralized excerpt's wrapper protects against prompt-injection
    inside the page body. Title + description live OUTSIDE the wrapper
    because they're already short-summarised by the upstream site;
    putting them in the wrapper would just waste tokens.
    """
    content = (
        summary.neutralized.wrapped if summary.neutralized else "(no body extracted)"
    )
    return _OK_TEMPLATE.format(
        url=summary.url,
        title=summary.title or "(no title)",
        description=summary.description or "(none)",
        content=content,
    )


def render_failure_block(url: str, reason: str) -> str:
    """Placeholder for a URL we couldn't fetch. Telling the agent
    explicitly is much safer than dropping the URL silently — silent
    drops invite hallucination."""
    # Truncate reason so an attacker's extra-long error message can't
    # blow up the prompt; first line + 200 chars is plenty.
    line = reason.splitlines()[0] if reason else "unknown"
    return _ERR_TEMPLATE.format(url=url, reason=line[:200])


# ─── Aggregator ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class LinkInjectionResult:
    """All URLs we tried + their outcome blocks, ready to inject."""

    urls: list[str] = field(default_factory=list)
    blocks: list[str] = field(default_factory=list)
    failures: list[tuple[str, str]] = field(default_factory=list)  # (url, reason)

    @property
    def joined(self) -> str:
        """Single string with two-newline separators; empty when no URLs."""
        return "\n\n".join(self.blocks)

    @property
    def has_content(self) -> bool:
        return bool(self.blocks)


async def fetch_and_render(
    urls: list[str],
    *,
    per_url_timeout_s: float = 15.0,
    overall_timeout_s: Optional[float] = 30.0,
) -> LinkInjectionResult:
    """Fetch each URL concurrently, render an inject-ready block per URL.

    Failures get a placeholder block (so the agent knows we tried) —
    they do NOT abort the whole batch.

    ``overall_timeout_s=None`` disables the wall-clock cap (per-URL
    timeout still applies). Default 30s = 2× the per-URL timeout to
    leave headroom for the slowest one.
    """
    if not urls:
        return LinkInjectionResult()

    async def _one(url: str) -> tuple[str, str | None, str | None]:
        """Returns (url, block, failure_reason). Exactly one of
        block/failure_reason is non-None."""
        try:
            summary = await understand_link(url, timeout_s=per_url_timeout_s)
            return (url, render_block(summary), None)
        except LinkUnderstandingError as exc:
            reason = f"{type(exc).__name__}: {exc}"
            return (url, render_failure_block(url, reason), reason)
        except Exception as exc:  # noqa: BLE001 — last-resort guard
            logger.exception("[link_injection] unexpected fetch failure")
            reason = f"{type(exc).__name__}: {exc}"
            return (url, render_failure_block(url, reason), reason)

    tasks = [asyncio.create_task(_one(u)) for u in urls]
    try:
        if overall_timeout_s is not None:
            done = await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=False),
                timeout=overall_timeout_s,
            )
        else:
            done = await asyncio.gather(*tasks)
    except asyncio.TimeoutError:
        # Cancel anything still running so we don't leak background work,
        # then collect whatever DID complete.
        for t in tasks:
            if not t.done():
                t.cancel()
        done = []
        for t in tasks:
            try:
                done.append(t.result())
            except (asyncio.CancelledError, BaseException):
                # Treat the cancelled URL as a timeout failure
                pass

    blocks: list[str] = []
    failures: list[tuple[str, str]] = []
    for url, block, reason in done:
        blocks.append(block)
        if reason is not None:
            failures.append((url, reason))
    return LinkInjectionResult(urls=urls, blocks=blocks, failures=failures)


__all__ = [
    "LinkInjectionResult",
    "extract_urls",
    "extract_urls_from_messages",
    "fetch_and_render",
    "render_block",
    "render_failure_block",
]
