# app/core/deps.py

"""
依赖注入模块

提供 Supabase 客户端依赖注入和认证功能。
支持同步和异步客户端。
支持两种认证方式：
1. Bearer Token (JWT) - 通过 Authorization header
2. API Key - 通过 X-API-Key header

JWT 验签策略：本地 JWKS 验签（PyJWT + PyJWKClient），不再每次请求打 GoTrue 网络。
JWKS 端点 = f"{SUPABASE_URL}/auth/v1/.well-known/jwks.json"，PyJWKClient 自带 lru cache。
仅支持 ES256（asymmetric）。老 HS256 session 切到 ES256 后会被拒，
用户需重新登录拿到新签名 token —— 这是 2026-05-07 ES256 切换决定接受的代价。
"""

import asyncio
from dataclasses import dataclass
from typing import Annotated, List, Optional

import jwt
from fastapi import Depends, Header, HTTPException, Request, status
from loguru import logger
from supabase._async.client import AsyncClient

from app.core.api_key_scopes import check_scope_permission, get_required_scopes
from app.core.config import settings
from app.db.supabase_client import get_async_supabase as _get_async_supabase
from app.db.supabase_client import get_async_supabase_admin as _get_async_supabase_admin

# Module-level JWKS client singleton (lazy-initialized).
# PyJWKClient 自带 lru_cache (default lifespan ~5min)，足够覆盖 hot path；
# 测试可通过把 _jwks_client 重置为 None 强制重新初始化。
_jwks_client: Optional[jwt.PyJWKClient] = None


def _get_jwks_client() -> jwt.PyJWKClient:
    """Lazily build the module-level PyJWKClient against current SUPABASE_URL."""
    global _jwks_client
    if _jwks_client is None:
        url = f"{settings.SUPABASE_URL.rstrip('/')}" f"/auth/v1/.well-known/jwks.json"
        _jwks_client = jwt.PyJWKClient(url, cache_keys=True, lifespan=300)
    return _jwks_client


async def verify_jwt(token: str) -> dict:
    """Verify a JWT against the JWKS, return its claims.

    Runs the (sync) PyJWKClient lookup + jwt.decode in a worker thread so the
    async handler isn't blocked while the very first request fetches JWKS.
    Raises jwt.InvalidTokenError (or subclasses) on any verification failure.
    """
    client = _get_jwks_client()

    def _decode_sync() -> dict:
        signing_key = client.get_signing_key_from_jwt(token)
        return jwt.decode(
            token,
            signing_key.key,
            algorithms=["ES256"],
            audience="authenticated",
        )

    return await asyncio.to_thread(_decode_sync)


async def get_async_supabase() -> AsyncClient:
    """获取异步 Supabase 客户端"""
    return await _get_async_supabase()


async def get_async_supabase_admin() -> AsyncClient:
    """获取异步 Supabase Admin 客户端"""
    return await _get_async_supabase_admin()


# 依赖注入类型
AsyncSupabaseDep = Annotated[AsyncClient, Depends(get_async_supabase)]
AsyncSupabaseAdminDep = Annotated[AsyncClient, Depends(get_async_supabase_admin)]


@dataclass
class AuthContext:
    """认证上下文"""

    user_id: str
    auth_type: str  # "jwt" or "api_key"
    scopes: Optional[List[str]] = None  # API Key 的权限范围
    api_key_id: Optional[str] = None  # API Key 的标识符


async def get_current_user(authorization: str = Header(...)):
    """
    从 Authorization header 获取当前用户

    用于需要认证的端点。返回 dict 形态：{id, email, role, aud}。
    callers 已经容错访问（getattr or .get），切换不破坏现有路由。
    """
    try:
        token = authorization.replace("Bearer ", "")
        claims = await verify_jwt(token)

        if not claims.get("sub"):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="无效的认证令牌"
            )

        return {
            "id": str(claims["sub"]),
            "email": claims.get("email"),
            "role": claims.get("role"),
            "aud": claims.get("aud"),
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=f"认证失败: {str(e)}"
        )


async def get_auth(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
) -> AuthContext:
    """
    获取认证上下文

    支持两种认证方式：
    1. Bearer Token: Authorization: Bearer <jwt>
    2. API Key: X-API-Key: dk_<secret>

    优先使用 API Key（如果提供）

    Args:
        request: FastAPI 请求对象
        authorization: Authorization header
        x_api_key: X-API-Key header

    Returns:
        AuthContext: 认证上下文

    Raises:
        HTTPException: 认证失败或权限不足
    """
    # 优先检查 API Key
    if x_api_key:
        return await _validate_api_key(request, x_api_key)

    # 检查 Bearer Token
    if authorization:
        return await _validate_bearer_token(authorization)

    # 都没有提供
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="未提供认证凭据",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_optional_auth(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
) -> Optional[AuthContext]:
    """
    获取可选的认证上下文

    与 get_auth 类似，但不强制要求认证

    Returns:
        AuthContext 或 None
    """
    try:
        if x_api_key:
            return await _validate_api_key(request, x_api_key)
        if authorization:
            return await _validate_bearer_token(authorization)
        return None
    except HTTPException:
        return None


