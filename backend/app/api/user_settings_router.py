# backend/app/api/user_settings_router.py

"""
用户设置路由

管理用户个人设置的 API 端点。
需要认证才能访问。
"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from loguru import logger
from pydantic import BaseModel

from app.core.deps import AuthDep
from app.repositories.cookies_repository import CookiesRepository
from app.repositories.user_settings_repository import UserSettingsRepository

router = APIRouter(prefix="/settings", tags=["用户设置"])


# ============================================
# 请求/响应模型
# ============================================


class UserSettingsRequest(BaseModel):
    """用户设置请求"""

    download_path: Optional[str] = None
    settings_json: Optional[Dict[str, Any]] = None


class UserSettingsResponse(BaseModel):
    """用户设置响应"""

    id: Optional[str] = None
    user_id: str
    download_path: str
    settings_json: Optional[Dict[str, Any]] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


# ============================================
# API 端点
# ============================================


@router.get("", response_model=UserSettingsResponse)
async def get_user_settings(auth: AuthDep):
    """
    获取当前用户设置

    获取已认证用户的个人设置。如果用户尚未配置设置，则返回默认值。

    需要认证：Bearer Token 或 API Key
    """
    try:
        repo = UserSettingsRepository()
        settings = await repo.get_by_user_id(auth.user_id)

        if settings:
            return UserSettingsResponse(
                id=settings.get("id"),
                user_id=settings.get("user_id"),
                download_path=settings.get(
                    "download_path", "/home/user/downloads/mediahub"
                ),
                settings_json=settings.get("settings_json"),
                created_at=settings.get("created_at"),
                updated_at=settings.get("updated_at"),
            )

        # 返回默认设置
        return UserSettingsResponse(
            user_id=auth.user_id,
            download_path="/home/user/downloads/mediahub",
            settings_json={},
        )

    except Exception as e:
        logger.error(f"获取用户设置失败: {e}")
        raise HTTPException(status_code=500, detail="获取用户设置失败")


@router.put("", response_model=UserSettingsResponse)
async def update_user_settings(request: UserSettingsRequest, auth: AuthDep):
    """
    更新用户设置

    创建或更新已认证用户的个人设置。

    - **download_path**: 默认下载路径
    - **settings_json**: 其他扩展设置（JSON 格式）

    需要认证：Bearer Token 或 API Key
    """
    try:
        repo = UserSettingsRepository()

        # 构建更新数据
        update_data = {}
        if request.download_path is not None:
            update_data["download_path"] = request.download_path
        if request.settings_json is not None:
            update_data["settings_json"] = request.settings_json

        if not update_data:
            raise HTTPException(status_code=400, detail="没有提供要更新的数据")

        settings = await repo.upsert(auth.user_id, update_data)

        if settings:
            return UserSettingsResponse(
                id=settings.get("id"),
                user_id=settings.get("user_id"),
                download_path=settings.get(
                    "download_path", "/home/user/downloads/mediahub"
                ),
                settings_json=settings.get("settings_json"),
                created_at=settings.get("created_at"),
                updated_at=settings.get("updated_at"),
            )

        raise HTTPException(status_code=500, detail="保存设置失败")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新用户设置失败: {e}")
        raise HTTPException(status_code=500, detail="更新用户设置失败")


@router.delete("")
async def delete_user_settings(auth: AuthDep):
    """
    删除用户设置

    删除已认证用户的个人设置，重置为默认值。

    需要认证：Bearer Token 或 API Key
    """
    try:
        repo = UserSettingsRepository()
        success = await repo.delete(auth.user_id)

        if success:
            return {"success": True, "message": "设置已重置为默认值"}

        raise HTTPException(status_code=500, detail="删除设置失败")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"删除用户设置失败: {e}")
        raise HTTPException(status_code=500, detail="删除用户设置失败")


# ============================================
# Parse Mode Settings
# ============================================


class ParseModeRequest(BaseModel):
    """解析模式请求"""

    mode: str  # 'lighthttp' or 'drissionpage'


class ParseModeResponse(BaseModel):
    """解析模式响应"""

    mode: str
    description: str


@router.get("/parse-mode", response_model=ParseModeResponse)
async def get_parse_mode(auth: AuthDep):
    """
    获取当前解析模式

    返回用户设置的解析模式：
    - lighthttp: 快速轻量解析（默认）
    - drissionpage: 浏览器模拟解析（稳定）

    需要认证：Bearer Token 或 API Key
    """
    try:
        repo = UserSettingsRepository()
        settings = await repo.get_by_user_id(auth.user_id)

        mode = "lighthttp"  # 默认值
        if settings and settings.get("settings_json"):
            mode = settings["settings_json"].get("parse_mode", "lighthttp")

        descriptions = {
            "lighthttp": "Fast HTTP parsing (recommended)",
            "drissionpage": "Browser-based parsing (more stable)",
        }

        return ParseModeResponse(
            mode=mode, description=descriptions.get(mode, "Unknown mode")
        )

    except Exception as e:
        logger.error(f"获取解析模式失败: {e}")
        raise HTTPException(status_code=500, detail="获取解析模式失败")


@router.put("/parse-mode", response_model=ParseModeResponse)
async def set_parse_mode(request: ParseModeRequest, auth: AuthDep):
    """
    设置解析模式

    可选模式：
    - lighthttp: 快速轻量解析
    - drissionpage: 浏览器模拟解析

    需要认证：Bearer Token 或 API Key
    """
    valid_modes = ["lighthttp", "drissionpage"]
    if request.mode not in valid_modes:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid mode. Valid options: {', '.join(valid_modes)}",
        )

    try:
        repo = UserSettingsRepository()

        # 获取现有设置
        settings = await repo.get_by_user_id(auth.user_id)
        settings_json = settings.get("settings_json", {}) if settings else {}

        # 更新解析模式
        settings_json["parse_mode"] = request.mode

        # 保存
        await repo.upsert(auth.user_id, {"settings_json": settings_json})

        descriptions = {
            "lighthttp": "Fast HTTP parsing (recommended)",
            "drissionpage": "Browser-based parsing (more stable)",
        }

        logger.info(f"用户 {auth.user_id} 设置解析模式为: {request.mode}")

        return ParseModeResponse(
            mode=request.mode,
            description=descriptions.get(request.mode, "Unknown mode"),
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"设置解析模式失败: {e}")
        raise HTTPException(status_code=500, detail="设置解析模式失败")


# ============================================
# Cookie Management
# ============================================

SUPPORTED_PLATFORMS = ["douyin", "bilibili", "youtube", "qishui"]


class CookieStatusItem(BaseModel):
    """Single platform cookie status (no cookie content for security)"""

    platform: str
    has_cookie: bool
    is_valid: bool
    error_message: Optional[str] = None
    updated_at: Optional[str] = None


class CookieListResponse(BaseModel):
    """Response for GET /settings/cookies"""

    cookies: List[CookieStatusItem]


class CookieUpsertRequest(BaseModel):
    """Request body for PUT /settings/cookies/{platform}"""

    cookie_text: Optional[str] = None
    cookie_file: Optional[str] = None


@router.get("/cookies", response_model=CookieListResponse)
async def list_cookies(auth: AuthDep):
    """
    List cookie status for all supported platforms

    Returns the presence and validity of cookies per platform.
    Cookie content is never returned for security reasons.

    Requires authentication: Bearer Token or API Key
    """
    try:
        repo = CookiesRepository()
        rows = await repo.get_all_by_user(auth.user_id)

        # Index existing rows by platform for quick lookup
        row_by_platform: Dict[str, Dict] = {r["platform"]: r for r in rows}

        items: List[CookieStatusItem] = []
        for platform in SUPPORTED_PLATFORMS:
            row = row_by_platform.get(platform)
            if row:
                items.append(
                    CookieStatusItem(
                        platform=platform,
                        has_cookie=True,
                        is_valid=row.get("is_valid", True),
                        error_message=row.get("error_message"),
                        updated_at=row.get("updated_at"),
                    )
                )
            else:
                items.append(
                    CookieStatusItem(
                        platform=platform,
                        has_cookie=False,
                        is_valid=False,
                        error_message=None,
                        updated_at=None,
                    )
                )

        return CookieListResponse(cookies=items)

    except Exception as e:
        logger.error(f"获取 Cookie 列表失败: {e}")
        raise HTTPException(status_code=500, detail="获取 Cookie 列表失败")


@router.put("/cookies/{platform}")
async def set_cookie(platform: str, request: CookieUpsertRequest, auth: AuthDep):
    """
    Set or update cookie for a platform

    One of cookie_text or cookie_file must be provided.
    Saving resets is_valid to true automatically.

    Requires authentication: Bearer Token or API Key
    """
    if platform not in SUPPORTED_PLATFORMS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported platform. Valid options: {', '.join(SUPPORTED_PLATFORMS)}",
        )

    if not request.cookie_text and not request.cookie_file:
        raise HTTPException(
            status_code=400,
            detail="Either cookie_text or cookie_file must be provided",
        )

    try:
        repo = CookiesRepository()

        data: Dict[str, Any] = {}
        if request.cookie_text is not None:
            data["cookie_text"] = request.cookie_text
        if request.cookie_file is not None:
            data["cookie_file"] = request.cookie_file

        result = await repo.upsert(auth.user_id, platform, data)

        if result is None:
            raise HTTPException(status_code=500, detail="保存 Cookie 失败")

        # qishui-only: validate the saved cookie via a login check. The upsert
        # already forced is_valid=True, so if validation fails we mark it invalid.
        # A validation error itself (network/etc.) must not 500 the save.
        if platform == "qishui":
            try:
                from app.services.media.parsers.soda_music.cookie_validate import (
                    validate_soda_cookie,
                )

                cookie_val = request.cookie_text or request.cookie_file or ""
                ok, err = await validate_soda_cookie(cookie_val)
                if not ok:
                    await repo.mark_invalid(
                        auth.user_id, platform, err or "validation failed"
                    )
            except Exception as e:
                logger.warning(
                    f"qishui Cookie 校验异常（保存已成功，跳过校验）: user={auth.user_id}, error={e}"
                )

        logger.info(f"用户 {auth.user_id} 保存 {platform} Cookie 成功")
        return {"success": True, "platform": platform}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"保存 Cookie 失败: platform={platform}, error={e}")
        raise HTTPException(status_code=500, detail="保存 Cookie 失败")


@router.delete("/cookies/{platform}")
async def delete_cookie(platform: str, auth: AuthDep):
    """
    Delete cookie for a platform

    Requires authentication: Bearer Token or API Key
    """
    if platform not in SUPPORTED_PLATFORMS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported platform. Valid options: {', '.join(SUPPORTED_PLATFORMS)}",
        )

    try:
        repo = CookiesRepository()
        success = await repo.delete(auth.user_id, platform)

        if not success:
            raise HTTPException(status_code=500, detail="删除 Cookie 失败")

        logger.info(f"用户 {auth.user_id} 删除 {platform} Cookie 成功")
        return {"success": True, "platform": platform}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"删除 Cookie 失败: platform={platform}, error={e}")
        raise HTTPException(status_code=500, detail="删除 Cookie 失败")


# ─── Custom Headers ───────────────────────────────────────


class HeadersUpsertRequest(BaseModel):
    """Request body for PUT /settings/headers/{platform}"""

    headers_text: str


@router.get("/headers/{platform}")
async def get_headers(platform: str, auth: AuthDep):
    """Get custom headers for a platform."""
    if platform not in SUPPORTED_PLATFORMS:
        raise HTTPException(status_code=400, detail="Unsupported platform")
    try:
        repo = CookiesRepository()
        row = await repo.get_by_user_and_platform(auth.user_id, platform)
        return {
            "platform": platform,
            "headers_text": row.get("custom_headers", "") if row else "",
        }
    except Exception as e:
        logger.error(f"获取 Headers 失败: {e}")
        raise HTTPException(status_code=500, detail="Failed to get headers")


@router.put("/headers/{platform}")
async def set_headers(platform: str, request: HeadersUpsertRequest, auth: AuthDep):
    """Set custom headers for a platform (stored alongside cookies)."""
    if platform not in SUPPORTED_PLATFORMS:
        raise HTTPException(status_code=400, detail="Unsupported platform")
    try:
        repo = CookiesRepository()
        await repo.upsert(
            auth.user_id, platform, {"custom_headers": request.headers_text}
        )
        return {"success": True, "platform": platform}
    except Exception as e:
        logger.error(f"保存 Headers 失败: {e}")
        raise HTTPException(status_code=500, detail="Failed to save headers")
