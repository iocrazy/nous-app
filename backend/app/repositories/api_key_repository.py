# backend/app/repositories/api_key_repository.py

"""
API 密钥数据仓储

提供 API 密钥的 CRUD 操作和验证功能。
"""

import hashlib
import secrets
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Tuple
from loguru import logger

from app.db.supabase_client import get_supabase_admin


class ApiKeyRepository:
    """API 密钥数据仓储"""

    TABLE_NAME = "api_keys"

    def __init__(self):
        # 使用 admin 客户端绑过 RLS
        self.client = get_supabase_admin()
        self.table = self.client.table(self.TABLE_NAME)

    @staticmethod
    def generate_key() -> Tuple[str, str, str, str]:
        """
        生成 API 密钥

        Returns:
            (key_id, full_key, key_hash, key_prefix)
            - key_id: 公开标识符，用于查找
            - full_key: 完整密钥，仅返回一次
            - key_hash: SHA-256 哈希，存储在数据库
            - key_prefix: 显示前缀，用于用户识别
        """
        # 生成 key_id（公开标识符）
        key_id = secrets.token_hex(16)  # 32 字符

        # 生成完整密钥
        secret = secrets.token_hex(32)  # 64 字符
        full_key = f"dk_{secret}"  # dk = douyin key，共 67 字符

        # 计算哈希
        key_hash = hashlib.sha256(full_key.encode()).hexdigest()

        # 密钥前缀（用于用户识别）
        key_prefix = f"dk_{secret[:8]}..."

        return key_id, full_key, key_hash, key_prefix

    @staticmethod
    def hash_key(key: str) -> str:
        """计算密钥的 SHA-256 哈希"""
        return hashlib.sha256(key.encode()).hexdigest()

    async def create(
        self,
        user_id: str,
        name: str,
        scopes: List[str],
        description: Optional[str] = None,
        expires_at: Optional[datetime] = None,
        rate_limit: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        创建 API 密钥

        Args:
            user_id: 用户 ID
            name: 密钥名称
            scopes: 权限范围列表
            description: 描述
            expires_at: 过期时间
            rate_limit: 速率限制

        Returns:
            创建的记录（包含 secret_key）
        """
        key_id, full_key, key_hash, key_prefix = self.generate_key()

        data = {
            "key_id": key_id,
            "key_hash": key_hash,
            "key_prefix": key_prefix,
            "name": name,
            "description": description,
            "user_id": user_id,
            "scopes": scopes,
            "status": "active",
            "expires_at": expires_at.isoformat() if expires_at else None,
            "rate_limit": rate_limit,
            "usage_count": 0
        }

        try:
            result = self.table.insert(data).execute()

            if result.data:
                record = result.data[0]
                record["secret_key"] = full_key  # 仅返回一次
                logger.info(f"创建 API 密钥成功: {key_id} for user {user_id}")
                return record

            raise Exception("创建 API 密钥失败：无返回数据")

        except Exception as e:
            logger.error(f"创建 API 密钥失败: {e}")
            raise

    async def get_by_key_hash(self, key_hash: str) -> Optional[Dict[str, Any]]:
        """
        通过密钥哈希查找

        Args:
            key_hash: 密钥的 SHA-256 哈希

        Returns:
            密钥记录或 None
        """
        try:
            result = self.table.select("*").eq("key_hash", key_hash).execute()
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error(f"查询 API 密钥失败: {e}")
            return None

    async def get_by_key_id(self, key_id: str) -> Optional[Dict[str, Any]]:
        """
        通过 key_id 查找

        Args:
            key_id: 密钥公开标识符

        Returns:
            密钥记录或 None
        """
        try:
            result = self.table.select("*").eq("key_id", key_id).execute()
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error(f"查询 API 密钥失败: {e}")
            return None

    async def get_user_keys(
        self,
        user_id: str,
        include_revoked: bool = False
    ) -> List[Dict[str, Any]]:
        """
        获取用户的所有 API 密钥

        Args:
            user_id: 用户 ID
            include_revoked: 是否包含已撤销的密钥

        Returns:
            密钥列表
        """
        try:
            query = self.table.select("*").eq("user_id", user_id)

            if not include_revoked:
                query = query.neq("status", "revoked")

            query = query.order("created_at", desc=True)
            result = query.execute()
            return result.data or []

        except Exception as e:
            logger.error(f"获取用户 API 密钥列表失败: {e}")
            return []

    async def update(
        self,
        key_id: str,
        user_id: str,
        data: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """
        更新 API 密钥

        Args:
            key_id: 密钥公开标识符
            user_id: 用户 ID（用于权限验证）
            data: 更新数据

        Returns:
            更新后的记录或 None
        """
        try:
            data["updated_at"] = datetime.now(timezone.utc).isoformat()

            result = self.table.update(data).eq(
                "key_id", key_id
            ).eq(
                "user_id", user_id
            ).execute()

            if result.data:
                logger.info(f"更新 API 密钥成功: {key_id}")
                return result.data[0]
            return None

        except Exception as e:
            logger.error(f"更新 API 密钥失败: {e}")
            raise

    async def delete(self, key_id: str, user_id: str) -> bool:
        """
        删除 API 密钥

        Args:
            key_id: 密钥公开标识符
            user_id: 用户 ID（用于权限验证）

        Returns:
            是否删除成功
        """
        try:
            self.table.delete().eq(
                "key_id", key_id
            ).eq(
                "user_id", user_id
            ).execute()

            logger.info(f"删除 API 密钥成功: {key_id}")
            return True

        except Exception as e:
            logger.error(f"删除 API 密钥失败: {e}")
            return False

    async def revoke(self, key_id: str, user_id: str) -> Optional[Dict[str, Any]]:
        """
        撤销 API 密钥（软删除）

        Args:
            key_id: 密钥公开标识符
            user_id: 用户 ID

        Returns:
            更新后的记录或 None
        """
        return await self.update(key_id, user_id, {"status": "revoked"})

    async def update_usage(self, key_id: str) -> None:
        """
        更新使用统计

        Args:
            key_id: 密钥公开标识符
        """
        try:
            # 使用 RPC 调用原子更新
            self.client.rpc(
                "increment_api_key_usage",
                {"p_key_id": key_id}
            ).execute()
        except Exception as e:
            # 使用统计失败不应影响请求
            logger.warning(f"更新 API 密钥使用统计失败: {e}")

    async def validate_key(self, full_key: str) -> Optional[Dict[str, Any]]:
        """
        验证 API 密钥

        检查密钥是否有效（存在、状态为 active、未过期）

        Args:
            full_key: 完整密钥（如 dk_xxx...）

        Returns:
            有效时返回密钥记录（包含 user_id、scopes 等）
            无效返回 None
        """
        # 检查格式
        if not full_key or not full_key.startswith("dk_"):
            return None

        # 计算哈希
        key_hash = self.hash_key(full_key)

        # 查询数据库
        key_data = await self.get_by_key_hash(key_hash)

        if not key_data:
            logger.debug("API 密钥不存在")
            return None

        # 检查状态
        if key_data.get("status") != "active":
            logger.debug(f"API 密钥状态无效: {key_data.get('status')}")
            return None

        # 检查过期
        expires_at = key_data.get("expires_at")
        if expires_at:
            try:
                # 处理时区
                if isinstance(expires_at, str):
                    expires_at = datetime.fromisoformat(
                        expires_at.replace("Z", "+00:00")
                    )
                if expires_at < datetime.now(timezone.utc):
                    logger.debug("API 密钥已过期")
                    return None
            except Exception as e:
                logger.warning(f"解析过期时间失败: {e}")

        return key_data

    async def count_user_keys(self, user_id: str) -> int:
        """
        统计用户的活跃密钥数量

        Args:
            user_id: 用户 ID

        Returns:
            活跃密钥数量
        """
        try:
            result = self.table.select(
                "*",
                count="exact"
            ).eq(
                "user_id", user_id
            ).eq(
                "status", "active"
            ).execute()

            return result.count or 0

        except Exception as e:
            logger.error(f"统计用户 API 密钥数量失败: {e}")
            return 0
