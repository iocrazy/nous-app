"""CoverTemplates ORM model — 封面工作室的样图模板库 (migration 435).

一个模板 = 一张参考图 + 一个名字。取图锚在 ``generated_media`` 而不是
``resources``，因为出图链路的 ``params.source_urls`` 只认
``/api/v1/generated-media/{id}/(cover|stream|file)`` 这一种 URL；别的 URL 会被
``generated_media_local_path()`` 静默丢弃。完整理由见迁移 435 的文件头。
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    Text,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.orm_base import Base


class CoverTemplates(Base):
    __tablename__ = "cover_templates"
    __table_args__ = (
        ForeignKeyConstraint(
            ["generated_media_id"],
            ["public.generated_media.id"],
            # RESTRICT，不是 CASCADE：模板是用户攒出来的资产，不该被另一个界面
            # 的清理动作顺手带走。代价是删除被引用的图会抛 IntegrityError，
            # 调用方必须把它翻译成类型化 409 而不是漏成 500。
            ondelete="RESTRICT",
            name="cover_templates_generated_media_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="cover_templates_pkey"),
        CheckConstraint(
            "source_kind IN ('upload', 'library', 'generated')",
            name="cover_templates_source_kind_check",
        ),
        Index(
            "idx_cover_templates_scope_usage",
            "scope_id",
            "usage_count",
            "created_at",
        ),
        Index(
            "ux_cover_templates_scope_media",
            "scope_id",
            "generated_media_id",
            unique=True,
        ),
        Index(
            "idx_cover_templates_source_resource",
            "source_resource_id",
            postgresql_where=text("source_resource_id IS NOT NULL"),
        ),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, server_default=text("generate_snowflake_id()")
    )
    # teams.id（个人团队）。与 generated_media.scope_id 同义、同样不加 FK。
    scope_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    creator_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    generated_media_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # 'upload' | 'library' | 'generated' —— 三条来路语义不同，见迁移 435。
    source_kind: Mapped[str] = mapped_column(Text, nullable=False)
    # 仅溯源，不参与取图；刻意无 FK（素材被删不该让模板消失）。
    source_resource_id: Mapped[int | None] = mapped_column(BigInteger)
    usage_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    # 与 usage_count 分开：计数说"好不好用"，时间说"最近在用哪一批"。
    last_used_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
