"""Provider cost-governance ORM models (Nous models epic).

The rate/contract/credit side of AI spend — distinct from ``billing`` (which is
the user-facing credits/points/orders ledger). These tables are what
``compute_cost()`` reads to turn a provider call into money:

  * ``ProviderPricing``       — provider_pricing (historical rate table per
    provider/model/modality/region/contract; ``effective_to`` NULL = in effect)
  * ``ProviderContracts``     — provider_contracts (enterprise discount /
    volume tier / prepay / overage terms)
  * ``ProviderCredits``       — provider_credits (vendor credits consumed
    before real spend)
  * ``ProviderMonthlySpend``  — provider_monthly_spend (rollup cache feeding
    the volume tiers)
  * ``ProviderByokKeys``      — provider_byok_keys (BYOK identification;
    flagged calls are zero-billed)
  * ``FxRates``               — fx_rates (rate history; the id is frozen into
    cost_snapshot so old calls replay correctly)
  * ``CostAuditLog``          — cost_audit_log (append-only compliance trail)

Backend-only service-role tables; no scope mixin.
"""

from __future__ import annotations

import datetime
import decimal
import uuid
from typing import Optional

from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    Numeric,
    PrimaryKeyConstraint,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.orm_base import Base


class ProviderPricing(Base):
    __tablename__ = "provider_pricing"
    __table_args__ = (
        CheckConstraint(
            "fail_billing_policy = ANY (ARRAY['no_charge'::text, 'partial'::text,"
            " 'full'::text])",
            name="provider_pricing_fail_policy_check",
        ),
        CheckConstraint(
            "modality = ANY (ARRAY['text_chat'::text, 'text_embedding'::text,"
            " 'image_generation'::text, 'image_editing'::text, 'image_input'::text,"
            " 'video_generation'::text, 'video_input'::text, 'audio_tts'::text,"
            " 'audio_stt'::text, 'audio_input'::text, 'reasoning'::text])",
            name="provider_pricing_modality_check",
        ),
        ForeignKeyConstraint(
            ["contract_id"],
            ["public.provider_contracts.contract_id"],
            ondelete="SET NULL",
            name="provider_pricing_contract_fk",
        ),
        PrimaryKeyConstraint("id", name="provider_pricing_pkey"),
        UniqueConstraint(
            "provider_slug",
            "model_slug",
            "region",
            "modality",
            "contract_id",
            "effective_from",
            name="provider_pricing_provider_slug_model_slug_region_modality_c_key",
        ),
        {
            "comment": (
                "Historical rate table per (provider, model, modality, region, "
                "contract).\n   effective_to NULL = currently in effect. Old rows "
                "kept immutably for audit."
            ),
            "schema": "public",
        },
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, server_default=text("generate_snowflake_id()")
    )
    provider_slug: Mapped[str] = mapped_column(Text, nullable=False)
    model_slug: Mapped[str] = mapped_column(Text, nullable=False)
    modality: Mapped[str] = mapped_column(Text, nullable=False)
    batch_discount_pct: Mapped[decimal.Decimal] = mapped_column(
        Numeric(5, 2),
        nullable=False,
        server_default=text("0"),
        comment="Async-batch discount %. 0 = no batch tier available.",
    )
    cache_ttl_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("300")
    )
    fail_billing_policy: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'no_charge'::text"),
        comment="no_charge / partial / full — vendor policy on failed calls.",
    )
    effective_from: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    extra: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    added_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    region: Mapped[Optional[str]] = mapped_column(Text)
    list_input_per_1m_usd: Mapped[Optional[decimal.Decimal]] = mapped_column(
        Numeric(12, 6)
    )
    list_output_per_1m_usd: Mapped[Optional[decimal.Decimal]] = mapped_column(
        Numeric(12, 6)
    )
    list_cache_read_per_1m_usd: Mapped[Optional[decimal.Decimal]] = mapped_column(
        Numeric(12, 6)
    )
    list_cache_write_per_1m_usd: Mapped[Optional[decimal.Decimal]] = mapped_column(
        Numeric(12, 6)
    )
    list_reasoning_per_1m_usd: Mapped[Optional[decimal.Decimal]] = mapped_column(
        Numeric(12, 6)
    )
    list_tool_use_per_1m_usd: Mapped[Optional[decimal.Decimal]] = mapped_column(
        Numeric(12, 6)
    )
    list_vision_per_1m_usd: Mapped[Optional[decimal.Decimal]] = mapped_column(
        Numeric(12, 6)
    )
    list_audio_per_1m_usd: Mapped[Optional[decimal.Decimal]] = mapped_column(
        Numeric(12, 6)
    )
    list_image_per_unit_usd: Mapped[Optional[decimal.Decimal]] = mapped_column(
        Numeric(12, 6)
    )
    list_image_per_megapixel_usd: Mapped[Optional[decimal.Decimal]] = mapped_column(
        Numeric(12, 6)
    )
    list_video_per_second_usd: Mapped[Optional[decimal.Decimal]] = mapped_column(
        Numeric(12, 6)
    )
    list_audio_per_second_usd: Mapped[Optional[decimal.Decimal]] = mapped_column(
        Numeric(12, 6)
    )
    list_audio_per_char_usd: Mapped[Optional[decimal.Decimal]] = mapped_column(
        Numeric(12, 6)
    )
    contract_id: Mapped[Optional[str]] = mapped_column(Text)
    effective_to: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    source: Mapped[Optional[str]] = mapped_column(Text)
    source_url: Mapped[Optional[str]] = mapped_column(Text)
    source_pdf_url: Mapped[Optional[str]] = mapped_column(Text)
    added_by: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)


