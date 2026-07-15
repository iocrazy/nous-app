"""Project authored-library ORM models (character canvas epic).

  * ``ProjectCharacters``  — project_characters   (mig 357)
  * ``ProjectLibEntities`` — project_lib_entities  (mig 358; locations + props
    in one table, keyed by ``entity_type``)

Snowflake BIGINT ids ride as strings at the API boundary (bigIntSafeFetch);
the repos ``str()`` ``id``/``project_id`` on the way out. No scope mixin:
ownership is scoped by the explicit ``project_id`` predicate in every repo
method (service-role/RLS-bypass model), so the choke point stays inert.
"""

from __future__ import annotations

import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.orm_base import Base


class ProjectCharacters(Base):
    """Authored character library rows for a project (mig 357)."""

    __tablename__ = "project_characters"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id"],
            ["public.projects.id"],
            ondelete="CASCADE",
            name="project_characters_project_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="project_characters_pkey"),
        CheckConstraint(
            "role_tag IN ('', 'lead', 'support', 'antagonist')",
            name="project_characters_role_tag_check",
        ),
        CheckConstraint(
            "source IN ('manual', 'script')",
            name="project_characters_source_check",
        ),
        Index("uq_project_characters_project_name", "project_id", "name", unique=True),
        Index("idx_project_characters_project", "project_id", "sort_order"),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_snowflake_id()"),
    )
    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    role_tag: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("''::text")
    )
    description: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("''::text")
    )
    tags: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    portrait_url: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'manual'::text")
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


class ProjectLibEntities(Base):
    """Generalized project library: locations + props keyed by entity_type
    (mig 358)."""

    __tablename__ = "project_lib_entities"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id"],
            ["public.projects.id"],
            ondelete="CASCADE",
            name="project_lib_entities_project_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="project_lib_entities_pkey"),
        CheckConstraint(
            "entity_type IN ('location', 'prop')",
            name="project_lib_entities_entity_type_check",
        ),
        CheckConstraint(
            "source IN ('manual', 'script')",
            name="project_lib_entities_source_check",
        ),
        Index(
            "uq_project_lib_entities_ptn",
            "project_id",
            "entity_type",
            "name",
            unique=True,
        ),
        Index(
            "idx_project_lib_entities_project_type",
            "project_id",
            "entity_type",
            "sort_order",
        ),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_snowflake_id()"),
    )
    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    entity_type: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    badge_tag: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("''::text")
    )
    description: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("''::text")
    )
    tags: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    cover_url: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'manual'::text")
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
