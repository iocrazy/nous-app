"""Tests for compute_cost — pure rate × usage → CostSnapshot computation.

No DB hit; we construct EffectiveRate / FxRate / TokenUsage in-test.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.services.ai.cost import (
    TokenUsage,
    compute_cost,
)
from app.services.ai.cost.snapshot import (
    CreditConsumption,
    EffectiveRate,
    FxRate,
    VolumeDiscountTier,
)

# ============================================================================
# Fixtures
# ============================================================================

FIXED_NOW = datetime(2026, 6, 10, 14, 23, 0, tzinfo=timezone.utc)


def _make_rate(**overrides) -> EffectiveRate:
    """A representative claude-sonnet-4-6 rate with 20% enterprise discount."""
    defaults = dict(
        rate_id=142,
        provider_slug="anthropic",
        model_slug="claude-sonnet-4-6",
        modality="text_chat",
        region=None,
        contract_id="anthropic_2026_q2",
        enterprise_discount_pct=20.0,
        list_input_usd=Decimal("3.00"),
        effective_input_usd=Decimal("2.40"),
        effective_output_usd=Decimal("12.00"),
        effective_cache_read_usd=Decimal("0.24"),
        effective_cache_write_usd=Decimal("3.00"),
        effective_reasoning_usd=Decimal("0"),
        effective_tool_use_usd=Decimal("0"),
        effective_vision_usd=Decimal("0"),
        effective_audio_usd=Decimal("0"),
        batch_discount_pct=0.0,
        fail_billing_policy="no_charge",
        cache_ttl_seconds=300,
    )
    defaults.update(overrides)
    return EffectiveRate(**defaults)


def _make_fx() -> FxRate:
    return FxRate(
        fx_rate_id=88,
        from_currency="USD",
        to_currency="CNY",
        rate=Decimal("7.30"),
    )


def _make_usage(**overrides) -> TokenUsage:
    return TokenUsage(**overrides)


# ============================================================================
# Happy path
# ============================================================================


class TestHappyPath:
    def test_simple_text_call(self):
        result = compute_cost(
            rate=_make_rate(),
            usage=_make_usage(input=1000, output=200),
            fx=_make_fx(),
            now=FIXED_NOW,
        )

        # input: 1000 tokens × $2.40/1M = $0.0024
        # output: 200 tokens × $12.00/1M = $0.0024
        # total = $0.0048
        assert result.cost.discounted_usd == pytest.approx(0.0048, abs=1e-6)
        # 0.0048 USD × 7.30 CNY/USD = 0.03504 CNY → 3.504 cents → rounds to 4
        assert result.cost.local_cents == 4
        assert result.billing.is_byok is False
        assert result.billing.call_status == "success"
        assert result.metadata.computed_at == FIXED_NOW.isoformat()

    def test_cache_savings_calculated(self):
        result = compute_cost(
            rate=_make_rate(),
            usage=_make_usage(input=100, output=50, cache_read=1000),
            fx=_make_fx(),
            now=FIXED_NOW,
        )

        # cache_saved = (effective_input 2.40 - cache_read 0.24) × 1000 / 1M = 0.00216
        assert result.cost.saved_by_cache_usd == pytest.approx(0.00216, abs=1e-6)

    def test_image_modality(self):
        rate = _make_rate(
            modality="image_generation",
            list_input_usd=Decimal("0"),  # image rates aren't per-token
            effective_input_usd=Decimal("0"),
            effective_output_usd=Decimal("0"),
            effective_cache_read_usd=Decimal("0"),
            effective_cache_write_usd=Decimal("0"),
            effective_reasoning_usd=Decimal("0"),
            effective_tool_use_usd=Decimal("0"),
            effective_vision_usd=Decimal("0"),
            effective_audio_usd=Decimal("0"),
            effective_image_per_unit_usd=Decimal("0.04"),  # $0.04 per image
        )
        result = compute_cost(
            rate=rate,
            usage=_make_usage(image_units=3),
            fx=_make_fx(),
            now=FIXED_NOW,
        )
        assert result.cost.discounted_usd == pytest.approx(0.12, abs=1e-6)


# ============================================================================
# Discount stacking
# ============================================================================


class TestDiscounts:
    def test_enterprise_already_in_rate(self):
        """Enterprise discount is baked into effective_* by the VIEW; we just
        copy it into discounts_applied for transparency."""
        rate = _make_rate(enterprise_discount_pct=25.0)
        result = compute_cost(
            rate=rate,
            usage=_make_usage(input=1000),
            fx=_make_fx(),
            now=FIXED_NOW,
        )
        assert result.discounts_applied.enterprise == 25.0

    def test_volume_tier_picks_highest_eligible(self):
        tiers = [
            VolumeDiscountTier(threshold_monthly_usd=Decimal("100"), discount_pct=5.0),
            VolumeDiscountTier(
                threshold_monthly_usd=Decimal("1000"), discount_pct=15.0
            ),
            VolumeDiscountTier(
                threshold_monthly_usd=Decimal("10000"), discount_pct=25.0
            ),
        ]
        # Spend $2000 so the $1000 tier (15%) applies
        result = compute_cost(
            rate=_make_rate(),
            usage=_make_usage(input=1000, output=1000),
            fx=_make_fx(),
            monthly_spend_so_far_usd=Decimal("2000"),
            volume_tiers=tiers,
            now=FIXED_NOW,
        )
        assert result.discounts_applied.volume_tier == 15.0

    def test_volume_tier_below_threshold(self):
        tiers = [
            VolumeDiscountTier(threshold_monthly_usd=Decimal("1000"), discount_pct=5.0),
        ]
        result = compute_cost(
            rate=_make_rate(),
            usage=_make_usage(input=1000),
            fx=_make_fx(),
            monthly_spend_so_far_usd=Decimal("500"),  # below threshold
            volume_tiers=tiers,
            now=FIXED_NOW,
        )
        assert result.discounts_applied.volume_tier == 0.0

    def test_batch_discount_applied(self):
        rate = _make_rate(batch_discount_pct=50.0)
        result_normal = compute_cost(
            rate=rate,
            usage=_make_usage(input=1000),
            fx=_make_fx(),
            is_batch=False,
            now=FIXED_NOW,
        )
        result_batch = compute_cost(
            rate=rate,
            usage=_make_usage(input=1000),
            fx=_make_fx(),
            is_batch=True,
            now=FIXED_NOW,
        )
        assert result_batch.cost.discounted_usd == pytest.approx(
            result_normal.cost.discounted_usd / 2, abs=1e-6
        )
        assert result_batch.discounts_applied.batch == 50.0


# ============================================================================
# Failure billing policy
# ============================================================================


class TestFailurePolicy:
    def test_no_charge_on_failure(self):
        rate = _make_rate(fail_billing_policy="no_charge")
        result = compute_cost(
            rate=rate,
            usage=_make_usage(input=1000, output=200),
            fx=_make_fx(),
            call_status="failed",
            now=FIXED_NOW,
        )
        assert result.cost.discounted_usd == 0.0
        assert result.billing.fail_billing_policy_applied is True

    def test_partial_charge_on_failure(self):
        rate = _make_rate(fail_billing_policy="partial")
        result = compute_cost(
            rate=rate,
            usage=_make_usage(input=1000, output=200),
            fx=_make_fx(),
            call_status="failed",
            now=FIXED_NOW,
        )
        # Half of normal: input 0.0024 + output 0.0024 = 0.0048 → 0.0024
        assert result.cost.discounted_usd == pytest.approx(0.0024, abs=1e-6)
        assert result.billing.fail_billing_policy_applied is True

    def test_full_charge_on_failure(self):
        rate = _make_rate(fail_billing_policy="full")
        result = compute_cost(
            rate=rate,
            usage=_make_usage(input=1000, output=200),
            fx=_make_fx(),
            call_status="failed",
            now=FIXED_NOW,
        )
        assert result.cost.discounted_usd == pytest.approx(0.0048, abs=1e-6)
        assert result.billing.fail_billing_policy_applied is False


# ============================================================================
# BYOK short-circuit
# ============================================================================


class TestBYOK:
    def test_byok_zero_cost(self):
        result = compute_cost(
            rate=_make_rate(),
            usage=_make_usage(input=10_000, output=5_000),
            fx=_make_fx(),
            is_byok=True,
            byok_key_id=12345,
            now=FIXED_NOW,
        )
        assert result.cost.discounted_usd == 0.0
        assert result.cost.raw_usd == 0.0
        assert result.cost.local_cents == 0
        assert result.billing.is_byok is True
        assert result.billing.byok_key_id == 12345
        # Tokens still recorded for observability
        assert result.tokens["input"] == 10_000
        assert result.tokens["output"] == 5_000

    def test_byok_no_credit_consumed(self):
        result = compute_cost(
            rate=_make_rate(),
            usage=_make_usage(input=1000),
            fx=_make_fx(),
            is_byok=True,
            credit=CreditConsumption(credit_id=99, credit_consumed_usd=Decimal("5.00")),
            now=FIXED_NOW,
        )
        assert result.billing.credit_consumed_id is None
        assert result.billing.credit_consumed_usd == 0.0


# ============================================================================
# Credit absorption
# ============================================================================


class TestCredit:
    def test_credit_caps_at_discounted_total(self):
        result = compute_cost(
            rate=_make_rate(),
            usage=_make_usage(input=1000, output=200),  # ~$0.0048
            fx=_make_fx(),
            credit=CreditConsumption(
                credit_id=99, credit_consumed_usd=Decimal("10.00")
            ),
            now=FIXED_NOW,
        )
        # We don't consume more than the actual bill
        assert result.cost.discounted_usd == 0.0
        assert result.billing.credit_consumed_id == 99
        assert result.billing.credit_consumed_usd == pytest.approx(0.0048, abs=1e-6)

    def test_credit_partial(self):
        result = compute_cost(
            rate=_make_rate(),
            usage=_make_usage(input=1000, output=200),  # ~$0.0048
            fx=_make_fx(),
            credit=CreditConsumption(
                credit_id=88, credit_consumed_usd=Decimal("0.002")
            ),
            now=FIXED_NOW,
        )
        # 0.0048 - 0.002 = 0.0028
        assert result.cost.discounted_usd == pytest.approx(0.0028, abs=1e-6)
        assert result.billing.credit_consumed_usd == pytest.approx(0.002, abs=1e-6)


# ============================================================================
# FX conversion
# ============================================================================


class TestFx:
    def test_local_cents_includes_fx(self):
        result = compute_cost(
            rate=_make_rate(),
            usage=_make_usage(input=1_000_000),  # exactly 1M input tokens
            fx=_make_fx(),
            now=FIXED_NOW,
        )
        # 1M × $2.40/1M = $2.40
        # × 7.30 CNY = ¥17.52 = 1752 cents
        assert result.cost.local_cents == 1752
        assert result.cost.local_currency == "CNY"
        assert result.cost.fx_rate_used == 7.30
        assert result.cost.fx_rate_id == 88


# ============================================================================
# Output shape — JSONB round-trip
# ============================================================================


class TestSnapshotShape:
    def test_to_jsonb_keys(self):
        result = compute_cost(
            rate=_make_rate(),
            usage=_make_usage(input=100),
            fx=_make_fx(),
            now=FIXED_NOW,
        )
        d = result.to_jsonb()
        assert d["provider_slug"] == "anthropic"
        assert d["model_slug"] == "claude-sonnet-4-6"
        assert "tokens" in d
        assert "cost" in d
        assert "rates" in d
        assert "billing" in d
        assert d["metadata"]["otel_semconv_version"] == "1.27"

    def test_snapshot_is_serializable(self):
        """asdict() output must contain no Decimal — only str/int/float/None/dict/list."""
        result = compute_cost(
            rate=_make_rate(),
            usage=_make_usage(input=100, output=50, cache_read=1000),
            fx=_make_fx(),
            now=FIXED_NOW,
        )
        d = result.to_jsonb()
        _assert_jsonable(d)


def _assert_jsonable(obj):
    if isinstance(obj, dict):
        for v in obj.values():
            _assert_jsonable(v)
    elif isinstance(obj, list):
        for item in obj:
            _assert_jsonable(item)
    elif obj is None or isinstance(obj, (bool, int, float, str)):
        return
    else:
        raise AssertionError(f"non-jsonable value: {type(obj).__name__} = {obj!r}")
