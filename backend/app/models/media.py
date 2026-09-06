"""Media-related ORM models: ParsedMedia, Resources, ResourceItems, ResourceVersions,
ResourceTags, ResourceSummaries, ResourceTranscripts, ResourceAccessLogs,
ResourceAnalysis, Folders.

``ResourceAnalysis`` maps ``resource_analysis`` — created in its final post-076
form by migration 262 (the original 014→076 chain was never applied to the
self-hosted prod instance). Validated: schema-drift guard + the repo integration
tests pass against the live dev DB (== prod schema).
"""

from __future__ import annotations

import datetime
import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    Double,
    Enum,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    PrimaryKeyConstraint,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.orm_base import Base, UserScoped
from app.models._enums import AiTaskStatus, DownloadStatus


class ParsedMedia(Base):
    __tablename__ = "parsed_media"
    __table_args__ = (
        CheckConstraint(
            "extract_audio_status = ANY (ARRAY['pending'::text, 'processing'::text,"
            " 'completed'::text, 'failed'::text, 'skipped'::text])",
            name="parsed_media_extract_audio_status_check",
        ),
        PrimaryKeyConstraint("id", name="videos_pkey"),
        UniqueConstraint(
            "platform_id",
            "source_platform",
            name="videos_platform_source_unique",
        ),
        Index("idx_parsed_media_author", "author"),
        Index("idx_parsed_media_cover_status", "cover_download_status"),
        Index("idx_parsed_media_created_at", "created_at"),
        Index("idx_parsed_media_download_status", "video_download_status"),
        Index(
            "idx_parsed_media_image_status",
            "image_download_status",
            postgresql_where="(image_download_status <> 'skipped'::download_status)",
        ),
        Index("idx_parsed_media_media_type", "media_type"),
        Index("idx_parsed_media_platform_id", "platform_id"),
        Index("idx_parsed_media_source_platform", "source_platform"),
        Index("idx_parsed_media_storage_size", "storage_size"),
        Index("idx_parsed_media_view_count", "view_count"),
        {
            "comment": "Main media table (renamed from videos). PK is BIGINT Snowflake ID.",
            "schema": "public",
        },
    )

    platform_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="Unique identifier on the source platform (was aweme_id)",
    )
    original_url: Mapped[str] = mapped_column(String(512), nullable=False)
    source_platform: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        server_default=text("'douyin'::character varying"),
        comment="Source platform: douyin, youtube, bilibili, twitter, other",
    )
    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_snowflake_id()"),
        comment="Snowflake BIGINT primary key (was display_id, originally UUID)",
    )
    extract_audio_status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'pending'::text"),
        comment=(
            "Status of the extract_audio_workflow for this media: pending (not run\n"
            "   yet) / processing / completed / failed / skipped (no video to extract\n"
            "   audio from, e.g. image carousels). Distinct from music_download_status\n"
            "   (which tracks downloaded music URLs). Drives the MediaCard audio icon."
        ),
    )
    download_retry_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    like_count: Mapped[int | None] = mapped_column(Integer, server_default=text("0"))
    comment_count: Mapped[int | None] = mapped_column(Integer, server_default=text("0"))
    share_count: Mapped[int | None] = mapped_column(Integer, server_default=text("0"))
    favorite_count: Mapped[int | None] = mapped_column(
        Integer, server_default=text("0")
    )
    duration: Mapped[str | None] = mapped_column(String(50))
    resolution: Mapped[str | None] = mapped_column(String(50))
    datasize: Mapped[str | None] = mapped_column(String(50))
    hashtags: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
    author: Mapped[str | None] = mapped_column(String(255))
    title: Mapped[str | None] = mapped_column(Text)
    media_type: Mapped[str | None] = mapped_column(
        String(50),
        comment="Content type: video, carousel, image_text, special, short, live_clip",
    )
    description: Mapped[str | None] = mapped_column(Text)
    video_download_urls: Mapped[dict | None] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb")
    )
    image_download_urls: Mapped[dict | None] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb")
    )
    music_name: Mapped[str | None] = mapped_column(String(255))
    video_download_status: Mapped[DownloadStatus | None] = mapped_column(
        Enum(
            DownloadStatus,
            values_callable=lambda cls: [member.value for member in cls],
            name="download_status",
        ),
        server_default=text("'pending'::download_status"),
    )
    music_download_status: Mapped[DownloadStatus | None] = mapped_column(
        Enum(
            DownloadStatus,
            values_callable=lambda cls: [member.value for member in cls],
            name="download_status",
        ),
        server_default=text("'pending'::download_status"),
    )
    download_duration: Mapped[float | None] = mapped_column(Double(53))
    download_path: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    download_time: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
    created_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(True), server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(True), server_default=text("now()")
    )
    cover_urls: Mapped[dict | None] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb")
    )
    dynamic_cover_url: Mapped[str | None] = mapped_column(Text)
    cover_download_status: Mapped[DownloadStatus | None] = mapped_column(
        Enum(
            DownloadStatus,
            values_callable=lambda cls: [member.value for member in cls],
            name="download_status",
        ),
        server_default=text("'pending'::download_status"),
    )
    cover_download_path: Mapped[str | None] = mapped_column(Text)
    ai_extract_text: Mapped[str | None] = mapped_column(
        Text, comment="AI extracted summary from video content"
    )
    ai_rewrite_text: Mapped[str | None] = mapped_column(
        Text, comment="AI rewritten video description"
    )
    ai_analyze_text: Mapped[str | None] = mapped_column(
        Text, comment="AI content analysis result"
    )
    ai_generated_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(True),
        comment="Timestamp when AI content was last generated",
    )
    view_count: Mapped[int | None] = mapped_column(Integer, server_default=text("0"))
    last_viewed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
    storage_size: Mapped[int | None] = mapped_column(BigInteger)
    keep_forever: Mapped[bool | None] = mapped_column(
        Boolean, server_default=text("false")
    )
    datasize_bytes: Mapped[int | None] = mapped_column(
        BigInteger,
        server_default=text("0"),
        comment="Raw video file size in bytes for aggregation queries",
    )
    hls_path: Mapped[str | None] = mapped_column(
        Text,
        comment="HLS playlist path relative to downloads directory",
    )
    media_format: Mapped[str | None] = mapped_column(
        String(10),
        server_default=text("'mp4'::character varying"),
        comment="Storage format: mp4 (legacy) or hls (new)",
    )
    image_download_status: Mapped[DownloadStatus | None] = mapped_column(
        Enum(
            DownloadStatus,
            values_callable=lambda cls: [member.value for member in cls],
            name="download_status",
        ),
        server_default=text("'skipped'::download_status"),
    )
    image_download_path: Mapped[str | None] = mapped_column(Text)
    music_download_path: Mapped[str | None] = mapped_column(
        Text, comment="Relative path to downloaded music file"
    )
    music_play_urls: Mapped[dict | None] = mapped_column(
        JSONB,
        server_default=text("'[]'::jsonb"),
        comment=(
            "Standalone music play URLs for carousel/image-text content (type 2/68)."
            " List of fallback URLs from music.play_url.url_list."
        ),
    )
    extract_audio_path: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict | None] = mapped_column(
        "metadata", JSONB, server_default=text("'{}'::jsonb")
    )


