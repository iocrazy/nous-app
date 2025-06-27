from sqlalchemy import String, Text, Integer, Enum, ForeignKey, Column, Table
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import DBModel
from app.core.enums import RoleType


# 定义角色类型枚举


# 使用正确的 Column 定义关联表
associate_roles_permissions = Table(
    'associate_roles_permissions',
    DBModel.metadata,
    Column('role_name', Enum(
        RoleType,
        values_callable=lambda enum_cls: [e.value for e in enum_cls],
        native_enum=False
    ), ForeignKey('roles.name'), primary_key=True),
    Column('permission_id', Integer, ForeignKey('permissions.id'), primary_key=True)
)

associate_users_roles = Table(
    'associate_users_roles',
    DBModel.metadata,
    Column('user_id', Integer, ForeignKey('users.id'), primary_key=True),
    Column('role_name', Enum(
        RoleType,
        values_callable=lambda enum_cls: [e.value for e in enum_cls],
        native_enum=False
    ), ForeignKey('roles.name'), primary_key=True)
)

class User(DBModel):
    """用户数据模型"""

    __tablename__ = 'users'

    # 用户唯一标识
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True, autoincrement=True)

    # 用户信息
    username: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    password: Mapped[str] = mapped_column(String(255), nullable=False)

    # role_name: Mapped[RoleType] = mapped_column(Enum(RoleType), ForeignKey('roles.name'), nullable=False)

    # # 使用role.name作为外键
    # role_name: Mapped[RoleType] = mapped_column(
    #     Enum(
    #         RoleType,
    #         values_callable=lambda enum_cls: [e.value for e in enum_cls],
    #         native_enum=False,
    #     ),
    #     ForeignKey('roles.name'),
    #     nullable=False
    # )
    roles: Mapped[list['Role']] = relationship(secondary= associate_users_roles,back_populates='users')

    # 添加与令牌的关系
    tokens: Mapped[list["UserToken"]] = relationship("UserToken", back_populates="user", cascade="all, delete-orphan")

    def __str__(self):
        """返回用户的字符串表示"""
        return f"User(id={self.id}, username={self.username})"


    def __repr__(self):
        """返回用户的字符串表示"""
        return f"User(id={self.id}, username={self.username}, role_name={self.role_name})"



class Role(DBModel):
    """角色数据模型"""
    __tablename__ = 'roles'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True, autoincrement=True)
    # name: Mapped[RoleType] = mapped_column(Enum(RoleType), nullable=False, unique=True, index=True)

    # values_callable 告诉 SQLAlchemy 使用枚举的值列表作为数据库中允许的枚举值。
    name: Mapped[RoleType] = mapped_column(
        Enum(
            RoleType,
            values_callable=lambda enum_cls: [e.value for e in enum_cls],
            native_enum=False,  # 根据需要决定是否使用数据库原生枚举类型
        ),
        nullable=False,
        unique=True,
        index=True
    )

    description: Mapped[str] = mapped_column(Text, nullable=True)

    # 其他字段和关系
    users: Mapped[list["User"]] = relationship(secondary= associate_users_roles,back_populates='roles')
    permissions: Mapped[list["Permission"]] = relationship(
        secondary=associate_roles_permissions,
        back_populates='roles'
    )

    def __str__(self):
        """返回角色的字符串表示"""
        return f"Role(id={self.id}, name={self.name}, description={self.description} )"

    def __repr__(self):
        """返回角色的字符串表示"""
        return f"Role(id={self.id}, name={self.name}, description={self.description} )"

class Permission(DBModel):
    """权限数据模型"""
    __tablename__ = 'permissions'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    description: Mapped[str] = mapped_column(Text, nullable=True)

    # 其他字段和关系
    roles: Mapped[list["Role"]] = relationship(
        secondary=associate_roles_permissions,
        back_populates='permissions'
    )

    def __str__(self):
        """返回权限的字符串表示"""
        return f"Permission(id={self.id}, name={self.name}, description={self.description})"

    def __repr__(self):
        """返回权限的字符串表示"""
        return f"Permission(id={self.id}, name={self.name}, description={self.description})"

class UserToken(DBModel):
    """用户令牌模型，用于跟踪用户的活跃令牌"""
    __tablename__ = 'user_tokens'

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey('users.id'), nullable=False)
    token: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)




    # 关系
    user: Mapped["User"] = relationship("User", back_populates="tokens")
