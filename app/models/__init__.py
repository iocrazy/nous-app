# app/models/__init__.py

"""
数据模型模块初始化文件

导入所有数据模型，确保 Alembic 能够发现所有表结构。
包含基础模型类和具体业务模型的导出。
"""
from app.models.base import Base, DBModel, TimeStampedMixin
from app.models.user import User, Role, Permission, RoleType, associate_roles_permissions, associate_users_roles
from app.models.douyin import Douyin

# 确保所有模型都被导入，供Alembic使用
__all__ = [
    "Base",
    "DBModel",
    "TimeStampedMixin",
    "User",
    "Role",
    "Permission",
    "Douyin",
    "associate_roles_permissions",
    "associate_users_roles"
]