# The values ``resources.source_type`` may hold. ONE source of truth for the
# ORM CHECK below and for every router allowlist that validates the column
# (``resources_crud_router``, ``resources_search_router``). The DB side is
# migration 363 (309 added ``generated``, 363 added ``derived``); the mirror
# test ``tests/test_resources_source_type_mirror.py`` reads that migration and
# refuses a drift in either direction — the model declared ``('web','upload')``
# for a year after the DB had four values, and nothing noticed until a filter
# started depending on the wider set.
RESOURCE_SOURCE_TYPES: tuple[str, ...] = ("web", "upload", "generated", "derived")


def _resource_source_type_check_sql() -> str:
    """Render the CHECK the way ``pg_get_constraintdef`` prints it, so a
    model-vs-live comparison sees the same shape it would for any other
    varchar enum constraint in this file."""
    members = ", ".join(
        f"'{v}'::character varying::text" for v in RESOURCE_SOURCE_TYPES
    )
    return f"source_type::text = ANY (ARRAY[{members}])"


class Resources(Base, UserScoped):
    """Resource-library rows, owned per-user via ``creator_id`` (a UUID — the
    Supabase auth ``sub``).

    First production opt-in of the app-layer tenant-scope choke point
    (app/db/scope.py). ``UserScoped`` names ``creator_id`` as the owner column so
    the choke point can inject ``creator_id == scope.user_id`` on reads and
    fail-closed on cross-user writes. The mixin is ALWAYS on (stable class
    hierarchy), but enforcement is GATED by ``settings.SCOPE_ENFORCE_RESOURCES``
    (default false): inert until the flag flips on (epic A). The mixin declares
    NO column — ``creator_id`` already exists below."""

    __tablename__ = "resources"
    __tenant_user_col__ = "creator_id"
    __table_args__ = (
        CheckConstraint("rating >= 0 AND rating <= 5", name="resources_rating_check"),
        CheckConstraint(
            "prompt_origin IN ('typed', 'extracted', 'captioned')",
            name="resources_prompt_origin_check",
        ),
        CheckConstraint(
            _resource_source_type_check_sql(),
            name="resources_source_type_check",
        ),
        ForeignKeyConstraint(
            ["media_id"],
            ["public.parsed_media.id"],
            ondelete="SET NULL",
            name="resources_video_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="resources_pkey"),
        Index("idx_resources_aspect_bucket", "aspect_bucket"),
        Index("idx_resources_creator", "creator_id"),
        Index(
            "idx_resources_file_hash",
            "file_hash",
            postgresql_where="((file_hash IS NOT NULL) AND (is_trashed = false))",
        ),
        Index(
            "idx_resources_media_id",
            "media_id",
            postgresql_where="(media_id IS NOT NULL)",
        ),
        Index(
            "idx_resources_summary_status",
            "summary_status",
            postgresql_where="(summary_status <> 'none'::ai_task_status)",
        ),
        Index(
            "idx_resources_transcript_status",
            "transcript_status",
            postgresql_where="(transcript_status <> 'none'::ai_task_status)",
        ),
        Index(
            "idx_resources_trashed",
            "is_trashed",
            postgresql_where="(is_trashed = true)",
        ),
        {"schema": "public"},
    )

    creator_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    source_type: Mapped[str] = mapped_column(String(20), nullable=False)
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    current_version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )
    is_trashed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_snowflake_id()"),
    )
    transcript_status: Mapped[AiTaskStatus] = mapped_column(
        Enum(
            AiTaskStatus,
            values_callable=lambda cls: [member.value for member in cls],
            name="ai_task_status",
        ),
        nullable=False,
        server_default=text("'none'::ai_task_status"),
        comment="Per-user AI transcript status: none|pending|processing|completed|failed",
    )
    summary_status: Mapped[AiTaskStatus] = mapped_column(
        Enum(
            AiTaskStatus,
            values_callable=lambda cls: [member.value for member in cls],
            name="ai_task_status",
        ),
        nullable=False,
        server_default=text("'none'::ai_task_status"),
        comment="Per-user AI summary status: none|pending|processing|completed|failed",
    )
    visual_analysis_status: Mapped[AiTaskStatus] = mapped_column(
        Enum(
            AiTaskStatus,
            values_callable=lambda cls: [member.value for member in cls],
            name="ai_task_status",
        ),
        nullable=False,
        server_default=text("'none'::ai_task_status"),
        comment="Per-user AI visual analysis status: none|pending|processing|completed|failed",
    )
    file_type: Mapped[str | None] = mapped_column(String(50))
    mime_type: Mapped[str | None] = mapped_column(String(100))
    file_path: Mapped[str | None] = mapped_column(Text)
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    resolution: Mapped[str | None] = mapped_column(String(50))
    thumbnail_path: Mapped[str | None] = mapped_column(Text)
    cover_image_path: Mapped[str | None] = mapped_column(Text)
    trashed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
    media_id: Mapped[int | None] = mapped_column(BigInteger)
    file_hash: Mapped[str | None] = mapped_column(
        String(64),
        comment="SHA-256 hex digest of the latest version file content",
    )
    notes: Mapped[str | None] = mapped_column(Text)
    gen_prompt: Mapped[str | None] = mapped_column(
        Text,
        comment="AI generation prompt for this asset (user-entered or auto-extracted)",
    )
    gen_prompt_zh: Mapped[str | None] = mapped_column(
        Text,
        comment="Chinese-language AI generation prompt (user-entered or provider-translated)",
    )
    gen_prompt_negative: Mapped[str | None] = mapped_column(
        Text, comment="Negative AI generation prompt (EN side)"
    )
    gen_prompt_negative_zh: Mapped[str | None] = mapped_column(
        Text, comment="Negative AI generation prompt (ZH side)"
    )
    slide_prompts: Mapped[dict | None] = mapped_column(
        JSONB,
        comment=(
            "Per-slide prompts for download albums, keyed by slide filename: "
            '{"<name>": {en, zh, neg_en, neg_zh}}'
        ),
    )
    gen_params: Mapped[dict | None] = mapped_column(
        JSONB,
        comment=(
            "Normalised AI generation parameters (tool/model/sampler/steps/"
            "cfg/seed/width/height/loras...); every key optional (440)"
        ),
    )
    summary_follow_up: Mapped[dict | None] = mapped_column(
        JSONB,
        comment=(
            "One-shot server-side intent: summarize after the in-flight/next "
            "transcription completes. {requested_by, requested_at}. Written "
            "by the transcribe trigger endpoint, consumed (read + cleared) by "
            "ai_transcription's success chain. NULL = nobody waiting (438)."
        ),
    )
    gen_prompt_json: Mapped[str | None] = mapped_column(
        Text,
        comment="Structured JSON prompt (subject/style/composition/lighting/color/text/aspect_ratio)",
    )
    prompt_origin: Mapped[str | None] = mapped_column(
        Text,
        comment="Last writer of the prompt text: typed | extracted | captioned (mig 455)",
    )
    url: Mapped[str | None] = mapped_column(Text)
    rating: Mapped[int | None] = mapped_column(SmallInteger, server_default=text("0"))
    last_folder_id: Mapped[int | None] = mapped_column(BigInteger)
    last_library_id: Mapped[int | None] = mapped_column(BigInteger)
    last_scope_type: Mapped[str | None] = mapped_column(String(20))
    last_scope_id: Mapped[str | None] = mapped_column(Text)
    aspect_bucket: Mapped[str | None] = mapped_column(
        Text,
        Computed("resource_aspect_bucket((resolution)::text)", persisted=True),
    )
    # Added to the DB by a later migration than the model reflection; the ORM
    # repo's read dicts dropped them silently (the GET/PUT lyrics endpoints and
    # the audio-bitrate field), so map them to keep ORM parity with the REST/
    # PostgREST `select(*)` shape.
    audio_bitrate_kbps: Mapped[int | None] = mapped_column(Integer)
    lyrics_json: Mapped[dict | None] = mapped_column(JSONB)
    chorus_start_ms: Mapped[int | None] = mapped_column(Integer)


