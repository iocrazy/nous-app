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

    # ============================================
    # Feature flags — optional capabilities (default off; flip via .env)
    # ============================================
    FEATURE_AGENT_MEMORY: bool = Field(
        default=False,
        description="Enable scoped agent-memory recall on the chat hot path "
        "(Phase A). When off (default) _safe_recall_agent_memory returns [] "
        "immediately — prompt output is unchanged (behavior-neutral). Flip "
        "true once the agent_memories table is populated and recall quality "
        "is validated in production.",
    )
    FEATURE_GROUP_AGENT_MEMORY: bool = Field(
        default=False,
        description="Group-chat agent memory: inject conversation_memory "
        "summary + agent_memory recall into conversation agent turns, and "
        "compact after turns. Off (default) = Phase 1 behavior (20-msg tail).",
    )
    FEATURE_COPILOT_OPS: bool = Field(
        default=False,
        description="Enable the copilot free-text reconciler endpoint "
        "(POST /scenes/{id}/copilot-ops): an LLM turns a director's "
        "instruction into anchor-based element ops, dry-run-validated "
        "server-side before returning (the endpoint never writes — the "
        "editor dispatches through the existing If-Match ops channel). Off "
        "(default) = the endpoint 404s (existence hidden); this is an "
        "independent switch for the LLM cost surface. Flip true once the "
        "prompt + dry-run loop is validated on the target stack.",
    )
    FEATURE_CHAT_MEDIA_OBJECT_STORE: bool = Field(
        default=False,
        description="Route new chat/AI-generated small-image writes to the "
        "Supabase Storage `chat-media` bucket (sb:// paths) instead of the "
        "filesystem. Off (default) = every write stays on the filesystem "
        "(current behavior). Readers auto-resolve either path shape, so "
        "flipping on is forward-only and rollback (flag off) keeps already-"
        "written sb:// rows readable. Flip only after verifying storage-api "
        "is deployed + healthy on the target stack (Phase 2 ops gate).",
    )
    FEATURE_UNIFIED_STORAGE: bool = Field(
        default=False,
        description="Dev/test SHORT-CIRCUIT for the unified-storage write "
        "path — true forces new library writes to the `library` bucket "
        "without a DB read. The REAL control is the admin Module Control "
        "Center switch (`storage.unified_storage`, Admin → System → Modules) "
        "consulted per write via storage_flag.unified_storage_enabled(); "
        "prod never sets this env var. Readers auto-resolve both path shapes "
        "via resolve_media_source, so flipping is forward-only and rollback "
        "keeps already-written sb:// rows readable.",
    )
    FEATURE_SHOT_GENERATE: bool = Field(
        default=False,
        description="Enable single-shot image generation (POST "
        "/shots/{id}/generate): dispatches a DBOS workflow that runs the "
        "storyboard image-provider chain and writes the produced URL onto the "
        "shot row. Off (default) = the endpoint 404s (existence hidden) — this "
        "is an independent switch for the image-generation cost surface. Flip "
        "true once the generate chain is validated on the target stack.",
    )
    FEATURE_SHOT_VIDEO: bool = Field(
        default=False,
        description="Enable single-shot video generation (POST "
        "/shots/{id}/generate-video): dispatches a DBOS workflow that runs the "
        "DB-catalog video provider (jimeng-cli / seedance) and writes the "
        "produced clip's durable URL onto the shot's video_url column. Off "
        "(default) = the endpoint 404s (existence hidden) — an independent "
        "switch for the video-generation cost surface, separate from "
        "FEATURE_SHOT_GENERATE. Flip true once the video chain is validated on "
        "the target stack (needs the dreamina CLI logged in on the NAS).",
    )
    STORAGE_SIGNED_URL_PUBLIC_BASE: str = Field(
        default="",
        description="OPTIONAL public base URL (scheme+host[+port]) for storage "
        "signed URLs handed to CLOUD model providers (agent vision). Empty "
        "(default) = vision inlines object-store images as base64 data URLs, "
        "which always works. Set this ONLY to a base the provider can reach "
        "from the public internet (e.g. https://cn-sb.nous.ink:88) — "
        "our SUPABASE_URL is a LAN address, and a LAN-based signed URL "
        "silently breaks vision for object-store images.",
    )
    HLS_OBJECT_STORE: bool = Field(
        default=False,
        description="Write HLS transcode output (playlists + segments) to the "
        "object store instead of the local filesystem. Deliberately SEPARATE "
        "from where the SOURCE video lives: a filesystem source may still "
        "publish its HLS to the store, and vice versa. Off by default so "
        "existing deployments keep the on-disk layout until the backfill has "
        "run — playback reads the row's own hls_path, so both shapes coexist.",
    )
    FEATURE_RUST_STREAM_IO: bool = Field(
        default=False,
        description=(
            "把 materialize / put_file 的字节搬运交给 nous_core（Rust）。"
            "关掉即回退纯 Python 路径。这是实现替换而非业务能力,故用 env "
            "flag 而不进 Module Control Center。"
        ),
    )

    # ============================================
    # 下载设置
    # ============================================
    # Docker 部署时使用默认值 /app/downloads（容器内路径）
    # 本地开发时可通过 .env 覆盖为实际路径
    DOWNLOAD_PATH: str = Field(default="/app/downloads", description="视频存储路径")
    # Production compose sets this: DOWNLOAD_PATH must then carry the host
    # volume's ``.mounted`` marker or startup readiness degrades (see
    # startup/work_dir_probe.py). Off by default so dev boxes / CI, which have
    # no bind mount, are not asked for one.
    MEDIA_WORK_DIR_REQUIRE_MARKER: bool = Field(
        default=False,
        description="DOWNLOAD_PATH 必须带 .mounted marker（生产 compose 打开）",
    )
    # materialize() 的 S3 读通缓存目录。必须指向部署机本地盘(compose bind
    # /app/s3cache → gpupc NVMe)——放 CIFS 上比直接拉 S3 还慢(实测 145 vs
    # 266 MB/s),等于负优化。空 = 禁用(退回 temp 下载用完即删的旧行为)。
    MEDIA_S3_CACHE_DIR: str = Field(default="", description="materialize S3 缓存目录")
    MEDIA_S3_CACHE_MAX_GB: float = Field(
        default=20.0, description="materialize S3 缓存目录容量上限(GB)"
    )
    COOKIES_DIR: str = Field(
        default="", description="Path to directory containing platform cookie files"
    )
    HTTP_TIMEOUT: float = Field(default=30.0, description="HTTP请求超时(秒)")
    DOWNLOAD_TIMEOUT: float = Field(default=60.0, description="下载超时(秒)")
    COVER_DOWNLOAD_TIMEOUT: float = Field(
        default=45.0,
        description=(
            "封面下载的总时限(秒)。DOWNLOAD_TIMEOUT 是 httpx 的 per-read 超时,"
            "对慢速 trickle 的 CDN 无总上界 → 封面拉取可 hang 数十分钟拖垮整个任务。"
            "用 asyncio.wait_for 包一层总 deadline,超时即放弃该 URL 试下一个。"
        ),
    )

    # 用户代理列表（通用）
    USER_AGENTS: list[str] = Field(
        default_factory=lambda: [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            " (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
        ]
    )

    # Douyin 专用 UA 池。一次解析任务挑一条，贯穿 ABogus/DrissionPage
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
        default=5.0,
        description="DNS resolution timeout for url_guard async path. "
        "Bumped 2.0->5.0: douyin domains (v.douyin.com / www.douyin.com) "
        "resolve from the NAS in 0.9-5.0s (measured), and a 2s cap "
        "intermittently tripped 'dns resolution timed out' in the SSRF "
        "guard's pre-fetch check — which is the FIRST step of the douyin "
        "ABogus parser (short-link resolution, before any cookie use), so a "
        "slow-DNS moment failed the whole parse with 'All enabled Douyin "
        "parse methods failed'. Not a cookie problem.",
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

    # OpenAI credentials are DB-ONLY too (2026-07-07, follow-up to the LLM
    # retirement): OPENAI_API_KEY / OPENAI_MODEL were removed. Visual analysis
    # and embedding resolve through the mediahub_models catalog / user BYOK.

    # ============================================
    # LLM Configuration (Script / Storyboard AI)
    # ============================================
    # LLM credentials are DB-ONLY (铁律 2026-07-07): LLM_API_URL / LLM_API_KEY /
    # LLM_MODEL, DEEPSEEK_*, DOUBAO_* and CLAUDE_API_KEY were removed. Platform
    # models live in the admin-managed ``mediahub_models`` catalog (encrypted
    # at rest); users bring their own keys in Settings → AI Providers. Only
    # non-credential knobs (timeouts, budgets) remain here.
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

    # needs_input 一等状态（spec 2026-07-30）：recv 挂起 TTL 与单次 dispatch 等待轮上限
    NEEDS_INPUT_RECV_TTL_HOURS: int = Field(
        default=72,
        description="How long a dispatch workflow stays suspended waiting for the "
        "user's answer before giving up (falls back to today's terminate-and-"
        "reply-restart behavior)",
    )
    NEEDS_INPUT_MAX_WAIT_ROUNDS: int = Field(
        default=5,
        description="Max needs_input suspend rounds per dispatch — caps an agent "
        "that keeps asking follow-up questions in one workflow",
    )

    MEDIA_PUBLIC_URL: str = Field(
        default="https://cn.nous.ink:88",
        description="Public URL for media file access. Defaults to the "
        "mainland direct-connect entrypoint (nas-A :88 -> ZeroTier -> gpupc); "
        "override to https://api.nous.ink when the consumer is an overseas "
        "service that cannot reach a non-standard port.",
    )
    FRONTEND_URL: str = Field(
        default="http://localhost:5175",
        description="Frontend base URL for OAuth redirect back to the SPA",
    )
    NEWSNOW_API_URL: str = Field(
        default="http://localhost:4000",
        description=(
            "Self-hosted NewsNow API base URL — env FALLBACK only; the "
            "authoritative value is system_settings newsnow.api_url (DB)"
        ),
    )

    # ============================================
    # Distribution — session channel (nous-browser)
    # See docs/superpowers/specs/2026-08-04-distribution-session-channel-design.md
    # ============================================
    BROWSER_SERVICE_URL: str = Field(
        default="http://nous-browser:8090",
        description="nous-browser (Playwright + Xvfb) base URL — docker "
        "internal network only, never mapped to a host port. Used by the "
        "distribution session channel for cookie-session validation, QR "
        "login and DOM publishing.",
    )
    BROWSER_INTERNAL_TOKEN: str = Field(
        default="",
        description="Shared secret sent as the X-Internal-Token header on "
        "every nous-browser call. Machine-specific secret — belongs in "
        "secrets/backend.env, NOT config.yml. Empty means the session "
        "channel is disabled: BrowserClient fails loud with "
        "error_kind='not_configured' rather than calling unauthenticated "
        "(the service holds DECRYPTED platform sessions).",
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
