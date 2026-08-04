"""Project authored-library ORM models (character canvas epic).

  * ``ProjectCharacters``     — project_characters    (mig 357)
  * ``ProjectLibEntities``    — project_lib_entities  (mig 358; locations +
    props in one table, keyed by ``entity_type``)
  * ``ProjectStyleProfile``   — project_style_profile (Canvas+AI M8; one row
    per project)

Snowflake BIGINT ids ride as strings at the API boundary (bigIntSafeFetch);
the repos ``str()`` ``id``/``project_id`` on the way out. No scope mixin:
ownership is scoped by the explicit ``project_id`` predicate in every repo
method (service-role/RLS-bypass model), so the choke point stays inert.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.orm_base import Base


class ProjectStages(Base):
    """Global SOP lifecycle stage catalog (Phase 5b)."""

    __tablename__ = "project_stages"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="project_stages_pkey"),
        UniqueConstraint("slug", name="project_stages_slug_key"),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_snowflake_id()"),
    )
    slug: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    tools_recommended: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    # Node-bank upgrade (mig 380): SOP catalog rows double as the workflow node
    # library. These describe each node's phase/role/deliverable/review defaults.
    phase: Mapped[str | None] = mapped_column(Text)
    default_role_label: Mapped[str | None] = mapped_column(Text)
    deliverable_label: Mapped[str | None] = mapped_column(Text)
    review_required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )


class ProjectStageHistory(Base):
    """Append-only per-project stage transition history (Phase 5b)."""

    __tablename__ = "project_stage_history"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id"],
            ["public.projects.id"],
            ondelete="CASCADE",
            name="project_stage_history_project_id_fkey",
        ),
        ForeignKeyConstraint(
            ["stage_id"],
            ["public.project_stages.id"],
            name="project_stage_history_stage_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="project_stage_history_pkey"),
        Index("idx_project_stage_history_project", "project_id"),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_snowflake_id()"),
    )
    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    stage_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    entered_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    exited_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
    transitioned_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)


class ProjectStyleProfile(Base):
    """Per-project style profile (1 row / project). PK = project_id."""

    __tablename__ = "project_style_profile"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id"],
            ["public.projects.id"],
            ondelete="CASCADE",
            name="project_style_profile_project_id_fkey",
        ),
        PrimaryKeyConstraint("project_id", name="project_style_profile_pkey"),
        {"schema": "public"},
    )

    project_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    style_md: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("''::text")
    )
    visual_style: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    reference_links: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    updated_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )


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


class WorkflowTemplates(Base):
    """Team-level workflow template (mig 380). At most one default per team
    (partial unique index). Nodes hang off it via CASCADE."""

    __tablename__ = "workflow_templates"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="workflow_templates_pkey"),
        Index(
            "uq_workflow_templates_team_default",
            "team_id",
            unique=True,
            postgresql_where=text("is_default"),
        ),
        Index("idx_workflow_templates_team", "team_id"),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_snowflake_id()"),
    )
    team_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    is_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )


class WorkflowTemplateNodes(Base):
    """Ordered node inside a template (mig 380). owner is single (user XOR
    agent); members live in ``workflow_template_node_members``. Agent ids are
    UUID (ai_agents.id)."""

    __tablename__ = "workflow_template_nodes"
    __table_args__ = (
        ForeignKeyConstraint(
            ["template_id"],
            ["public.workflow_templates.id"],
            ondelete="CASCADE",
            name="workflow_template_nodes_template_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="workflow_template_nodes_pkey"),
        CheckConstraint(
            "NOT (default_owner_user_id IS NOT NULL AND "
            "default_owner_agent_id IS NOT NULL)",
            name="wtn_owner_xor",
        ),
        # Surface (mig 402, B1): NULL (deliverable-type node) or one of the
        # known creation surfaces. See the ``surface`` column comment below.
        CheckConstraint(
            "surface IS NULL OR surface = ANY "
            "(ARRAY['script'::text, 'storyboard'::text, 'renders'::text])",
            name="workflow_template_nodes_surface_check",
        ),
        Index("idx_wtn_template", "template_id", "sort_order"),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_snowflake_id()"),
    )
    template_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)
    parallel_group: Mapped[int | None] = mapped_column(Integer)
    default_owner_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    default_owner_agent_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    skip_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    review_required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    deliverable_required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    deliverable_label: Mapped[str | None] = mapped_column(Text)
    source_stage_id: Mapped[int | None] = mapped_column(BigInteger)
    duration_days: Mapped[int | None] = mapped_column(Integer)
    completion_policy: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'owner'")
    )
    events: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text(
            '\'{"notify_on_arrival": true, "notify_on_complete": false, '
            '"suggest_agent_run": false}\'::jsonb'
        ),
    )
    # Form-based deliverable schema (mig 390, M3 PR-I): the field defs an
    # editor authors on the template node; copied verbatim into
    # ProjectStageNodes.form_schema at instantiation (I2's territory — this
    # column and its model mapping are I1's).
    form_schema: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    # Surface (mig 402, B1): which creation surface (script/storyboard/
    # renders) this template node corresponds to; NULL = deliverable-type
    # node (no in-app surface). Nullable with NO server_default, unlike
    # completion_policy/events/form_schema above -- NULL here is a real,
    # meaningful value, not "config not yet supplied". Copied verbatim to
    # ProjectStageNodes.surface at instantiation and frozen there (same
    # instantiate-then-freeze idiom as those three columns).
    surface: Mapped[str | None] = mapped_column(Text)


class WorkflowTemplateNodeMembers(Base):
    """Default members of a template node (mig 380). Surrogate ``id`` PK — a
    null-able user/agent XOR pair cannot form a composite PK, and the
    schema-drift gate maps every domain table as a declarative model."""

    __tablename__ = "workflow_template_node_members"
    __table_args__ = (
        ForeignKeyConstraint(
            ["node_id"],
            ["public.workflow_template_nodes.id"],
            ondelete="CASCADE",
            name="workflow_template_node_members_node_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="workflow_template_node_members_pkey"),
        CheckConstraint(
            "(user_id IS NULL) <> (agent_id IS NULL)",
            name="wtnm_xor",
        ),
        Index(
            "uq_wtnm_node_user",
            "node_id",
            "user_id",
            unique=True,
            postgresql_where=text("user_id IS NOT NULL"),
        ),
        Index(
            "uq_wtnm_node_agent",
            "node_id",
            "agent_id",
            unique=True,
            postgresql_where=text("agent_id IS NOT NULL"),
        ),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_snowflake_id()"),
    )
    node_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    agent_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)


class WorkflowTemplateNodeDeps(Base):
    """Dependency edge between two nodes of the SAME template (mig 391, M3
    PR-J). Both columns are NOT NULL so the composite PK needs no surrogate
    ``id`` (unlike the nullable-XOR member tables above) — same idiom as
    ``canvas_resource_refs``. Backward-only (``depends_on_node_id`` must have
    a strictly smaller ``sort_order`` than ``node_id``) is an application-
    layer invariant enforced by ``workflow_templates_repository
    ._validate_deps_backward`` — never assume it holds from the schema alone."""

    __tablename__ = "workflow_template_node_deps"
    __table_args__ = (
        ForeignKeyConstraint(
            ["node_id"],
            ["public.workflow_template_nodes.id"],
            ondelete="CASCADE",
            name="workflow_template_node_deps_node_id_fkey",
        ),
        ForeignKeyConstraint(
            ["depends_on_node_id"],
            ["public.workflow_template_nodes.id"],
            ondelete="CASCADE",
            name="workflow_template_node_deps_depends_on_node_id_fkey",
        ),
        PrimaryKeyConstraint(
            "node_id", "depends_on_node_id", name="workflow_template_node_deps_pkey"
        ),
        {"schema": "public"},
    )

    node_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    depends_on_node_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)


class ProjectStageNodes(Base):
    """Per-project workflow node instance (mig 380). Copied from a template at
    project creation (PR-B), then independent. Each node has its own status;
    ``projects.current_node_id`` is the active-group cursor -- UNTOUCHED by
    mig 402/B1. ``episode_id`` (mig 402, B1) is a second, nullable cursor
    dimension: NULL means this node is legacy project-level (every row today,
    plus anything created before B3 wires per-episode instantiation);
    ``episodes.current_node_id`` is the per-episode cursor B2 will read
    instead, once it rewires ``advance_service``."""

    __tablename__ = "project_stage_nodes"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id"],
            ["public.projects.id"],
            ondelete="CASCADE",
            name="project_stage_nodes_project_id_fkey",
        ),
        ForeignKeyConstraint(
            ["folder_id"],
            ["public.project_folders.id"],
            ondelete="SET NULL",
            name="project_stage_nodes_folder_id_fkey",
        ),
        ForeignKeyConstraint(
            ["episode_id"],
            ["public.episodes.id"],
            ondelete="SET NULL",
            name="project_stage_nodes_episode_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="project_stage_nodes_pkey"),
        CheckConstraint(
            "status IN ('pending', 'in_progress', 'in_review', 'done', 'skipped')",
            name="project_stage_nodes_status_check",
        ),
        CheckConstraint(
            "NOT (owner_user_id IS NOT NULL AND owner_agent_id IS NOT NULL)",
            name="psn_owner_xor",
        ),
        # Surface (mig 402, B1): NULL (deliverable-type node) or one of the
        # known creation surfaces. See the ``surface`` column comment below.
        CheckConstraint(
            "surface IS NULL OR surface = ANY "
            "(ARRAY['script'::text, 'storyboard'::text, 'renders'::text])",
            name="project_stage_nodes_surface_check",
        ),
        Index("psn_project_idx", "project_id", "sort_order"),
        # Composite, not a bare episode_id index (mig 402, B1): every real
        # query already has project_id in hand (a node is always reached
        # through its project), and this column order serves both the
        # existing "all nodes in a project" query (leading column alone) and
        # B2's future "all nodes in THIS episode of THIS project" query
        # (first two columns as equality, sort_order for the existing
        # ORDER BY) without needing a second index.
        Index(
            "idx_project_stage_nodes_episode", "project_id", "episode_id", "sort_order"
        ),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_snowflake_id()"),
    )
    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_template_node_id: Mapped[int | None] = mapped_column(BigInteger)
    legacy_stage_id: Mapped[int | None] = mapped_column(BigInteger)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)
    parallel_group: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'pending'::text")
    )
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    owner_agent_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    planned_start: Mapped[datetime.date | None] = mapped_column(Date)
    planned_due: Mapped[datetime.date | None] = mapped_column(Date)
    review_required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    deliverable_required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    deliverable_label: Mapped[str | None] = mapped_column(Text)
    skipped: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    # The project_folders row that holds this node's filed deliverables (mig
    # 383). Backfilled lazily on arrival; null until a folder is materialized.
    folder_id: Mapped[int | None] = mapped_column(BigInteger)
    completion_policy: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'owner'")
    )
    events: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text(
            '\'{"notify_on_arrival": true, "notify_on_complete": false, '
            '"suggest_agent_run": false}\'::jsonb'
        ),
    )
    # Free-form business decoration for a live node instance (mig 389, M3
    # PR-H1). Nothing reads this yet -- form_schema / dependency data (H2/H3
    # territory) will land here. Instance-only: no template-side counterpart.
    metadata_: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    # Form-based deliverables (mig 390, M3 PR-I). ``form_schema`` is copied
    # verbatim from the template node at instantiation (same idiom as
    # events/completion_policy, mig 386); ``form_data`` is instance-only —
    # the actual values a user has entered into this live node's form, no
    # template-side counterpart (same shape as ``metadata_`` above).
    form_schema: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    form_data: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    # Runtime "heads up before you start" text (mig 395, M4 Autopilot §1).
    # Instance-only, no template-side counterpart (like form_data/metadata_
    # above) — writable any time before/while the node is open via NodePatch.
    brief: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("''::text")
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    # Episode scoping (mig 402, B1): nullable FK to ``episodes``. NULL means a
    # legacy project-level node -- every row in the DB predates this column,
    # and B3 (not B1) is what starts populating it for new per-episode chains.
    episode_id: Mapped[int | None] = mapped_column(BigInteger)
    # Surface (mig 402, B1): which creation surface (script/storyboard/
    # renders) this instance node corresponds to; NULL = deliverable-type
    # node (no in-app surface, completed via upload+review) -- also the
    # correct reading for every legacy instance predating this column (spec
    # §5: "遗留节点 surface 为空 -> 按交付物型降级"). Copied verbatim from the
    # template at instantiation (``instantiate_from_template``) and frozen
    # there -- deliberately absent from ``update_node``'s keyword whitelist,
    # same idiom as completion_policy/events/form_schema. Nullable with NO
    # server_default, unlike those three: NULL here is a real, meaningful
    # value, not "config not yet supplied".
    surface: Mapped[str | None] = mapped_column(Text)


class ProjectStageNodeMembers(Base):
    """Members of a project stage-node instance (mig 380). Surrogate ``id`` PK
    for the same reason as ``WorkflowTemplateNodeMembers``."""

    __tablename__ = "project_stage_node_members"
    __table_args__ = (
        ForeignKeyConstraint(
            ["node_id"],
            ["public.project_stage_nodes.id"],
            ondelete="CASCADE",
            name="project_stage_node_members_node_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="project_stage_node_members_pkey"),
        CheckConstraint(
            "(user_id IS NULL) <> (agent_id IS NULL)",
            name="psnm_xor",
        ),
        Index(
            "uq_psnm_node_user",
            "node_id",
            "user_id",
            unique=True,
            postgresql_where=text("user_id IS NOT NULL"),
        ),
        Index(
            "uq_psnm_node_agent",
            "node_id",
            "agent_id",
            unique=True,
            postgresql_where=text("agent_id IS NOT NULL"),
        ),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_snowflake_id()"),
    )
    node_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    agent_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)


class ProjectStageNodeDeps(Base):
    """Dependency edge between two nodes of the SAME project instance (mig
    391, M3 PR-J). Copied from ``workflow_template_node_deps`` at
    instantiation (template node id -> instance node id, the same map
    ``instantiate_from_template`` already builds for ``source_template_node_
    id``); independent of the template thereafter, same as every other
    instance-side table. Backward-only is enforced by ``project_stage_nodes_
    repository.update_node`` against the node's LIVE project siblings, not by
    any DB constraint."""

    __tablename__ = "project_stage_node_deps"
    __table_args__ = (
        ForeignKeyConstraint(
            ["node_id"],
            ["public.project_stage_nodes.id"],
            ondelete="CASCADE",
            name="project_stage_node_deps_node_id_fkey",
        ),
        ForeignKeyConstraint(
            ["depends_on_node_id"],
            ["public.project_stage_nodes.id"],
            ondelete="CASCADE",
            name="project_stage_node_deps_depends_on_node_id_fkey",
        ),
        PrimaryKeyConstraint(
            "node_id", "depends_on_node_id", name="project_stage_node_deps_pkey"
        ),
        {"schema": "public"},
    )

    node_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    depends_on_node_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)


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
