"""AI agents, sessions, messages, skills, and pricing models."""

from __future__ import annotations

import datetime
import decimal
import uuid
from typing import Optional

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    Double,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as Uuid
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import text

from app.db.orm_base import Base


class AiAgents(Base):
    __tablename__ = "ai_agents"
    __table_args__ = (
        CheckConstraint(
            "memory_injection_top_n IS NULL OR memory_injection_top_n >= 0 AND memory_injection_top_n <= 50",
            name="ai_agents_memory_injection_top_n_check",
        ),
        CheckConstraint(
            "paused_reason IS NULL OR (paused_reason = ANY (ARRAY['budget'::text, 'manual'::text]))",
            name="ai_agents_paused_reason_check",
        ),
        PrimaryKeyConstraint("id", name="ai_agents_pkey"),
        Index(
            "idx_ai_agents_user", "user_id", postgresql_where="(user_id IS NOT NULL)"
        ),
        Index(
            "ux_ai_agents_slug_system",
            "slug",
            postgresql_where="(is_system_preset = true)",
            unique=True,
        ),
        Index(
            "ux_ai_agents_slug_user",
            "user_id",
            "slug",
            postgresql_where="((is_system_preset = false) AND (user_id IS NOT NULL))",
            unique=True,
        ),
        {
            "comment": "AI Agent definitions with personas and configurations",
            "schema": "public",
        },
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    current_version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )
    fallback_models: Mapped[list[str]] = mapped_column(
        ARRAY(Text()),
        nullable=False,
        server_default=text("'{}'::text[]"),
        comment="Ordered fallback model chain. Adapter walks this after primary exhausts retries.",
    )
    persistent: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    description: Mapped[Optional[str]] = mapped_column(Text)
    model: Mapped[Optional[str]] = mapped_column(
        Text, server_default=text("'qwen-max'::text")
    )
    temperature: Mapped[Optional[decimal.Decimal]] = mapped_column(
        Numeric, server_default=text("0.7")
    )
    max_tokens: Mapped[Optional[int]] = mapped_column(
        Integer, server_default=text("4096")
    )
    team_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    project_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    enabled: Mapped[Optional[bool]] = mapped_column(
        Boolean, server_default=text("true")
    )
    sort_order: Mapped[Optional[int]] = mapped_column(Integer, server_default=text("0"))
    created_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(True), server_default=text("now()")
    )
    updated_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(True), server_default=text("now()")
    )
    slug: Mapped[Optional[str]] = mapped_column(
        String(64),
        comment="Stable identifier (e.g. script_ai, summarize); unique per scope",
    )
    identity_md: Mapped[Optional[str]] = mapped_column(
        Text, comment="Markdown: who the agent is (static persona)"
    )
    soul_md: Mapped[Optional[str]] = mapped_column(
        Text, comment="Markdown: core values / tone / voice"
    )
    agent_md: Mapped[Optional[str]] = mapped_column(
        Text, comment="Markdown: operating instructions / capabilities"
    )
    is_system_preset: Mapped[Optional[bool]] = mapped_column(
        Boolean,
        server_default=text("false"),
        comment="true = platform-managed preset, false = user-created",
    )
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    icon: Mapped[Optional[str]] = mapped_column(Text)
    monthly_token_budget: Mapped[Optional[int]] = mapped_column(Integer)
    monthly_cost_cents_budget: Mapped[Optional[decimal.Decimal]] = mapped_column(
        Numeric(12, 6)
    )
    paused_reason: Mapped[Optional[str]] = mapped_column(Text)
    budget_per_run_cents: Mapped[Optional[decimal.Decimal]] = mapped_column(
        Numeric(8, 2),
        server_default=text("50.0"),
        comment="Per-run BudgetGuard threshold in cents. NULL = unlimited.",
    )
    memory_injection_top_n: Mapped[Optional[int]] = mapped_column(
        Integer,
        comment="Phase M M3.D: per-agent override for retriever top_n. NULL = use code default (5).",
    )
    seed_hash: Mapped[Optional[str]] = mapped_column(
        Text,
        comment="sha256 of IDENTITY.md + SOUL.md + AGENT.md + frontmatter; null = always re-upsert",
    )


