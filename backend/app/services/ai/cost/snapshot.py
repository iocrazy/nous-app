"""compute_cost — synchronous pure-Python rate × usage → cost_snapshot.

Inputs are dataclasses; outputs are dataclasses that round-trip cleanly
to JSONB via :func:`dataclasses.asdict`. The caller is responsible for:

- Fetching the effective rate row from ``provider_effective_rate`` view
  (migration 165). compute_cost takes the resolved rate as input.
- Fetching the FX rate from ``fx_rates``.
- Persisting the resulting CostSnapshot into ``agent_run_events.cost_snapshot``.

Design: keep this module pure / sync / no DB / no I/O. Easy to unit-test;
easy to call from a thin async wrapper.

OpenTelemetry GenAI Semantic Conventions alignment: where field names
overlap with OTel (gen_ai.usage.* etc), we use the OTel spelling so future
Langfuse / OTel-collector integration doesn't require a re-mapping pass.
See https://opentelemetry.io/docs/specs/semconv/gen-ai/.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Literal, Optional

CallStatus = Literal["success", "failed", "timeout"]
FailBillingPolicy = Literal["no_charge", "partial", "full"]
Modality = Literal[
    "text_chat",
    "text_embedding",
    "image_generation",
    "image_editing",
    "image_input",
    "video_generation",
    "video_input",
    "audio_tts",
    "audio_stt",
    "audio_input",
    "reasoning",
]


# ============================================================================
# Inputs
# ============================================================================


@dataclass(frozen=True)
class TokenUsage:
    """Per-call token / unit consumption.

    All fields default to 0 / None. Pass only what the call actually used.
    """

    # Text-mode token counts
    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0
    reasoning: int = 0
    tool_use: int = 0
    vision: int = 0
    audio_input: int = 0

    # Media-mode units
    image_units: Optional[int] = None
    video_seconds: Optional[float] = None
    audio_seconds: Optional[float] = None
    audio_chars: Optional[int] = None


@dataclass(frozen=True)
class EffectiveRate:
    """Row shape returned by ``provider_effective_rate`` view (migration 165)."""

    rate_id: int
    provider_slug: str
    model_slug: str
    modality: Modality
    region: Optional[str]
    contract_id: Optional[str]
    enterprise_discount_pct: float

    # Effective per-1M-token USD rates (enterprise discount applied)
    effective_input_usd: Decimal
    effective_output_usd: Decimal
    effective_cache_read_usd: Decimal
    effective_cache_write_usd: Decimal
    effective_reasoning_usd: Decimal
    effective_tool_use_usd: Decimal
    effective_vision_usd: Decimal
    effective_audio_usd: Decimal

    # Media per-unit USD rates
    effective_image_per_unit_usd: Optional[Decimal] = None
    effective_video_per_second_usd: Optional[Decimal] = None

    # Original list prices (for the "raw_usd" in CostBreakdown)
    list_input_usd: Decimal = Decimal("0")

    # Policy fields
    batch_discount_pct: float = 0.0
    fail_billing_policy: FailBillingPolicy = "no_charge"
    cache_ttl_seconds: int = 300


@dataclass(frozen=True)
class FxRate:
    """Row shape from ``fx_rates`` table (migration 165)."""

    fx_rate_id: int
    from_currency: str
    to_currency: str
    rate: Decimal


@dataclass(frozen=True)
class VolumeDiscountTier:
    """One element of ``provider_contracts.volume_tier_json``."""

    threshold_monthly_usd: Decimal
    discount_pct: float


@dataclass(frozen=True)
class CreditConsumption:
    """Result of trying to consume a credit row at compute time."""

    credit_id: int
    credit_consumed_usd: Decimal


# ============================================================================
# Outputs
# ============================================================================


@dataclass(frozen=True)
class RatesUsed:
    rate_id: int
    contract_id: Optional[str]
    list_input_usd: float
    effective_input_usd: float
    enterprise_discount_pct: float


@dataclass(frozen=True)
class DiscountsApplied:
    enterprise: float = 0.0
    volume_tier: float = 0.0
    batch: Optional[float] = None
    credit_id: Optional[int] = None


@dataclass(frozen=True)
class CostBreakdown:
    raw_usd: float
    discounted_usd: float
    saved_by_cache_usd: float
    local_cents: int
    local_currency: str
    fx_rate_used: float
    fx_rate_id: int


@dataclass(frozen=True)
class BillingMetadata:
    is_byok: bool = False
    byok_key_id: Optional[int] = None
    call_status: CallStatus = "success"
    fail_billing_policy_applied: bool = False
    credit_consumed_id: Optional[int] = None
    credit_consumed_usd: float = 0.0


@dataclass(frozen=True)
class SnapshotMetadata:
    computed_at: str  # ISO-8601 UTC
    stream_early_stop: bool = False
    actual_output_tokens: int = 0
    max_output_tokens: int = 0
    otel_semconv_version: str = "1.27"


@dataclass(frozen=True)
class CostSnapshot:
    """Full per-call cost record. JSONB-serialised into agent_run_events."""

    # Identity / OTel-aligned top-level fields
    provider_slug: str
    model_slug: str
    modality: Modality
    region: Optional[str]

    # Per-call usage
    tokens: Dict[str, int]
    image_units: Optional[int]
    video_seconds: Optional[float]

    # Computed breakdown
    rates: RatesUsed
    discounts_applied: DiscountsApplied
    cost: CostBreakdown
    billing: BillingMetadata
    metadata: SnapshotMetadata

    def to_jsonb(self) -> Dict[str, Any]:
        """Render to plain dict suitable for Supabase insert."""
        return asdict(self)


# ============================================================================
# compute_cost — main entry
# ============================================================================


def compute_cost(
    *,
    rate: EffectiveRate,
    usage: TokenUsage,
    fx: FxRate,
    local_currency: str = "CNY",
    # BYOK short-circuit
    is_byok: bool = False,
    byok_key_id: Optional[int] = None,
    # Call status (failure shapes affect billing per provider policy)
    call_status: CallStatus = "success",
    # Volume tier from monthly_spend cache (caller looked it up)
    monthly_spend_so_far_usd: Decimal = Decimal("0"),
    volume_tiers: Optional[List[VolumeDiscountTier]] = None,
    # Batch / async path
    is_batch: bool = False,
    # Credit consumption (caller decided to apply this row)
    credit: Optional[CreditConsumption] = None,
    # Streaming early-stop signal
    stream_early_stop: bool = False,
    # max_tokens (for forensics, not cost calc)
    max_output_tokens: int = 0,
    # Override "now" for deterministic tests
    now: Optional[datetime] = None,
) -> CostSnapshot:
    """Pure computation: rate × usage → CostSnapshot.

    All discount layers applied in order:
        1. Enterprise discount (already baked into rate.effective_* by VIEW)
        2. Volume tier (looked up in volume_tiers by monthly_spend_so_far_usd)
        3. Batch (rate.batch_discount_pct, only if is_batch=True)
        4. Failure policy (rate.fail_billing_policy)
        5. Credit absorbed (subtracted last)

    BYOK calls short-circuit to zero billable cost. The snapshot still
    records tokens used for latency / quality tracking.
    """

    # BYOK: zero cost ledger, but still record the call shape for observability.
    if is_byok:
        return _build_byok_snapshot(
            rate=rate,
            usage=usage,
            byok_key_id=byok_key_id,
            local_currency=local_currency,
            fx=fx,
            stream_early_stop=stream_early_stop,
            max_output_tokens=max_output_tokens,
            now=now,
        )

    # ----- Step 1: raw USD using list price (for "you would have paid X without enterprise discount") -----
    raw_input_usd = _rate_times_tokens(rate.list_input_usd, usage.input)
    raw_output_usd = _rate_times_tokens(rate.list_input_usd, usage.output)  # use input list for "raw" floor; will be replaced below
    # Actually: raw_usd uses LIST prices, not effective. For Plan dashboard view "saved by enterprise".
    # We approximate raw via list_input on input only (other dims need their own list values, which
    # the view doesn't currently expose — keep raw_usd as "list-input baseline" for now;
    # the cache_saved figure below is what we really show to users).

    # ----- Step 2: discounted USD using effective rates (enterprise already baked in) -----
    discounted_usd = (
        _rate_times_tokens(rate.effective_input_usd, usage.input)
        + _rate_times_tokens(rate.effective_output_usd, usage.output)
        + _rate_times_tokens(rate.effective_cache_read_usd, usage.cache_read)
        + _rate_times_tokens(rate.effective_cache_write_usd, usage.cache_write)
        + _rate_times_tokens(rate.effective_reasoning_usd, usage.reasoning)
        + _rate_times_tokens(rate.effective_tool_use_usd, usage.tool_use)
        + _rate_times_tokens(rate.effective_vision_usd, usage.vision)
        + _rate_times_tokens(rate.effective_audio_usd, usage.audio_input)
    )

    # Add media costs if applicable
    if usage.image_units and rate.effective_image_per_unit_usd:
        discounted_usd += Decimal(usage.image_units) * rate.effective_image_per_unit_usd
    if usage.video_seconds and rate.effective_video_per_second_usd:
        discounted_usd += Decimal(str(usage.video_seconds)) * rate.effective_video_per_second_usd

    # ----- Step 3: cache savings (what user would have paid without cache hits) -----
    saved_by_cache_usd = _rate_times_tokens(
        rate.effective_input_usd - rate.effective_cache_read_usd,
        usage.cache_read,
    )

    # ----- Step 4: volume tier discount -----
    volume_pct = _pick_volume_tier_pct(volume_tiers or [], monthly_spend_so_far_usd)
    if volume_pct > 0:
        discounted_usd *= Decimal(str(1.0 - volume_pct / 100.0))

    # ----- Step 5: batch discount -----
    batch_pct_applied: Optional[float] = None
    if is_batch and rate.batch_discount_pct > 0:
        discounted_usd *= Decimal(str(1.0 - rate.batch_discount_pct / 100.0))
        batch_pct_applied = rate.batch_discount_pct

    # ----- Step 6: failure billing policy -----
    fail_applied = False
    if call_status != "success":
        if rate.fail_billing_policy == "no_charge":
            discounted_usd = Decimal("0")
            fail_applied = True
        elif rate.fail_billing_policy == "partial":
            discounted_usd *= Decimal("0.5")
            fail_applied = True
        # 'full' = leave as-is

    # ----- Step 7: credit absorption -----
    credit_consumed_usd = Decimal("0")
    credit_id: Optional[int] = None
    if credit is not None:
        absorb = min(credit.credit_consumed_usd, discounted_usd)
        discounted_usd -= absorb
        credit_consumed_usd = absorb
        credit_id = credit.credit_id

    # ----- Step 8: raw_usd floor for display = list_input × tokens (approximation; see comment in Step 1) -----
    raw_usd = (
        _rate_times_tokens(rate.list_input_usd, usage.input)
        + _rate_times_tokens(rate.list_input_usd, usage.output)
    )
    # Ensure raw_usd >= discounted_usd + saved_by_cache so display doesn't show negative savings.
    if raw_usd < discounted_usd + saved_by_cache_usd:
        raw_usd = discounted_usd + saved_by_cache_usd

    # ----- Step 9: FX to local currency -----
    local_amount = discounted_usd * fx.rate
    local_cents = int((local_amount * Decimal("100")).quantize(Decimal("1")))

    now_iso = (now or datetime.now(timezone.utc)).isoformat()

    return CostSnapshot(
        provider_slug=rate.provider_slug,
        model_slug=rate.model_slug,
        modality=rate.modality,
        region=rate.region,
        tokens={
            "input": usage.input,
            "output": usage.output,
            "cache_read": usage.cache_read,
            "cache_write": usage.cache_write,
            "reasoning": usage.reasoning,
            "tool_use": usage.tool_use,
            "vision": usage.vision,
            "audio_input": usage.audio_input,
        },
        image_units=usage.image_units,
        video_seconds=usage.video_seconds,
        rates=RatesUsed(
            rate_id=rate.rate_id,
            contract_id=rate.contract_id,
            list_input_usd=float(rate.list_input_usd),
            effective_input_usd=float(rate.effective_input_usd),
            enterprise_discount_pct=rate.enterprise_discount_pct,
        ),
        discounts_applied=DiscountsApplied(
            enterprise=rate.enterprise_discount_pct,
            volume_tier=volume_pct,
            batch=batch_pct_applied,
            credit_id=credit_id,
        ),
        cost=CostBreakdown(
            raw_usd=float(raw_usd),
            discounted_usd=float(discounted_usd),
            saved_by_cache_usd=float(saved_by_cache_usd),
            local_cents=local_cents,
            local_currency=local_currency,
            fx_rate_used=float(fx.rate),
            fx_rate_id=fx.fx_rate_id,
        ),
        billing=BillingMetadata(
            is_byok=False,
            byok_key_id=None,
            call_status=call_status,
            fail_billing_policy_applied=fail_applied,
            credit_consumed_id=credit_id,
            credit_consumed_usd=float(credit_consumed_usd),
        ),
        metadata=SnapshotMetadata(
            computed_at=now_iso,
            stream_early_stop=stream_early_stop,
            actual_output_tokens=usage.output,
            max_output_tokens=max_output_tokens,
        ),
    )


# ============================================================================
# Helpers
# ============================================================================


def _rate_times_tokens(rate_per_1m: Decimal, tokens: int) -> Decimal:
    """Compute (rate USD per 1M tokens) × token count → USD."""
    if not rate_per_1m or not tokens:
        return Decimal("0")
    return rate_per_1m * Decimal(tokens) / Decimal("1000000")


def _pick_volume_tier_pct(
    tiers: List[VolumeDiscountTier], spend_so_far_usd: Decimal
) -> float:
    """Pick the highest-applicable volume tier discount pct.

    Tiers don't have to be sorted; we pick the one with the highest threshold
    the current spend has crossed.
    """
    eligible = [t for t in tiers if spend_so_far_usd >= t.threshold_monthly_usd]
    if not eligible:
        return 0.0
    return max(eligible, key=lambda t: t.threshold_monthly_usd).discount_pct


def _build_byok_snapshot(
    *,
    rate: EffectiveRate,
    usage: TokenUsage,
    byok_key_id: Optional[int],
    local_currency: str,
    fx: FxRate,
    stream_early_stop: bool,
    max_output_tokens: int,
    now: Optional[datetime],
) -> CostSnapshot:
    """BYOK calls: zero cost ledger; just record shape for observability."""
    now_iso = (now or datetime.now(timezone.utc)).isoformat()
    return CostSnapshot(
        provider_slug=rate.provider_slug,
        model_slug=rate.model_slug,
        modality=rate.modality,
        region=rate.region,
        tokens={
            "input": usage.input,
            "output": usage.output,
            "cache_read": usage.cache_read,
            "cache_write": usage.cache_write,
            "reasoning": usage.reasoning,
            "tool_use": usage.tool_use,
            "vision": usage.vision,
            "audio_input": usage.audio_input,
        },
        image_units=usage.image_units,
        video_seconds=usage.video_seconds,
        rates=RatesUsed(
            rate_id=rate.rate_id,
            contract_id=rate.contract_id,
            list_input_usd=float(rate.list_input_usd),
            effective_input_usd=float(rate.effective_input_usd),
            enterprise_discount_pct=rate.enterprise_discount_pct,
        ),
        discounts_applied=DiscountsApplied(),
        cost=CostBreakdown(
            raw_usd=0.0,
            discounted_usd=0.0,
            saved_by_cache_usd=0.0,
            local_cents=0,
            local_currency=local_currency,
            fx_rate_used=float(fx.rate),
            fx_rate_id=fx.fx_rate_id,
        ),
        billing=BillingMetadata(
            is_byok=True,
            byok_key_id=byok_key_id,
            call_status="success",
        ),
        metadata=SnapshotMetadata(
            computed_at=now_iso,
            stream_early_stop=stream_early_stop,
            actual_output_tokens=usage.output,
            max_output_tokens=max_output_tokens,
        ),
    )


__all__ = [
    "BillingMetadata",
    "CostBreakdown",
    "CostSnapshot",
    "CreditConsumption",
    "DiscountsApplied",
    "EffectiveRate",
    "FxRate",
    "RatesUsed",
    "SnapshotMetadata",
    "TokenUsage",
    "VolumeDiscountTier",
    "compute_cost",
]
