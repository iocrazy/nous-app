# app/models/base.py

"""
数据模型基础类模块

定义 SQLAlchemy 的基础模型类和通用混入类。
包含时间戳混入类和抽象基础模型类，为所有数据模型提供统一的基础结构。
"""
import json
from datetime import datetime

from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import Integer,  DateTime
from sqlalchemy import TypeDecorator, Text

class Base(DeclarativeBase):
    pass


class TimeStampedMixin:
    created_at: Mapped[DateTime] = mapped_column(DateTime, default=datetime.now(), nullable=False)
    updated_at: Mapped[DateTime] = mapped_column(DateTime, default=datetime.now(), onupdate=datetime.now(), nullable=False)


class DBModel(TimeStampedMixin, Base):
    __abstract__ = True
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True, autoincrement=True)

class JsonList(TypeDecorator):
    """将 Python 列表转换为 JSON 字符串存储在数据库中"""
    impl = Text
    cache_ok = True  # 添加缓存支持

    def process_bind_param(self, value, dialect):
        """将 Python 值转换为数据库值"""
        if value is None:
            return None
        if not isinstance(value, list):
            raise ValueError(f"Expected list, got {type(value)}")
        return json.dumps(value, ensure_ascii=False)


    def process_result_value(self, value, dialect):
        """将数据库值转换为 Python 值"""
        if value is None:
            return []
        try:
            result = json.loads(value)
            return result if isinstance(result, list) else []
        except (json.JSONDecodeError, TypeError):
            return []