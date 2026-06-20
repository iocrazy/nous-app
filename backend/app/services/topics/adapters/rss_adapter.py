from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from time import mktime

import feedparser
import httpx

from app.services.topics.adapters.base import HotspotCandidate, SourceAdapter


class RssAdapter(SourceAdapter):
    async def _get_text(self, url: str, timeout: float) -> str:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.get(url, timeout=timeout)
            resp.raise_for_status()
            return resp.text

    async def fetch(self, source: dict) -> list[HotspotCandidate]:
        cfg = source.get("config") or {}
        url = cfg.get("url")
        if not url:
            raise ValueError("rss source missing config.url")
        text = await self._get_text(url, timeout=15.0)
        parsed = await asyncio.to_thread(feedparser.parse, text)
        label = f"{source.get('name', 'RSS')} (RSS)"
        out: list[HotspotCandidate] = []
        for e in parsed.entries:
            title = (getattr(e, "title", "") or "").strip()
            if not title:
                continue
            captured = None
            if getattr(e, "published_parsed", None):
                captured = datetime.fromtimestamp(
                    mktime(e.published_parsed), tz=timezone.utc
                )
            out.append(
                HotspotCandidate(
                    title=title,
                    url=getattr(e, "link", None),
                    content=(getattr(e, "summary", "") or "")[:5000],
                    source_label=label,
                    captured_at=captured,
                )
            )
        if not out:
            raise ValueError(f"rss source produced 0 items: {url}")
        return out
