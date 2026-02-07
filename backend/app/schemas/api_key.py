# backend/app/schemas/api_key.py

"""
API 密钥数据验证模式

定义 API 密钥相关的请求和响应模型。
"""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field, model_validator

from app.core.api_key_scopes import get_valid_scopes


class ApiKeyBase(BaseModel):
    """API 密钥基础模型"""

    name: str = Field(..., min_length=1, max_length=255, description="密钥名称")
    description: Optional[str] = Field(None, max_length=1000, description="密钥描述")
    scopes: List[str] = Field(..., min_length=1, description="权限范围列表")
    expires_at: Optional[datetime] = Field(
        None, description="过期时间，为空表示永不过期"
    )
    rate_limit: Optional[int] = Field(
        None, ge=1, le=10000, description="每分钟请求限制"
    )


class ApiKeyCreate(ApiKeyBase):
    """创建 API 密钥请求"""

    @model_validator(mode="after")
    def validate_scopes(self):
        """验证权限范围是否有效"""
        valid_scopes = get_valid_scopes()
        invalid = [s for s in self.scopes if s not in valid_scopes]
        if invalid:
            raise ValueError(f"无效的权限范围: {', '.join(invalid)}")
        return self

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "name": "我的 API 密钥",
                    "description": "用于自动化脚本",
                    "scopes": ["douyin:fetch", "douyin:videos:read"],
                    "expires_at": "2026-12-31T23:59:59Z",
                }
            ]
        }
    }


class ApiKeyUpdate(BaseModel):
    """更新 API 密钥请求"""

    name: Optional[str] = Field(
        None, min_length=1, max_length=255, description="密钥名称"
    )
    description: Optional[str] = Field(None, max_length=1000, description="密钥描述")
    scopes: Optional[List[str]] = Field(None, description="权限范围列表")
    status: Optional[str] = Field(
        None, pattern="^(active|revoked)$", description="密钥状态"
    )

    @model_validator(mode="after")
    def validate_scopes(self):
        """验证权限范围是否有效"""
        if self.scopes:
            valid_scopes = get_valid_scopes()
            invalid = [s for s in self.scopes if s not in valid_scopes]
            if invalid:
                raise ValueError(f"无效的权限范围: {', '.join(invalid)}")
        return self


class ApiKeyResponse(BaseModel):
    """API 密钥响应（不包含敏感信息）"""

    id: int = Field(..., description="密钥 ID")
    key_id: str = Field(..., description="密钥公开标识符")
    key_prefix: str = Field(..., description="密钥前缀（如 dk_xxxx...）")
    name: str = Field(..., description="密钥名称")
    description: Optional[str] = Field(None, description="密钥描述")
    scopes: List[str] = Field(..., description="权限范围")
    status: str = Field(..., description="密钥状态")
    expires_at: Optional[datetime] = Field(None, description="过期时间")
    last_used_at: Optional[datetime] = Field(None, description="最后使用时间")
    usage_count: int = Field(0, description="使用次数")
    rate_limit: Optional[int] = Field(None, description="速率限制")
    created_at: datetime = Field(..., description="创建时间")
    updated_at: datetime = Field(..., description="更新时间")

    model_config = {"from_attributes": True}


class ApiKeyCreateResponse(BaseModel):
    """创建 API 密钥响应（仅首次返回完整密钥）"""

    success: bool = True
    message: str = "API 密钥创建成功，请妥善保存密钥！"
    id: int
    key_id: str
    key_prefix: str
    name: str
    description: Optional[str] = None
    scopes: List[str]
    status: str
    expires_at: Optional[datetime] = None
    rate_limit: Optional[int] = None
    created_at: datetime
    updated_at: datetime

    # 完整密钥（仅首次返回）
    secret_key: str = Field(..., description="完整密钥（仅显示一次，请妥善保存）")


class ApiKeyListResponse(BaseModel):
    """API 密钥列表响应"""

    success: bool = True
    count: int = Field(..., description="密钥数量")
    keys: List[ApiKeyResponse] = Field(..., description="密钥列表")


class ApiKeyScopeInfo(BaseModel):
    """权限范围信息"""

    scope: str = Field(..., description="权限范围标识")
    name: str = Field(..., description="权限名称")
    description: str = Field(..., description="权限描述")
    category: str = Field(..., description="权限分类")


class ApiKeyScopesResponse(BaseModel):
    """权限范围列表响应"""

    success: bool = True
    scopes: List[ApiKeyScopeInfo] = Field(..., description="可用权限范围列表")
