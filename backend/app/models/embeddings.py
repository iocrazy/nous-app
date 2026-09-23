"""embedding_spaces / resource_embeddings ORM models (migration 499).

Vector width comes from ``app.core.embedding_space.EMBEDDING_DIM`` (one
source); the column is ``halfvec`` so HNSW can index 2048 dims (``vector``
caps at 2000). A vector is keyed by (resource, layer, space) and is only
comparable with vectors of the same space.
"""

from __future__ import annotations

import datetime
from typing import Any

from pgvector.sqlalchemy import HALFVEC
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.embedding_space import EMBEDDING_DIM
from app.db.orm_base import Base


class EmbeddingSpaces(Base):
    __tablename__ = "embedding_spaces"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="embedding_spaces_pkey"),
        UniqueConstraint(
            "actual_model", "dims", name="embedding_spaces_model_dims_key"
        ),
        CheckConstraint(
            "dims > 0 AND dims <= 4000", name="embedding_spaces_dims_check"
        ),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, server_default=text("generate_snowflake_id()")
    )
    actual_model: Mapped[str] = mapped_column(Text, nullable=False)
    protocol: Mapped[str] = mapped_column(Text, nullable=False)
    dims: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("2048")
    )
    modalities: Mapped[Any] = mapped_column(
        JSONB, nullable=False, server_default=text("'[\"text\"]'::jsonb")
    )
    instruction_version: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'en_keyword_v1'::text")
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )


class ResourceEmbeddings(Base):
    __tablename__ = "resource_embeddings"
    __table_args__ = (
        PrimaryKeyConstraint(
            "resource_id", "layer", "space_id", name="resource_embeddings_pkey"
        ),
        ForeignKeyConstraint(
            ["resource_id"],
            ["public.resources.id"],
            ondelete="CASCADE",
            name="resource_embeddings_resource_id_fkey",
        ),
        ForeignKeyConstraint(
            ["space_id"],
            ["public.embedding_spaces.id"],
            ondelete="CASCADE",
            name="resource_embeddings_space_id_fkey",
        ),
        CheckConstraint(
            "layer IN ('semantic', 'transcript')",
            name="resource_embeddings_layer_check",
        ),
        Index(
            "resource_embeddings_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "halfvec_cosine_ops"},
        ),
        Index("resource_embeddings_space_layer_idx", "space_id", "layer"),
        {"schema": "public"},
    )

    resource_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    layer: Mapped[str] = mapped_column(Text, primary_key=True)
    space_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    embedding: Mapped[list[float]] = mapped_column(
        HALFVEC(EMBEDDING_DIM), nullable=False
    )
    source_hash: Mapped[str] = mapped_column(Text, nullable=False)
    source_text: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
