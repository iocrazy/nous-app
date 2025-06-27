# app/db/init_db.py

"""
数据库初始化模块

负责创建数据库目录、初始化数据库表结构等功能。
确保应用程序启动时数据库环境正确配置。
"""

import os
from pathlib import Path
from app.db.database import async_engine
from app.models.base import Base
from app.core.config import settings
from loguru import logger
from sqlalchemy import select
from app.db.session import AsyncSessionLocal
from app.models.user import Role, Permission, RoleType


async def init_roles():
    """初始化基本角色"""
    logger.info("开始初始化基本角色、权限...")
    # todo 放到repo中
    async with AsyncSessionLocal() as db:
        try:
            # 检查并创建基本角色
            for role_type in RoleType:
                # 查询角色是否已存在
                stmt = select(Role).where(Role.name == role_type.value)
                result = await db.execute(stmt)
                role = result.scalar_one_or_none()
                
                if role is None:
                    # 创建角色
                    description = {
                        RoleType.ADMIN.value: "系统管理员",
                        RoleType.USER.value: "普通用户"

                    }.get(role_type.value, f"{role_type.value} 角色")
                    
                    logger.info(f"创建角色: {role_type.value} ({description})")
                    role = Role(name=role_type.value, description=description)
                    db.add(role)
                    # 提交事务
                    await db.commit()
                else:
                    # 角色已存在，输出角色信息
                    logger.info(f"角色已存在: {role_type.value} (ID: {role.id}, 描述: {role.description})")
            

            logger.success("基本角色初始化成功")
            
        except Exception as e:
            logger.error(f"初始化角色时出错: {e}")
            await db.rollback()
            raise


async def init_db():
    """初始化数据库，创建所有表"""
    # 确保数据目录存在
    data_dir = settings.ROOT_DIR / "data"
    os.makedirs(data_dir, exist_ok=True)

    logger.info(f"创建数据库目录: {data_dir}")
    logger.info(f"使用数据库URL: {settings.DATABASE_URL}")

    # 创建所有表
    try:
        async with async_engine.begin() as conn:
            logger.info("创建数据库表...")
            await conn.run_sync(Base.metadata.create_all)
        logger.success("数据库表创建成功")
        
        # 初始化基本角色
        await init_roles()
        
    except Exception as e:
        logger.error(f"创建数据库表时出错: {e}")
        raise
