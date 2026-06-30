from app.services.topics.scoring import (
    DEFAULT_FEATURED_MIN_SCORE,
    DIM_WEIGHTS,
    TIER_WEIGHTS,
    compute_quality,
    config_payload,
    default_scoring_config,
    merge_scoring_config,
    normalize_dims,
    tier_weight,
)


def test_compute_quality_weighted_composite_tier1():
    # all dimensions 1.0, tier 1 (no discount) → 1.0
    dims = {
        k: 1.0
        for k in ("novelty", "impact", "credibility", "actionability", "shareability")
    }
    assert compute_quality(dims, tier=1) == 1.0


def test_tier_discounts_the_whole_score():
    dims = {
        k: 1.0
        for k in ("novelty", "impact", "credibility", "actionability", "shareability")
    }
    # same dims, lower tier → strictly lower quality (source prior bites)
    assert (
        compute_quality(dims, tier=3)
        < compute_quality(dims, tier=2)
        < compute_quality(dims, tier=1)
    )
    assert compute_quality(dims, tier=3) == round(TIER_WEIGHTS[3], 4)


def test_famous_but_low_value_retweet_stays_low():
    # The aihot bug: a fluff retweet from a big account. Content dims are low
    # (no novelty/impact/facts); even on a tier-2 source it can't reach featured.
    dims = {
        "novelty": 0.15,
        "impact": 0.1,
        "credibility": 0.4,
        "actionability": 0.1,
        "shareability": 0.3,
    }
    assert compute_quality(dims, tier=2) < 0.3


def test_missing_dims_count_as_zero_never_raises():
    assert compute_quality({"impact": 0.8}, tier=1) == round(0.30 * 0.8, 4)
    assert compute_quality({}, tier=1) == 0.0
    assert compute_quality(None, tier=1) == 0.0
    # garbage values coerce to 0, unknown tier falls back to default
    assert compute_quality({"impact": "x"}, tier="bad") == 0.0


def test_tier_weight_fallback():
    assert tier_weight(2) == TIER_WEIGHTS[2]
    assert tier_weight(None) == TIER_WEIGHTS[2]
    assert tier_weight(99) == TIER_WEIGHTS[2]


def test_compute_quality_honors_weight_overrides():
    dims = {"impact": 1.0, "novelty": 1.0}
    # override: all weight on impact → score = impact (tier 1)
    w = {
        "novelty": 0.0,
        "impact": 1.0,
        "credibility": 0.0,
        "actionability": 0.0,
        "shareability": 0.0,
    }
    assert compute_quality(dims, tier=1, weights=w) == 1.0
    # custom tier weights also flow through
    tw = {1: 0.5, 2: 0.5, 3: 0.5}
    assert compute_quality(dims, tier=1, weights=w, tier_weights=tw) == 0.5


def test_merge_scoring_config_overrides_and_defaults():
    cfg = merge_scoring_config(
        {
            "dim_weights": {"impact": 0.5},  # partial → other dims keep defaults
            "tier_weights": {"3": 0.7},  # str key coerced to int
            "featured_min_score": 0.8,
        }
    )
    assert cfg.dim_weights["impact"] == 0.5
    assert cfg.dim_weights["novelty"] == DIM_WEIGHTS["novelty"]  # default preserved
    assert cfg.tier_weights[3] == 0.7
    assert cfg.tier_weights[1] == TIER_WEIGHTS[1]
    assert cfg.featured_min_score == 0.8


def test_merge_scoring_config_garbage_falls_back():
    assert merge_scoring_config(None).featured_min_score == DEFAULT_FEATURED_MIN_SCORE
    assert merge_scoring_config("nope").dim_weights == dict(DIM_WEIGHTS)
    bad = merge_scoring_config(
        {"dim_weights": {"impact": "x"}, "featured_min_score": "y"}
    )
    assert bad.dim_weights["impact"] == DIM_WEIGHTS["impact"]
    assert bad.featured_min_score == DEFAULT_FEATURED_MIN_SCORE


def test_config_payload_roundtrips_through_merge():
    payload = config_payload(default_scoring_config())
    assert set(payload["tier_weights"].keys()) == {"1", "2", "3"}  # str keys for jsonb
    assert "summary_max_chars" in payload
    # payload feeds back through merge unchanged
    assert merge_scoring_config(payload).tier_weights == TIER_WEIGHTS


def test_scoring_enabled_defaults_on_and_parses():
    # master switch defaults on; explicit false is honored; garbage → default on
    assert default_scoring_config().enabled is True
    assert merge_scoring_config({"enabled": False}).enabled is False
    assert merge_scoring_config({"enabled": "no"}).enabled is True
    assert merge_scoring_config({}).enabled is True
    assert "enabled" in config_payload(default_scoring_config())


def test_summary_max_chars_parsed_clamped_and_defaulted():
    # admin sets a cap → parsed as int
    assert merge_scoring_config({"summary_max_chars": 40}).summary_max_chars == 40
    # float coerced, ceiling-clamped, never negative
    assert merge_scoring_config({"summary_max_chars": 9999}).summary_max_chars == 500
    assert merge_scoring_config({"summary_max_chars": -5}).summary_max_chars == 0
    # absent / garbage → default 0 (= agent default governs)
    assert merge_scoring_config({}).summary_max_chars == 0
    assert merge_scoring_config({"summary_max_chars": "x"}).summary_max_chars == 0


def test_normalize_dims_clamps_and_drops_empty():
    assert normalize_dims({"impact": 1.5, "novelty": -1}) == {
        "impact": 1.0,
        "novelty": 0.0,
    }
    assert normalize_dims({"unknown": 0.5}) is None
    assert normalize_dims("nope") is None
