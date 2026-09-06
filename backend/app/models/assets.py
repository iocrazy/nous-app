"""Asset Library ORM models (mig 445) — spec 2026-08-28-asset-library-loadout-design §3.

No scope mixin on purpose (the stance the retired ``ProjectCharacters`` model
took, dropped with its table in mig 451): every repo method carries an
explicit ``scope_id`` predicate, so the choke point stays inert. Column shapes
mirror the migration 1:1 — tests/db/test_schema_drift.py enforces it.

``assets.updated_at`` has no touch trigger (mig 445 deliberately ships none):
every write path must set it explicitly.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import (
    BigInteger,
    Boolean,
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
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.orm_base import Base

ASSET_TYPES = ("character", "location", "prop", "costume", "prompt", "audio")
ASSET_SOURCES = (
    "manual",
    "script_import",
    "generated",
    "migrated",
    "duplicated",
    "system_preset",
)
LINK_RELATIONS = ("wears", "holds", "ambience_of", "voice_of")


class Assets(Base):
    __tablename__ = "assets"
    __table_args__ = (
        ForeignKeyConstraint(
            ["scope_id"],
            ["public.teams.id"],
            ondelete="CASCADE",
            name="assets_scope_id_fkey",
        ),
        ForeignKeyConstraint(
            ["cover_file_id"],
            ["public.resources.id"],
            ondelete="SET NULL",
            name="assets_cover_file_id_fkey",
        ),
        ForeignKeyConstraint(
            ["duplicated_from"],
            ["public.assets.id"],
            ondelete="SET NULL",
            name="assets_duplicated_from_fkey",
        ),
        PrimaryKeyConstraint("id", name="assets_pkey"),
        CheckConstraint(
            "asset_type IN ('character','location','prop','costume','prompt','audio')",
            name="assets_asset_type_check",
        ),
        CheckConstraint(
            "source IN ('manual','script_import','generated','migrated','duplicated','system_preset')",
            name="assets_source_check",
        ),
        CheckConstraint(
            "scope_id IS NOT NULL OR is_system_preset", name="assets_scope_or_preset"
        ),
        # mig 458: the three jsonb columns must hold OBJECTS. NOT NULL does
        # not stop the JSON value ``null`` (2026-09-06 outage, see the
        # AssetResponse validator).
        CheckConstraint(
            "jsonb_typeof(attrs) = 'object' AND jsonb_typeof(platform_params) = 'object'"
            " AND jsonb_typeof(tags) = 'object'",
            name="assets_json_columns_are_objects",
        ),
        Index(
            "idx_assets_scope_type",
            "scope_id",
            "asset_type",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # mig 449 — the shelf/counts predicate, membership folded in.
        Index(
            "idx_assets_scope_library",
            "scope_id",
            "asset_type",
            postgresql_where=text("in_library AND deleted_at IS NULL"),
        ),
        Index(
            "uq_assets_scope_type_name",
            text("COALESCE(scope_id, 0)"),
            "asset_type",
            text("lower(name)"),
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, server_default=text("generate_snowflake_id()")
    )
    # NULL only for global system presets (assets_scope_or_preset CHECK).
    scope_id: Mapped[int | None] = mapped_column(BigInteger)
    asset_type: Mapped[str] = mapped_column(Text, nullable=False)
    subtype: Mapped[str | None] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    role_tag: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("''")
    )
    description: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("''")
    )
    attrs: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    prompt_positive: Mapped[str | None] = mapped_column(Text)
    prompt_negative: Mapped[str | None] = mapped_column(Text)
    prompt_positive_zh: Mapped[str | None] = mapped_column(Text)
    prompt_negative_zh: Mapped[str | None] = mapped_column(Text)
    platform_params: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    cover_file_id: Mapped[int | None] = mapped_column(BigInteger)
    source: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'manual'")
    )
    duplicated_from: Mapped[int | None] = mapped_column(BigInteger)
    is_system_preset: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    # mig 449 — EXPLICIT library membership. True = the user deliberately put
    # this asset in the scope's library (manual create, duplicate,
    # save-as-asset); False = it arrived as a side effect of project work
    # (source ``script_import`` / ``migrated``) and is visible on its project's
    # page but not on the shelf until someone adds it. Distinct from ``source``
    # on purpose: provenance is written once, membership is toggled.
    #
    # ⚠️ DEPLOY ORDER: mig 449 MUST land before this code. ``select(Assets)``
    # names every mapped column, so this attribute appears in the SELECT list of
    # EVERY asset read — not just the ones that filter on it. Against a database
    # without the column, the shelf, the sheet, the counts badges, the project
    # panel, Link From Library and the backfill workflow all fail with
    # ``column assets.in_library does not exist`` (42703) until the migration
    # runs. ``run-migration.yml`` and ``deploy-gpu.yml`` have no ordering
    # guarantee (CLAUDE.md 已知缺口), so this is a live race, not a hypothetical.
    # The reverse order is safe: an older model omits the column and its INSERTs
    # take the DEFAULT.
    in_library: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    tags: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    # No touch trigger on this table — every write path bumps it explicitly.
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    deleted_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))


class AssetLoadouts(Base):
    __tablename__ = "asset_loadouts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["asset_id"],
            ["public.assets.id"],
            ondelete="CASCADE",
            name="asset_loadouts_asset_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="asset_loadouts_pkey"),
        Index("idx_asset_loadouts_asset", "asset_id", "sort_order"),
        Index(
            "uq_loadout_default",
            "asset_id",
            unique=True,
            postgresql_where=text("is_default"),
        ),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, server_default=text("generate_snowflake_id()")
    )
    asset_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    is_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    costume_ids: Mapped[list[int]] = mapped_column(
        ARRAY(BigInteger), nullable=False, server_default=text("'{}'::bigint[]")
    )
    prop_ids: Mapped[list[int]] = mapped_column(
        ARRAY(BigInteger), nullable=False, server_default=text("'{}'::bigint[]")
    )
    prompt_extra: Mapped[str | None] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )


class AssetFiles(Base):
    __tablename__ = "asset_files"
    __table_args__ = (
        ForeignKeyConstraint(
            ["asset_id"],
            ["public.assets.id"],
            ondelete="CASCADE",
            name="asset_files_asset_id_fkey",
        ),
        ForeignKeyConstraint(
            ["resource_id"],
            ["public.resources.id"],
            ondelete="CASCADE",
            name="asset_files_resource_id_fkey",
        ),
        ForeignKeyConstraint(
            ["loadout_id"],
            ["public.asset_loadouts.id"],
            ondelete="SET NULL",
            name="asset_files_loadout_id_fkey",
        ),
        PrimaryKeyConstraint(
            "asset_id", "resource_id", "slot", name="asset_files_pkey"
        ),
        Index("idx_asset_files_resource", "resource_id"),
        {"schema": "public"},
    )

    asset_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    resource_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    slot: Mapped[str] = mapped_column(Text, primary_key=True)
    loadout_id: Mapped[int | None] = mapped_column(BigInteger)
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    note: Mapped[str | None] = mapped_column(Text)
    attached_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    attached_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )


class AssetLinks(Base):
    __tablename__ = "asset_links"
    __table_args__ = (
        ForeignKeyConstraint(
            ["from_asset_id"],
            ["public.assets.id"],
            ondelete="CASCADE",
            name="asset_links_from_asset_id_fkey",
        ),
        ForeignKeyConstraint(
            ["to_asset_id"],
            ["public.assets.id"],
            ondelete="CASCADE",
            name="asset_links_to_asset_id_fkey",
        ),
        PrimaryKeyConstraint(
            "from_asset_id", "to_asset_id", "relation", name="asset_links_pkey"
        ),
        CheckConstraint(
            "relation IN ('wears','holds','ambience_of','voice_of')",
            name="asset_links_relation_check",
        ),
        CheckConstraint("from_asset_id <> to_asset_id", name="asset_links_no_self"),
        Index("idx_asset_links_to", "to_asset_id", "relation"),
        {"schema": "public"},
    )

    from_asset_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    to_asset_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    relation: Mapped[str] = mapped_column(Text, primary_key=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )


class AssetProjectRefs(Base):
    __tablename__ = "asset_project_refs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["asset_id"],
            ["public.assets.id"],
            ondelete="CASCADE",
            name="asset_project_refs_asset_id_fkey",
        ),
        ForeignKeyConstraint(
            ["project_id"],
            ["public.projects.id"],
            ondelete="CASCADE",
            name="asset_project_refs_project_id_fkey",
        ),
        PrimaryKeyConstraint("asset_id", "project_id", name="asset_project_refs_pkey"),
        Index("idx_apr_project", "project_id"),
        {"schema": "public"},
    )

    asset_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    project_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    linked_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    linked_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )


class CanvasAssetRefs(Base):
    """Derived canvas→asset references (mig 445). Maintained by CanvasService
    in P4; rebuildable from nodes_json."""

    __tablename__ = "canvas_asset_refs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["canvas_id"],
            ["public.canvases.id"],
            ondelete="CASCADE",
            name="canvas_asset_refs_canvas_id_fkey",
        ),
        ForeignKeyConstraint(
            ["asset_id"],
            ["public.assets.id"],
            ondelete="CASCADE",
            name="canvas_asset_refs_asset_id_fkey",
        ),
        ForeignKeyConstraint(
            ["loadout_id"],
            ["public.asset_loadouts.id"],
            ondelete="SET NULL",
            name="canvas_asset_refs_loadout_id_fkey",
        ),
        PrimaryKeyConstraint(
            "canvas_id", "asset_id", "node_id", name="canvas_asset_refs_pkey"
        ),
        Index("idx_car_asset", "asset_id"),
        {"schema": "public"},
    )

    canvas_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    asset_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    node_id: Mapped[str] = mapped_column(Text, primary_key=True)
    loadout_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
