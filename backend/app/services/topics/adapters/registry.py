from __future__ import annotations

from app.services.topics.adapters.base import SourceAdapter
from app.services.topics.adapters.newsnow_adapter import NewsNowAdapter
from app.services.topics.adapters.rss_adapter import RssAdapter


def get_adapter(kind: str) -> SourceAdapter:
    if kind == "newsnow":
        return NewsNowAdapter()
    if kind == "rss":
        return RssAdapter()
    raise ValueError(f"unsupported source kind: {kind}")
