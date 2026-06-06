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
        "the already-broken callers that write phantom columns "
        "(script_ai_router node scene_number/camera_notes; both workflow frame "
        "steps order_index/prompt/notes/status/source_image_path) RAISE exactly "
        "as under REST today (inert parity — no repair). All writes commit via "
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
