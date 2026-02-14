# backend/app/api/supabase_auth_router.py

"""
Supabase 认证路由

基于 Supabase Auth 的用户认证 API 端点。
"""

from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException
from loguru import logger
from pydantic import BaseModel, EmailStr

from app.db.supabase_client import get_async_supabase_admin
from app.repositories.user_logs_repository import log_user_action
from app.services.points_service import PointsService
from app.services.supabase_auth_service import (
    SupabaseAdminAuthService,
    SupabaseAuthService,
)

router = APIRouter(prefix="/auth", tags=["认证"])


# ============================================
# Background helpers
# ============================================


async def _create_team_quota_for_new_user(user_id: str) -> None:
    """Look up the user's team and create a quota with free welcome points.

    This runs as a background task so it never blocks the signup response.
    """
    try:
        client = await get_async_supabase_admin()
        team_result = (
            await client.table("team_members")
            .select("team_id")
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
        if not team_result.data:
            logger.info(
                f"No team found for new user {user_id} – skipping quota creation"
            )
            return

        team_id = team_result.data[0]["team_id"]
        points_service = PointsService()
        await points_service.ensure_team_quota(team_id, grant_free_points=True, user_id=user_id)
        logger.info(
            f"Created team quota with welcome points for user {user_id}, "
            f"team {team_id}"
        )
    except Exception as e:
        logger.error(f"Failed to create team quota for user {user_id}: {e}")


# ============================================
# 请求/响应模型
# ============================================


class SignUpRequest(BaseModel):
    """注册请求"""

    email: EmailStr
    password: str
    username: Optional[str] = None


class SignInRequest(BaseModel):
    """登录请求"""

    email: EmailStr
    password: str


class RefreshTokenRequest(BaseModel):
    """刷新令牌请求"""

    refresh_token: str


class ResetPasswordRequest(BaseModel):
    """重置密码请求"""

    email: EmailStr


class UpdateUserRequest(BaseModel):
    """更新用户请求"""

    email: Optional[EmailStr] = None
    password: Optional[str] = None
    username: Optional[str] = None


class UpdateRoleRequest(BaseModel):
    """更新角色请求"""

    user_id: str
    role: str


# ============================================
# 路由端点
# ============================================


@router.post("/signup")
async def sign_up(request: SignUpRequest, background_tasks: BackgroundTasks):
    """
    用户注册

    - **email**: 邮箱地址
    - **password**: 密码（至少6位）
    - **username**: 用户名（可选）
    """
    auth_service = SupabaseAuthService()

    metadata = {}
    if request.username:
        metadata["username"] = request.username

    result = await auth_service.sign_up(
        email=request.email,
        password=request.password,
        metadata=metadata if metadata else None,
    )

    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("message", "注册失败"))

    # Log signup and create team quota with welcome points
    user_id = result.get("user", {}).get("id")
    if user_id:
        background_tasks.add_task(
            log_user_action,
            user_id=user_id,
            action="auth",
            message="User registered",
            status="success",
        )
        background_tasks.add_task(
            _create_team_quota_for_new_user,
            user_id,
        )

    return result


@router.post("/signin")
async def sign_in(request: SignInRequest, background_tasks: BackgroundTasks):
    """
    用户登录

    - **email**: 邮箱地址
    - **password**: 密码
    """
    auth_service = SupabaseAuthService()

    result = await auth_service.sign_in(email=request.email, password=request.password)

    if not result.get("success"):
        raise HTTPException(status_code=401, detail=result.get("message", "登录失败"))

    # Log signin
    user_id = result.get("session", {}).get("user", {}).get("id")
    if user_id:
        background_tasks.add_task(
            log_user_action,
            user_id=user_id,
            action="auth",
            message="User logged in",
            status="success",
        )

    return result


