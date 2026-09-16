"""检索投影表与引用镜像表（mig 472，3c §2.1 / §2.2）。

REFERENCE METADATA ONLY —— schema 归 supabase/migrations/*.sql。索引的名字与
**有序**列清单逐条镜像迁移：tests/db/test_orm_indexes_integration.py（C1）按
(名字, 轴) 对账，少一列、换个顺序、漏一个 partial 谓词都是一条新漂移。
"""

from __future__ import annotations

import datetime
import uuid
from typing import Optional

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    PrimaryKeyConstraint,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.orm_base import Base


class SearchDocs(Base):
    """统一检索的投影表：run 行 + output 行。

    entity_id 的拼法是与 Part C 的 SearchDocsRepository.upsert 共享的契约：
    run 行 = str(agent_runs.id)，output 行 = f"{kind}:{ref_id}:{version}"。
    两侧拼法不一致 = 同一个对象在表里并存两行，而 UNIQUE 拦不住。
    """

    __tablename__ = "search_docs"
    __table_args__ = (
        CheckConstraint(
            "entity_kind IN ('run','output')", name="search_docs_entity_kind_check"
        ),
        # 镜像随真相消失：run 没了，讲它的那条投影不该留着当搜索结果里的幽灵；
        # 议题删了只是这次运行不再挂在任何议题上，运行本身发生过。
        ForeignKeyConstraint(
            ["run_id"],
            ["public.agent_runs.id"],
            ondelete="CASCADE",
            name="search_docs_run_id_fkey",
        ),
        ForeignKeyConstraint(
            ["issue_id"],
            ["public.issues.id"],
            ondelete="SET NULL",
            name="search_docs_issue_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="search_docs_pkey"),
        UniqueConstraint("entity_kind", "entity_id", name="search_docs_entity_key"),
        Index(
            "idx_search_docs_title_trgm",
            "title",
            postgresql_using="gin",
            postgresql_ops={"title": "gin_trgm_ops"},
        ),
        Index(
            "idx_search_docs_body_trgm",
            "body",
            postgresql_using="gin",
            postgresql_ops={"body": "gin_trgm_ops"},
            postgresql_where=text("body IS NOT NULL"),
        ),
        Index("idx_search_docs_team_updated", "team_id", "updated_at"),
        Index("idx_search_docs_issue", "issue_id"),
        Index(
            "idx_search_docs_project",
            "project_id",
            postgresql_where=text("project_id IS NOT NULL"),
        ),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    entity_kind: Mapped[str] = mapped_column(Text, nullable=False)
    entity_id: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[Optional[str]] = mapped_column(Text)
    ref_id: Mapped[Optional[str]] = mapped_column(Text)
    version: Mapped[Optional[int]] = mapped_column(Integer)
    team_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    project_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    issue_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    run_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    owner_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    agent_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    #: NULL = 生产者从未交过正文；'' = 正文确实是空的。不许合并这两种。
    body: Mapped[Optional[str]] = mapped_column(Text)
    model: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[Optional[str]] = mapped_column(Text)
    error_code: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )


class OutputCitations(Base):
    """「这一版产出被哪条消息引用过」的物化镜像（messages.body jsonb 零索引）。

    UNIQUE (message_id, kind, ref_id, version)：一条消息里同一版被 @ 两次只算
    一次，重发/编辑重投也不会翻倍。
    """

    __tablename__ = "output_citations"
    __table_args__ = (
        # 同 SearchDocs：引用是镜像，消息删了这条引用就不该还在反查里出现。
        ForeignKeyConstraint(
            ["message_id"],
            ["public.messages.id"],
            ondelete="CASCADE",
            name="output_citations_message_id_fkey",
        ),
        ForeignKeyConstraint(
            ["issue_id"],
            ["public.issues.id"],
            ondelete="SET NULL",
            name="output_citations_issue_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="output_citations_pkey"),
        UniqueConstraint(
            "message_id",
            "kind",
            "ref_id",
            "version",
            name="output_citations_message_ref_key",
        ),
        Index("idx_output_citations_ref", "kind", "ref_id", "version"),
        Index("idx_output_citations_issue", "issue_id"),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    ref_id: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    issue_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    conversation_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    cited_by_user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
