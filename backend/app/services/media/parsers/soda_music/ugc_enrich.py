"""Best-effort douyin enrichment of qishui UGC videos.

A qishui UGC video's ``video_id`` IS a valid douyin aweme_id. The qishui share
page carries NO engagement stats / publish time, but the canonical douyin VIDEO
PAGE (``https://www.douyin.com/video/<video_id>``) sometimes does.

This module fetches that video page with a MOBILE iPhone Safari UA and NO cookie
(a cookie 302-redirects), scrapes the ``_ROUTER_DATA`` hydration blob, navigates
to the aweme item, and merges the missing engagement stats + publish time into
``parsed_data``.

**Intermittent by nature.** Douyin's anti-scrape is probabilistic: sometimes it
serves a clean HTML page whose ``_ROUTER_DATA`` carries the full stats, other
times it serves a JS-VM-protected page (contains ``jsvmprt``, no parseable
``_ROUTER_DATA``). So enrichment is strictly best-effort — stats get populated
when douyin serves the clean page and stay hidden otherwise. That is expected,
not a bug.

This helper NEVER raises and NEVER fails the UGC parse/download. On any failure
(JS-protected page, HTTP error, oversize, parse miss) it returns the input
``parsed_data`` unchanged.

The earlier ``IesDouyinParser._fetch_share_page`` approach (iesdouyin.com/share)
is now reliably JS-VM-protected by douyin and always failed — it was dropped in
favour of the direct video-page fetch above.
"""

from __future__ import annotations

import html as _html
import json
import re
from datetime import datetime, timezone
from typing import Any

from loguru import logger

from app.boundary import safe_async_client

# Reuse the soda_api string-aware brace-balanced JSON scanner — it already
# handles _ROUTER_DATA blobs robustly (string literals, escapes, decoy objects).
from app.services.media.parsers.soda_music.soda_api import _balanced_json_object

DOUYIN_VIDEO_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 "
    "Safari/604.1"
)
DOUYIN_VIDEO_BASE = "https://www.douyin.com/video"
MAX_DOUYIN_PAGE_BYTES = 8 * 1024 * 1024

# Same pattern soda_api uses to find _ROUTER_DATA assignments.
_ROUTER_DATA_ASSIGN = re.compile(r"_ROUTER_DATA\s*=\s*\{")


def extract_douyin_aweme_item(html: str) -> dict[str, Any] | None:
    """Pull the aweme item from a douyin video page's ``_ROUTER_DATA``.

    Navigates ``loaderData`` → the (dynamic-keyed) value that holds
    ``videoInfoRes.item_list`` → ``item_list[0]``. The page key is dynamic
    (e.g. ``"video_(<id>)/page"``), so we scan every dict value under
    ``loaderData`` rather than assuming a fixed key. Returns ``None`` on the
    JS-protected page (no ``_ROUTER_DATA``) or any parse failure.
    """
    try:
        for m in _ROUTER_DATA_ASSIGN.finditer(html):
            brace = m.end() - 1  # the '{' the regex matched
            blob = _balanced_json_object(html, brace)
            if not blob:
                continue
            try:
                data = json.loads(blob)
            except json.JSONDecodeError:
                try:
                    data = json.loads(_html.unescape(blob))
                except json.JSONDecodeError:
                    continue
            if not isinstance(data, dict):
                continue
            loader_data = data.get("loaderData") or {}
            if not isinstance(loader_data, dict):
                continue
            for v in loader_data.values():
                if isinstance(v, dict):
                    items = ((v.get("videoInfoRes") or {}).get("item_list")) or []
                    if (
                        items
                        and isinstance(items[0], dict)
                        and items[0].get("aweme_id")
                    ):
                        return items[0]
        return None
    except (ValueError, KeyError, TypeError):
        return None


async def _fetch_douyin_video_page(video_id: str) -> str | None:
    """GET the douyin video page (mobile UA, NO cookie — a cookie 302s).

    Capped at ``MAX_DOUYIN_PAGE_BYTES``. Returns the HTML text, or ``None`` on
    any HTTP failure / oversize. Outbound goes through the SSRF-safe boundary
    client (same as ``get_ugc_video``).
    """
    url = f"{DOUYIN_VIDEO_BASE}/{video_id}"
    async with safe_async_client() as client:
        resp = await client.get(
            url, headers={"User-Agent": DOUYIN_VIDEO_UA}, timeout=15
        )
        resp.raise_for_status()
        cl = (getattr(resp, "headers", {}) or {}).get("content-length")
        if cl and int(cl) > MAX_DOUYIN_PAGE_BYTES:
            return None
        text = resp.text
    if len(text.encode("utf-8", "ignore")) > MAX_DOUYIN_PAGE_BYTES:
        return None
    return text


async def enrich_ugc_with_douyin_stats(
    parsed_data: dict[str, Any],
    *,
    video_id: str,
    user_id: str | None = None,
    html_fetcher: Any | None = None,
) -> dict[str, Any]:
    """Best-effort: fetch the douyin video page by the (qishui==douyin)
    aweme_id and merge engagement stats + publish time the qishui share page
    lacks. Returns a NEW dict (immutable).

    NEVER raises — returns ``parsed_data`` unchanged on any failure (douyin
    anti-scrape JS page, HTTP error, parse miss). Intermittent by nature:
    stats populate only when douyin serves the clean page.

    ``user_id`` is accepted for caller-signature stability but UNUSED — the
    douyin video page must be fetched WITHOUT a cookie (a cookie 302-redirects).
    ``html_fetcher`` is injectable for tests: async ``(video_id) -> str | None``.
    """
    try:
        fetch = html_fetcher or _fetch_douyin_video_page
        html = await fetch(video_id)
        if not html:
            return parsed_data
        item = extract_douyin_aweme_item(html)
        if not item:
            return parsed_data

        out = dict(parsed_data)

        stats = item.get("statistics") or {}
        _set_if_present(out, "like_count", stats.get("digg_count"))
        _set_if_present(out, "comment_count", stats.get("comment_count"))
        _set_if_present(out, "share_count", stats.get("share_count"))
        _set_if_present(out, "favorite_count", stats.get("collect_count"))

        create_time = item.get("create_time")
        if create_time:
            try:
                out["published_at"] = datetime.fromtimestamp(
                    int(create_time), tz=timezone.utc
                )
            except (TypeError, ValueError, OSError):
                # Bad/overflowing timestamp — leave published_at untouched.
                pass

        return out
    except Exception as exc:  # noqa: BLE001 — best-effort, never fail the parse.
        logger.warning(f"[ugc_enrich] douyin enrichment skipped for {video_id}: {exc}")
        return parsed_data


def _set_if_present(out: dict[str, Any], key: str, value: Any) -> None:
    """Set ``out[key] = value`` only when ``value is not None``."""
    if value is not None:
        out[key] = value