class ProviderContracts(Base):
    __tablename__ = "provider_contracts"
    __table_args__ = (
        CheckConstraint(
            "status = ANY (ARRAY['active'::text, 'expired'::text, 'pending'::text,"
            " 'terminated'::text])",
            name="provider_contracts_status_check",
        ),
        PrimaryKeyConstraint("contract_id", name="provider_contracts_pkey"),
        {
            "comment": (
                "Contract terms (enterprise discount / volume tier / prepay / "
                "overage).\n   tenant_id NULL = platform-default; non-null = "
                "team-specific (BYOK-enterprise)."
            ),
            "schema": "public",
        },
    )

    contract_id: Mapped[str] = mapped_column(Text, primary_key=True)
    provider_slug: Mapped[str] = mapped_column(Text, nullable=False)
    enterprise_discount_pct: Mapped[decimal.Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, server_default=text("0")
    )
    overage_rate_multiplier: Mapped[decimal.Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, server_default=text("1.0")
    )
    auto_renew: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'active'::text")
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    tenant_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    volume_tier_json: Mapped[Optional[dict]] = mapped_column(
        JSONB,
        comment=(
            "JSONB array of {threshold_monthly_usd, discount_pct} tiers. "
            "Applied at compute_cost() time."
        ),
    )
    monthly_min_commit_usd: Mapped[Optional[decimal.Decimal]] = mapped_column(
        Numeric(12, 2)
    )
    monthly_min_commit_local: Mapped[Optional[decimal.Decimal]] = mapped_column(
        Numeric(12, 2)
    )
    monthly_min_commit_currency: Mapped[Optional[str]] = mapped_column(Text)
    prepay_total_usd: Mapped[Optional[decimal.Decimal]] = mapped_column(Numeric(12, 2))
    prepay_remaining_usd: Mapped[Optional[decimal.Decimal]] = mapped_column(
        Numeric(12, 2)
    )
    prepay_expires_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(True)
    )
    start_date: Mapped[Optional[datetime.date]] = mapped_column(Date)
    end_date: Mapped[Optional[datetime.date]] = mapped_column(Date)
    contact_email: Mapped[Optional[str]] = mapped_column(Text)
    contact_phone: Mapped[Optional[str]] = mapped_column(Text)
    account_manager_name: Mapped[Optional[str]] = mapped_column(Text)
    contract_pdf_url: Mapped[Optional[str]] = mapped_column(Text)
    signed_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    notes: Mapped[Optional[str]] = mapped_column(Text)