class AiModelPrices(Base):
    __tablename__ = "ai_model_prices"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="ai_model_prices_pkey"),
        UniqueConstraint(
            "model",
            "provider",
            "effective_at",
            name="ai_model_prices_model_provider_effective_at_key",
        ),
        Index("idx_ai_model_prices_lookup", "model", "provider", "effective_at"),
        {"schema": "public"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    model: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_cents_per_1k: Mapped[decimal.Decimal] = mapped_column(
        Numeric(12, 6), nullable=False
    )
    completion_cents_per_1k: Mapped[decimal.Decimal] = mapped_column(
        Numeric(12, 6), nullable=False
    )
    effective_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    supports_vision: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("false"),
        comment=(
            "TRUE if the model accepts image_url multipart parts (OpenAI multimodal shape). "
            "Read by app.services.ai.model_capabilities.model_supports_vision."
        ),
    )
    cached_input_cents_per_1k: Mapped[Optional[decimal.Decimal]] = mapped_column(
        Numeric
    )


class AiUsageLogs(Base):
    __tablename__ = "ai_usage_logs"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="ai_usage_logs_pkey"),
        Index("idx_ai_usage_project", "project_id", "created_at"),
        Index("idx_ai_usage_user", "user_id", "created_at"),
        {"comment": "Token consumption tracking for billing", "schema": "public"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    team_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    project_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    session_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    agent_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    action: Mapped[Optional[str]] = mapped_column(Text)
    total_tokens: Mapped[Optional[int]] = mapped_column(
        Integer, Computed("(prompt_tokens + completion_tokens)", persisted=True)
    )
    cost_points: Mapped[Optional[decimal.Decimal]] = mapped_column(Numeric)
    created_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(True), server_default=text("now()")
    )


class AiAgentVersions(Base):
    __tablename__ = "ai_agent_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["agent_id"],
            ["public.ai_agents.id"],
            ondelete="CASCADE",
            name="ai_agent_versions_agent_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="ai_agent_versions_pkey"),
        UniqueConstraint(
            "agent_id", "version_number", name="ux_ai_agent_versions_agent_version"
        ),
        Index("ix_ai_agent_versions_agent_id_desc", "agent_id", "version_number"),
        {"schema": "public"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    identity_md: Mapped[Optional[str]] = mapped_column(Text)
    soul_md: Mapped[Optional[str]] = mapped_column(Text)
    agent_md: Mapped[Optional[str]] = mapped_column(Text)
    model: Mapped[Optional[str]] = mapped_column(Text)
    temperature: Mapped[Optional[float]] = mapped_column(Double(53))
    max_tokens: Mapped[Optional[int]] = mapped_column(Integer)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)


class AiSessions(Base):
    __tablename__ = "ai_sessions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["agent_id"],
            ["public.ai_agents.id"],
            ondelete="SET NULL",
            name="ai_sessions_agent_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="ai_sessions_pkey"),
        Index(
            "idx_ai_sessions_agent",
            "agent_id",
            postgresql_where="(agent_id IS NOT NULL)",
        ),
        Index(
            "idx_ai_sessions_project",
            "project_id",
            "updated_at",
            postgresql_where="(project_id IS NOT NULL)",
        ),
        Index("idx_ai_sessions_user", "user_id", "updated_at"),
        {
            "comment": "Multi-turn conversation threads per user per project",
            "schema": "public",
        },
    )

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, server_default=text("generate_snowflake_id()")
    )
    team_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    project_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    title: Mapped[Optional[str]] = mapped_column(
        Text, server_default=text("'New Chat'::text")
    )
    context_type: Mapped[Optional[str]] = mapped_column(Text)
    context_id: Mapped[Optional[str]] = mapped_column(Text)
    total_tokens: Mapped[Optional[int]] = mapped_column(
        Integer, server_default=text("0")
    )
    message_count: Mapped[Optional[int]] = mapped_column(
        Integer, server_default=text("0")
    )
    status: Mapped[Optional[str]] = mapped_column(
        Text, server_default=text("'active'::text")
    )
    created_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(True), server_default=text("now()")
    )
    updated_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(True), server_default=text("now()")
    )
    agent_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid, comment="Bound agent for consistency across session turns"
    )
    agent_slug: Mapped[Optional[str]] = mapped_column(
        String(64), comment="Denormalized agent slug — avoids join for hot read paths"
    )