class ResourceAccessLogs(Base):
    __tablename__ = "resource_access_logs"
    __table_args__ = (
        CheckConstraint(
            "action::text = ANY (ARRAY['view'::character varying::text,"
            " 'play'::character varying::text, 'share'::character varying::text,"
            " 'download_again'::character varying::text, 'export'::character varying::text])",
            name="video_access_logs_action_check",
        ),
        ForeignKeyConstraint(
            ["resource_id"],
            ["public.resources.id"],
            ondelete="CASCADE",
            name="resource_access_logs_resource_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="video_access_logs_pkey"),
        Index("idx_resource_access_logs_resource_id", "resource_id"),
        Index("idx_resource_access_logs_user_id", "user_id"),
        {"comment": "User access logs for parsed media.", "schema": "public"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    resource_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB)
    created_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(True), server_default=text("now()")
    )


class ResourceSummaries(Base):
    __tablename__ = "resource_summaries"
    __table_args__ = (
        ForeignKeyConstraint(
            ["resource_id"],
            ["public.resources.id"],
            ondelete="CASCADE",
            name="resource_summaries_resource_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="resource_summaries_pkey"),
        UniqueConstraint("resource_id", name="resource_summaries_resource_id_key"),
        {"comment": "AI-generated summaries for parsed media.", "schema": "public"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    resource_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    summary_type: Mapped[str | None] = mapped_column(String(20))
    summary_text: Mapped[str | None] = mapped_column(Text)
    key_points: Mapped[dict | None] = mapped_column(JSONB)
    topics: Mapped[dict | None] = mapped_column(JSONB)
    llm_model: Mapped[str | None] = mapped_column(String(50))
    llm_provider: Mapped[str | None] = mapped_column(String(50))
    created_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(True), server_default=text("now()")
    )


class ResourceTranscripts(Base):
    __tablename__ = "resource_transcripts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["resource_id"],
            ["public.resources.id"],
            ondelete="CASCADE",
            name="resource_transcripts_resource_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="resource_transcripts_pkey"),
        UniqueConstraint("resource_id", name="resource_transcripts_resource_id_key"),
        {"comment": "AI-generated transcripts for parsed media.", "schema": "public"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    resource_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    language: Mapped[str | None] = mapped_column(String(10))
    full_text: Mapped[str | None] = mapped_column(Text)
    segments: Mapped[dict | None] = mapped_column(JSONB)
    whisper_model: Mapped[str | None] = mapped_column(String(50))
    duration_seconds: Mapped[float | None] = mapped_column(Double(53))
    created_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(True), server_default=text("now()")
    )


class ResourceVersions(Base):
    __tablename__ = "resource_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["resource_id"],
            ["public.resources.id"],
            ondelete="CASCADE",
            name="resource_versions_resource_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="resource_versions_pkey"),
        UniqueConstraint(
            "resource_id",
            "version_number",
            name="resource_versions_resource_id_version_number_key",
        ),
        Index("idx_resource_versions_resource", "resource_id"),
        Index("idx_resource_versions_uploaded_by", "uploaded_by"),
        {"schema": "public"},
    )

    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    resource_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_snowflake_id()"),
    )
    filename: Mapped[str | None] = mapped_column(String(500))
    file_path: Mapped[str | None] = mapped_column(Text)
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    mime_type: Mapped[str | None] = mapped_column(String(100))
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    resolution: Mapped[str | None] = mapped_column(String(50))
    thumbnail_path: Mapped[str | None] = mapped_column(Text)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    notes: Mapped[str | None] = mapped_column(Text)
    hls_path: Mapped[str | None] = mapped_column(
        Text,
        comment="Relative path to master.m3u8 (null = not transcoded)",
    )
    transcode_status: Mapped[str | None] = mapped_column(
        String(20),
        server_default=text("NULL::character varying"),
        comment="pending / processing / completed / failed",
    )
    transcode_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(True), comment="When transcoding completed"
    )
    file_hash: Mapped[str | None] = mapped_column(
        String(64),
        comment="SHA-256 hex digest of this version file content",
    )
    audio_bitrate_kbps: Mapped[int | None] = mapped_column(Integer)
    storage_status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'ok'::text"),
        comment="迁移/读取时的源文件存在性: ok=正常, source_missing=文件已丢失(记录保留)",
    )


