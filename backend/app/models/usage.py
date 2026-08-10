"""AI usage-observability ORM models (W3c).

Two tables, both introduced in migration 374:

  * ``AiUsageHourly``  — ai_usage_hourly: a DERIVED hourly rollup CACHE of AI
    token spend, keyed by the attribution dimensions (team / project / agent /
    model / module / attribution). It is NOT a source of truth — it is
    accumulated in app code (app.services.ai_usage.record_usage) alongside each
    agent_runs finish, so the Usage panel can answer "team X's spend this month
    grouped by agent" from a single small table instead of scanning raw runs.

  * ``TeamAiBudgets`` — team_ai_budgets: per-team monthly spend ceiling in
    cents (config→DB 铁律). NULL budget = unlimited. Read by the budget breaker.

Pricing is intentionally NOT modelled here — cost_cents flows in already priced
from ai_model_prices via RunRecorder. See the migration header for the route-C
rationale (why there is no separate ai_usage_events token store).
"""

from __future__ import annotations

import datetime
import decimal
import uuid
from typing import Optional

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    PrimaryKeyConstraint,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.orm_base import Base


class AiUsageHourly(Base):
    __tablename__ = "ai_usage_hourly"
    __table_args__ = (
        CheckConstraint(
            "attribution = ANY (ARRAY['direct_human'::text, 'rule_owner'::text])",
            name="ai_usage_hourly_attribution_check",
        ),
        ForeignKeyConstraint(
            ["team_id"],
            ["public.teams.id"],
            ondelete="SET NULL",
            name="ai_usage_hourly_team_id_fkey",
        ),
        ForeignKeyConstraint(
            ["project_id"],
            ["public.projects.id"],
            ondelete="SET NULL",
            name="ai_usage_hourly_project_id_fkey",
        ),
        ForeignKeyConstraint(
            ["agent_id"],
            ["public.ai_agents.id"],
            ondelete="SET NULL",
            name="ai_usage_hourly_agent_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="ai_usage_hourly_pkey"),
        UniqueConstraint(
            "bucket_hour",
            "team_id",
            "project_id",
            "agent_id",
            "model",
            "module",
            "attribution",
            name="ai_usage_hourly_dims_uq",
            postgresql_nulls_not_distinct=True,
        ),
        Index(
            "idx_ai_usage_hourly_team_bucket",
            "team_id",
            "bucket_hour",
            postgresql_where="(team_id IS NOT NULL)",
        ),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, server_default=text("generate_snowflake_id()")
    )
    bucket_hour: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False
    )
    team_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    project_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    agent_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    model: Mapped[Optional[str]] = mapped_column(Text)
    module: Mapped[str] = mapped_column(Text, nullable=False)
    attribution: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    completion_tokens: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    total_tokens: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        Computed("(prompt_tokens + completion_tokens)", persisted=True),
    )
    cached_input_tokens: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    cost_cents: Mapped[decimal.Decimal] = mapped_column(
        Numeric(18, 6), nullable=False, server_default=text("0")
    )
    event_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )


class TeamAiBudgets(Base):
    __tablename__ = "team_ai_budgets"
    __table_args__ = (
        ForeignKeyConstraint(
            ["team_id"],
            ["public.teams.id"],
            ondelete="CASCADE",
            name="team_ai_budgets_team_id_fkey",
        ),
        PrimaryKeyConstraint("team_id", name="team_ai_budgets_pkey"),
        {"schema": "public"},
    )

    team_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    monthly_budget_cents: Mapped[Optional[decimal.Decimal]] = mapped_column(
        Numeric(12, 2)
    )
    updated_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
