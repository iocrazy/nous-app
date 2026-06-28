import pytest

from app.services.topics.adapters.base import HotspotCandidate
from app.services.topics.adapters.registry import get_adapter
from app.services.topics.adapters.rss_adapter import RssAdapter
from app.services.topics.keyword_filter import (
    PrefilterConfig,
    default_prefilter_config,
    keyword_filter,
    merge_prefilter_config,
    prefilter_payload,
    relevance_filter,
)


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


def test_prefilter_disabled_passes_everything_even_tier3():
    # media operator turns the gate off → noisy tier-3 items all flow in
    cfg = PrefilterConfig(enabled=False, keywords=("英伟达",), tier_from=3)
    cands = [_c("维尼修斯进球"), _c("金价大跌"), _c("英伟达新芯片")]
    assert len(relevance_filter(cands, tier=3, config=cfg)) == 3


def test_prefilter_custom_keywords_for_media():
    # swap AI keywords for media ones → entertainment items survive on tier-3
    cfg = PrefilterConfig(enabled=True, keywords=("综艺", "明星"), tier_from=3)
    cands = [_c("某综艺官宣阵容"), _c("英伟达新芯片")]
    out = relevance_filter(cands, tier=3, config=cfg)
    assert [c.title for c in out] == ["某综艺官宣阵容"]


def test_prefilter_tier_from_controls_scope():
    # raise tier_from to 4 → tier-3 sources no longer gated (nothing reaches 4)
    cfg = PrefilterConfig(enabled=True, keywords=("英伟达",), tier_from=4)
    assert len(relevance_filter([_c("金价大跌")], tier=3, config=cfg)) == 1


def test_merge_prefilter_config_semantics():
    # absent keywords → AI default; explicit empty list → no keyword gate
    assert merge_prefilter_config({}).keywords == default_prefilter_config().keywords
    assert merge_prefilter_config({"keywords": []}).keywords == ()
    # enabled coerced, tier_from clamped to 1..4, garbage → defaults
    c = merge_prefilter_config({"enabled": False, "keywords": ["综艺"], "tier_from": 2})
    assert c.enabled is False and c.keywords == ("综艺",) and c.tier_from == 2
    assert merge_prefilter_config({"tier_from": 99}).tier_from == 3
    assert merge_prefilter_config("nope").enabled is True


def test_prefilter_payload_roundtrips():
    payload = prefilter_payload(default_prefilter_config())
    assert set(payload.keys()) == {"enabled", "keywords", "tier_from"}
    assert (
        merge_prefilter_config(payload).keywords == default_prefilter_config().keywords
    )


def test_registry_returns_rss():
    assert isinstance(get_adapter("rss"), RssAdapter)


def test_registry_unknown_raises():
    with pytest.raises(ValueError):
        get_adapter("nope")
