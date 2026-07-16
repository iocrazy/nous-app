"""Alerting / monitoring ORM models.

  * ``AlertRules``   — alert_rules (threshold definitions per metric; muting
    and notification-channel routing live here)
  * ``AlertHistory`` — alert_history (one row per fired alert; ``rule_name`` is
    denormalized so history survives the rule being renamed or deleted)

Split out of ``ops`` rather than appended to it: ops is at its size ceiling,
and alerting is a self-contained domain. Backend-only service-role tables.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Double,
    ForeignKeyConstraint,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.orm_base import Base


class AlertRules(Base):
    __tablename__ = "alert_rules"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="alert_rules_pkey"),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, server_default=text("generate_snowflake_id()")
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    metric_type: Mapped[str] = mapped_column(String(50), nullable=False)
    condition: Mapped[str] = mapped_column(String(10), nullable=False)
    threshold: Mapped[float] = mapped_column(Double(53), nullable=False)
    window_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("5")
    )
    notification_channel: Mapped[str] = mapped_column(
        String(50), nullable=False, server_default=text("'discord'::character varying")
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    is_muted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    mute_until: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    created_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(True), server_default=text("now()")
    )
    updated_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(True), server_default=text("now()")
    )


class AlertHistory(Base):
    __tablename__ = "alert_history"
    __table_args__ = (
        ForeignKeyConstraint(
            ["rule_id"],
            ["public.alert_rules.id"],
            ondelete="CASCADE",
            name="alert_history_rule_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="alert_history_pkey"),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, server_default=text("generate_snowflake_id()")
    )
    rule_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    rule_name: Mapped[str] = mapped_column(String(255), nullable=False)
    metric_type: Mapped[str] = mapped_column(String(50), nullable=False)
    metric_value: Mapped[float] = mapped_column(Double(53), nullable=False)
    threshold: Mapped[float] = mapped_column(Double(53), nullable=False)
    condition: Mapped[str] = mapped_column(String(10), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    notified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    resolved: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    resolved_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    created_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(True), server_default=text("now()")
    )
