# app/core/config.py

"""
应用程序配置模块

配置优先级：环境变量 > .env 文件 > config.yml 文件 > 默认值
"""

from pathlib import Path
from typing import ClassVar

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic_settings.sources import YamlConfigSettingsSource


class Settings(BaseSettings):
    """应用程序设置类"""

    # ============================================
    # 基础设置
    # ============================================
    APP_NAME: str = Field(default="抖音视频分析系统", description="应用名称")

    # 项目根目录
    ROOT_DIR: ClassVar[Path] = Path(__file__).parent.parent.parent

    # ============================================
    # 服务器设置
    # ============================================
    HOST: str = Field(default="0.0.0.0", description="服务器监听地址")
    APP_PORT: int = Field(default=8080, description="服务器端口")
    RELOAD: bool = Field(default=False, description="是否启用热重载")

    # ============================================
    # CORS 设置
    # ============================================
    CORS_ORIGINS: list[str] = Field(default=["*"], description="允许的跨域来源")
    CORS_CREDENTIALS: bool = Field(default=True, description="是否允许凭证")

    # ============================================
    # Supabase 配置（必需）
    # ============================================
    SUPABASE_URL: str = Field(default="", description="Supabase 项目 URL")
    SUPABASE_ANON_KEY: str = Field(default="", description="Supabase 匿名密钥")
    SUPABASE_SERVICE_ROLE_KEY: str = Field(
        default="", description="Supabase 服务角色密钥"
    )
    MEDIA_TOKEN_SECRET: str = Field(
        default="",
        description="dedicated HMAC key for media tokens (#276); falls back to SUPABASE_SERVICE_ROLE_KEY when empty",
    )
    SUPABASE_TENANT_ID: str = Field(
        default="", description="Supabase 多租户 ID (自托管)"
    )

    # ============================================
    # Supavisor 直连 PG（绕开 PostgREST/HTTP 层 — Bug C 治本）
    # ============================================
    # Format:
    #   postgresql://postgres.{tenant_id}:{password}@{host}:{port}/{db}
    # Transaction-mode pooler (port 6543 inside container, ${HOST_PORT}
    # mapped on host). Tenant ID encoded in user name per Supavisor
    # convention. Empty string = feature disabled (repository_base
    # falls back to supabase-py path).
    SUPAVISOR_DATABASE_URL: str = Field(
        default="",
        description="asyncpg DSN to Supavisor transaction-mode pooler",
    )
    SUPAVISOR_POOL_MIN_SIZE: int = Field(
        default=2, description="asyncpg pool min connections"
    )
    SUPAVISOR_POOL_MAX_SIZE: int = Field(
        default=10, description="asyncpg pool max connections"
    )
    # Per-repository feature flags. Default false = legacy supabase-py
    # path. Set true on a single repo to A/B-test the asyncpg version
    # without affecting the rest. After a repo is proven stable for a
    # week in prod the default flips and the legacy code is removed.
    USE_ORM_AGENT_RUNS: bool = Field(
        default=False,
        description="Route AgentRunsRepository through the SQLAlchemy 2.0 ORM "
        "session layer (Task 5.3 — replaces the asyncpg agent_runs path; fixes "
        "the silent-rollback P0 by committing writes via write_scope(). Covers "
        "list_by_agent / get_by_id / list_children reads + request_cancel / "
        "mark_heartbeat_lost writes + monthly_usage_by_agent aggregation)",
    )
    USE_ORM_AGENTS: bool = Field(
        default=False,
        description="Route AgentRepository through the SQLAlchemy 2.0 ORM "
        "session layer (Phase 2 pilot — replaces the supabase-py REST path for "
        "ai_agents / agent_skills / ai_agent_versions). Strategy C value-type "
        "parity: uuid columns (id / user_id / created_by) are coerced to str at "
        "the dict boundary to match the REST baseline (consumers do "
        "UUID(agent['id']) / dict-key lookups / supabase inserts that break on a "
        "native uuid.UUID); bigint team_id / project_id stay native int (REST "
        "returned int; consumers do int(...) / bare-int compares). Covers "
        "get_by_slug / get_by_id / list_persistent / list_accessible / "
        "get_skill_ids reads + update_skill_bindings / update_fields / insert / "
        "update_fields_versioned writes (writes commit via write_scope())",
    )
    USE_ORM_RESOURCES: bool = Field(
        default=False,
        description="Route ResourcesRepository through the SQLAlchemy 2.0 ORM "
        "session layer (Task 5.2 — replaces the asyncpg resources path; fixes "
        "the silent-rollback P0 by committing writes via write_scope() and "
        "running folder cascades atomically in one write_scope(). Covers "
        "resources / resource_items / resource_versions / folders; the "
        "resource_tags + smart-folder methods inherit legacy via MRO)",
    )
    USE_ORM_MEDIA: bool = Field(
        default=False,
        description="Route MediaRepository through the SQLAlchemy 2.0 ORM "
        "session layer (Task 5.1 — replaces the asyncpg media path; fixes "
        "the silent-rollback P0 by committing writes via write_scope(). "
        "Covers parsed_media CRUD + lists + search + statistics; the 9 "
        "wrapper methods route through the ORM overrides via Python MRO)",
    )
    USE_ORM_STYLE_TEMPLATES: bool = Field(
        default=False,
        description="Route StyleTemplateRepository through the SQLAlchemy 2.0 "
        "ORM session layer (Batch L1 — replaces the supabase-py REST path for "
        "the style_templates table). Strategy C: created_by uuid → str at the "
        "dict boundary (REST-parity); bigint id / team_id stay native int; "
        "created_at / updated_at → ISO str. Covers get_by_id / list_templates "
        "reads + create / update / hard_delete writes (commit via "
        "write_scope()). NOTE: style_templates_router is a 301 redirect to "
        "/skills, so this repo has no live call sites today.",
    )
    USE_ORM_TAG_PREFERENCES: bool = Field(
        default=False,
        description="Route TagPreferencesRepository through the SQLAlchemy 2.0 "
        "ORM session layer (Batch L1 — replaces the supabase-py REST path for "
        "the user_tag_preferences table). Strategy C is a no-op: the public "
        "return shape is a fixed 3-key defaults-merged dict "
        "(starred_tag_ids list / picker_settings dict / panel_size dict) with "
        "no uuid / datetime / bigint output field; user_id is an input only. "
        "Covers get_preferences read + upsert_preferences write (ON CONFLICT "
        "user_id, commits via write_scope()).",
    )
    USE_ORM_LIBRARIES: bool = Field(
        default=False,
        description="Route LibrariesRepository through the SQLAlchemy 2.0 ORM "
        "session layer (Batch L1 — replaces the supabase-py REST path for the "
        "libraries table). Strategy C: created_by uuid → str at the dict "
        "boundary (REST-parity); bigint id stays native int; text "
        "scope_id / scope_type stay str; created_at / updated_at → ISO str. "
        "Covers get_by_id / list_by_scope reads + create / update / delete "
        "writes (commit via write_scope(); create/update return {} on empty "
        "per REST contract).",
    )
    USE_ORM_NOTIFICATIONS: bool = Field(
        default=False,
        description="Route NotificationRepository through the SQLAlchemy 2.0 "
        "ORM session layer (Batch L1b — replaces the supabase-py REST path for "
        "the notifications / user_notifications / team_members tables). "
        "Strategy C: bigint id / notification_id / team_id stay native int (the "
        "5.3 trap — the team-membership filter does `n['team_id'] in team_ids` "
        "with int both sides); created_at → ISO str; created_by uuid → str for "
        "shape parity (router serializes straight to HTTP; no type-sensitive "
        "Python consumer). user_id is an input only. Covers get_user_"
        "notifications (multi-table read + read-status join) / get_unread_count "
        "/ mark_as_read / mark_all_as_read writes (upsert, commit via "
        "write_scope()). delete_notification preserves the legacy graceful "
        "no-op contract (writes a phantom dismissed_at column → returns False).",
    )
    USE_ORM_SCRIPTS: bool = Field(
        default=False,
        description="Route the four Script*Repository classes (ScriptProject / "
        "ScriptChapter / ScriptAsset / ScriptStoryboardLink) through the "
        "SQLAlchemy 2.0 ORM session layer (Batch L1b — replaces the supabase-py "
        "REST path for script_projects / script_chapters / script_assets / "
        "script_storyboard_links). Strategy C: all ids are bigint and stay "
        "native int (the 5.3 trap); created_by uuid → str for shape parity; "
        "created_at / updated_at → ISO str. Covers the BaseRepository CRUD "
        "surface + list_by_project (paginated count) / bulk_upsert / "
        "get_by_script / list_by_script / list_by_chapter / list_by_storyboard "
        "(writes commit via write_scope()).",
    )
    USE_ORM_LOGS: bool = Field(
        default=False,
        description="Route LogsRepository (the user-facing user_logs viewer / "
        "CSV export) through the SQLAlchemy 2.0 ORM session layer (Batch L2). "
        "Strategy C: bigint id stays native int (the 5.3 trap); user_id uuid → "
        "str for shape parity (the LogEntry response model has no user_id field "
        "and no consumer reads it type-sensitively); created_at → ISO str "
        "(CONSUMED — the CSV export does str(created_at)). Covers get_logs / "
        "get_logs_for_export reads + create_log / delete_logs writes (commit "
        "via write_scope()). NOTE: user_logs is also served by "
        "UserLogsRepository (USE_ORM_USER_LOGS) — disjoint method sets, both "
        "live.",
    )
    USE_ORM_USER_LOGS: bool = Field(
        default=False,
        description="Route UserLogsRepository (the append-only user_logs writer "
        "+ get_recent / get_paginated / get_by_aweme_id reads) through the "
        "SQLAlchemy 2.0 ORM session layer (Batch L2). Strategy C: bigint id "
        "stays native int; user_id uuid → str (shape parity); created_at → ISO "
        "str. PRESERVES the legacy create() soft-skip on a missing user_id "
        "(Celery orphan-download NOT-NULL spam guard). Writes commit via "
        "write_scope(). NOTE: shares the user_logs table with LogsRepository "
        "(USE_ORM_LOGS).",
    )
    SCOPE_ENFORCE_RESOURCES: bool = Field(
        default=False,
        description="Activate the app-layer tenant-scope choke point "
        "(app/db/scope.py) for the `resources` table (epic A / A1). The "
        "`Resources` model ALWAYS mixes in `UserScoped(creator_id)` for a stable "
        "class hierarchy, but the choke point ENFORCES resources only when this "
        "flag is on: off (default) = fully legacy/inert (resources behaves "
        "exactly as an unscoped table — no tenant injection, no fail-closed "
        "raise, no compile overhead); on = deny-by-default tenant isolation "
        "(reads injected with `creator_id == scope.user_id`, cross-user bulk "
        "DML forbidden, unscoped access fail-closed). Default-off so the mixin "
        "and all callers can land BEFORE enforcement flips on. Instant rollback "
        "= flip back to false.",
    )
    USE_ORM_USER_SETTINGS: bool = Field(
        default=False,
        description="Route UserSettingsRepository reads + the settings_json "
        "atomic merge through the SQLAlchemy 2.0 ORM session layer (Task 5.4). "
        "Reads run on read_scope() + select(UserSettings); the canonical "
        "settings_json merge runs the SAME COALESCE(existing,'{}'::jsonb) || "
        "CAST(:patch AS jsonb) ON CONFLICT statement inside write_scope() "
        "(committing) instead of db_engine.execute_returning_one. The "
        "merge-not-replace guarantee (the #485 shared-blob clobber P0) is "
        "preserved byte-for-byte. The PostgREST read-merge-write path stays "
        "as the engine-not-configured fallback",
    )
    USE_ORM_PROJECTS: bool = Field(
        default=False,
        description="Route ProjectsRepository (the MediaTrack project system: "
        "projects / project_files / project_folders / project_members / "
        "project_tasks / file_versions / review_comments / parsed_media / "
        "shares / project_collections — 10 tables) through the SQLAlchemy 2.0 "
        "ORM session layer (Phase 2 L-solo). Strategy C value-type parity: ALL "
        "bigint ids + FKs (project/file/folder/task/version/share/collection ids "
        "+ project_id / media_id / folder_id / parent_id / file_id) stay NATIVE "
        "int (the 5.3 trap — they flow into scope / membership / FK / dict-key "
        "compares); uuid columns are coerced to str at the dict boundary to "
        "match the REST baseline (CONSUMED: owner_id == user_id ownership checks, "
        "project_members.user_id as an enrich-email dict key); timestamptz → ISO "
        "str and date (project_tasks.due_date) → 'YYYY-MM-DD' str; numeric "
        "(fps/timecode) left native. parsed_media Enum(DownloadStatus) columns "
        "are unwrapped to bare strings and the renamed metadata_ → 'metadata' "
        "column is resolved via the mapper. Writes commit via write_scope(). "
        "Inert parity migration — NO endpoint changes behavior on flip. The "
        "auth-admin methods (enrich_members_with_email / get_user_email), the "
        "review_comments methods, and project_members update/delete all INHERIT "
        "the legacy supabase path via MRO (auth-admin hits Supabase Auth not a "
        "table; the other two are deferred product decisions over schema drift — "
        "see the projects_repository_orm module docstring). projects.display_code "
        "is a phantom column preserved as a graceful no-op (REST parity). "
        "Co-fixed prod bug: the legacy get_members ordered by a phantom "
        "created_at column (silently returned [] via swallowed PGRST 400) — now "
        "orders by joined_at on both paths.",
    )
    USE_ORM_STORYBOARD: bool = Field(
        default=False,
        description="Route the SIX Storyboard Workbench repos (StoryboardProject"
        " / Node / Edge / Frame / Character / Asset — over storyboard_projects / "
        "_nodes / _edges / _frames / _characters / _assets) through the "
        "SQLAlchemy 2.0 ORM session layer (Phase 2 L-solo). One flag gates all "
        "six factories. Strategy C value-type parity: ALL ids + FKs are BIGINT "
        "snowflake (the six ids + project_id / node_id / source_node_id / "
        "target_node_id + storyboard_projects.team_id which FKs teams.id, NOT a "
        "uuid) → stay NATIVE int (the 5.3 trap; team_id is CONSUMED by "
        "verify_project_access's team_members eq filter + NAS-path str()). The "
        "only uuid column is storyboard_projects.created_by → str for REST-shape "
        "parity (response model declares created_by: str; no type-sensitive "
        "consumer). timestamptz (created_at / updated_at) → ISO str; NO date "
        "columns; numeric canvas coords (position_x/y, width, height, "
        "duration_seconds — all Double) left NATIVE float (frontend does float "
        "math); jsonb (data_json / viewport_json / settings_json / visual_traits "
        "/ annotations_json / metadata_json) → native dict. No SQLAlchemy Enum "
        "columns (status / node_type / edge_type / transition_type / source_type "
        "are plain String + DB CheckConstraint) and no renamed columns. The "
        "three bulk_upsert methods run row-by-row pg_insert ON CONFLICT (id) DO "
        "UPDATE inside one write_scope() (atomic) to sidestep the mixed-PK "
        "multi-VALUES CompileError; they pass row keys THROUGH (not filtered) so "
        "any phantom (non-column) key RAISES at compile time = REST-parity 500. "
        "The two formerly-broken AI-gen callers were FIXED (BUG 6): "
        "script_ai_router now nests scene_number/camera_notes in data_json + adds "
        "node_type; both workflow frame steps now write real columns "
        "(frame_index/note/image_url + project_id/node_id) with prompt/status "
        "nested in annotations_json — so they succeed instead of 500ing. All "
        "writes commit via "
        "write_scope(). No date/timestamp range filters → no timestamptz<VARCHAR "
        "hazard. The storyboard_frame_characters junction table has no consumer "
        "(nothing to migrate). Instant rollback = flip back to false.",
    )
    USE_ORM_NOUS: bool = Field(
        default=False,
        description="Route NousRepository (admin-configured platform AI models "
        "over nous_models) through the SQLAlchemy 2.0 ORM session layer (Phase 2 "
        "M batch). Strategy C value-type parity: id (BIGINT snowflake) stays "
        "NATIVE int (the 5.3 trap; every consumer does str(id) or passes it to a "
        "response model — never int() math); created_at / updated_at → ISO str; "
        "pricing_value (Numeric pricing/cost column) left NATIVE Decimal — the "
        "numeric decision: every consumer wraps it in float() before any math "
        "(float() works on both a REST str and a native Decimal), so no str() "
        "coercion is needed. No uuid / jsonb columns. The legacy update injects "
        "an 'updated_at=now()' string sentinel — the ORM drops it and sets "
        "updated_at=func.now() (binding the literal string would error). Reads "
        "swallow + return []/None; create/update swallow + return None (legacy "
        "parity, NOT re-raise); delete returns False on failure. Writes commit "
        "via write_scope(). Inert — flip back to false to roll back.",
    )
    USE_ORM_SESSION_MEMORY: bool = Field(
        default=False,
        description="Route SessionMemoryRepository (ai_session_memory — one "
        "markdown-body row per ai_sessions row) through the SQLAlchemy 2.0 ORM "
        "session layer (Phase 2 M batch). The repo returns a SessionMemoryRow "
        "dataclass whose inherited _row_to_obj constructor already normalises "
        "every field (session_id → str, last_updated_at → _parse_ts datetime, "
        "counters → int), so strategy-C parity is handled by the dataclass — the "
        "ORM just feeds it a native-typed row dict. The ONLY ORM-specific "
        "coercion: session_id is a BIGINT (FK → ai_sessions.id snowflake) but "
        "callers pass it as a str, so binds are int-coerced (_bigint) — asyncpg "
        "int8 codec is strict. upsert reproduces ON CONFLICT (session_id) DO "
        "UPDATE with the legacy load-then-bump-version logic; now() is written "
        "native UTC datetime. No phantom columns, no date range filters. load / "
        "upsert swallow + return None (must not crash the chat path); delete "
        "returns False on failure. Writes commit via write_scope(). Inert — flip "
        "back to false to roll back.",
    )
    USE_ORM_PERMISSION: bool = Field(
        default=False,
        description="Route PermissionRepository (the ReBAC effective-role read "
        "surface: five read-only lookups over access_overrides / folders / "
        "libraries / team_members / resource_items) through the SQLAlchemy 2.0 "
        "ORM session layer (Phase 2 M batch). READ-ONLY repo → no write paths, "
        "all reads on read_scope(). Strategy C value-type parity: bigint ids / "
        "FKs (folders.id/parent_id/scope_id, resource_items.scope_id/folder_id, "
        "libraries.id) stay NATIVE int (the 5.3 trap — folder.parent_id / "
        "scope.folder_id recurse into bigint folders.id lookups); the ONLY "
        "ORM-specific coercion is get_access_override str()ing its object_id "
        "param (a TEXT column) so a native-int folder id binds — reproducing "
        "PostgREST's int→text cast exactly. access_overrides.* uuids (id / "
        "user_id / granted_by) → str for shape parity (consumer reads only "
        "['role']); created_at → ISO str. libraries.scope_id is TEXT (native "
        "str). visibility ('restricted') and role compares are str==str. No date "
        "range filters → no timestamptz<VARCHAR hazard. Every method swallows + "
        "returns None on failure (legacy parity). Inert — flip back to false to "
        "roll back.",
    )

    USE_ORM_INVITE: bool = Field(
        default=False,
        description="Route InviteRepository (team_invites + the team_members "
        "membership checks accept/delete walk) through the SQLAlchemy 2.0 ORM "
        "session layer (Phase 2 M batch). Strategy C value-type parity: id / "
        "team_id (BIGINT snowflake) stay NATIVE int (the 5.3 trap; router wraps "
        "them in str() for the str InviteResponse fields, accept_invite str()s "
        "team_id at the AcceptInviteResponse boundary); created_by (uuid) → str "
        "(REQUIRED — InviteResponse.created_by is a str field, pydantic v2 "
        "rejects a native UUID); expires_at → ISO str (REQUIRED — the inherited "
        "accept_invite expiry check calls .replace()/fromisoformat() on it, a "
        "native datetime would AttributeError); created_at → ISO str; max_uses / "
        "use_count native int. get_invite_by_code reproduces the PostgREST "
        "teams(id,name) embed as a nested dict. Callers pass team_id as a STR → "
        "_bigint int-coerces every bigint bind; created_by/user_id are uuid "
        "strings (asyncpg Uuid codec accepts). No date range filters. Reads "
        "return []/None; create_invite raises on failure; accept_invite raises "
        "the same message strings the router pattern-matches (incl. the 23505 → "
        "'Already a member' path). Writes commit via write_scope(). Inert — flip "
        "back to false to roll back.",
    )
    USE_ORM_AI: bool = Field(
        default=False,
        description="Route AIRepository (resource_transcripts / "
        "resource_summaries upsert-by-resource_id + the AI status columns on "
        "resources) through the SQLAlchemy 2.0 ORM session layer (Phase 2 M "
        "batch). Strategy C value-type parity: transcript/summary id (uuid) → "
        "str (shape only — no consumer reads the row id); resource_id (bigint) "
        "native int; created_at → ISO str (REQUIRED — Transcript/SummaryResponse "
        ".created_at are typed Optional[str], pydantic rejects a native "
        "datetime); segments / key_points / topics (jsonb) → native dict/list; "
        "duration_seconds (double) → native float. resources.*_status are "
        "Enum(AiTaskStatus) columns — writes bind the bare status string "
        "(SQLAlchemy Enum accepts the matching value), filters compare to the "
        "string; the get_videos_needing_* SELECTs don't project a status column "
        "so no Enum read-unwrap. upsert reproduces ON CONFLICT (resource_id) DO "
        "UPDATE. No date range filters. save_* / get_* swallow + return None; "
        "update_media_ai_status returns False on failure; get_videos_* return [] "
        "(both cold/uncalled in app today, migrated for completeness). Writes "
        "commit via write_scope(). Inert — flip back to false to roll back.",
    )
    USE_ORM_ISSUE: bool = Field(
        default=False,
        description="Route IssueRepository (the issues table — top-level "
        "user-visible 'thing') through the SQLAlchemy 2.0 ORM session layer "
        "(Phase 2 M batch). THE M-BATCH UUID HOT SPOT: created_by_user_id and "
        "assignee_user_id are str()'d because THREE app-layer call sites "
        "(issues_router._assert_visibility, issue_messages_router."
        "_assert_issue_visible, ws_router._resolve_issue_ws_user) compare them "
        "==/in a STRING user_id for authz — a native uuid.UUID would compare "
        "unequal forever (silent 404/4001 for the legitimate owner, no error/no "
        "log). created_by_agent_id / assignee_agent_id → str for shape parity. "
        "ai_session_id + all bigint ids/FKs (id / issue_number / team_id / "
        "project_id / parent_id / goal_id) stay NATIVE int (5.3 trap; "
        "ai_session_id is fed to a supabase .eq that coerces). status / priority "
        "/ origin_kind are plain Text columns (CHECK-constrained, NOT SQLAlchemy "
        "Enum) → native str, no _plain unwrap. timestamps → ISO str; "
        "execution_state (jsonb) → native dict. atomic_create keeps the "
        "counter-UPDATE+INSERT atomic by calling the SAME issue_create_atomic "
        "SECURITY DEFINER proc (mig 173) via SELECT * FROM "
        "issue_create_atomic(CAST(:payload AS jsonb)) inside write_scope(). "
        "list_for_user reproduces the own-OR-assignee OR filter + count='exact'. "
        "No date range filters. get_* return None; update raises ValueError on "
        "not-found/no-op; atomic_create raises RuntimeError on empty result. "
        "Writes commit via write_scope(). Inert — flip back to false to roll "
        "back.",
    )
    USE_ORM_TAGS: bool = Field(
        default=False,
        description="Route TagsRepository (the tag system — tags table CRUD + the "
        "resource_tags M:N junction + tag_groups embed + RPC-backed counts) "
        "through the SQLAlchemy 2.0 ORM session layer (Phase 2 M batch). ID-TYPE "
        "FINDING: contrary to the brief's hint, tags.id is BIGINT (Snowflake), NOT "
        "uuid — so every tag id stays NATIVE int (the 5.3 trap; every consumer "
        "str()s it at the boundary / routes it through the SnowflakeId response "
        "type). resource_tags PK is composite (resource_id, tag_id) BIGINT → no "
        "mixed-PK bulk_upsert hazard; add/bulk_add reproduce the PostgREST ON "
        "CONFLICT DO UPDATE via pg_insert().on_conflict_do_update in one "
        "write_scope(). The ONLY uuid is tags.user_id → str (TagResponse.user_id "
        "is a str field; pydantic rejects native UUID). created_at → ISO str; "
        "confidence (double) native float; type is CHECK-text NOT Enum (no _plain "
        "unwrap). get_all_tags / get_tag_counts reproduce the get_tag_counts_by_ids "
        "/ get_user_tag_counts RPCs (DB-side GROUP BY) with the fallback. Two "
        "schema-drift bugs were FIXED: _get_tag_counts_fallback now filters on the "
        "real resources.creator_id (was the nonexistent resources.user_id → PG "
        "42703); and the get_user_tag_counts RPC itself is repaired by migration "
        "256 (was JOINing the dropped media_tags / parsed_media.user_id). No "
        "date range filters. Reads return []/None; create raises; writes commit via "
        "write_scope(). Inert — flip back to false to roll back.",
    )
    USE_ORM_SKILL: bool = Field(
        default=False,
        description="Route SkillRepository (the AI-Library skill surface — skills "
        "CRUD + skill_files multi-file CRUD + skill_versions/skill_file_versions "
        "snapshots + the agent_skills reverse index) through the SQLAlchemy 2.0 "
        "ORM session layer (Phase 2 M batch). 15 callsites, all routed through "
        "get_skill_repository(). ID TYPES: skills.id is BIGINT → native int (5.3 "
        "trap; every consumer int()s it). skill_files.id is UUID → str for SHAPE "
        "parity (SkillFileOut.id: UUID accepts str or native; NO consumer does "
        "UUID()/==/dict-key on a file id — audited). skills.created_by / "
        "default_agent_id (uuid) → str. timestamps → ISO str; frontmatter_json / "
        "input_schema (jsonb) → native dict; trigger_keywords (text[]) → native "
        "list; status/category/file_type are CHECK-text NOT Enum (no _plain). "
        "upsert_file reproduces the (skill_id,path) ON CONFLICT DO UPDATE "
        "(ux_skill_files_path); the versioned writes keep the legacy "
        "snapshot-then-update two-step (now inside one write_scope() — no "
        "half-commit, behavior preserved, documented non-atomicity not 'repaired'). "
        "No date range filters. Reads swallow + return None/[]; writes raise. "
        "Writes commit via write_scope(). Inert — flip back to false to roll back.",
    )

    USE_ORM_REVIEW: bool = Field(
        default=False,
        description="Route ReviewRepository (the review system — review_comments "
        "threaded comments/replies + review_annotations + review_status "
        "approvals) through the SQLAlchemy 2.0 ORM session layer (Phase 2 H "
        "batch — authz-sensitive). UUID AUTHZ HOT SPOT: review_comments.author_id "
        "is str()'d because review_service.update_comment / delete_comment gate "
        "on ``comment['author_id'] != user_id`` (a STRING auth subject) — a "
        "native uuid.UUID would compare unequal forever (silent wrong-DENY: the "
        "author locked out of their own comment, no error/log). "
        "review_status.reviewer_id → str for shape parity (only consumed in a log "
        "f-string, no ==/!= consumer). All bigint ids/FKs (id / resource_id / "
        "parent_id / version_id / comment_id) stay NATIVE int (the 5.3 trap). "
        "status is CHECK/plain VARCHAR NOT Enum (no _plain unwrap); timecode "
        "(double) / frame_number (int) / data (jsonb) native; timestamps → ISO "
        "str. TEMPORAL-WRITE: the legacy stamps updated_at='now()' (a PostgREST "
        "sentinel) — the ORM substitutes datetime.now(utc) at the write boundary "
        "(asyncpg can't bind the literal 'now()'). upsert_review_status "
        "reproduces the read-then-branch upsert (no DB unique on the "
        "resource/reviewer/version triple) inside one write_scope(). No date "
        "range filters. Reads return None/[]; writes commit via write_scope(). "
        "Inert — flip back to false to roll back.",
    )

    USE_ORM_TEAM: bool = Field(
        default=False,
        description="Route TeamRepository (CROWN JEWEL — the team authorization "
        "surface: teams + team_members; the team_invites table is a separate "
        "InviteRepository) through the SQLAlchemy 2.0 ORM session layer (Phase 2 "
        "H batch — authz-sensitive). THE 'all-bigint = safe' TRAP: teams.id is "
        "BIGINT but teams.owner_id and team_members.user_id are UUID. "
        "team_members.user_id is str()'d because teams_router.update_member_role "
        "does ``next(m for m in members if m['user_id'] == user_id)`` against the "
        "STRING {user_id} path param — a native uuid.UUID == str is False forever "
        "(silent 404 'Member not found' after a successful role update). "
        "teams.owner_id → str for shape parity (TeamResponse.owner_id is a str "
        "field); remove_member's owner-protection compares str(owner_id) == "
        "str(target) explicitly (a SECOND real Python uuid compare — a native "
        "UUID there would let the owner be removed). All bigint ids (teams.id / "
        "team_members.team_id) stay NATIVE int (the 5.3 trap; router str()s them "
        "for the response). role is CHECK/plain VARCHAR NOT Enum (str membership "
        "checks, no _plain). settings_json/enabled_modules (jsonb) native; "
        "created_at/joined_at → ISO str. create_team relies on the "
        "teams_invite_code_trigger (mig 009) to fill invite_code and uses "
        "RETURNING to read it + the snowflake id back; the owner membership is "
        "added by the add_owner_as_member trigger (mig 009). PROD-BUG CO-FIX "
        "(2026-06-06): the legacy ALSO explicitly inserted the owner member, "
        "which collided with that trigger on the team_members PK (23505) → every "
        "POST /teams 500'd; the redundant insert was DROPPED in BOTH the legacy "
        "and ORM create_team so the trigger is the single source AND team "
        "creation works. add_member returns None on 23505 (duplicate member), "
        "re-raises other IntegrityErrors. No date range filters; no temporal "
        "writes. KNOWN PRE-EXISTING FOLLOW-UP (not a parity issue, documented): "
        "the router validates role in [admin/editor/reviewer/viewer] but the "
        "team_members.role CHECK only allows [owner/admin/member] — writing the "
        "others raises a CHECK violation under BOTH REST and ORM (a separate "
        "product question, preserved faithfully). Writes commit via "
        "write_scope(). Inert — flip back to false to roll back.",
    )

    USE_ORM_POINTS: bool = Field(
        default=False,
        description="Route PointsRepository (MONEY — the points capacity ledger: "
        "point_pricing / point_packages / team_quotas / member_quotas / "
        "point_transactions) through the SQLAlchemy 2.0 ORM (Phase 2 H batch — "
        "money). NUMERIC-PRECISION DECISION: every point/money column here is "
        "INTEGER/BigInteger (points_balance / amount / balance_after / "
        "storage_*_bytes / points_cost / points_amount / *_this_month), NOT "
        "Numeric — REST returned a JSON NUMBER for these, so they STAY NATIVE int "
        "(the 5.3 trap) AND every consumer does exact int math on them "
        "(check_quota's ``balance < cost``, add_points' ``balance + amount``, "
        "reclaim's ``min``/``-``, admin_adjust's ``balance + amount``). str()ing "
        "any would silently break the +/</min money math. The ONLY Numeric column "
        "is point_transactions.duration_seconds → str()'d to match REST's "
        "JSON-string shape (write-only/unread today; no exact-decimal consumer). "
        "UUID sweep: point_pricing.id / point_packages.id / member_quotas.id+"
        "user_id / point_transactions.user_id → str (default-str-all-uuid; "
        "team_quotas has no uuid). ATOMIC MONEY: consume/refund go through the "
        "SAME rpc_consume_team_points (mig 120/124) / rpc_refund_team_points_"
        "idempotent (mig 123) SECURITY DEFINER functions via SELECT * FROM fn(...) "
        "inside write_scope() (team_id→int BIGINT param, user_id str UUID param; "
        "INTEGER return cols → native int) — a read_scope there would roll the "
        "decrement back = LOST MONEY. CONCERN (reported, not repaired — inert "
        "discipline): add_points / reclaim_daily_gift / admin_adjust(<0) / "
        "increment_member_usage are PRE-EXISTING non-atomic read-then-write "
        "balance paths; reproduced faithfully (no locking added). get_transactions"
        "(days=N) binds a tz-aware datetime for ``created_at >= cutoff`` (v3 "
        "rule). member_quotas upsert via ON CONFLICT (team_id,user_id). Writes "
        "commit via write_scope(). Inert — flip back to false to roll back.",
    )

    USE_ORM_PAYMENT: bool = Field(
        default=False,
        description="Route PaymentRepository (MONEY — payment orders, the "
        "purchase ledger: the orders table) through the SQLAlchemy 2.0 ORM "
        "(Phase 2 H batch — money). NUMERIC-PRECISION DECISION: orders has NO "
        "Numeric column — amount_cents / points_amount are INTEGER (REST returned "
        "numbers → STAY NATIVE int; the 5.3 trap). points_amount feeds "
        "points_service.add_points' ``balance + amount`` exact int math; str() "
        "would break it. id / team_id are BigInteger → native int (order_id binds "
        "to update_order's WHERE; team_id → add_points → int()). UUID sweep: "
        "user_id / package_id → str (default-str-all-uuid; user_id flows into "
        "add_points→create_transaction's Uuid write bind which accepts the str "
        "form). payment_status/method/currency are CHECK/plain String NOT Enum "
        "(handle_callback does ``status == 'paid'`` str==str — no _plain). "
        "WRITE-BINDING (v3): the legacy .isoformat()'d paid_at/expired_at/created_"
        "at/updated_at to ISO strings before REST; asyncpg REQUIRES native aware "
        "datetimes, so the ORM does the INVERSE — _coerce_temporal(ISO-str→"
        "datetime) at the write boundary; update_order + expire_pending_orders "
        "use func.now() (DB clock) for updated_at and the ``expired_at < NOW()`` "
        "filter (the legacy bound a naive-local string). create_order omits "
        "created_at/updated_at (server_default now()). Writes commit via "
        "write_scope() (a payment-state write silently rolled back = a paid order "
        "stuck pending). Inert — flip back to false to roll back.",
    )

    USE_ORM_COOKIES: bool = Field(
        default=False,
        description="Route CookiesRepository (SECRET — platform login cookies: "
        "the user_cookies table) through the SQLAlchemy 2.0 ORM (Phase 2 H batch "
        "— secret). SECRET-HANDLING: the cookie (cookie_text/cookie_file/"
        "custom_headers, all Text) is stored and returned in PLAINTEXT — the "
        "legacy does NO encryption and NO masking (no crypto helper imported), so "
        "the ORM reproduces raw-write / raw-read identically (consumers — "
        "abogus/ies/ytdlp/soda parsers — need the raw cookie to drive sessions). "
        "Plaintext-at-rest is a reported CONCERN, NOT changed here. UUID sweep: "
        "user_id → str on every return path (legacy supabase-py shape; consumers "
        "index by platform so no authz == on the returned dict, but str() keeps "
        "the dict byte-identical and is the WHERE-filter bind, adapted str→uuid). "
        "id (bigint, server_default generate_snowflake_id()) → native int (5.3 "
        "trap). is_valid (bool) native; created_at/updated_at → ISO str. NO Enum, "
        "NO JSONB, NO renamed column, NO date RANGE filter (all WHERE are user_id/"
        "platform equality). upsert reproduces ON CONFLICT (user_id, platform) DO "
        "UPDATE (backed by user_cookies_user_id_platform_key) forcing is_valid="
        "True + error_message=None + a fresh updated_at (native datetime, binds "
        "directly); a stray data key is filtered to a silent no-op (phantom "
        "screen). Reads return None/[]; writes commit via write_scope(); every "
        "method swallows to the legacy fallback. Inert — flip back to false.",
    )

    USE_ORM_API_KEY: bool = Field(
        default=False,
        description="Route ApiKeyRepository (SECRET — API bearer keys: the "
        "api_keys table) through the SQLAlchemy 2.0 ORM (Phase 2 H batch — "
        "secret). SECRET-HANDLING: HASH-ON-WRITE / LOOKUP-BY-HASH reproduced "
        "exactly — create() calls the base generate_key() static (UNCHANGED "
        "crypto) storing key_hash=SHA-256(full_key) + key_prefix (masked display) "
        "+ key_value (FULL PLAINTEXT, migration 039 'persistent full key access') "
        "and reveals the full key ONCE via secret_key. validate_key (inherited) "
        "does hash_key(full_key)→get_by_key_hash. EXPOSURE parity: reads return "
        "the raw SELECT * incl. key_value (plaintext) + key_hash — IDENTICAL to "
        "the legacy; the router surfaces key_value on list/get/update "
        "(ApiKeyResponse.key_value). ⚠️ plaintext key at rest + returned on list "
        "is a pre-existing over-exposure — reported as a CONCERN, reproduced "
        "UNCHANGED (NOT narrowed/widened; narrowing breaks the UI re-copy). NO "
        "encryption added (inert; would orphan existing plaintext rows). UUID "
        "AUTHZ HOT SPOT: user_id → str on every read (get_api_key does "
        "``key_data['user_id'] != auth.user_id`` str-compare; deps builds "
        "AuthContext(user_id: str)). status is Enum(ApiKeyStatus) on the model → "
        "_plain-unwrapped to bare str ('active'/'revoked'/'expired') so "
        "validate_key's ``status != 'active'`` and the router str field match; "
        "WHERE binds use the bare string literal. id (bigint) native int (5.3 "
        "trap); scopes (jsonb array) native list; usage_count/rate_limit int; "
        "timestamps → ISO str on reads. v3 temporal: NO SQL expiry filter "
        "(validate_key checks expires_at in PYTHON on the ISO str — inherited, "
        "unchanged); WRITE-BINDING — create/update _coerce_temporal(ISO-str→aware "
        "datetime) for expires_at, update stamps updated_at=now(utc) native. "
        "update_usage reproduces the SECURITY DEFINER atomic-increment RPC "
        "increment_api_key_usage(key_id) (migration 003) via SELECT inside "
        "write_scope() — no read-modify-write race; failure swallowed. Phantom "
        "screen filters update data to mapped attrs. Writes commit via "
        "write_scope(). Inert — flip back to false to roll back.",
    )
    USE_ORM_COMMITMENT: bool = Field(
        default=False,
        description="Route CommitmentRepository (agent_commitments — cross-session "
        "agent followups) through the SQLAlchemy 2.0 ORM (Phase 2 H batch — "
        "FROZEN-DATACLASS parity). Returns the frozen ``Commitment`` value object, "
        "NOT a dict. PARITY APPROACH = builder reuse: ORM rows → REST-shaped dict "
        "(uuid→str, datetime→ISO str, bigint native int) → the UNCHANGED inherited "
        "``_row_to_commitment(dict)`` reconstructs the dataclass byte-identically "
        "(structurally guaranteed). UUID AUDIT: agent_id/user_id/session_id are "
        "str()'d BY the builder → Commitment.user_id is str and the router "
        "fulfill/cancel authz check ``existing.user_id != str(auth.user_id)`` is "
        "str==str (a native UUID would 404 the owner). trigger_type/status are "
        "plain Text columns (NOT Enum) → bare str → __post_init__ coerces to "
        "TriggerType/CommitmentStatus enums (no _plain unwrap needed). "
        "fulfillment_run_id is BIGINT on the column but STR on the dataclass — the "
        "builder str()s it (kept native int by _rest_row). id (bigint) native int "
        "(5.3 trap). v3 temporal: list_due_time / list_expired_pending bind the "
        "NATIVE aware datetime cutoff (never the legacy ISO string) in the "
        "trigger_at/expires_at range filter; create / _set_terminal_status "
        "_coerce_temporal the inherited ISO-string timestamps (trigger_at / "
        "expires_at / fulfilled_at) → aware datetime for the asyncpg bind. Phantom "
        "screen: all insert/update keys are mapped columns. create raises on empty "
        "row; _set_terminal_status returns None on no-pending-row; get_by_id "
        "swallows→None; list_* raise. Writes commit via write_scope(). Inert — "
        "flip back to false to roll back.",
    )
    USE_ORM_APPROVAL: bool = Field(
        default=False,
        description="Route ApprovalRequestsRepository (agent_approval_requests — "
        "the human-in-loop approval-gate state machine) through the SQLAlchemy 2.0 "
        "ORM (Phase 2 H batch — FROZEN-DATACLASS parity). Returns the frozen "
        "``ApprovalRequest`` dataclass, NOT a dict. PARITY APPROACH = builder "
        "reuse: ORM rows → REST-shaped dict (uuid→str, datetime→ISO str, bigint "
        "native int) → the UNCHANGED inherited ``ApprovalRequest.from_row(dict)`` "
        "reconstructs the dataclass byte-identically (wrapping the uuid strings "
        "back to native ``UUID``). UUID AUDIT: id/user_id/agent_id/decided_by are "
        "wrapped to native uuid.UUID BY the builder (the dataclass fields ARE typed "
        "UUID) → the router approve/reject authz check ``existing.user_id != "
        "user_uuid`` is UUID==UUID (str()ing them would BREAK parity here — the "
        "INVERSE of most H repos). session_id/run_id are BIGINT columns but STR "
        "dataclass fields (mig 231/232 snowflakes) — the builder str()s them (kept "
        "native int by _rest_row). status is plain Text (NOT Enum) → bare str; "
        "router ``existing.status != 'pending'`` is str==str. v3 temporal: "
        "mark_expired binds the NATIVE aware datetime cutoff (never the legacy ISO "
        "string) in the expires_at range filter; decide / mark_expired "
        "_coerce_temporal the ISO-string decided_at → aware datetime. create binds "
        "a native aware expires_at (now+ttl). CONCERN (NOT repaired — inert "
        "discipline): decide() returns True UNCONDITIONALLY on a clean execute "
        "(does not check rowcount), replicating the legacy quirk — a 0-row no-op "
        "still returns True (the router's prior get_by_id guard is the real gate). "
        "Phantom screen: all insert/update keys are mapped columns. create raises "
        "on exception; get_by_id/list swallow→None/[]; decide→False on exception; "
        "mark_expired→0 on exception. Writes commit via write_scope(). Inert — "
        "flip back to false to roll back.",
    )
    USE_ORM_WORKFORCE: bool = Field(
        default=False,
        description="Route AgentWorkforceRepository (the M2 Persistent Workforce "
        "bounded context — FIVE tables in one repo: agent_workers / agent_inbox / "
        "task_tracking[agent_task rows] / agent_state_history / agent_outbox) "
        "through the SQLAlchemy 2.0 ORM (Phase 2 H batch — highest-risk). "
        "THE H-BATCH CRASHER HOT SPOT: ~17 bare UUID(row[...]) consumers across "
        "app/services/workforce/* (agent_worker / inbox_processor / "
        "outbox_dispatcher / delegate_tool). UUID(native_uuid) raises TypeError, "
        "so EVERY returned uuid MUST be a str. Defense = the GENERIC _parity "
        "sweep (any uuid.UUID→str, any datetime/date→ISO str) over every returned "
        "dict — structurally guaranteeing every uuid column is a str so every "
        "UUID(returned) consumer works. Audited columns: agent_inbox.id / "
        "task.agent_id / task.user_id / agent_outbox.id+recipient_agent_id+"
        "sender_agent_id / agent_state_history refs — all uuid→str. task PK "
        "dbos_workflow_id is TEXT (already str). NO ==/!= silent-killer authz "
        "compares in the workforce consumers (they route uuids through UUID() = "
        "crash not silent; the ==/in checks are uuid-vs-uuid AFTER UUID() wrap). "
        "NO SQLAlchemy Enum columns (all state/status/kind/phase are plain Text "
        "+ CHECK) → no _plain unwrap; the ONE renamed col task_tracking.metadata→"
        "metadata_ is handled by _name_to_attr. task_tracking DISCIPLINE: this "
        "repo touches ONLY task_kind='agent_task' rows, which the "
        "mirror_dbos_lifecycle_to_tracking trigger does NOT mirror (app code is "
        "the lifecycle source of truth per the model comment) — so writing "
        "phase/status/started_at/completed_at/error_msg on agent_task rows is "
        "CORRECT and reproduced verbatim from the legacy (NOT a discipline "
        "violation; workflow rows are never touched). v3 temporal: "
        "list_stale_workers binds the NATIVE aware stale_before datetime in the "
        "heartbeat_at < cutoff filter; all timestamptz column writes bind native "
        "datetime objects (not ISO strings) for the asyncpg DateTime(True) bind. "
        "upsert_worker reproduces the ON CONFLICT (agent_id) via "
        "pg_insert().on_conflict_do_update. No batch inserts. Every method "
        "soft-fails to the legacy fallback (None/False/[]/{items:[],total:0}) — "
        "the sweeper/state-machine layers depend on it. Writes commit via "
        "write_scope(). Inert — flip back to false to roll back.",
    )

    # ── Phase 2 admin wave (5 small logs/stats/settings repos) ──────────
    USE_ORM_ADMIN_AUDIT_LOGS: bool = Field(
        default=False,
        description="Route AuditLogsRepository (admin activity trail on "
        "audit_logs) through the SQLAlchemy 2.0 ORM (Phase 2 admin wave). "
        "Strategy C: id + admin_id (uuid) → str — admin_id is a DICT KEY in the "
        "router (admin_info.get(aid)) and a str field on AuditLogResponse; id is "
        "str()'d into the response. created_at (timestamptz) → ISO str (CONSUMED "
        "— the /stats endpoint does created_at[:10] string-slicing). No "
        "SQLAlchemy Enum / no renamed column on AuditLogs. Date-range filters "
        "(start_date/end_date on created_at) bind NATIVE tz-aware datetimes (v3 "
        "rule). Reads only — no writes in this repo. Inert; flip false to revert.",
    )
    USE_ORM_ADMIN_MONITORING: bool = Field(
        default=False,
        description="Route MonitoringRepository (admin monitoring dashboard — "
        "reads api_request_logs / application_logs / frontend_error_logs) through "
        "the SQLAlchemy 2.0 ORM (Phase 2 admin wave). COLUMN-SUBSET selects (not "
        "SELECT *). Strategy C: timestamp / logged_at (timestamptz) → ISO str "
        "(CONSUMED — the router does ts.replace('Z',...)+fromisoformat(ts) and "
        "sets RecentErrorEntry.logged_at:str); status_code / response_time_ms "
        "(int) stay native; frontend_error_count returns native int. No uuid in "
        "any projection. Date-range filters bind NATIVE tz-aware datetimes (v3 "
        "rule; these are the LOTS-of-date-windows surface). Reads only. Inert.",
    )
    USE_ORM_ADMIN_STATS: bool = Field(
        default=False,
        description="Route AdminStatsRepository (admin dashboard aggregations "
        "over user_profiles / parsed_media / teams / user_logs) through the "
        "SQLAlchemy 2.0 ORM (Phase 2 admin wave). COUNT(*) returns native int "
        "(the 5.3 trap). distinct_active_users_since returns native int. "
        "created_at (timestamptz) → ISO str (CONSUMED — the growth/video-stats "
        "endpoints do created_at[:10] string-slicing). video_download_status is "
        "a SQLAlchemy Enum(DownloadStatus) → unwrapped to its bare .value via "
        "_plain (CONSUMED — the router does status == 'completed'). user_id "
        "(uuid) → str (DICT KEY in the storage endpoint). since/date filters bind "
        "NATIVE tz-aware datetimes (v3 rule). NOTE — completed_videos_by_user() "
        "was a PRE-EXISTING BROKEN endpoint (it selected parsed_media.user_id, "
        "DROPPED in migration 083 → PG 42703 on /storage). FIXED (BUG 3, "
        "resource-centric): both the REST and ORM repos now count per "
        "resources.creator_id where is_trashed=false and return one "
        "{'user_id': str} row per resource (the shape the /storage handler groups "
        "on). Reads only. Inert; flip false to revert.",
    )
    USE_ORM_ADMIN_SYSTEM_SETTINGS: bool = Field(
        default=False,
        description="Route SystemSettingsRepository (system_settings admin CRUD) "
        "through the SQLAlchemy 2.0 ORM (Phase 2 admin wave). Strategy C: "
        "updated_by (uuid) → str (SystemSettingResponse.updated_by:Optional[str]); "
        "updated_at (timestamptz) → ISO str; value/options (jsonb) → native dict. "
        "The 'exclude transcode_* from list' policy is preserved verbatim. "
        "update() WRITES value + updated_by and COMMITS via write_scope() (the "
        "silent-rollback P0 lesson); exists() uses maybe_single parity. No date "
        "filters. No SQLAlchemy Enum / no renamed column. Inert; flip false.",
    )
    USE_ORM_ADMIN_TABLE_PREFERENCES: bool = Field(
        default=False,
        description="Route AdminTablePreferencesRepository "
        "(admin_table_preferences — Notion-style per-user table config) through "
        "the SQLAlchemy 2.0 ORM (Phase 2 admin wave). COLUMN-SUBSET selects "
        "(table_key, filters, sorts, visible_columns, column_order) — NO uuid / "
        "timestamptz in the projection, so no value-type coercion is needed. "
        "filters/sorts (jsonb) → native dict/list, visible_columns/column_order "
        "(text[]) → native list[str]. upsert() reproduces the legacy "
        "on_conflict='user_id,table_key' via pg_insert().on_conflict_do_update "
        "and COMMITS via write_scope(); delete() commits. No date filters. Inert; "
        "flip false to revert.",
    )

    # ── Phase 2 admin wave pass 2 (search / alerts / logs / tasks / videos) ──
    USE_ORM_ADMIN_SEARCH: bool = Field(
        default=False,
        description="Route AdminSearchRepository (admin cross-log search + request "
        "trace over api_request_logs / application_logs / frontend_error_logs / "
        "admin_audit_logs) through the SQLAlchemy 2.0 ORM (Phase 2 admin wave). "
        "Strategy C: all timestamptz (timestamp / logged_at / created_at) → ISO str "
        "(CONSUMED — the router feeds them to fromisoformat via _parse_ts and stores "
        "them on str fields); id (BIGINT) → native int (router str()s it); no uuid "
        "in any real projection. JSONB filter extra->>request_id reproduced via "
        "extra['request_id'].astext. Time bounds (ISO str) coerced to NATIVE "
        "tz-aware datetimes before binding (v3 rule). TWO PRE-EXISTING SCHEMA-DRIFT "
        "bugs were FIXED in both the REST and ORM repos: frontend_logs() now selects "
        "the real column stack (was the nonexistent stack_trace → PG 42703); "
        "audit_logs() now queries the real table audit_logs via admin_id (was the "
        "nonexistent table admin_audit_logs → PG 42P01). Reads only. Inert.",
    )
    USE_ORM_ADMIN_ALERT_RULES: bool = Field(
        default=False,
        description="Route AlertRulesRepository (admin alert rules + alert history "
        "on alert_rules / alert_history) through the SQLAlchemy 2.0 ORM (Phase 2 "
        "admin wave). NO ORM MODEL exists for either table (mig 094, never "
        "sqlacodegen'd) → reproduced via PARAMETERIZED text() inside read/write "
        "scopes (no new models added to the shared package in this inert wave). "
        "Strategy C: created_by (uuid) → str (AlertRuleItem.created_by:str); "
        "created_at / updated_at / mute_until / resolved_at (timestamptz) → ISO str "
        "(CONSUMED — str Pydantic fields; mute_until fed to fromisoformat); "
        "threshold / metric_value (float8) → native float; id / rule_id (BIGINT) → "
        "native int (str Pydantic fields coerce). WRITES (create/update/delete/"
        "insert_history/resolve_history) COMMIT via write_scope(); update_rule "
        "stamps updated_at=now() and guards against phantom keys via a column "
        "allow-list. list_history date filters bind NATIVE tz-aware datetimes (v3). "
        "Inert; flip false to revert.",
    )
    USE_ORM_ADMIN_REQUEST_LOGS: bool = Field(
        default=False,
        description="Route the three admin-console log repositories "
        "(RequestLogsRepository / FrontendErrorLogsRepository / AppLogsRepository — "
        "api_request_logs / frontend_error_logs / application_logs) through the "
        "SQLAlchemy 2.0 ORM (Phase 2 admin wave). SELECT * via _orm_obj_to_dict "
        "(FrontendErrorLogs has a renamed metadata→metadata_ — keyed back as "
        "'metadata'). Strategy C: timestamp / created_at / logged_at (timestamptz) "
        "→ ISO str (CONSUMED — str Pydantic fields; stats does ts[:13] slicing); id "
        "(BIGINT) → native int (router str()s it); status_code / response_time_ms / "
        "line (int) → native int; user_id (uuid) → str (in SELECT * but not a "
        "dict-key here); jsonb → native dict. has_exception True → exception IS NOT "
        "NULL / False → IS NULL (legacy parity); NOISE_MODULES exclusion preserved. "
        "Date-range filters bind NATIVE tz-aware datetimes (v3 — many windows). "
        "Reads only. Inert; flip false to revert.",
    )
    USE_ORM_ADMIN_TASKS: bool = Field(
        default=False,
        description="Route AdminTasksRepository (admin Task Center on task_tracking) "
        "through the SQLAlchemy 2.0 ORM (Phase 2 admin wave). COLUMN-SUBSET "
        "projection (LIST_COLUMNS). Strategy C: user_id (uuid) → str (DICT-KEY trap "
        "— the router uses email_map.get(str(user_id)); a native UUID would silently "
        "miss); created_at / started_at / completed_at (timestamptz) → ISO str (str "
        "Pydantic fields); metadata (renamed metadata_) → native dict keyed as "
        "'metadata'; status / phase are plain text (NOT Enum); progress / speed / "
        "total_bytes / cost_cents (int) → native int; COUNT → native int. or_ search "
        "reproduced (incl. metadata->>original_url ilike + digit media_id/resource_id "
        "eq). ⚠️ update() WRITES the EXACT changes dict verbatim incl. TRIGGER-OWNED "
        "columns (status/phase/progress/started_at/completed_at/error_msg) — this "
        "REPRODUCES a PRE-EXISTING task_tracking-discipline violation in the legacy "
        "admin cancel/retry path (NOT introduced / NOT repaired here; flagged as a "
        "CONCERN); COMMITS via write_scope(). No date-range filter. Inert; flip false.",
    )
    USE_ORM_ADMIN_VIDEOS: bool = Field(
        default=False,
        description="Route AdminVideosRepository (admin video management on "
        "parsed_media) through the SQLAlchemy 2.0 ORM (Phase 2 admin wave). SELECT * "
        "via _orm_obj_to_dict. Strategy C: video/music/cover_download_status are "
        "SQLAlchemy Enum(DownloadStatus) → unwrapped to bare .value via _plain "
        "(CONSUMED — str Pydantic fields + status=='completed' compares); id / "
        "datasize_bytes / counts (int/bigint) → native int; COUNT → native int; "
        "sum_storage_bytes pushes SUM to PG → native int; created_at / download_time "
        "/ updated_at (timestamptz) → ISO str (datetime Pydantic fields parse it); "
        "jsonb → native list. parsed_media has NO uuid column (user_id dropped mig "
        "083) so no uuid coercion is load-bearing; THIS repo never selects user_id "
        "(the stats repo's old user_id endpoint was fixed under USE_ORM_ADMIN_STATS). or_ search "
        "(title/platform_id ilike) reproduced; sort validated vs ALLOWED_SORT_FIELDS. "
        "WRITES delete() + reset_for_retry() COMMIT via write_scope() and return "
        "bool(rowcount) via RETURNING id (REST bool(result.data) parity). No "
        "date-range filter. Inert; flip false to revert.",
    )
    USE_ORM_ADMIN_TAGS: bool = Field(
        default=False,
        description="Route AdminTagsRepository (admin tags + tag_groups console on "
        "tags / tag_groups / resource_tags) through the SQLAlchemy 2.0 ORM (Phase 2 "
        "admin wave). Strategy C: tags.id / tag_groups.id / group_id / "
        "resource_tags.tag_id are ALL BIGINT (NOT uuid — checked) → native int (the "
        "5.3 trap; consumers str() at dict-key lookup boundaries, str(int) "
        "round-trips). The only uuid column, tags.user_id, → str (generic sweep; "
        "list_tags returns the row dict RAW to the JSON encoder, REST emitted a "
        "string). created_at (timestamptz) → ISO str; type is plain String + DB "
        "CHECK (NOT Enum). The PostgREST embed select('*, tag_groups(name)') is "
        "reproduced via a LEFT OUTER JOIN attaching the nested {'tag_groups': "
        "{'name': ...} | None} shape. WRITES (create/update/delete/batch/reorder for "
        "tags + groups) COMMIT via write_scope(); delete cascades resource_tags "
        "first (verbatim legacy order); bool returns mirror REST bool(result.data) "
        "via RETURNING. No date-range filter. Inert; flip false to revert.",
    )
    USE_ORM_ADMIN_TRANSCODE: bool = Field(
        default=False,
        description="Route AdminTranscodeRepository (HLS transcode admin console on "
        "resource_versions / resources / parsed_media + system_settings) through the "
        "SQLAlchemy 2.0 ORM (Phase 2 admin wave). Strategy C: ALL ids/FKs are BIGINT "
        "(resource_versions.id/.resource_id, resources.id/.media_id, parsed_media.id) "
        "→ native int (the 5.3 trap; the consumed maps str() their keys). NO uuid is "
        "selected in ANY path (uploaded_by / creator_id / updated_by never "
        "projected) so uuid coercion is not load-bearing (defensive sweep kept). "
        "created_at / transcode_at (timestamptz) → ISO str; transcode_status is "
        "plain String (NOT Enum); cover_urls (jsonb) → native; COUNT → native int. "
        "No NUMERIC columns selected. LIST filters (mime_type LIKE 'video/%', "
        "status null/eq, min_size_mb gte) + VALID_SORT_FIELDS reproduced. WRITES: "
        "mark_pending UPDATE + upsert_setting (pg_insert ON CONFLICT (key) DO UPDATE) "
        "COMMIT via write_scope(). No date-range filter. Inert; flip false to revert.",
    )
    USE_ORM_ADMIN_USERS: bool = Field(
        default=False,
        description="Route AdminUsersRepository (admin user-management console on "
        "user_profiles) through the SQLAlchemy 2.0 ORM (Phase 2 admin wave). "
        "SELECT * via _orm_obj_to_dict. ⚠️ DICT-KEY UUID TRAP (M-tier core): "
        "user_profiles.id (uuid) → str — the router builds user_ids from rows and "
        "looks email/count enrichment up via batch helpers that key on the passed "
        "id AND feed it to Supabase .eq filters / auth-admin; id is also compared "
        "user_id==auth.user_id (str). A native UUID would diverge from the REST str "
        "shape → silent enrichment miss / dead self-modify guard. role is "
        "Enum(UserRole) → unwrapped to bare .value via _plain (CONSUMED: router does "
        "str(role); str(UserRole.ADMIN) would yield 'UserRole.ADMIN' not 'admin'). "
        "created_at / updated_at (timestamptz) → ISO str; display_id (bigint) → "
        "native int. WRITES (update / set_banned) COMMIT via write_scope() and "
        "RETURN the full row (REST result.data[0] parity). No date-range filter. "
        "Inert; flip false to revert.",
    )
    USE_ORM_ADMIN_TEAMS: bool = Field(
        default=False,
        description="Route AdminTeamsRepository (admin team-management console on "
        "teams / team_members / team_quotas / collections) through the SQLAlchemy "
        "2.0 ORM (Phase 2 admin wave). ⚠️ DICT-KEY UUID TRAP (M-tier core): "
        "teams.owner_id (uuid) → str (router does set(owner_ids) + "
        "owner_info.get(oid) email enrichment AND new_owner_id==old_owner_id compare "
        "— a native UUID breaks set membership / makes the 'same owner' 400 guard "
        "never fire); team_members.user_id (uuid) → str (user_ids → batch_get_user_info "
        "→ user_info.get(uid) enrichment). teams.id / team_id (bigint) → native int "
        "(the 5.3 trap; batch_points_balances keys str(team_id), router does "
        "member_counts.get(tid) + points_balances.get(str(tid)) — str(int) "
        "round-trips). created_at / joined_at (timestamptz) → ISO str; settings_json "
        "/ enabled_modules (jsonb) → native dict; kind is plain String (NOT Enum; "
        "router: kind=='personal'). WRITES (update / delete / unlink_collections / "
        "update_member_role / delete_member) COMMIT via write_scope(). get / "
        "get_member reproduce the legacy .single() RAISE-on-0-rows quirk (HTTP 500; "
        "the router's None-guard is dead for missing rows — NOT repaired, inert "
        "discipline). No date-range filter. Inert; flip false to revert.",
    )
    USE_ORM_ADMIN_CREDITS: bool = Field(
        default=False,
        description="Route AdminCreditsRepository (admin CREDIT-ADMINISTRATION "
        "console on point_packages / point_pricing / point_transactions / orders / "
        "teams / team_quotas) through the SQLAlchemy 2.0 ORM (Phase 2 admin wave — "
        "★ MONEY ★). ALL money columns are Integer/BigInteger → NATIVE int (the 5.3 "
        "trap): points_balance / amount / balance_after / amount_cents / "
        "points_amount / points_cost / price_cents / storage_*_bytes — consumers "
        "sum/abs/+= them. point_transactions.duration_seconds (the ONLY Numeric) → "
        "str (REST JSON-string shape; no admin consumer does decimal math). ⚠️ "
        "DICT-KEY UUID TRAPS: point_transactions.user_id / orders.user_id → str "
        "(batch_get_user_auth_info str-keyed email enrichment); orders.package_id → "
        "str (get_package_names str-keyed map); teams.owner_id / pricing+package ids "
        "→ str. bigint ids (teams.id / team_id / txn+order id) → native int. "
        "timestamptz (orders.paid_at / created_at) → ISO str (the router reparses "
        "paid_at via datetime.fromisoformat). PR-E quirk: is_personal DERIVED from "
        "teams.kind=='personal' (kind is plain Text, not Enum) and injected. READ "
        "date filter orders_by_status/revenue_chart bind a tz-aware datetime (v3); "
        "WRITE update_order coerces ISO-str paid_at/updated_at → datetime (v3 "
        "mirror, asyncpg strict timestamptz codec). WRITES (package/pricing CRUD + "
        "update_order) COMMIT via write_scope(); NONE touch a balance column (the "
        "grant/adjust/refund money flows live in the router → PointsService, behind "
        "USE_ORM_POINTS). ⚠️ CONCERN (pre-existing, NOT fixed): confirm_order/"
        "refund_order do update_order THEN add_points as two un-transactioned awaits "
        "— cross-repo non-atomic; flagged for a human. Inert; flip false to revert.",
    )

    # ============================================
    # 下载设置
    # ============================================
    # Docker 部署时使用默认值 /app/downloads（容器内路径）
    # 本地开发时可通过 .env 覆盖为实际路径
    DOWNLOAD_PATH: str = Field(default="/app/downloads", description="视频存储路径")
    COOKIES_DIR: str = Field(
        default="", description="Path to directory containing platform cookie files"
    )
    HTTP_TIMEOUT: float = Field(default=30.0, description="HTTP请求超时(秒)")
    DOWNLOAD_TIMEOUT: float = Field(default=60.0, description="下载超时(秒)")

    # 用户代理列表（通用）
    USER_AGENTS: list[str] = Field(
        default_factory=lambda: [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            " (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
        ]
    )

    # Douyin 专用 UA 池。一次解析任务挑一条，贯穿 LightHTTP/ABogus/DrissionPage
    # 和 yt-dlp 下载——ABogus 签名绑定 UA，混用会让服务端验签失败。
    DOUYIN_USER_AGENTS: list[str] = Field(
        default_factory=lambda: [
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
            " (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
        ],
        description="Douyin UA pool (Chrome-family only, rotated per parse task)",
    )

    # ============================================
    # Daily free points
    # ============================================
    DAILY_FREE_POINTS: int = Field(
        default=100,
        description="Daily free points granted to each active user",
    )

    # ============================================
    # Notion 集成（可选）
    # ============================================
    NOTION_API_KEY: str = Field(default="", description="Notion API密钥")
    NOTION_DATABASE_ID: str = Field(default="", description="Notion数据库ID")
    PUSH_TO_NOTION: bool = Field(default=False, description="是否推送到Notion")

    # ============================================
    # Redis 配置 (download progress + UnifiedProgressTracker KV)
    # ============================================
    REDIS_URL: str = Field(
        default="redis://localhost:6379/0",
        description="Redis URL (download progress, live KV state)",
    )

    # ============================================
    # Transcode 配置
    # ============================================
    TRANSCODE_ENABLED: bool = Field(
        default=True,
        description="Master switch: enable/disable HLS transcoding",
    )
    TRANSCODE_TIERS: str = Field(
        default="480p,720p,1080p",
        description="Comma-separated list of enabled tiers: 480p,720p,1080p",
    )
    FFMPEG_ENCODER: str = Field(
        default="auto",
        description="Video encoder: auto (detect GPU), libx264, h264_nvenc, h264_videotoolbox, h264_qsv",
    )
    FFMPEG_PRESET: str = Field(
        default="medium",
        description="Encoding preset: ultrafast/fast/medium/slow (CPU) or p1-p7 (NVENC)",
    )
    TRANSCODE_PARALLEL_TIERS: bool = Field(
        default=True,
        description="Encode tiers (480p/720p/1080p) in parallel",
    )
    TRANSCODE_MIN_SIZE_MB: int = Field(
        default=100,
        description="Minimum file size (MB) to auto-trigger HLS transcode",
    )

    # ============================================
    # Boundary layer (SSRF guard) — Sprint 1 v2
    # See docs/architecture/boundary-layer.md
    # ============================================
    SSRF_EXTRA_BLOCKED_NETWORKS: list[str] = Field(
        default_factory=lambda: ["192.168.50.0/24"],
        description="CIDR list of additional networks blocked by url_guard "
        "(beyond Python ipaddress.is_private). Default blocks NAS subnet.",
    )
    SSRF_DEV_ALLOWLIST: list[str] = Field(
        default_factory=list,
        description="CIDR list of explicitly allowed networks for dev. "
        "MUST be empty in production. Example: 192.168.50.10/32,127.0.0.1/32",
    )
    SSRF_DNS_TIMEOUT_SECONDS: float = Field(
        default=2.0,
        description="DNS resolution timeout for url_guard async path",
    )
    SSRF_DNS_CACHE_TTL_SECONDS: int = Field(
        default=60,
        description="LRU cache TTL for DNS results (defends DNS rebinding)",
    )
    SSRF_PROXY_URL: str = Field(
        default="",
        description="Local SsrfProxy URL (auto-populated at startup, "
        "e.g. http://127.0.0.1:55001). Subprocess + browser clients "
        "(yt-dlp, DrissionPage) are configured to route through this. "
        "Empty value means proxy not started — clients run unproxied "
        "(degraded boundary).",
    )
    SSRF_PREFER_IPV4: bool = Field(
        default=True,
        description="When True, pinned_dns picks an IPv4 address before "
        "an IPv6 one even if getaddrinfo returns AAAA first. Default ON "
        "because the prod NAS Docker bridge network has no IPv6 route — "
        "an AAAA-first pick caused yt-dlp `Read timed out` on bilibili "
        "(2026-05-12). Set False on hosts with working IPv6 egress.",
    )

    # ============================================
    # OpenAI Configuration (for visual analysis)
    # ============================================
    OPENAI_API_KEY: str = Field(default="", description="OpenAI API Key")
    OPENAI_MODEL: str = Field(
        default="gpt-4o", description="OpenAI model for visual analysis"
    )
    OPENAI_EMBEDDING_MODEL: str = Field(
        default="text-embedding-3-small", description="OpenAI embedding model"
    )

    # ============================================
    # LLM Configuration (Script / Storyboard AI)
    # ============================================
    LLM_API_URL: str = Field(
        default="http://localhost:8000/v1", description="LLM API base URL"
    )
    LLM_API_KEY: str = Field(default="", description="LLM API key")
    LLM_MODEL: str = Field(default="gpt-4o", description="LLM model name")
    LLM_TIMEOUT_SECONDS: float = Field(
        default=120.0, description="LLM request timeout in seconds"
    )
    LLM_MAX_CONTEXT_TOKENS: int = Field(
        default=28000,
        description="Token budget for LLM context window (leave headroom for response)",
    )
    LLM_MAX_HISTORY_MESSAGES: int = Field(
        default=50,
        description="Maximum prior messages loaded before applying token budget",
    )
    LLM_AGENT_CACHE_TTL_SECONDS: int = Field(
        default=300,
        description="Cache TTL for agent config lookups (seconds)",
    )

    # ============================================
    # AI Provider Configuration
    # ============================================
    # DeepSeek provider (optional — set when using deepseek-* models)
    DEEPSEEK_API_URL: str = Field(
        default="https://api.deepseek.com/v1/chat/completions",
        description="DeepSeek chat-completions endpoint URL",
    )
    DEEPSEEK_API_KEY: str = Field(default="", description="DeepSeek API Key")

    # Claude (Anthropic) provider (optional — set when using claude-* models)
    CLAUDE_API_KEY: str = Field(default="", description="Anthropic API key.")

    # Doubao (Volcengine Ark) provider (optional — set when using doubao-*/ep-* models)
    DOUBAO_API_URL: str = Field(
        default="https://ark.cn-beijing.volces.com/api/v3/chat/completions",
        description="Doubao (Volcengine Ark) chat-completions endpoint URL",
    )
    DOUBAO_API_KEY: str = Field(default="", description="Doubao API Key")
    WHISPER_PROVIDER: str = Field(
        default="openai_api",
        description="Whisper provider: openai_api, volcengine, or local",
    )
    VOLCENGINE_APP_ID: str = Field(default="", description="Volcengine ASR App ID")
    VOLCENGINE_ACCESS_TOKEN: str = Field(
        default="", description="Volcengine ASR Access Token"
    )
    MEDIA_PUBLIC_URL: str = Field(
        default="https://mediahubserver.heygo.cn:88",
        description="Public URL for media file access",
    )
    AI_DEFAULT_SUMMARY_MODEL: str = Field(
        default="gpt-4o-mini", description="Default LLM model for summaries"
    )
    AI_DEFAULT_ANALYSIS_MODEL: str = Field(
        default="gpt-4o", description="Default LLM model for visual analysis"
    )

    model_config = SettingsConfigDict(
        env_file=str(ROOT_DIR / ".env"),
        env_file_encoding="utf-8",
        yaml_file=str(ROOT_DIR / "config.yml"),
        yaml_file_encoding="utf-8",
        # 在运行时赋值时验证字段值，确保类型安全
        validate_assignment=True,
        # 环境变量名称是否区分大小写
        case_sensitive=True,
        # 忽略额外字段
        extra="ignore",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls,  # Settings 类本身
        init_settings,  # 初始化时传入的参数
        env_settings,  # 环境变量设置源
        dotenv_settings,  # .env 文件设置源
        file_secret_settings,  # 文件密钥设置源（如 Docker secrets）
    ):
        """
        自定义配置源加载顺序
        优先级：环境变量 > .env 文件 > config.yml 文件 > 默认值
        """
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            YamlConfigSettingsSource(settings_cls),
            file_secret_settings,
        )


# 创建全局设置实例
settings = Settings()
