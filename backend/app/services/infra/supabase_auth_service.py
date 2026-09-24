# app/services/supabase_auth_service.py

"""
Supabase 认证服务

基于 Supabase Auth 的用户认证服务，提供注册、登录、登出等功能。
使用异步 Supabase 客户端。
"""

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Dict, Optional

import httpx
from loguru import logger
from supabase_auth import AsyncGoTrueClient

from app.core.config import settings
from app.db.supabase_client import (
    _HTTPX_LIMITS,
    _HTTPX_TIMEOUT,
    get_async_supabase_admin,
)


def _auth_http_client() -> httpx.AsyncClient:
    """The httpx client one isolated GoTrue client talks through.

    Same limits as the shared Supabase clients (keep-alive off — see
    ``app/db/supabase_client.py``). Tests swap this for a ``MockTransport``.
    """
    return httpx.AsyncClient(limits=_HTTPX_LIMITS, timeout=_HTTPX_TIMEOUT)


@asynccontextmanager
async def _isolated_auth_client() -> AsyncIterator[AsyncGoTrueClient]:
    """A GoTrue client that lives for ONE request and remembers nothing.

    These calls used to go through the per-loop anon ``AsyncClient`` that the
    whole process shares. GoTrue clients are stateful: ``sign_in`` /
    ``sign_up`` / ``refresh_session`` / ``set_session`` store the session on
    the client, and ``sign_out()`` revokes whatever session is stored. So
    ``POST /auth/signout`` — which needs no credentials — logged out (scope
    ``global``: every device) the last person who had signed in through this
    process, and that person's refresh token sat in server memory with an
    auto-refresh timer. A fresh client per call, with ``persist_session`` and
    ``auto_refresh_token`` off, has no session to leak between callers.
    """
    headers = {
        "apiKey": settings.SUPABASE_ANON_KEY or "",
        "Authorization": f"Bearer {settings.SUPABASE_ANON_KEY or ''}",
    }
    if settings.SUPABASE_TENANT_ID:
        headers["X-Tenant-ID"] = settings.SUPABASE_TENANT_ID
    http_client = _auth_http_client()
    try:
        yield AsyncGoTrueClient(
            url=f"{(settings.SUPABASE_URL or '').rstrip('/')}/auth/v1",
            headers=headers,
            auto_refresh_token=False,
            persist_session=False,
            http_client=http_client,
        )
    finally:
        await http_client.aclose()


