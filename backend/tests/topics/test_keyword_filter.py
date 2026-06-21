import pytest

from app.services.topics.adapters.base import HotspotCandidate
from app.services.topics.adapters.registry import get_adapter
from app.services.topics.adapters.rss_adapter import RssAdapter
from app.services.topics.keyword_filter import keyword_filter


def _c(title):
    return HotspotCandidate(title=title)


def test_include_empty_keeps_all():
    out = keyword_filter([_c("AI news"), _c("cooking")], include=[], exclude=[])
    assert len(out) == 2


def test_include_filters_by_any_match():
    out = keyword_filter(
        [_c("AI model"), _c("cooking")], include=["ai", "llm"], exclude=[]
    )
    assert [c.title for c in out] == ["AI model"]


def test_exclude_removes_match():
    out = keyword_filter(
        [_c("AI sale ad"), _c("AI paper")], include=["ai"], exclude=["sale"]
    )
    assert [c.title for c in out] == ["AI paper"]


def test_registry_returns_rss():
    assert isinstance(get_adapter("rss"), RssAdapter)


def test_registry_unknown_raises():
    with pytest.raises(ValueError):
        get_adapter("nope")