class ProviderCredits(Base):
    __tablename__ = "provider_credits"
    __table_args__ = (
        CheckConstraint(
            "credit_type = ANY (ARRAY['free_trial'::text, 'promotion'::text,"
            " 'sla_refund'::text, 'goodwill'::text, 'prepay'::text])",
            name="provider_credits_type_check",
        ),
        ForeignKeyConstraint(
            ["contract_id"],
            ["public.provider_contracts.contract_id"],
            ondelete="SET NULL",
            name="provider_credits_contract_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="provider_credits_pkey"),
        {
            "comment": (
                "Vendor credits: free trial, promotion, SLA refund, goodwill, "
                "prepay top-up.\n   compute_cost() consumes from this table when "
                "remaining_usd > 0."
            ),
            "schema": "public",
        },
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, server_default=text("generate_snowflake_id()")
    )
    provider_slug: Mapped[str] = mapped_column(Text, nullable=False)
    credit_type: Mapped[str] = mapped_column(Text, nullable=False)
    amount_usd: Mapped[decimal.Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    remaining_usd: Mapped[decimal.Decimal] = mapped_column(
        Numeric(12, 2), nullable=False
    )
    earned_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    contract_id: Mapped[Optional[str]] = mapped_column(Text)
    expires_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    consumed_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    reason: Mapped[Optional[str]] = mapped_column(Text)
    evidence_url: Mapped[Optional[str]] = mapped_column(Text)


class ProviderMonthlySpend(Base):
    __tablename__ = "provider_monthly_spend"
    __table_args__ = (
        PrimaryKeyConstraint(
            "provider_slug",
            "contract_id",
            "year_month",
            name="provider_monthly_spend_pkey",
        ),
        {
            "comment": (
                "Rollup cache. Background job refreshes from agent_run_events "
                "every 5 min.\n   compute_cost() reads this to apply "
                "volume_tier_json from contracts."
            ),
            "schema": "public",
        },
    )

    provider_slug: Mapped[str] = mapped_column(Text, primary_key=True)
    contract_id: Mapped[str] = mapped_column(Text, primary_key=True)
    year_month: Mapped[str] = mapped_column(CHAR(7), primary_key=True)
    total_usd: Mapped[decimal.Decimal] = mapped_column(
        Numeric(12, 4), nullable=False, server_default=text("0")
    )
    total_local_cents: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    call_count: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    last_updated: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )


class ProviderByokKeys(Base):
    __tablename__ = "provider_byok_keys"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="provider_byok_keys_pkey"),
        UniqueConstraint(
            "user_id",
            "provider_slug",
            "key_hash",
            name="provider_byok_keys_user_id_provider_slug_key_hash_key",
        ),
        {
            "comment": (
                "BYOK identification. Calls flagged byok_key_id NOT NULL are "
                "zero-billed\n   on cost ledger (user owes vendor directly). "
                "Latency/quality still tracked."
            ),
            "schema": "public",
        },
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, server_default=text("generate_snowflake_id()")
    )
    provider_slug: Mapped[str] = mapped_column(Text, nullable=False)
    key_hash: Mapped[str] = mapped_column(Text, nullable=False)
    added_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    user_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    team_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    key_label: Mapped[Optional[str]] = mapped_column(Text)
    last_used_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))


class FxRates(Base):
    __tablename__ = "fx_rates"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="fx_rates_pkey"),
        UniqueConstraint(
            "from_currency",
            "to_currency",
            "effective_at",
            name="fx_rates_from_currency_to_currency_effective_at_key",
        ),
        {
            "comment": (
                "FX rate history. compute_cost() picks the rate effective at call "
                "time\n   and persists the fx_rate_id in cost_snapshot so old calls "
                "replay correctly."
            ),
            "schema": "public",
        },
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, server_default=text("generate_snowflake_id()")
    )
    from_currency: Mapped[str] = mapped_column(Text, nullable=False)
    to_currency: Mapped[str] = mapped_column(Text, nullable=False)
    rate: Mapped[decimal.Decimal] = mapped_column(Numeric(15, 8), nullable=False)
    effective_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    source: Mapped[Optional[str]] = mapped_column(Text)


class CostAuditLog(Base):
    __tablename__ = "cost_audit_log"
    __table_args__ = (
        CheckConstraint(
            "action = ANY (ARRAY['create'::text, 'update'::text, 'invalidate'::text,"
            " 'delete'::text])",
            name="cost_audit_log_action_check",
        ),
        PrimaryKeyConstraint("id", name="cost_audit_log_pkey"),
        {
            "comment": (
                "Compliance audit trail for price/contract/credit changes. "
                "Append-only.\n   Future: tighten with trigger to capture all "
                "writes automatically."
            ),
            "schema": "public",
        },
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, server_default=text("generate_snowflake_id()")
    )
    entity_type: Mapped[str] = mapped_column(Text, nullable=False)
    entity_id: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    changed_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    changed_by: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    before: Mapped[Optional[dict]] = mapped_column(JSONB)
    after: Mapped[Optional[dict]] = mapped_column(JSONB)
    reason: Mapped[Optional[str]] = mapped_column(Text)