async def _validate_api_key(request: Request, api_key: str) -> AuthContext:
    """
    验证 API Key

    Args:
        request: FastAPI 请求对象
        api_key: API Key 值

    Returns:
        AuthContext

    Raises:
        HTTPException: 验证失败或权限不足
    """
    # 延迟导入避免循环依赖
    from app.repositories.api_key_repository import get_api_key_repository

    repo = get_api_key_repository()
    key_data = await repo.validate_key(api_key)

    if not key_data:
        logger.warning(f"无效的 API Key: {api_key[:10]}...")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="无效的 API Key"
        )

    # 检查权限范围
    user_scopes = key_data.get("scopes", [])
    method = request.method
    path = request.url.path

    # 移除 /api/v1 前缀（如果有）
    if path.startswith("/api/v1"):
        path = path[7:]

    required_scopes = get_required_scopes(method, path)

    if not required_scopes:
        # Endpoint not in scope map — deny by default (security: least privilege)
        logger.warning(f"API Key denied: endpoint {method} {path} has no scope mapping")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This endpoint is not available via API Key",
        )

    if not check_scope_permission(required_scopes, user_scopes):
        logger.warning(f"API Key 权限不足: 需要 {required_scopes}, 拥有 {user_scopes}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"权限不足，该操作需要以下权限之一: {', '.join(required_scopes)}",
        )

    # 更新使用统计（异步，不阻塞请求）
    try:
        await repo.update_usage(key_data["key_id"])
    except Exception as e:
        logger.warning(f"更新 API Key 使用统计失败: {e}")

    return AuthContext(
        user_id=key_data["user_id"],
        auth_type="api_key",
        scopes=user_scopes,
        api_key_id=key_data["key_id"],
    )


async def _validate_bearer_token(authorization: str) -> AuthContext:
    """
    验证 Bearer Token (JWT)

    Args:
        authorization: Authorization header 值

    Returns:
        AuthContext

    Raises:
        HTTPException: 验证失败
    """
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效的认证格式，需要 Bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = authorization[7:]  # 移除 "Bearer " 前缀

    try:
        claims = await verify_jwt(token)

        if not claims.get("sub"):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="无效的认证令牌"
            )

        return AuthContext(
            user_id=str(claims["sub"]),
            auth_type="jwt",
            scopes=None,  # JWT 用户拥有完整权限
        )

    except HTTPException:
        raise
    except Exception as e:
        # Malformed / expired client tokens are user-input errors, not
        # server bugs — they're handled by the 401 response. Logging
        # them at ERROR pollutes the dashboard and triggers ops alerts
        # for what is just "user logged out an hour ago and their cached
        # tab tried again". WARNING is the right level: visible in logs
        # for diagnosis, doesn't trip alerting thresholds.
        logger.warning(f"JWT 验证失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=f"认证失败: {str(e)}"
        )


# 当前用户依赖（保持向后兼容）
CurrentUserDep = Annotated[dict, Depends(get_current_user)]

# 新的认证依赖
AuthDep = Annotated[AuthContext, Depends(get_auth)]
OptionalAuthDep = Annotated[Optional[AuthContext], Depends(get_optional_auth)]


# ============================================
# Team membership helpers
# ============================================


async def get_team_id_for_user(user_id: str) -> Optional[str]:
    """Return the personal team_id for a user, falling back to any team."""
    admin = await _get_async_supabase_admin()

    # Prefer personal team (downloads always go to personal team)
    personal = (
        await admin.table("teams")
        .select("id")
        .eq("owner_id", user_id)
        .eq("kind", "personal")
        .limit(1)
        .execute()
    )
    if personal.data:
        return str(personal.data[0]["id"])

    # Fallback: any team membership
    result = (
        await admin.table("team_members")
        .select("team_id")
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )
    return str(result.data[0]["team_id"]) if result.data else None


async def require_team_id(user_id: str) -> str:
    """Return team_id or raise 400 if user belongs to no team."""
    team_id = await get_team_id_for_user(user_id)
    if not team_id:
        raise HTTPException(status_code=400, detail="User has no associated team")
    return team_id
