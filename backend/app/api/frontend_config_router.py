# backend/app/api/frontend_config_router.py

"""
前端配置路由

管理前端应用配置的 API 端点。
这些端点不需要认证，因为配置信息在登录前就需要使用。
"""

from pathlib import Path
from typing import Optional

import yaml
from fastapi import APIRouter, HTTPException
from loguru import logger
from pydantic import BaseModel

from app.core.config import settings

router = APIRouter(prefix="/config", tags=["前端配置"])

# 配置文件路径
CONFIG_FILE = Path(__file__).parent.parent.parent / "frontend_config.yml"


# ============================================
# 请求/响应模型
# ============================================


class FrontendConfig(BaseModel):
    """前端配置"""

    supabase_url: Optional[str] = None
    supabase_anon_key: Optional[str] = None
    default_download_path: Optional[str] = None


class UpdateConfigRequest(BaseModel):
    """更新配置请求"""

    supabase_url: Optional[str] = None
    supabase_anon_key: Optional[str] = None
    default_download_path: Optional[str] = None


# ============================================
# 辅助函数
# ============================================


def get_default_download_path() -> str:
    """获取默认下载路径（从环境变量）"""
    return settings.DOWNLOAD_PATH


def load_config() -> dict:
    """加载配置文件"""
    if not CONFIG_FILE.exists():
        return {
            "supabase": {"url": "", "anon_key": ""},
            "default_download_path": get_default_download_path(),
        }

    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception as e:
        logger.error(f"加载前端配置失败: {e}")
        return {}


def save_config(config: dict) -> bool:
    """保存配置文件"""
    try:
        # 构建 YAML 内容（带注释）
        content = """# ===========================================
# 前端配置文件 (frontend_config.yml)
# 用于存储前端应用的配置信息
# 优先级: 此文件 > .env 环境变量
# ===========================================

# Supabase 数据库配置
# 如果为空，前端将使用 .env 中的 VITE_SUPABASE_URL 和 VITE_SUPABASE_ANON_KEY
supabase:
  url: "{supabase_url}"
  anon_key: "{supabase_anon_key}"

# 默认下载路径（服务器端）
# 如果用户已登录，将使用数据库中的用户设置覆盖此值
default_download_path: "{default_download_path}"
""".format(
            supabase_url=config.get("supabase", {}).get("url", ""),
            supabase_anon_key=config.get("supabase", {}).get("anon_key", ""),
            default_download_path=config.get(
                "default_download_path", get_default_download_path()
            ),
        )

        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            f.write(content)

        logger.info("前端配置已保存")
        return True
    except Exception as e:
        logger.error(f"保存前端配置失败: {e}")
        return False


# ============================================
# API 端点
# ============================================


@router.get("", response_model=FrontendConfig)
async def get_frontend_config():
    """
    获取前端配置

    返回前端应用的配置信息。此端点不需要认证。
    如果 YAML 文件中的值为空，前端应使用 .env 中的默认值。
    """
    try:
        config = load_config()

        return FrontendConfig(
            supabase_url=config.get("supabase", {}).get("url") or None,
            supabase_anon_key=config.get("supabase", {}).get("anon_key") or None,
            default_download_path=config.get("default_download_path")
            or get_default_download_path(),
        )
    except Exception as e:
        logger.error(f"获取前端配置失败: {e}")
        raise HTTPException(status_code=500, detail="获取配置失败")


@router.put("", response_model=FrontendConfig)
async def update_frontend_config(request: UpdateConfigRequest):
    """
    更新前端配置

    更新前端应用的配置信息。此端点不需要认证。
    只更新请求中提供的字段，其他字段保持不变。
    """
    try:
        # 加载现有配置
        config = load_config()

        # 确保 supabase 字典存在
        if "supabase" not in config:
            config["supabase"] = {"url": "", "anon_key": ""}

        # 更新提供的字段
        if request.supabase_url is not None:
            config["supabase"]["url"] = request.supabase_url

        if request.supabase_anon_key is not None:
            config["supabase"]["anon_key"] = request.supabase_anon_key

        if request.default_download_path is not None:
            config["default_download_path"] = request.default_download_path

        # 保存配置
        if not save_config(config):
            raise HTTPException(status_code=500, detail="保存配置失败")

        return FrontendConfig(
            supabase_url=config["supabase"].get("url") or None,
            supabase_anon_key=config["supabase"].get("anon_key") or None,
            default_download_path=config.get("default_download_path")
            or get_default_download_path(),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新前端配置失败: {e}")
        raise HTTPException(status_code=500, detail="更新配置失败")
