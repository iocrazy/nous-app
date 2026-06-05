"""Billing, credits, points, orders, and pricing models."""

from __future__ import annotations

import datetime
import decimal
import uuid
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.orm_base import Base


class CreditPricing(Base):
    __tablename__ = "credit_pricing"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="credit_pricing_pkey"),
        UniqueConstraint("action", name="credit_pricing_action_key"),
        {"comment": "Configurable pricing for different actions", "schema": "public"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    action: Mapped[str] = mapped_column(
        String(100), nullable=False, comment="Action identifier (e.g., parse, download)"
    )
    cost: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
        comment="Credit cost for this action",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("true"),
        comment="Whether this pricing rule is active",
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    description: Mapped[Optional[str]] = mapped_column(Text)


class CreditTransactions(Base):
    __tablename__ = "credit_transactions"
    __table_args__ = (
        CheckConstraint(
            "type::text = ANY (ARRAY['recharge'::character varying::text,"
            " 'consume'::character varying::text,"
            " 'refund'::character varying::text,"
            " 'gift'::character varying::text,"
            " 'adjustment'::character varying::text])",
            name="credit_transactions_type_check",
        ),
        PrimaryKeyConstraint("id", name="credit_transactions_pkey"),
        Index("idx_credit_transactions_admin_id", "admin_id"),
        Index("idx_credit_transactions_user_id", "user_id"),
        {"comment": "Log of all credit transactions", "schema": "public"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment="Transaction type: recharge, consume, refund, gift, adjustment",
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    description: Mapped[Optional[str]] = mapped_column(Text)
    related_id: Mapped[Optional[str]] = mapped_column(
        String(255),
        comment="Related entity ID (e.g., video aweme_id for consumption)",
    )
    admin_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid, comment="Admin who performed the action (for adjustments/gifts)"
    )


class PointPackages(Base):
    __tablename__ = "point_packages"
    __table_args__ = (
        CheckConstraint("points_amount > 0", name="point_packages_points_amount_check"),
        CheckConstraint("price_cents > 0", name="point_packages_price_cents_check"),
        PrimaryKeyConstraint("id", name="point_packages_pkey"),
        UniqueConstraint("name", name="point_packages_name_key"),
        {"schema": "public"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    points_amount: Mapped[int] = mapped_column(Integer, nullable=False)
    price_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(
        String(10), nullable=False, server_default=text("'CNY'::character varying")
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    description: Mapped[Optional[str]] = mapped_column(Text)


class PointPricing(Base):
    __tablename__ = "point_pricing"
    __table_args__ = (
        CheckConstraint("points_cost >= 0", name="point_pricing_points_cost_check"),
        PrimaryKeyConstraint("id", name="point_pricing_pkey"),
        UniqueConstraint("action_type", name="point_pricing_action_type_key"),
        {"schema": "public"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    action_type: Mapped[str] = mapped_column(String(50), nullable=False)
    points_cost: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    description: Mapped[Optional[str]] = mapped_column(Text)


class PointTransactions(Base):
    __tablename__ = "point_transactions"
    __table_args__ = (
        CheckConstraint(
            "type::text = ANY (ARRAY['purchase'::character varying,"
            " 'consume'::character varying,"
            " 'refund'::character varying,"
            " 'gift'::character varying,"
            " 'admin_adjust'::character varying,"
            " 'daily_gift'::character varying,"
            " 'daily_gift_reclaim'::character varying]::text[])",
            name="point_transactions_type_check",
        ),
        ForeignKeyConstraint(
            ["team_id"],
            ["public.teams.id"],
            ondelete="CASCADE",
            name="point_transactions_team_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="point_transactions_pkey"),
        Index(
            "idx_point_transactions_unique_refund",
            "team_id",
            "reference_type",
            "reference_id",
            postgresql_where=(
                "(((type)::text = 'refund'::text) AND (reference_id IS NOT NULL))"
            ),
            unique=True,
        ),
        Index("idx_point_transactions_user_id", "user_id"),
        {"schema": "public"},
    )

    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    balance_after: Mapped[int] = mapped_column(Integer, nullable=False)
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, server_default=text("generate_snowflake_id()")
    )
    team_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    reference_type: Mapped[Optional[str]] = mapped_column(String(50))
    reference_id: Mapped[Optional[str]] = mapped_column(String(200))
    description: Mapped[Optional[str]] = mapped_column(Text)
    provider: Mapped[Optional[str]] = mapped_column(Text)
    model: Mapped[Optional[str]] = mapped_column(Text)
    duration_seconds: Mapped[Optional[decimal.Decimal]] = mapped_column(Numeric)
    is_nous: Mapped[Optional[bool]] = mapped_column(
        Boolean, server_default=text("false")
    )


class DailyPointGifts(Base):
    __tablename__ = "daily_point_gifts"
    __table_args__ = (
        CheckConstraint(
            "status = ANY (ARRAY['granted'::text, 'reclaimed'::text])",
            name="daily_point_gifts_status_check",
        ),
        PrimaryKeyConstraint("id", name="daily_point_gifts_pkey"),
        UniqueConstraint(
            "user_id", "gift_date", name="daily_point_gifts_user_id_gift_date_key"
        ),
        Index("idx_daily_point_gifts_date_status", "gift_date", "status"),
        {"schema": "public"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    team_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    gift_date: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    amount_granted: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    amount_consumed: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    amount_reclaimed: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'granted'::text")
    )
    granted_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    reclaimed_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))


class Orders(Base):
    __tablename__ = "orders"
    __table_args__ = (
        CheckConstraint("amount_cents > 0", name="orders_amount_cents_check"),
        CheckConstraint(
            "payment_method::text = ANY (ARRAY['wechat'::character varying::text,"
            " 'alipay'::character varying::text])",
            name="orders_payment_method_check",
        ),
        CheckConstraint(
            "payment_status::text = ANY (ARRAY['pending'::character varying::text,"
            " 'paid'::character varying::text,"
            " 'failed'::character varying::text,"
            " 'expired'::character varying::text,"
            " 'refunded'::character varying::text])",
            name="orders_payment_status_check",
        ),
        CheckConstraint("points_amount > 0", name="orders_points_amount_check"),
        ForeignKeyConstraint(
            ["package_id"], ["public.point_packages.id"], name="orders_package_id_fkey"
        ),
        ForeignKeyConstraint(
            ["team_id"],
            ["public.teams.id"],
            ondelete="CASCADE",
            name="orders_team_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="orders_pkey"),
        Index("idx_orders_package_id", "package_id"),
        Index("idx_orders_team_id", "team_id"),
        Index("idx_orders_user_id", "user_id"),
        {"schema": "public"},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    points_amount: Mapped[int] = mapped_column(Integer, nullable=False)
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(
        String(10), nullable=False, server_default=text("'CNY'::character varying")
    )
    payment_method: Mapped[str] = mapped_column(String(20), nullable=False)
    payment_status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'pending'::character varying")
    )
    expired_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True),
        nullable=False,
        server_default=text("(now() + '00:30:00'::interval)"),
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, server_default=text("generate_snowflake_id()")
    )
    team_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    package_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    trade_no: Mapped[Optional[str]] = mapped_column(String(200))
    payment_url: Mapped[Optional[str]] = mapped_column(Text)
    paid_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
