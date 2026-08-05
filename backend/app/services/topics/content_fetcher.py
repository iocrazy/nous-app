"""L0.5 content enrichment — fetch the real article body for curated news.

NewsNow hands us a title + a URL only; the body is empty. A 22-char headline is
too thin to score credibility/depth or to summarize honestly (the model ends up
paraphrasing the title). For curated, article-bearing sources (tier ≤ tier_max)
we fetch the page and extract clean main text with trafilatura, backfill
``content_original``, and clear the embedding so it re-embeds on the richer text.

This runs ONLY on tier ≤ tier_max sources because tier-3 social hot-lists
(Weibo/Douyin/…) point at search/topic aggregation pages, not articles — there's
no body to extract there.

Admin-tunable via ``system_settings['topics.content_fetch']``. Disabled by
default so it's an explicit opt-in after deploy (and a one-flip kill switch).
NEVER reads env.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Optional

from loguru import logger

CONTENT_FETCH_CONFIG_KEY = "topics.content_fetch"

# Trafilatura's own fetch is blocking (urllib) — run it off the event loop.
# Bound text length so a runaway page can't bloat a row / the embedder input.
_MAX_BODY_CHARS = 4000

# Site-chrome markers. On JS-rendered pages (e.g. cls.cn 财联社) trafilatura can
# latch onto the nav/footer shell instead of the article ("关于我们 / 网站声明 /
# 联系方式 / …"). favor_precision avoids most of it; this is the belt-and-braces
# guard — ≥2 markers in the extracted text = it's chrome, not an article → drop,
# so navigation junk never pollutes the embedding/score.
_BOILERPLATE_MARKERS = ("网站声明", "联系方式", "网站地图", "用户反馈", "关于我们")


def _looks_like_boilerplate(text: str) -> bool:
    return sum(1 for m in _BOILERPLATE_MARKERS if m in text) >= 2


@dataclass(frozen=True)
class ContentFetchConfig:
    """Admin-tunable content-enrichment knobs."""

    enabled: bool
    tier_max: int  # only fetch bodies for sources at/below this tier
    max_items: int  # items per pass
    concurrency: int  # parallel fetches (politeness bound)
    timeout_s: int  # per-request timeout
    min_chars: int  # extracted text shorter than this is discarded (junk page)


def default_content_fetch_config() -> ContentFetchConfig:
    return ContentFetchConfig(
        enabled=False,
        tier_max=2,
        max_items=40,
        concurrency=4,
        timeout_s=15,
        min_chars=80,
    )


def merge_content_fetch_config(raw: Any) -> ContentFetchConfig:
    """Merge an admin jsonb blob over code defaults. Never raises; each field
    falls back to its default on bad/missing input. Numeric knobs are clamped to
    sane bounds so a fat-fingered value can't hammer a site or stall the loop."""
    d = default_content_fetch_config()
    if not isinstance(raw, dict):
        return d

    def _int(key: str, lo: int, hi: int, default: int) -> int:
        v = raw.get(key)
        return max(lo, min(hi, int(v))) if isinstance(v, (int, float)) else default

    enabled = raw.get("enabled")
    return ContentFetchConfig(
        enabled=bool(enabled) if isinstance(enabled, bool) else d.enabled,
        tier_max=_int("tier_max", 1, 4, d.tier_max),
        max_items=_int("max_items", 1, 200, d.max_items),
        concurrency=_int("concurrency", 1, 16, d.concurrency),
        timeout_s=_int("timeout_s", 3, 60, d.timeout_s),
        min_chars=_int("min_chars", 1, 2000, d.min_chars),
    )


def content_fetch_payload(cfg: Optional[ContentFetchConfig] = None) -> dict[str, Any]:
    """Serialize to the admin GET/PUT jsonb shape."""
    c = cfg or default_content_fetch_config()
    return {
        "enabled": c.enabled,
        "tier_max": c.tier_max,
        "max_items": c.max_items,
        "concurrency": c.concurrency,
        "timeout_s": c.timeout_s,
        "min_chars": c.min_chars,
    }


async def load_content_fetch_config() -> ContentFetchConfig:
    """Admin-tuned config from ``system_settings``, merged over code defaults.
    Service-role engine read. Never raises — failure/missing key → defaults
    (which are disabled, so a config-read failure can't start fetching)."""
    try:
        from sqlalchemy import select

        from app.db import engine as db_engine
        from app.db.session import read_scope
        from app.models import SystemSettings

        if not db_engine.is_configured():
            return default_content_fetch_config()
        async with read_scope() as session:
            raw = await session.scalar(
                select(SystemSettings.value).where(
                    SystemSettings.key == CONTENT_FETCH_CONFIG_KEY
                )
            )
        return merge_content_fetch_config(raw)
    except Exception:  # noqa: BLE001
        logger.warning("[content-fetch] config read failed — using code defaults")
        return default_content_fetch_config()


def _extract_sync(url: str) -> Optional[str]:
    """Blocking fetch + extract (runs in a worker thread). Returns clean main
    text, or None when the page can't be fetched/parsed. Never raises."""
    try:
        import trafilatura

        html = trafilatura.fetch_url(url)
        if not html:
            return None
        # favor_precision: prefer the article body over sidebars/nav — without it
        # JS-heavy pages (cls.cn) yield the nav shell.
        text = trafilatura.extract(
            html,
            include_comments=False,
            include_tables=False,
            favor_precision=True,
        )
        text = (text or "").strip()[:_MAX_BODY_CHARS]
        if not text or _looks_like_boilerplate(text):
            return None
        return text
    except Exception as e:  # noqa: BLE001 — best-effort enrichment
        logger.debug(f"[content-fetch] extract failed for {url}: {e}")
        return None


async def fetch_article_text(url: str, *, timeout_s: int = 15) -> Optional[str]:
    """Async wrapper: fetch + extract one article's body off the event loop.
    Returns clean text or None. Times out gracefully."""
    if not url or not url.startswith("http"):
        return None
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_extract_sync, url), timeout=timeout_s
        )
    except (asyncio.TimeoutError, Exception):  # noqa: BLE001
        return None