class SupabaseAuthService:
    """Supabase 认证服务 (异步)

    Every call runs on its own :func:`_isolated_auth_client`; nothing about
    one caller's session survives into the next call.
    """

    def __init__(self):
        pass

    async def sign_up(
        self, email: str, password: str, metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        用户注册

        Args:
            email: 邮箱
            password: 密码
            metadata: 用户元数据（如用户名、头像等）

        Returns:
            注册结果
        """
        try:
            # Omit the "options" key entirely when there is no metadata.
            # ``"options": None`` crashes inside gotrue-py (it chains
            # ``.get()`` off the value, and ``None.get`` raises
            # AttributeError) — which 500'd every signup without a
            # username while the router's except turned it into an
            # opaque "'NoneType' object has no attribute 'get'".
            credentials: Dict[str, Any] = {"email": email, "password": password}
            if metadata:
                credentials["options"] = {"data": metadata}

            async with _isolated_auth_client() as auth:
                response = await auth.sign_up(credentials)

            if response.user:
                logger.info(f"用户注册成功: {email}")
                return {
                    "success": True,
                    "user": {
                        "id": response.user.id,
                        "email": response.user.email,
                        "created_at": (
                            str(response.user.created_at)
                            if response.user.created_at
                            else None
                        ),
                    },
                    "session": (
                        {
                            "access_token": (
                                response.session.access_token
                                if response.session
                                else None
                            ),
                            "refresh_token": (
                                response.session.refresh_token
                                if response.session
                                else None
                            ),
                            "expires_at": (
                                response.session.expires_at
                                if response.session
                                else None
                            ),
                        }
                        if response.session
                        else None
                    ),
                }
            else:
                return {"success": False, "message": "注册失败"}

        except Exception as e:
            logger.error(f"用户注册失败: {e}")
            return {"success": False, "message": str(e)}

    async def sign_in(self, email: str, password: str) -> Dict[str, Any]:
        """
        用户登录

        Args:
            email: 邮箱
            password: 密码

        Returns:
            登录结果
        """
        try:
            async with _isolated_auth_client() as auth:
                response = await auth.sign_in_with_password(
                    {"email": email, "password": password}
                )

            if response.user and response.session:
                logger.info(f"用户登录成功: {email}")
                return {
                    "success": True,
                    "user": {
                        "id": response.user.id,
                        "email": response.user.email,
                        "user_metadata": response.user.user_metadata,
                    },
                    "session": {
                        "access_token": response.session.access_token,
                        "refresh_token": response.session.refresh_token,
                        "expires_at": response.session.expires_at,
                        "token_type": response.session.token_type,
                    },
                }
            else:
                return {"success": False, "message": "登录失败"}

        except Exception as e:
            logger.error(f"用户登录失败: {e}")
            return {"success": False, "message": str(e)}

    async def sign_out(self, access_token: str) -> Dict[str, Any]:
        """用户登出：撤销 ``access_token`` 所属用户的会话（scope ``global``）。

        Only the caller's own token is ever revoked; there is no stored
        session to fall back to.
        """
        try:
            async with _isolated_auth_client() as auth:
                await auth.admin.sign_out(access_token, "global")
            logger.info("用户登出成功")
            return {"success": True, "message": "登出成功"}
        except Exception as e:
            logger.error(f"用户登出失败: {e}")
            return {"success": False, "message": str(e)}

    async def get_user(self, access_token: str) -> Optional[Dict[str, Any]]:
        """
        根据 access_token 获取用户信息（本地 JWKS 验签，零网络）。

        返回结构与历史版本对齐（id / email / user_metadata / app_metadata /
        created_at），让现有 caller（/auth/me、realtime、media_auth）不变。
        created_at 不在 JWT claims 里，所以始终为 None — 需要 admin 字段
        请改用 SupabaseAdminAuthService.get_user_by_id。
        """
        # Local import to avoid circular dependency with app.core.deps
        from app.core.deps import verify_jwt

        try:
            claims = await verify_jwt(access_token)
            sub = claims.get("sub")
            if not sub:
                return None
            return {
                "id": str(sub),
                "email": claims.get("email"),
                "user_metadata": claims.get("user_metadata") or {},
                "app_metadata": claims.get("app_metadata") or {},
                "created_at": None,
            }
        except Exception as e:
            logger.warning(f"获取用户信息失败 (token verify): {e}")
            return None

    async def refresh_session(self, refresh_token: str) -> Dict[str, Any]:
        """
        刷新会话

        Args:
            refresh_token: 刷新令牌

        Returns:
            新的会话信息
        """
        try:
            async with _isolated_auth_client() as auth:
                response = await auth.refresh_session(refresh_token)
            if response.session:
                return {
                    "success": True,
                    "session": {
                        "access_token": response.session.access_token,
                        "refresh_token": response.session.refresh_token,
                        "expires_at": response.session.expires_at,
                    },
                }
            return {"success": False, "message": "刷新会话失败"}
        except Exception as e:
            logger.error(f"刷新会话失败: {e}")
            return {"success": False, "message": str(e)}

    async def reset_password(self, email: str) -> Dict[str, Any]:
        """
        发送密码重置邮件

        Args:
            email: 邮箱

        Returns:
            结果
        """
        try:
            async with _isolated_auth_client() as auth:
                await auth.reset_password_email(email)
            logger.info(f"密码重置邮件已发送: {email}")
            return {"success": True, "message": "密码重置邮件已发送"}
        except Exception as e:
            logger.error(f"发送密码重置邮件失败: {e}")
            return {"success": False, "message": str(e)}

    async def update_user(
        self,
        access_token: str,
        email: Optional[str] = None,
        password: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        更新用户信息

        Args:
            access_token: 访问令牌
            email: 新邮箱
            password: 新密码
            metadata: 新的用户元数据

        Returns:
            更新结果
        """
        try:
            update_data = {}
            if email:
                update_data["email"] = email
            if password:
                update_data["password"] = password
            if metadata:
                update_data["data"] = metadata

            # 在这次请求自己的客户端上设置会话：update_user 只会作用于这个 token
            async with _isolated_auth_client() as auth:
                await auth.set_session(access_token, "")
                response = await auth.update_user(update_data)
            if response.user:
                logger.info(f"用户信息更新成功: {response.user.email}")
                return {
                    "success": True,
                    "user": {
                        "id": response.user.id,
                        "email": response.user.email,
                        "user_metadata": response.user.user_metadata,
                    },
                }
            return {"success": False, "message": "更新失败"}
        except Exception as e:
            logger.error(f"更新用户信息失败: {e}")
            return {"success": False, "message": str(e)}


class SupabaseAdminAuthService:
    """Supabase 管理员认证服务（使用 service_role key）(异步)"""

    def __init__(self):
        pass

    async def _get_admin_client(self):
        """Get async admin client (loop-aware, safe for Celery workers)."""
        return await get_async_supabase_admin()

    async def get_user_by_id(self, user_id: str) -> Optional[Dict[str, Any]]:
        """根据用户 ID 获取用户信息"""
        try:
            client = await self._get_admin_client()
            response = await client.auth.admin.get_user_by_id(user_id)
            if response.user:
                return {
                    "id": response.user.id,
                    "email": response.user.email,
                    "user_metadata": response.user.user_metadata,
                    "app_metadata": response.user.app_metadata,
                }
            return None
        except Exception as e:
            logger.error(f"获取用户信息失败: {e}")
            return None

    async def list_users(self, page: int = 1, per_page: int = 50) -> Dict[str, Any]:
        """获取用户列表"""
        try:
            client = await self._get_admin_client()
            response = await client.auth.admin.list_users(page=page, per_page=per_page)
            users = []
            for user in response:
                users.append(
                    {
                        "id": user.id,
                        "email": user.email,
                        "created_at": str(user.created_at) if user.created_at else None,
                        "user_metadata": user.user_metadata,
                    }
                )
            return {"success": True, "users": users}
        except Exception as e:
            logger.error(f"获取用户列表失败: {e}")
            return {"success": False, "message": str(e), "users": []}

    async def delete_user(self, user_id: str) -> Dict[str, Any]:
        """删除用户"""
        try:
            client = await self._get_admin_client()
            await client.auth.admin.delete_user(user_id)
            logger.info(f"用户已删除: {user_id}")
            return {"success": True, "message": "用户已删除"}
        except Exception as e:
            logger.error(f"删除用户失败: {e}")
            return {"success": False, "message": str(e)}

    async def update_user_role(self, user_id: str, role: str) -> Dict[str, Any]:
        """更新用户角色（存储在 app_metadata 中）"""
        try:
            client = await self._get_admin_client()
            response = await client.auth.admin.update_user_by_id(
                user_id, {"app_metadata": {"role": role}}
            )
            if response.user:
                logger.info(f"用户角色已更新: {user_id} -> {role}")
                return {"success": True, "message": f"用户角色已更新为 {role}"}
            return {"success": False, "message": "更新失败"}
        except Exception as e:
            logger.error(f"更新用户角色失败: {e}")
            return {"success": False, "message": str(e)}
