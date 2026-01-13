# app/services/supabase_auth_service.py

"""
Supabase 认证服务

基于 Supabase Auth 的用户认证服务，提供注册、登录、登出等功能。
"""

from typing import Optional, Dict, Any
from loguru import logger

from app.db.supabase_client import get_supabase, get_supabase_admin


class SupabaseAuthService:
    """Supabase 认证服务"""

    def __init__(self):
        self.client = get_supabase()

    async def sign_up(
        self,
        email: str,
        password: str,
        metadata: Optional[Dict[str, Any]] = None
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
            options = {}
            if metadata:
                options["data"] = metadata

            response = self.client.auth.sign_up({
                "email": email,
                "password": password,
                "options": options if options else None
            })

            if response.user:
                logger.info(f"用户注册成功: {email}")
                return {
                    "success": True,
                    "user": {
                        "id": response.user.id,
                        "email": response.user.email,
                        "created_at": str(response.user.created_at) if response.user.created_at else None
                    },
                    "session": {
                        "access_token": response.session.access_token if response.session else None,
                        "refresh_token": response.session.refresh_token if response.session else None,
                        "expires_at": response.session.expires_at if response.session else None
                    } if response.session else None
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
            response = self.client.auth.sign_in_with_password({
                "email": email,
                "password": password
            })

            if response.user and response.session:
                logger.info(f"用户登录成功: {email}")
                return {
                    "success": True,
                    "user": {
                        "id": response.user.id,
                        "email": response.user.email,
                        "user_metadata": response.user.user_metadata
                    },
                    "session": {
                        "access_token": response.session.access_token,
                        "refresh_token": response.session.refresh_token,
                        "expires_at": response.session.expires_at,
                        "token_type": response.session.token_type
                    }
                }
            else:
                return {"success": False, "message": "登录失败"}

        except Exception as e:
            logger.error(f"用户登录失败: {e}")
            return {"success": False, "message": str(e)}

    async def sign_out(self) -> Dict[str, Any]:
        """用户登出"""
        try:
            self.client.auth.sign_out()
            logger.info("用户登出成功")
            return {"success": True, "message": "登出成功"}
        except Exception as e:
            logger.error(f"用户登出失败: {e}")
            return {"success": False, "message": str(e)}

    async def get_user(self, access_token: str) -> Optional[Dict[str, Any]]:
        """
        根据 access_token 获取用户信息

        Args:
            access_token: JWT 访问令牌

        Returns:
            用户信息或 None
        """
        try:
            response = self.client.auth.get_user(access_token)
            if response.user:
                return {
                    "id": response.user.id,
                    "email": response.user.email,
                    "user_metadata": response.user.user_metadata,
                    "app_metadata": response.user.app_metadata,
                    "created_at": str(response.user.created_at) if response.user.created_at else None
                }
            return None
        except Exception as e:
            logger.error(f"获取用户信息失败: {e}")
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
            response = self.client.auth.refresh_session(refresh_token)
            if response.session:
                return {
                    "success": True,
                    "session": {
                        "access_token": response.session.access_token,
                        "refresh_token": response.session.refresh_token,
                        "expires_at": response.session.expires_at
                    }
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
            self.client.auth.reset_password_email(email)
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
        metadata: Optional[Dict[str, Any]] = None
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

            # 设置当前会话
            self.client.auth.set_session(access_token, "")

            response = self.client.auth.update_user(update_data)
            if response.user:
                logger.info(f"用户信息更新成功: {response.user.email}")
                return {
                    "success": True,
                    "user": {
                        "id": response.user.id,
                        "email": response.user.email,
                        "user_metadata": response.user.user_metadata
                    }
                }
            return {"success": False, "message": "更新失败"}
        except Exception as e:
            logger.error(f"更新用户信息失败: {e}")
            return {"success": False, "message": str(e)}


class SupabaseAdminAuthService:
    """Supabase 管理员认证服务（使用 service_role key）"""

    def __init__(self):
        self.admin_client = get_supabase_admin()

    async def get_user_by_id(self, user_id: str) -> Optional[Dict[str, Any]]:
        """根据用户 ID 获取用户信息"""
        try:
            response = self.admin_client.auth.admin.get_user_by_id(user_id)
            if response.user:
                return {
                    "id": response.user.id,
                    "email": response.user.email,
                    "user_metadata": response.user.user_metadata,
                    "app_metadata": response.user.app_metadata
                }
            return None
        except Exception as e:
            logger.error(f"获取用户信息失败: {e}")
            return None

    async def list_users(
        self,
        page: int = 1,
        per_page: int = 50
    ) -> Dict[str, Any]:
        """获取用户列表"""
        try:
            response = self.admin_client.auth.admin.list_users(
                page=page,
                per_page=per_page
            )
            users = []
            for user in response:
                users.append({
                    "id": user.id,
                    "email": user.email,
                    "created_at": str(user.created_at) if user.created_at else None,
                    "user_metadata": user.user_metadata
                })
            return {"success": True, "users": users}
        except Exception as e:
            logger.error(f"获取用户列表失败: {e}")
            return {"success": False, "message": str(e), "users": []}

    async def delete_user(self, user_id: str) -> Dict[str, Any]:
        """删除用户"""
        try:
            self.admin_client.auth.admin.delete_user(user_id)
            logger.info(f"用户已删除: {user_id}")
            return {"success": True, "message": "用户已删除"}
        except Exception as e:
            logger.error(f"删除用户失败: {e}")
            return {"success": False, "message": str(e)}

    async def update_user_role(
        self,
        user_id: str,
        role: str
    ) -> Dict[str, Any]:
        """更新用户角色（存储在 app_metadata 中）"""
        try:
            response = self.admin_client.auth.admin.update_user_by_id(
                user_id,
                {"app_metadata": {"role": role}}
            )
            if response.user:
                logger.info(f"用户角色已更新: {user_id} -> {role}")
                return {"success": True, "message": f"用户角色已更新为 {role}"}
            return {"success": False, "message": "更新失败"}
        except Exception as e:
            logger.error(f"更新用户角色失败: {e}")
            return {"success": False, "message": str(e)}
