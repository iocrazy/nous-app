import pytest

from app.services.topics.adapters.base import HotspotCandidate
from app.services.topics.adapters.registry import get_adapter
from app.services.topics.adapters.rss_adapter import RssAdapter
from app.services.topics.keyword_filter import keyword_filter, relevance_filter


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


def test_relevance_filter_gates_noisy_tier3():
    # tier 3: only AI-relevant items survive (the visible-noise case:
    # football/gold-price get dropped, the chip story stays)
    cands = [
        _c("维尼修斯能否帮巴西队走更远"),
        _c("金饰克价跌至1215元"),
        _c("英伟达发布新芯片"),
    ]
    out = relevance_filter(cands, tier=3)
    assert [c.title for c in out] == ["英伟达发布新芯片"]


def test_relevance_filter_passes_curated_tiers_untouched():
    # tier 1/2: curated AI sources are not gated — even an off-keyword item stays
    cands = [_c("A quiet model release with no buzzwords"), _c("random")]
    assert len(relevance_filter(cands, tier=1)) == 2
    assert len(relevance_filter(cands, tier=2)) == 2


def test_relevance_filter_bad_tier_defaults_to_curated():
    # unparseable tier → default 2 → pass-through (never over-drop on bad data)
    assert len(relevance_filter([_c("random")], tier=None)) == 1


def test_registry_returns_rss():
    assert isinstance(get_adapter("rss"), RssAdapter)


def test_registry_unknown_raises():
    with pytest.raises(ValueError):
        get_adapter("nope")
