"""User schedules model.

Historically this module also held eight ``Storyboard*`` tombstone models
mapping ``zzz_deprecated_storyboard_*`` tables. Those tables were dropped by
migration 366 and the models deleted with them (the retired Storyboard
Workbench). ``UserSchedules`` is a normal LIVE table that happened to share
this file; it stays here to avoid churn on ~100 import sites.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Index,
    Integer,
    PrimaryKeyConstraint,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.orm_base import Base


class UserSchedules(Base):
    __tablename__ = "user_schedules"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="user_schedules_pkey"),
        Index(
            "idx_user_schedules_due",
            "next_fire_at",
            postgresql_where="(enabled = true)",
        ),
        Index("idx_user_schedules_user", "user_id", "enabled"),
        {
            "comment": (
                "User- or system-configured cron schedules. Master scheduler workflow\n"
                "     (app/workflows/scheduled_master.py) scans this table every minute and\n"
                "     dispatches due rows. Replaces hard-coded @DBOS.scheduled decorators\n"
                "     for user-facing recurring tasks; internal DBOS sweepers still use\n"
                "     the decorator pattern because they're system primitives."
            ),
            "schema": "public",
        },
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    cron_expr: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment=(
            "5-field cron expression in UTC (m h dom mon dow). Validated by\n"
            "     croniter on insert/update at the service layer (DB doesn't parse\n"
            '     cron). Examples: "0 9 * * *" daily 9am UTC, "*/15 * * * *" every\n'
            '     15 min, "0 0 * * 0" weekly Sunday midnight.'
        ),
    )
    task_type: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    lane: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'scheduled'::text")
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    next_fire_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True),
        nullable=False,
        comment=(
            "Pre-computed next fire time, kept in sync by the master scheduler\n"
            "     after each fire (or by service layer on insert/update). The\n"
            "     idx_user_schedules_due index makes finding due rows O(log n)."
        ),
    )
    fire_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    fail_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True),
        nullable=False,
        server_default=text("now()"),
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True),
        nullable=False,
        server_default=text("now()"),
    )
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid,
        comment=(
            "Owner of the schedule. NULL means system-owned (operator-managed\n"
            "     via DB / admin tools, not user-facing UI)."
        ),
    )
    last_fired_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    last_error: Mapped[Optional[str]] = mapped_column(Text)
    # W2a autopilot hardening (mig 370). See the migration for column semantics.
    timezone: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'UTC'::text"),
        comment=(
            "IANA timezone name the cron_expr is interpreted in. Cron fields are\n"
            "     wall-clock in this zone; next_fire_at is stored as UTC. Invalid\n"
            "     names fall back to UTC at compute time."
        ),
    )
    consecutive_fails: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
        comment=(
            "Run of dispatch failures since the last success. Reset to 0 on a\n"
            "     successful fire; NOT changed by skipped fires. Drives auto-pause."
        ),
    )
    paused_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(True),
        comment=(
            "When the master scheduler auto-paused this row (NULL = not\n"
            "     auto-paused). An operator/user disable leaves this NULL."
        ),
    )
    pause_reason: Mapped[Optional[str]] = mapped_column(
        Text,
        comment="Human-readable reason for an auto-pause, shown in the Routines UI.",
    )
    skipped_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
        comment=(
            "Count of fires discarded as stale or gated by delivery policy.\n"
            "     Telemetry only — excluded from the failure rate."
        ),
    )
    stale_after_minutes: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("60"),
        comment=(
            "A due fire more than this many minutes past its next_fire_at is\n"
            "     discarded (counted in skipped_count) instead of dispatched."
        ),
    )