class AiSessionMemory(Base):
    __tablename__ = "ai_session_memory"
    __table_args__ = (
        ForeignKeyConstraint(
            ["session_id"],
            ["public.ai_sessions.id"],
            ondelete="CASCADE",
            name="ai_session_memory_session_id_fkey",
        ),
        PrimaryKeyConstraint("session_id", name="ai_session_memory_pkey"),
        {
            "comment": (
                "Wave 5b: continuously-maintained session notes with fixed schema. "
                "Replaces compactor's one-shot summary at compaction time."
            ),
            "schema": "public",
        },
    )

    body_md: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("''::text"),
        comment="Markdown source of truth. Updated by background updater on dual-threshold trigger.",
    )
    sections_json: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
        comment=(
            "Parsed-out sections for query/admin: "
            "{title, current_state, task_spec, key_files, workflow_steps, errors_and_fixes}."
        ),
    )
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("1"),
        comment="Optimistic concurrency: bumped on each successful update.",
    )
    last_updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    tokens_at_last_update: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    tool_calls_at_last_update: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    turns_at_last_update: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    session_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)


class AiMessages(Base):
    __tablename__ = "ai_messages"
    __table_args__ = (
        CheckConstraint(
            "role = ANY (ARRAY['system'::text, 'user'::text, 'assistant'::text])",
            name="ai_messages_role_check",
        ),
        ForeignKeyConstraint(
            ["session_id"],
            ["public.ai_sessions.id"],
            ondelete="CASCADE",
            name="ai_messages_session_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="ai_messages_pkey"),
        Index("idx_ai_messages_session", "session_id", "created_at"),
        {"comment": "Chat messages within AI sessions", "schema": "public"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    role: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    agent_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    skill_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    metadata_json: Mapped[Optional[dict]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb")
    )
    prompt_tokens: Mapped[Optional[int]] = mapped_column(
        Integer, server_default=text("0")
    )
    completion_tokens: Mapped[Optional[int]] = mapped_column(
        Integer, server_default=text("0")
    )
    created_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(True), server_default=text("now()")
    )
    session_id: Mapped[Optional[int]] = mapped_column(BigInteger)


class NousModels(Base):
    __tablename__ = "nous_models"
    __table_args__ = (
        CheckConstraint(
            "category = ANY (ARRAY['transcription'::text, 'summarization'::text, 'analysis'::text])",
            name="nous_models_category_check",
        ),
        CheckConstraint(
            "pricing_type = ANY (ARRAY['per_hour'::text, 'per_request'::text, 'per_token'::text])",
            name="nous_models_pricing_type_check",
        ),
        PrimaryKeyConstraint("id", name="nous_models_pkey"),
        UniqueConstraint("name", name="nous_models_name_key"),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, server_default=text("generate_snowflake_id()")
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    actual_provider: Mapped[str] = mapped_column(Text, nullable=False)
    actual_model: Mapped[str] = mapped_column(Text, nullable=False)
    api_key: Mapped[str] = mapped_column(Text, nullable=False)
    pricing_type: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'per_hour'::text")
    )
    pricing_value: Mapped[decimal.Decimal] = mapped_column(
        Numeric, nullable=False, server_default=text("8")
    )
    is_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
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
    app_id: Mapped[Optional[str]] = mapped_column(Text)
    base_url: Mapped[Optional[str]] = mapped_column(Text)


class Skills(Base):
    __tablename__ = "skills"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id"], ["public.projects.id"], name="skills_project_id_fkey"
        ),
        ForeignKeyConstraint(
            ["team_id"], ["public.teams.id"], name="skills_team_id_fkey"
        ),
        PrimaryKeyConstraint("id", name="skills_pkey"),
        Index("idx_skills_created_by", "created_by"),
        Index("idx_skills_project_id", "project_id"),
        Index(
            "idx_skills_team_id",
            "team_id",
            postgresql_where="((status)::text = 'active'::text)",
        ),
        Index(
            "ux_skills_slug_creator",
            "created_by",
            "slug",
            postgresql_where="((created_by IS NOT NULL) AND (slug IS NOT NULL))",
            unique=True,
        ),
        Index(
            "ux_skills_slug_system",
            "slug",
            postgresql_where="((created_by IS NULL) AND (slug IS NOT NULL))",
            unique=True,
        ),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, server_default=text("generate_snowflake_id()")
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    current_version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )
    team_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    project_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    description: Mapped[Optional[str]] = mapped_column(Text)
    content_md: Mapped[Optional[str]] = mapped_column(Text)
    category: Mapped[Optional[str]] = mapped_column(Text)
    icon: Mapped[Optional[str]] = mapped_column(
        String(20), server_default=text("'✨'::character varying")
    )
    output_format: Mapped[Optional[str]] = mapped_column(Text)
    trigger_keywords: Mapped[Optional[list[str]]] = mapped_column(
        ARRAY(Text()), server_default=text("'{}'::text[]")
    )
    is_public: Mapped[Optional[bool]] = mapped_column(
        Boolean, server_default=text("false")
    )
    status: Mapped[Optional[str]] = mapped_column(
        String(20), server_default=text("'active'::character varying")
    )
    created_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(True), server_default=text("now()")
    )
    updated_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(True), server_default=text("now()")
    )
    prompt_template: Mapped[Optional[str]] = mapped_column(Text)
    input_schema: Mapped[Optional[dict]] = mapped_column(JSONB)
    default_agent_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    slug: Mapped[Optional[str]] = mapped_column(
        String(64), comment="Stable identifier (e.g. translate, compress)"
    )
    body_md: Mapped[Optional[str]] = mapped_column(
        Text, comment="Markdown body (skill prompt/instructions)"
    )
    frontmatter_json: Mapped[Optional[dict]] = mapped_column(
        JSONB,
        server_default=text("'{}'::jsonb"),
        comment="YAML frontmatter parsed as JSON (metadata, I/O schema hints)",
    )
    seed_hash: Mapped[Optional[str]] = mapped_column(
        Text, comment="sha256 of body_md + frontmatter_json; null = always re-upsert"
    )


