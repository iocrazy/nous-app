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
    USE_ASYNCPG_AGENT_RUNS: bool = Field(
        default=False,
        description="Route AgentRunsRepository through asyncpg + Supavisor",
    )
    USE_ASYNCPG_RESOURCES: bool = Field(
        default=False,
        description="Route ResourcesRepository through asyncpg + Supavisor "
        "(Phase 3a — covers the 10 methods on the resources table; "
        "items / versions / folders still use legacy supabase-py)",
    )
    USE_ORM_MEDIA: bool = Field(
        default=False,
        description="Route MediaRepository through the SQLAlchemy 2.0 ORM "
        "session layer (Task 5.1 — replaces the asyncpg media path; fixes "
        "the silent-rollback P0 by committing writes via write_scope(). "
        "Covers parsed_media CRUD + lists + search + statistics; the 9 "
        "wrapper methods route through the ORM overrides via Python MRO)",
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
