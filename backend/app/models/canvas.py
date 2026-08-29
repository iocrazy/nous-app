"""Canvas domain ORM models.

  * ``Canvases``          — canvases (mig 280 core schema + 355 deleted_at).
    ``base_updated_at`` is the optimistic-lock token; ``updated_at`` is bumped
    SQL-side by the ``trg_canvases_touch_updated_at`` trigger on every UPDATE.
  * ``CanvasResourceRefs`` — canvas_resource_refs (mig 290; derived,
    rebuildable refs extracted from nodes_json).

No scope mixin — canvas membership checks live at the route layer
(``ensure_canvas_access``), so the choke point stays inert.
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
    PrimaryKeyConstraint,
    String,
    Text,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.orm_base import Base


class Canvases(Base):
    """A canvas document (mig 280). Soft-deleted via ``deleted_at`` (mig 355)."""

    __tablename__ = "canvases"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id"],
            ["public.projects.id"],
            ondelete="CASCADE",
            name="canvases_project_id_fkey",
        ),
        ForeignKeyConstraint(
            ["episode_id"],
            ["public.episodes.id"],
            ondelete="CASCADE",
            name="canvases_episode_id_fkey",
        ),
        ForeignKeyConstraint(
            ["asset_id"],
            ["public.assets.id"],
            ondelete="SET NULL",
            name="canvases_asset_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="canvases_pkey"),
        CheckConstraint(
            "kind IN ('smart', 'lite', 'classic', 'character', 'location', "
            "'prop', 'costume', 'storyboard')",
            name="canvases_kind_check",
        ),
        Index("idx_canvases_project", "project_id"),
        Index(
            "idx_canvases_asset",
            "asset_id",
            postgresql_where=text("asset_id IS NOT NULL"),
        ),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_snowflake_id()"),
    )
    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # mig 421: set only when kind='storyboard' — the system per-episode shot
    # canvas (shot-nodes-on-canvas spec 2026-08-11 §2). NULL for every other
    # kind ('smart', 'lite', 'character', ...).
    episode_id: Mapped[int | None] = mapped_column(BigInteger)
    name: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        server_default=text("'Untitled'::character varying"),
    )
    kind: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'smart'::text")
    )
    # mig 446: owning asset for an entity workshop canvas (decision 13). NULL for
    # ordinary canvases; project_id stays the project it was opened from.
    asset_id: Mapped[int | None] = mapped_column(BigInteger)
    viewport_json: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text('\'{"x":0,"y":0,"zoom":1}\'::jsonb'),
    )
    nodes_json: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    connections_json: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    node_ops_json: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    connection_ops_json: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    # Optimistic-lock token: the frontend echoes the value it loaded; a PUT
    # only applies when it still matches (the 409 path lives in CanvasService).
    base_updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    # Bumped by trg_canvases_touch_updated_at (BEFORE UPDATE → now()).
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    deleted_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))


class CanvasResourceRefs(Base):
    """Derived canvas→resource references (mig 290). Composite PK; rebuildable
    from nodes_json, so partial-write failures are self-healing."""

    __tablename__ = "canvas_resource_refs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["canvas_id"],
            ["public.canvases.id"],
            ondelete="CASCADE",
            name="canvas_resource_refs_canvas_id_fkey",
        ),
        ForeignKeyConstraint(
            ["resource_id"],
            ["public.resources.id"],
            ondelete="CASCADE",
            name="canvas_resource_refs_resource_id_fkey",
        ),
        PrimaryKeyConstraint(
            "canvas_id", "resource_id", "node_id", name="canvas_resource_refs_pkey"
        ),
        CheckConstraint(
            "role IN ('reference', 'output')",
            name="canvas_resource_refs_role_check",
        ),
        {"schema": "public"},
    )

    canvas_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    resource_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    node_id: Mapped[str] = mapped_column(Text, primary_key=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