class ResourceItems(Base):
    __tablename__ = "resource_items"
    __table_args__ = (
        ForeignKeyConstraint(
            ["folder_id"],
            ["public.folders.id"],
            ondelete="SET NULL",
            name="resource_items_folder_id_fkey",
        ),
        ForeignKeyConstraint(
            ["library_id"],
            ["public.libraries.id"],
            ondelete="CASCADE",
            name="resource_items_library_id_fkey",
        ),
        ForeignKeyConstraint(
            ["resource_id"],
            ["public.resources.id"],
            ondelete="CASCADE",
            name="resource_items_resource_id_fkey",
        ),
        ForeignKeyConstraint(
            ["scope_id"],
            ["public.teams.id"],
            ondelete="CASCADE",
            name="resource_items_scope_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="resource_items_pkey"),
        Index("idx_resource_items_added_by", "added_by"),
        Index(
            "idx_resource_items_folder",
            "folder_id",
            postgresql_where="(folder_id IS NOT NULL)",
        ),
        Index(
            "idx_resource_items_library",
            "library_id",
            postgresql_where="(library_id IS NOT NULL)",
        ),
        Index("idx_resource_items_resource", "resource_id"),
        Index("idx_resource_items_scope_id", "scope_id"),
        {"schema": "public"},
    )

    scope_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    resource_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_snowflake_id()"),
    )
    added_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    folder_id: Mapped[int | None] = mapped_column(BigInteger)
    library_id: Mapped[int | None] = mapped_column(BigInteger)


