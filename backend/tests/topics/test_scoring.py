from app.services.topics.scoring import (
    TIER_WEIGHTS,
    compute_quality,
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


def test_normalize_dims_clamps_and_drops_empty():
    assert normalize_dims({"impact": 1.5, "novelty": -1}) == {
        "impact": 1.0,
        "novelty": 0.0,
    }
    assert normalize_dims({"unknown": 0.5}) is None
    assert normalize_dims("nope") is None
