
"""
用户相关的 Pydantic 模型

定义用户、角色和权限相关的数据验证模式，用于 API 请求和响应的数据验证。
"""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field, EmailStr, field_validator

from app.core.enums import RoleType

def validate_password_strength(password: str) -> str:
    """通用的密码强度验证函数"""
    if len(password) < 8:
        raise ValueError('密码长度必须至少为8个字符')
    if not any(char.isdigit() for char in password):
        raise ValueError('密码必须包含至少一个数字')
    if not any(char.isupper() for char in password):
        raise ValueError('密码必须包含至少一个大写字母')
    return password




# 权限相关模型
class PermissionBase(BaseModel):
    """权限基础模型"""
    name: str = Field(...,  min_length=1, max_length=255, description="权限名称" )
    description: Optional[str] = Field(None, description="权限描述")





class PermissionCreate(PermissionBase):
    """创建权限的请求模型"""
    pass


class PermissionUpdate(BaseModel):
    """更新权限的请求模型"""
    name: Optional[str] = Field(None, min_length=1, max_length=255, description="权限名称")
    description: Optional[str] = Field(None, description="权限描述")


class PermissionResponse(PermissionBase):
    """权限响应模型"""
    id: int = Field(..., description="权限ID")

    model_config = {
        "from_attributes": True
    }



# 角色相关模型
class RoleBase(BaseModel):
    """角色基础模型"""
    name: str = Field(..., min_length=1, max_length=255, description="角色名称")
    description: Optional[str] = Field(None, description="角色描述")


class RoleCreate(RoleBase):
    """创建角色的请求模型"""
    permission_ids: Optional[list[int]] = Field(None, description="权限ID列表")


class RoleUpdate(BaseModel):
    """更新角色的请求模型"""
    name: Optional[str] = Field(None, min_length=1, max_length=255, description="角色名称")
    description: Optional[str] = Field(None, description="角色描述")
    permission_ids: Optional[list[int]] = Field(None, description="权限ID列表")


class RoleResponse(RoleBase):
    """角色响应模型"""
    id: int = Field(..., description="角色ID")
    permissions: list[PermissionResponse] = Field(default_factory=list, description="权限列表")
    created_at: datetime = Field(..., description="创建时间")
    updated_at: datetime = Field(..., description="更新时间")


    model_config = {
        "from_attributes": True
    }



# 用户相关模型
class UserBase(BaseModel):
    """用户基础模型"""
    username: str = Field(..., min_length=3, max_length=255, description="用户名")


class UserCreate(UserBase):
    """创建用户的请求模型"""
    password: str = Field(..., min_length=8, description="密码")

    @field_validator('password')
    def password_strength(cls, v):
        """验证密码强度"""
        return validate_password_strength(v)


class UserUpdate(BaseModel):
    """更新用户的请求模型"""
    username: Optional[str] = Field(None, min_length=3, max_length=255, description="用户名")
    current_password: str = Field(..., description="当前密码")
    new_password: str = Field(..., min_length=8, description="新密码")



    # field_validator 装饰的方法实际上是一个类方法（classmethod）
    @field_validator('new_password')
    def password_strength(cls, v):
        """验证密码强度"""
        return validate_password_strength(v)





class UserResponse(UserBase):
    """用户响应模型"""
    id: int = Field(..., description="用户ID")
    role: RoleResponse = Field(..., description="用户角色")
    created_at: datetime = Field(..., description="创建时间")
    updated_at: datetime = Field(..., description="更新时间")

    # 允许 Pydantic 从普通对象属性读取数据，支持 ORM 对象转换
    model_config = {
        "from_attributes": True
    }


class UserWithoutRoleResponse(UserBase):
    """不包含角色信息的用户响应模型"""
    id: int = Field(..., description="用户ID")
    created_at: datetime = Field(..., description="创建时间")
    updated_at: datetime = Field(..., description="更新时间")

    model_config = {
        "from_attributes": True
    }



# 认证相关模型
class TokenResponse(BaseModel):
    """令牌响应模型"""
    access_token: str = Field(..., description="访问令牌")
    token_type: str = Field("bearer", description="令牌类型")


class TokenPayload(BaseModel):
    """令牌载荷模型"""
    sub: str = Field(..., description="主题（通常是用户ID）")
    exp: datetime = Field(..., description="过期时间")
    role: str = Field(..., description="用户角色")


# 用户角色分配模型
class UserRoleAssign(BaseModel):
    """用户角色分配模型"""
    username: str = Field(..., description="用户名")
    role_names: list[str] = Field(..., description="角色名称")


# 用户角色移除模型
class UserRoleRemove(BaseModel):
    """用户角色移除模型"""
    username: str = Field(..., description="用户名")
    role_names: list[str] = Field(..., description="角色名称")

# 角色权限分配模型
class RolePermissionAssign(BaseModel):
    """角色权限分配模型"""
    role_name: str = Field(..., description="角色名称")
    permission_ids: list[int] = Field(..., description="权限ID列表")


# 角色权限移除模型
class RolePermissionRemove(BaseModel):
    """角色权限移除模型"""
    role_name: str = Field(..., description="角色名称")
    permission_ids: list[int] = Field(..., description="要移除的权限ID列表")


# 添加用户权限响应模型
class UserPermissionsResponse(BaseModel):
    """用户权限响应模型"""
    username: str = Field(..., description="用户名")
    permissions: list[PermissionResponse] = Field(default_factory=list, description="用户拥有的权限列表")
    
    model_config = {
        "from_attributes": True
    }

class RolePermissionsResponse(BaseModel):
    """角色权限响应模型"""
    role_name: RoleType = Field(..., description="角色名称")
    permissions: list[PermissionResponse] = Field(default_factory=list, description="角色拥有的权限列表")

    model_config = {
        "from_attributes": True
    }

class UserRolesResponse(BaseModel):
    """用户角色响应模型"""
    username: str = Field(..., description="用户名")
    role_names: list[str] = Field(default_factory=list, description="用户拥有的角色列表")

    model_config = {
        "from_attributes": True
    }