class SkillFiles(Base):
    __tablename__ = "skill_files"
    __table_args__ = (
        CheckConstraint(
            "file_type = ANY (ARRAY['markdown'::text, 'script'::text, 'text-asset'::text, 'binary-ref'::text])",
            name="skill_files_file_type_check",
        ),
        ForeignKeyConstraint(
            ["skill_id"],
            ["public.skills.id"],
            ondelete="CASCADE",
            name="skill_files_skill_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="skill_files_pkey"),
        Index("idx_skill_files_skill", "skill_id", "sort_order"),
        Index("ux_skill_files_path", "skill_id", "path", unique=True),
        {
            "comment": "Supplementary files attached to a skill (references, templates, examples)",
            "schema": "public",
        },
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    skill_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    path: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Relative path within the skill bundle (e.g. SKILL.md, refs/example.md, assets/logo.png)",
    )
    file_type: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'markdown'::text"),
        comment="markdown | script | text-asset | binary-ref",
    )
    current_version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )
    content: Mapped[Optional[str]] = mapped_column(Text)
    sort_order: Mapped[Optional[int]] = mapped_column(Integer, server_default=text("0"))
    created_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(True), server_default=text("now()")
    )
    updated_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(True), server_default=text("now()")
    )
    binary_url: Mapped[Optional[str]] = mapped_column(
        Text, comment="URL for binary-ref assets (content is NULL in that case)"
    )
    seed_hash: Mapped[Optional[str]] = mapped_column(
        Text, comment="sha256 of file content; null = always re-upsert"
    )


class SkillVersions(Base):
    __tablename__ = "skill_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["skill_id"],
            ["public.skills.id"],
            ondelete="CASCADE",
            name="skill_versions_skill_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="skill_versions_pkey"),
        UniqueConstraint(
            "skill_id", "version_number", name="ux_skill_versions_skill_version"
        ),
        Index("ix_skill_versions_skill_id_desc", "skill_id", "version_number"),
        {"schema": "public"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    skill_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    body_md: Mapped[Optional[str]] = mapped_column(Text)
    frontmatter_json: Mapped[Optional[dict]] = mapped_column(JSONB)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)


class SkillFileVersions(Base):
    __tablename__ = "skill_file_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["skill_file_id"],
            ["public.skill_files.id"],
            ondelete="CASCADE",
            name="skill_file_versions_skill_file_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="skill_file_versions_pkey"),
        UniqueConstraint(
            "skill_file_id",
            "version_number",
            name="ux_skill_file_versions_file_version",
        ),
        Index("ix_skill_file_versions_file_id_desc", "skill_file_id", "version_number"),
        {"schema": "public"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    skill_file_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    content: Mapped[Optional[str]] = mapped_column(Text)
    file_type: Mapped[Optional[str]] = mapped_column(Text)
    binary_url: Mapped[Optional[str]] = mapped_column(Text)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
