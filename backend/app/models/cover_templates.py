"""CoverTemplateUsage ORM model — 封面模板「用过 N 次」 (migration 441).

The template LIBRARY is a system folder (``folders.system_key = 'cover_templates'``);
this table only carries the usage counter used for ordering. Keyed by
(scope, resource): the reference URL is minted on demand from the resource
(``/generated-media/import-from-resource``), which creates a fresh generated_media
row each time, so a gen id is not a stable key — the resource id is.
"""

from __future__ import annotations

import datetime

from sqlalchemy import BigInteger, DateTime, Integer, PrimaryKeyConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.orm_base import Base


class CoverTemplateUsage(Base):
    __tablename__ = "cover_template_usage"
    __table_args__ = (
        PrimaryKeyConstraint(
            "scope_id", "resource_id", name="cover_template_usage_pkey"
        ),
        {"schema": "public"},
    )

    scope_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    resource_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    usage_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    last_used_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