class ResourceTags(Base):
    __tablename__ = "resource_tags"
    __table_args__ = (
        ForeignKeyConstraint(
            ["resource_id"],
            ["public.resources.id"],
            ondelete="CASCADE",
            name="resource_tags_resource_id_fkey",
        ),
        ForeignKeyConstraint(
            ["tag_id"],
            ["public.tags.id"],
            ondelete="CASCADE",
            name="resource_tags_tag_id_fkey",
        ),
        PrimaryKeyConstraint("resource_id", "tag_id", name="resource_tags_pkey"),
        Index("idx_resource_tags_resource", "resource_id"),
        Index("idx_resource_tags_tag_id", "tag_id"),
        Index("idx_resource_tags_tagged_by", "tagged_by"),
        {"schema": "public"},
    )

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    resource_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tag_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tagged_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    source: Mapped[str | None] = mapped_column(
        String(50), server_default=text("'user'::character varying")
    )
    confidence: Mapped[float | None] = mapped_column(Double(53))


class GalleryItems(Base):
    __tablename__ = "gallery_items"
    __table_args__ = (
        ForeignKeyConstraint(
            ["gallery_id"],
            ["public.resources.id"],
            ondelete="CASCADE",
            name="gallery_items_gallery_id_fkey",
        ),
        ForeignKeyConstraint(
            ["image_id"],
            ["public.resources.id"],
            ondelete="CASCADE",
            name="gallery_items_image_id_fkey",
        ),
        PrimaryKeyConstraint("gallery_id", "image_id", name="gallery_items_pkey"),
        UniqueConstraint(
            "gallery_id", "position", name="gallery_items_gallery_id_position_key"
        ),
        Index("idx_gallery_items_image", "image_id"),
        {"schema": "public"},
    )

    gallery_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    image_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )


