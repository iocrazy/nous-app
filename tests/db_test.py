from typing import TypeVar, Generic, Type, List, Optional, Dict, Any, Union
from sqlalchemy import select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload, load_only ,contains_eager
from loguru import logger
import asyncio
import functools

from app.models import User, Permission, Role , RoleType

from app.db.session import  get_async_transaction_session
from app.repositories.user_repository import UserRepository,PermissionRepository, RoleRepository



# 装饰器：自动提供数据库会话
def with_db_session(func):
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        async with get_async_transaction_session() as db:
            return await func(*args, db=db, **kwargs)
    return wrapper

# 使用装饰器
@with_db_session
async def select_user( db: AsyncSession) :
    query = select(User).options(selectinload(User.role)).where(User.role_name == "user")
    result = await db.execute(query)
    users = result.scalars().all()
    for user in users:
        logger.info(f"{user.role}")
    return None

    # return result.scalar_one_or_none()

    # return result.scalars().all()



# 使用装饰器
@with_db_session
async def select_user_2( db: AsyncSession) :
    query3 = select(User).join(Role).options(contains_eager(User.role)).where(Role.name == "admin").limit(1)
    result = await db.execute(query3)

    return result.scalar_one_or_none()

# @with_db_session
# async def insert_user(db: AsyncSession) -> None:
#
#     r1 = Role( name = "test")
#     u1 = User(username="test_user",password="test_password", role=r1)
#     db.add(u1)
#
#     logger.info(f"添加用户: {u1.username}")
#     await db.commit()


@with_db_session
async def creat_role_permission(db: AsyncSession):
    # 1. 获取权限列表
    query_permissions = select(Permission)
    result_permissions = await db.execute(query_permissions)
    permissions = result_permissions.scalars().all()
    
    # 2. 获取角色，并预加载权限关系
    query_role = select(Role).where(Role.id == 1).options(selectinload(Role.permissions))
    result_role = await db.execute(query_role)
    role_to_add_permission = result_role.scalar_one_or_none()
    
    if not role_to_add_permission:
        logger.error("未找到ID为1的角色")
        return None
    
    # 3. 为角色添加权限
    for permission in permissions:
        logger.info(f"找到权限: {permission}")
        
        # 检查权限是否已经存在于角色的权限列表中
        if permission not in role_to_add_permission.permissions:
            role_to_add_permission.permissions.append(permission)
            logger.info(f"为角色 '{role_to_add_permission.name}' 添加权限: {permission.name}")
        else:
            logger.info(f"角色 '{role_to_add_permission.name}' 已经拥有权限: {permission.name}")
    
    # 4. 提交更改
    await db.commit()
    
    # 5. 返回更新后的角色
    return role_to_add_permission

@with_db_session
async def creat_user_role(db: AsyncSession):
    user_repo = UserRepository(db)
    user = await user_repo.get_one_by_username("test")
    if not user:
        logger.error("未找到用户")
    return user



# 调用时不需要提供db参数
async def main():
    # await insert_user()
     logger.info(await creat_user_role())

    # logger.info(f"找到角色: {role.name},type: {type(role.name)}")
    # logger.info(f"找到角色: {role.permissions},type: {type(role.permissions)}")






if __name__ == "__main__":
    asyncio.run(main())
