# backend/app/api/api_key_router.py

"""
API 密钥管理路由

提供 API 密钥的 CRUD 操作端点。
"""

from fastapi import APIRouter, HTTPException, status
from loguru import logger

from app.core.api_key_scopes import AVAILABLE_SCOPES
from app.core.deps import AuthDep
from app.repositories.api_key_repository import ApiKeyRepository
from app.schemas.api_key import (
    ApiKeyCreate,
    ApiKeyCreateResponse,
    ApiKeyListResponse,
    ApiKeyResponse,
    ApiKeyScopeInfo,
    ApiKeyScopesResponse,
    ApiKeyUpdate,
)

router = APIRouter(prefix="/api-keys", tags=["API 密钥管理"])

# 最大密钥数量限制
MAX_KEYS_PER_USER = 10


@router.get("/scopes", response_model=ApiKeyScopesResponse)
async def get_available_scopes():
    """
    获取可用的权限范围列表

    无需认证，供前端展示权限选项
    """
    scopes = [
        ApiKeyScopeInfo(
            scope=s["scope"],
            name=s["name"],
            description=s["description"],
            category=s["category"],
        )
        for s in AVAILABLE_SCOPES
    ]
    return ApiKeyScopesResponse(scopes=scopes)


@router.post("", response_model=ApiKeyCreateResponse)
async def create_api_key(request: ApiKeyCreate, auth: AuthDep):
    """
    创建新的 API 密钥

    返回完整密钥，可随时从列表中复制。
    """
    repo = ApiKeyRepository()

    # 检查密钥数量限制
    current_count = await repo.count_user_keys(auth.user_id)
    if current_count >= MAX_KEYS_PER_USER:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"已达到密钥数量上限（{MAX_KEYS_PER_USER}个）",
        )

    try:
        result = await repo.create(
            user_id=auth.user_id,
            name=request.name,
            scopes=request.scopes,
            description=request.description,
            expires_at=request.expires_at,
            rate_limit=request.rate_limit,
        )

        return ApiKeyCreateResponse(
            id=result["id"],
            key_id=result["key_id"],
            key_prefix=result["key_prefix"],
            name=result["name"],
            description=result.get("description"),
            scopes=result["scopes"],
            status=result["status"],
            expires_at=result.get("expires_at"),
            rate_limit=result.get("rate_limit"),
            created_at=result["created_at"],
            updated_at=result["updated_at"],
            secret_key=result["secret_key"],
        )

    except Exception as e:
        logger.error(f"创建 API 密钥失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"创建密钥失败: {str(e)}",
        )


@router.get("", response_model=ApiKeyListResponse)
async def list_api_keys(auth: AuthDep, include_revoked: bool = False):
    """
    获取当前用户的所有 API 密钥

    Args:
        include_revoked: 是否包含已撤销的密钥
    """
    repo = ApiKeyRepository()
    keys = await repo.get_user_keys(auth.user_id, include_revoked)

    key_responses = [
        ApiKeyResponse(
            id=k["id"],
            key_id=k["key_id"],
            key_prefix=k["key_prefix"],
            key_value=k.get("key_value"),
            name=k["name"],
            description=k.get("description"),
            scopes=k["scopes"],
            status=k["status"],
            expires_at=k.get("expires_at"),
            last_used_at=k.get("last_used_at"),
            usage_count=k.get("usage_count", 0),
            rate_limit=k.get("rate_limit"),
            created_at=k["created_at"],
            updated_at=k["updated_at"],
        )
        for k in keys
    ]

    return ApiKeyListResponse(count=len(key_responses), keys=key_responses)


@router.get("/{key_id}", response_model=ApiKeyResponse)
async def get_api_key(key_id: str, auth: AuthDep):
    """
    获取指定 API 密钥详情
    """
    repo = ApiKeyRepository()
    key_data = await repo.get_by_key_id(key_id)

    if not key_data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="密钥不存在")

    # 验证所有权
    if key_data["user_id"] != auth.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="无权访问此密钥"
        )

    return ApiKeyResponse(
        id=key_data["id"],
        key_id=key_data["key_id"],
        key_prefix=key_data["key_prefix"],
        key_value=key_data.get("key_value"),
        name=key_data["name"],
        description=key_data.get("description"),
        scopes=key_data["scopes"],
        status=key_data["status"],
        expires_at=key_data.get("expires_at"),
        last_used_at=key_data.get("last_used_at"),
        usage_count=key_data.get("usage_count", 0),
        rate_limit=key_data.get("rate_limit"),
        created_at=key_data["created_at"],
        updated_at=key_data["updated_at"],
    )


@router.patch("/{key_id}", response_model=ApiKeyResponse)
async def update_api_key(key_id: str, request: ApiKeyUpdate, auth: AuthDep):
    """
    更新 API 密钥
    """
    repo = ApiKeyRepository()

    # 构建更新数据
    update_data = request.model_dump(exclude_unset=True)

    if not update_data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="没有提供要更新的数据"
        )

    try:
        result = await repo.update(key_id, auth.user_id, update_data)

        if not result:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="密钥不存在或无权修改"
            )

        return ApiKeyResponse(
            id=result["id"],
            key_id=result["key_id"],
            key_prefix=result["key_prefix"],
            key_value=result.get("key_value"),
            name=result["name"],
            description=result.get("description"),
            scopes=result["scopes"],
            status=result["status"],
            expires_at=result.get("expires_at"),
            last_used_at=result.get("last_used_at"),
            usage_count=result.get("usage_count", 0),
            rate_limit=result.get("rate_limit"),
            created_at=result["created_at"],
            updated_at=result["updated_at"],
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新 API 密钥失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"更新密钥失败: {str(e)}",
        )


@router.delete("/{key_id}")
async def delete_api_key(key_id: str, auth: AuthDep):
    """
    删除 API 密钥（硬删除）
    """
    repo = ApiKeyRepository()
    success = await repo.delete(key_id, auth.user_id)

    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="密钥不存在或无权删除"
        )

    return {"success": True, "message": "密钥已删除"}


@router.post("/{key_id}/revoke")
async def revoke_api_key(key_id: str, auth: AuthDep):
    """
    撤销 API 密钥（软删除）

    撤销后密钥将无法使用，但记录仍保留
    """
    repo = ApiKeyRepository()
    result = await repo.revoke(key_id, auth.user_id)

    if not result:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="密钥不存在或无权撤销"
        )

    return {"success": True, "message": "密钥已撤销"}
