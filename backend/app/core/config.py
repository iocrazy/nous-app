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
    SUPABASE_SERVICE_ROLE_KEY: str = Field(default="", description="Supabase 服务角色密钥")
    SUPABASE_TENANT_ID: str = Field(default="", description="Supabase 多租户 ID (自托管)")

    # ============================================
    # 下载设置
    # ============================================
    # Docker 部署时使用默认值 /app/downloads（容器内路径）
    # 本地开发时可通过 .env 覆盖为实际路径
    DOWNLOAD_PATH: str = Field(default="/app/downloads", description="视频存储路径")
    HTTP_TIMEOUT: float = Field(default=30.0, description="HTTP请求超时(秒)")
    DOWNLOAD_TIMEOUT: float = Field(default=60.0, description="下载超时(秒)")

    # 用户代理列表
    USER_AGENTS: list[str] = Field(default_factory=lambda: [
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36'
    ])

    # ============================================
    # Notion 集成（可选）
    # ============================================
    NOTION_API_KEY: str = Field(default="", description="Notion API密钥")
    NOTION_DATABASE_ID: str = Field(default="", description="Notion数据库ID")
    PUSH_TO_NOTION: bool = Field(default=False, description="是否推送到Notion")

    # ============================================
    # Celery 配置
    # ============================================
    CELERY_BROKER_URL: str = Field(default="redis://localhost:6379/0", description="Celery 消息队列 URL")
    CELERY_RESULT_BACKEND: str = Field(default="redis://localhost:6379/0", description="Celery 结果存储 URL")
    CELERY_TASK_TIME_LIMIT: int = Field(default=600, description="任务超时时间(秒)")
    CELERY_WORKER_CONCURRENCY: int = Field(default=4, description="Worker 并发数")

    # ============================================
    # OpenAI Configuration (for visual analysis)
    # ============================================
    OPENAI_API_KEY: str = Field(default="", description="OpenAI API Key")
    OPENAI_MODEL: str = Field(default="gpt-4o", description="OpenAI model for visual analysis")
    OPENAI_EMBEDDING_MODEL: str = Field(default="text-embedding-3-small", description="OpenAI embedding model")

    # ============================================
    # AI Provider Configuration
    # ============================================
    DEEPSEEK_API_KEY: str = Field(default="", description="DeepSeek API Key")
    DOUBAO_API_KEY: str = Field(default="", description="Doubao API Key")
    WHISPER_PROVIDER: str = Field(default="openai_api", description="Whisper provider: openai_api or local")
    AI_DEFAULT_SUMMARY_MODEL: str = Field(default="gpt-4o-mini", description="Default LLM model for summaries")
    AI_DEFAULT_ANALYSIS_MODEL: str = Field(default="gpt-4o", description="Default LLM model for visual analysis")

    model_config = SettingsConfigDict(
        env_file=str(ROOT_DIR / '.env'),
        env_file_encoding='utf-8',
        yaml_file=str(ROOT_DIR / "config.yml"),
        yaml_file_encoding='utf-8',
        # 在运行时赋值时验证字段值，确保类型安全
        validate_assignment=True,

        # 环境变量名称是否区分大小写
        case_sensitive=True,
        # 忽略额外字段
        extra='ignore'
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


