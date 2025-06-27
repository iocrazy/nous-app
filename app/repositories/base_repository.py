# app/repositories/base_repository.py

"""
通用数据仓储基类模块

提供通用的数据库操作方法，减少重复代码，提高一致性。
所有具体的仓储类都应该继承这个基类。
"""

from typing import TypeVar, Generic, Type, List, Optional, Dict, Any, Union
from sqlalchemy import select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import or_, and_
from sqlalchemy.sql import ColumnElement
from loguru import logger

from app.models.base import DBModel

T = TypeVar('T', bound=DBModel)

class BaseRepository(Generic[T]):
    """通用数据仓储基类"""
    
    def __init__(self, db: AsyncSession, model_class: Type[T]):
        """
        初始化仓储
        
        Args:
            db: 异步数据库会话
            model_class: 模型类
        """
        self.db = db
        self.model_class = model_class
    
    async def create(self, data: Dict[str, Any]) -> T:
        """
        创建新记录
        
        Args:
            data: 数据字典
            
        Returns:
            T: 创建的记录
        """
        entity = self.model_class(**data)
        self.db.add(entity)
        # 移除 await self.db.commit() - 由调用者负责提交
        return entity
    
    async def create_entity(self, entity: T) -> T:
        """
        创建新记录（直接使用实体对象）
        
        Args:
            entity: 实体对象
            
        Returns:
            T: 创建的记录
        """
        self.db.add(entity)
        return entity
    
    async def get_by_id(self, entity_id: Union[int, str]) -> Optional[T]:
        """
        通过 ID 获取记录
        
        Args:
            entity_id: 记录 ID
        
        Returns:
            Optional[T]: 找到的记录，未找到则返回 None
        """
        query = select(self.model_class).where(self.model_class.id == entity_id)
        result = await self.db.execute(query)
        return result.scalar_one_or_none()
    
    async def get_all(self, skip: int = 0, limit: int = 100) -> List[T]:
        """
        获取所有记录，支持分页
        
        Args:
            skip: 跳过的记录数
            limit: 返回的最大记录数
            
        Returns:
            List[T]: 记录列表
        """
        query = select(self.model_class).offset(skip).limit(limit)
        result = await self.db.execute(query)
        # 使用 list() 将 Sequence 转换为 List
        return list(result.scalars().all())

    async def update(self, entity_id: Union[int, str], data: Dict[str, Any]) -> Optional[T]:
        """
        更新记录

        Args:
            entity_id: 记录 ID
            data: 要更新的数据字典

        Returns:
            Optional[T]: 更新后的记录，未找到则返回 None
        """
        stmt = update(self.model_class).where(self.model_class.id == entity_id).values(**data)
        result = await self.db.execute(stmt)

        if result.rowcount == 0:
            return None

        return await self.get_by_id(entity_id)

    async def delete(self, entity_id: Union[int, str]) -> bool:
        """
        删除记录
        
        Args:
            entity_id: 记录 ID
        
        Returns:
            bool: 删除成功返回 True，未找到记录返回 False
        """
        # 首先查询实体是否存在
        entity = await self.get_by_id(entity_id)
        if entity is None:  # 使用 is None 而不是 not entity
            return False
        
        # 使用 SQLAlchemy 的 delete 方法
        await self.db.delete(entity)

        return True

    def _build_condition(self, field_name: str, value: Any):
        """
        构建查询条件

        Args:
            field_name: 字段名
            value: 字段值

        Returns:
            ColumnElement: SQLAlchemy 条件表达式
        """
        # 处理不同类型的值
        if isinstance(value, bool):
            return getattr(self.model_class, field_name).is_(value)
        elif value is None:
            return getattr(self.model_class, field_name).is_(None)
        else:
            return getattr(self.model_class, field_name) == value



    async def find_by_field(self, field_name: str, value: Any) -> List[T]:
        """
        通过字段值查找记录

        Args:
            field_name: 字段名
            value: 字段值

        Returns:
            List[T]: 匹配的记录列表
        """

        query = select(self.model_class).where(getattr(self.model_class, field_name) == value)

        result = await self.db.execute(query)
        return list(result.scalars().all())


    async def find_one_by_field(self, field_name: str, value: Any) -> Optional[T]:
        """
        通过字段值查找单个记录

        Args:
            field_name: 字段名
            value: 字段值

        Returns:
            Optional[T]: 匹配的记录，未找到则返回 None
        """
        condition = self._build_condition(field_name, value)
        query = select(self.model_class).where(condition)

        result = await self.db.execute(query)
        return result.scalar_one_or_none()