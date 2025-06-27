from typing import Optional, Dict, Any
from sqlalchemy import select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload, contains_eager

from app.models.user import User, Role, Permission
from app.repositories.base_repository import BaseRepository

class UserRepository(BaseRepository[User]):
    """用户数据仓储类"""

    def __init__(self, db: AsyncSession):
        """
        初始化仓储

        Args:
            db: 异步数据库会话
        """
        super().__init__(db, User)
    
    # 用户特有的方法
    async def get_one_by_username(self, username: str) -> Optional[User]:
        """
        通过用户名获取用户

        Args:
            username: 用户名

        Returns:
            Optional[User]: 找到的用户，未找到则返回 None
        """
        return await self.find_one_by_field("username", username)

    async def get_one_by_username_with_roles(self, username: str) -> Optional[User]:
        """
        通过用户名获取用户及其角色

        Args:
            username: 用户名

        Returns:
            Optional[User]: 找到的用户，未找到则返回 None
        """
        stmt = select(User).where(User.username == username).options(
            selectinload(User.roles)
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

class RoleRepository(BaseRepository[Role]):
    """角色数据仓储类"""

    def __init__(self, db: AsyncSession):
        """
        初始化仓储

        Args:
            db: 异步数据库会话
        """
        super().__init__(db, Role)
    
    # 角色特有的方法
    async def get_one_by_name(self, name: str) -> Optional[Role]:
        """
        通过角色名获取角色

        Args:
            name: 角色名

        Returns:
            Optional[Role]: 找到的角色，未找到则返回 None
        """
        return await self.find_one_by_field("name", name)

    async def get_one_by_role_name_with_permissions(self, role_name: str) -> Optional[Role]:
        """
        通过角色名获取角色及其权限

        Args:
            role_name: 角色名

        Returns:
            Optional[Role]: 找到的角色，未找到则返回 None
        """
        stmt = select(Role).where(Role.name == role_name).options(
            selectinload(Role.permissions)
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

class PermissionRepository(BaseRepository[Permission]):
    """权限数据仓储类"""

    def __init__(self, db: AsyncSession):
        """
        初始化仓储

        Args:
            db: 异步数据库会话
        """
        super().__init__(db, Permission)
    
    # 权限特有的方法
    async def get_by_name(self, name: str) -> Optional[Permission]:
        """
        通过权限名获取权限

        Args:
            name: 权限名

        Returns:
            Optional[Permission]: 找到的权限，未找到则返回 None
        """
        return await self.find_one_by_field("name", name)

    async def get_by_id(self, id: int) -> Optional[Permission]:
        """
        通过ID获取权限

        Args:
            id: 权限ID

        Returns:
            Optional[Permission]: 找到的权限，未找到则返回 None
        """
        return await self.find_one_by_field("id", id)

