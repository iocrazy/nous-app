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
from app.repositories.cookies_repository import get_cookies_repository

# merge_settings_json is re-exported here: the canonical shared-column merge lives
# in the repo (every writer goes through UserSettingsRepository), but it's kept
# importable from this module for existing callers/tests (e.g.
# tests/test_user_settings_merge.py imports it from here). noqa: not unused.
from app.repositories.user_settings_repository import (  # noqa: F401
    UserSettingsRepository,
    merge_settings_json,
)

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
            # repo.upsert auto-merges into the shared blob (preserves ai_settings
            # and every untouched top-level key), reading the freshest committed
            # value — pass only the incoming keys. A bare replace clobbered AI
            # provider config on 2026-06-02.
            update_data["settings_json"] = request.settings_json

        if not update_data:
            raise HTTPException(status_code=400, detail="没有提供要更新的数据")

        settings = await repo.upsert(auth.user_id, update_data)

        # Live-apply the batch concurrency cap (Settings → General) without a
        # restart: emit on the lifecycle bus so the worker + gateway poller
        # pick up the new value. Honest semantics: the value is stored
        # per-user in settings_json, but the parse queue concurrency is one
        # global number (last save wins across users). Per-user *isolation* is
        # still automatic — the queue is partitioned by user_id, so the cap
        # applies per user regardless of who saved last. Correct for a
        # single-primary-user deploy. Non-fatal: never break the save.
        if request.settings_json is not None:
            raw_cap = request.settings_json.get("maxConcurrentDownloads")
            if isinstance(raw_cap, (int, float)) and not isinstance(raw_cap, bool):
                try:
                    from app.services.lifecycle_bus import get_bus

                    await get_bus().emit(
                        "config.parse_concurrency", {"value": int(raw_cap)}
                    )
                except Exception as emit_exc:
                    logger.warning(
                        f"[user_settings] parse_concurrency emit failed: {emit_exc}"
                    )

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
        repo = get_cookies_repository()
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
        repo = get_cookies_repository()

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
        repo = get_cookies_repository()
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
        repo = get_cookies_repository()
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
        repo = get_cookies_repository()
        await repo.upsert(
            auth.user_id, platform, {"custom_headers": request.headers_text}
        )
        return {"success": True, "platform": platform}
    except Exception as e:
        logger.error(f"保存 Headers 失败: {e}")
        raise HTTPException(status_code=500, detail="Failed to save headers")
