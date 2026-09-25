"""video_shots / video_shot_embeddings / video_shot_indexes ORM models
(migration 507) — the shot index behind the Visual retrieval layer.

A shot is a millisecond span of one video plus the frame that represents it.
Its vectors live in ``video_shot_embeddings`` keyed by (shot, kind, space):
``kind='frame'`` is the representative frame embedded as an image;
``'clip'`` is reserved for PR 4. The vector width is
``app.core.embedding_space.EMBEDDING_DIM`` (halfvec, HNSW), same as 499.

``video_shot_indexes`` records that a video HAS been cut (algorithm, when,
how many) independent of which spaces hold its vectors — a zero-shot video
is still indexed.
"""

from __future__ import annotations

import datetime

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
from sqlalchemy.dialects.postgresql import REAL
from sqlalchemy.orm import Mapped, mapped_column

from app.core.embedding_space import EMBEDDING_DIM
from app.db.orm_base import Base

#: ``video_shot_embeddings.kind`` values. ``clip`` has no writer until PR 4.
SHOT_EMBEDDING_KINDS = ("frame", "clip")


class VideoShots(Base):
    __tablename__ = "video_shots"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="video_shots_pkey"),
        ForeignKeyConstraint(
            ["resource_id"],
            ["public.resources.id"],
            ondelete="CASCADE",
            name="video_shots_resource_id_fkey",
        ),
        UniqueConstraint(
            "resource_id", "shot_index", name="video_shots_resource_shot_key"
        ),
        CheckConstraint(
            "start_ms >= 0 AND end_ms > start_ms", name="video_shots_span_check"
        ),
        CheckConstraint(
            "rep_frame_ms >= start_ms AND rep_frame_ms < end_ms",
            name="video_shots_rep_frame_check",
        ),
        Index("video_shots_resource_idx", "resource_id"),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, server_default=text("generate_snowflake_id()")
    )
    resource_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    shot_index: Mapped[int] = mapped_column(Integer, nullable=False)
    start_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    end_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    rep_frame_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    cut_score: Mapped[float | None] = mapped_column(REAL)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )


class VideoShotEmbeddings(Base):
    __tablename__ = "video_shot_embeddings"
    __table_args__ = (
        PrimaryKeyConstraint(
            "shot_id", "kind", "space_id", name="video_shot_embeddings_pkey"
        ),
        ForeignKeyConstraint(
            ["shot_id"],
            ["public.video_shots.id"],
            ondelete="CASCADE",
            name="video_shot_embeddings_shot_id_fkey",
        ),
        ForeignKeyConstraint(
            ["space_id"],
            ["public.embedding_spaces.id"],
            ondelete="CASCADE",
            name="video_shot_embeddings_space_id_fkey",
        ),
        CheckConstraint(
            "kind IN ('frame', 'clip')", name="video_shot_embeddings_kind_check"
        ),
        Index(
            "video_shot_embeddings_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "halfvec_cosine_ops"},
        ),
        Index("video_shot_embeddings_space_kind_idx", "space_id", "kind"),
        {"schema": "public"},
    )

    shot_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    kind: Mapped[str] = mapped_column(Text, primary_key=True)
    space_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    embedding: Mapped[list[float]] = mapped_column(
        HALFVEC(EMBEDDING_DIM), nullable=False
    )
    source_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )


class VideoShotIndexes(Base):
    __tablename__ = "video_shot_indexes"
    __table_args__ = (
        PrimaryKeyConstraint("resource_id", name="video_shot_indexes_pkey"),
        ForeignKeyConstraint(
            ["resource_id"],
            ["public.resources.id"],
            ondelete="CASCADE",
            name="video_shot_indexes_resource_id_fkey",
        ),
        {"schema": "public"},
    )

    resource_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    algo_version: Mapped[str] = mapped_column(Text, nullable=False)
    shot_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    indexed_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