@router.post("/signout")
async def sign_out(
    background_tasks: BackgroundTasks,
    authorization: Optional[str] = Header(None),
):
    """用户登出"""
    # Try to extract user_id before signing out
    user_id = None
    if authorization:
        try:
            token = authorization.replace("Bearer ", "")
            auth_svc = SupabaseAuthService()
            user = await auth_svc.get_user(token)
            if user:
                user_id = user.get("id")
        except Exception:
            pass

    auth_service = SupabaseAuthService()
    result = await auth_service.sign_out()

    if user_id:
        background_tasks.add_task(
            log_user_action,
            user_id=user_id,
            action="auth",
            message="User logged out",
            status="success",
        )

    return result


@router.get("/me")
async def get_current_user(authorization: str = Header(...)):
    """
    获取当前用户信息

    需要在 Header 中传入 Bearer Token
    """
    try:
        token = authorization.replace("Bearer ", "")
        auth_service = SupabaseAuthService()
        user = await auth_service.get_user(token)

        if not user:
            raise HTTPException(status_code=401, detail="无效的令牌")

        return {"success": True, "user": user}

    except Exception as e:
        logger.error(f"获取用户信息失败: {e}")
        raise HTTPException(status_code=401, detail="认证失败")


@router.post("/refresh")
async def refresh_session(request: RefreshTokenRequest):
    """
    刷新会话

    - **refresh_token**: 刷新令牌
    """
    auth_service = SupabaseAuthService()
    result = await auth_service.refresh_session(request.refresh_token)

    if not result.get("success"):
        raise HTTPException(status_code=401, detail=result.get("message", "刷新失败"))

    return result


@router.post("/reset-password")
async def reset_password(request: ResetPasswordRequest):
    """
    发送密码重置邮件

    - **email**: 邮箱地址
    """
    auth_service = SupabaseAuthService()
    result = await auth_service.reset_password(request.email)

    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("message", "发送失败"))

    return result


@router.put("/me")
async def update_user(request: UpdateUserRequest, authorization: str = Header(...)):
    """
    更新用户信息

    - **email**: 新邮箱（可选）
    - **password**: 新密码（可选）
    - **username**: 新用户名（可选）
    """
    try:
        token = authorization.replace("Bearer ", "")
        auth_service = SupabaseAuthService()

        metadata = {}
        if request.username:
            metadata["username"] = request.username

        result = await auth_service.update_user(
            access_token=token,
            email=request.email,
            password=request.password,
            metadata=metadata if metadata else None,
        )

        if not result.get("success"):
            raise HTTPException(
                status_code=400, detail=result.get("message", "更新失败")
            )

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新用户失败: {e}")
        raise HTTPException(status_code=500, detail="更新失败")


# ============================================
# 管理员端点
# ============================================


@router.get("/admin/users")
async def list_users(
    page: int = 1, per_page: int = 50, authorization: str = Header(...)
):
    """
    获取用户列表（管理员）

    需要管理员权限
    """
    # TODO: 验证管理员权限
    admin_service = SupabaseAdminAuthService()
    result = await admin_service.list_users(page=page, per_page=per_page)

    if not result.get("success"):
        raise HTTPException(status_code=500, detail=result.get("message", "获取失败"))

    return result


@router.put("/admin/role")
async def update_user_role(
    request: UpdateRoleRequest, authorization: str = Header(...)
):
    """
    更新用户角色（管理员）

    - **user_id**: 用户ID
    - **role**: 新角色（admin, user, test）
    """
    # TODO: 验证管理员权限
    admin_service = SupabaseAdminAuthService()
    result = await admin_service.update_user_role(
        user_id=request.user_id, role=request.role
    )

    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("message", "更新失败"))

    return result


@router.delete("/admin/users/{user_id}")
async def delete_user(user_id: str, authorization: str = Header(...)):
    """
    删除用户（管理员）

    - **user_id**: 用户ID
    """
    # TODO: 验证管理员权限
    admin_service = SupabaseAdminAuthService()
    result = await admin_service.delete_user(user_id)

    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("message", "删除失败"))

    return result