class Folders(Base):
    __tablename__ = "folders"
    __table_args__ = (
        CheckConstraint(
            "visibility::text = ANY (ARRAY['inherited'::character varying::text,"
            " 'restricted'::character varying::text])",
            name="folders_visibility_check",
        ),
        ForeignKeyConstraint(
            ["library_id"],
            ["public.libraries.id"],
            ondelete="CASCADE",
            name="folders_library_id_fkey",
        ),
        ForeignKeyConstraint(
            ["parent_id"],
            ["public.folders.id"],
            ondelete="CASCADE",
            name="folders_parent_id_fkey",
        ),
        ForeignKeyConstraint(
            ["scope_id"],
            ["public.teams.id"],
            ondelete="CASCADE",
            name="folders_scope_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="folders_pkey"),
        Index("idx_folders_created_by", "created_by"),
        Index(
            "idx_folders_library",
            "library_id",
            postgresql_where="(library_id IS NOT NULL)",
        ),
        Index("idx_folders_parent", "parent_id"),
        Index("idx_folders_scope_id", "scope_id"),
        Index(
            "idx_folders_trashed_scope_id",
            "scope_id",
            "is_trashed",
            postgresql_where="(is_trashed = true)",
        ),
        Index(
            "ux_folders_scope_system_key",
            "scope_id",
            "system_key",
            unique=True,
            postgresql_where="(system_key IS NOT NULL AND is_trashed = false)",
        ),
        {"schema": "public"},
    )

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    scope_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    is_system: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    # Stable identity of a system folder (e.g. 'cover_templates'), independent
    # of its display name. is_system is the "protected" switch; both are set
    # together. Migration 441.
    system_key: Mapped[str | None] = mapped_column(Text)
    visibility: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default=text("'inherited'::character varying"),
    )
    is_trashed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_snowflake_id()"),
    )
    icon: Mapped[str | None] = mapped_column(String(50))
    color: Mapped[str | None] = mapped_column(String(20))
    trashed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
    parent_id: Mapped[int | None] = mapped_column(BigInteger)
    library_id: Mapped[int | None] = mapped_column(BigInteger)
    is_smart: Mapped[bool | None] = mapped_column(Boolean, server_default=text("false"))
    smart_rules: Mapped[dict | None] = mapped_column(JSONB)


class ResourceAnalysis(Base):
    __tablename__ = "resource_analysis"
    __table_args__ = (
        CheckConstraint(
            "analysis_level::text = ANY (ARRAY['none'::character varying::text, "
            "'L1'::character varying::text, 'L2'::character varying::text, "
            "'L3'::character varying::text])",
            name="video_analysis_analysis_level_check",
        ),
        ForeignKeyConstraint(
            ["resource_id"],
            ["public.resources.id"],
            ondelete="CASCADE",
            name="resource_analysis_resource_id_fkey",
        ),
        PrimaryKeyConstraint(
            "resource_id", "analysis_level", name="resource_analysis_pkey"
        ),
        {"schema": "public"},
    )

    resource_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    analysis_level: Mapped[str] = mapped_column(
        String(10),
        primary_key=True,
        server_default=text("'none'::character varying"),
    )
    visual_description: Mapped[str | None] = mapped_column(Text)
    detected_objects: Mapped[dict | None] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb")
    )
    detected_scenes: Mapped[dict | None] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb")
    )
    detected_people: Mapped[dict | None] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb")
    )
    detected_text: Mapped[str | None] = mapped_column(Text)
    full_text_for_embedding: Mapped[str | None] = mapped_column(Text)
    content_embedding: Mapped[list[float] | None] = mapped_column(Vector(1536))
    analysis_model: Mapped[str | None] = mapped_column(String(50))
    analysis_cost: Mapped[float | None] = mapped_column(
        Numeric(10, 6), server_default=text("0")
    )
    analyzed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
    created_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(True), server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(True), server_default=text("now()")
    )
