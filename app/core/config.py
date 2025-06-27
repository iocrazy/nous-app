# app/core/config.py

"""
应用程序配置模块

包含应用程序的各种配置参数，如日志设置、数据库连接、API密钥等。
这些设置可以通过环境变量覆盖，以支持不同的部署环境。
"""

import os
from pathlib import Path
from typing import  ClassVar
from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict

# 应用常量
APP_PORT = 8080  # Docker容器内部固定端口




class Settings(BaseSettings):
    """应用程序设置类"""
    # 应用程序基本设置
    APP_NAME: str = Field(default="douyin推送下载app", description="抖音推送下载")

    # 项目根目录 - 使用 ClassVar 标记为类变量，而不是模型字段
    ROOT_DIR: ClassVar[Path] = Path(__file__).parent.parent.parent

    # 默认下载路径
    # BASE_DOWNLOAD_PATH: str = Field(default=str(ROOT_DIR / "videos"))

    # NAS设置
    NAS_BASE_PATH: str = Field(default="/app/videos", description="NAS上存储抖音视频的基础路径")

    # 可变类型，推荐用 Field + default_factory
    USER_AGENTS: list[str] = Field(default_factory=lambda: [
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 13_4_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.5 Safari/605.1.15',
        'Mozilla/5.0 (Linux; Android 13; Pixel 7 Pro) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.5735.198 Mobile Safari/537.36',
        'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1',
        'Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:115.0) Gecko/20100101 Firefox/115.0'
    ])

    # 数据库设置
    DATABASE_URL: str = Field(default=f"sqlite+aiosqlite:///{ROOT_DIR}/data/db.sqlite3")
    PUSH_TO_DB: bool = Field(default=True)

    #jwt 设置
    # 终端执行 openssl rand -hex 32 生成
    SECRET_KEY: str = Field(default="6387965d283ed44bc9b4baca450c958fd9c1edb624d4438b939c8065b0d2c475")
    ALGORITHM: str = Field(default="HS256")
    ACCESS_TOKEN_EXPIRE_TIME: int = Field(default=30)


    # 服务器设置
    HOST: str = Field(default="0.0.0.0", description="服务器监听的主机地址")
    APP_PORT: int = Field(default=8080, description="服务器监听的端口")
    RELOAD: bool = Field(default=True, description="是否启用热重载")

    # CORS设置
    CORS_ORIGINS: list[str] = Field(default=["*"], description="允许的跨域来源")
    CORS_CREDENTIALS: bool = Field(default=True, description="是否允许凭证")

    # HTTP客户端设置
    HTTP_TIMEOUT: float = Field(default=30.0, description="HTTP请求超时时间(秒)")

    # Notion API设置
    NOTION_API_KEY: str = Field(default="", description="Notion API密钥")
    NOTION_DATABASE_ID: str = Field(default="", description="Notion数据库ID")
    PUSH_TO_NOTION: bool = Field(default=True, description="是否自动推送到Notion")

    # 下载设置
    DOWNLOAD_TIMEOUT: float = Field(default=30.0, description="下载超时时间(秒)")



    model_config = SettingsConfigDict(
        env_file='.env',
        env_file_encoding='utf-8',
        yaml_file=str(ROOT_DIR / "config.yml"),
        yaml_file_encoding='utf-8',
        # 在运行时赋值时验证字段值，确保类型安全
        validate_assignment=True,

        # 环境变量名称是否区分大小写
        case_sensitive=True
    )






# 创建全局设置实例
settings = Settings()
