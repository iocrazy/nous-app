from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.api import api_router
from app.api.frontend_config_router import load_config as load_frontend_config
from app.api.ws_router import router as ws_router
from app.core.config import settings
from app.core.exceptions import register_exception_handlers
from app.core.redis import close_async_redis
from app.core.utils import Utils
from app.middleware.request_logging import RequestLoggingMiddleware
from app.services.douyin_parse.drissionpage_parser import DrissionPageParser

# 在应用启动前设置日志
Utils.setup_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):

    # Load persisted transcode settings from database (system_settings)
    try:
        from app.db import get_async_supabase_admin

        supabase = await get_async_supabase_admin()
        result = await (
            supabase.table("system_settings")
            .select("key, value")
            .like("key", "transcode_%")
            .execute()
        )
        db_map = {row["key"]: row["value"] for row in (result.data or [])}
        if "transcode_enabled" in db_map:
            settings.TRANSCODE_ENABLED = db_map["transcode_enabled"]
        if "transcode_tiers" in db_map:
            settings.TRANSCODE_TIERS = db_map["transcode_tiers"]
        if "transcode_encoder" in db_map:
            settings.FFMPEG_ENCODER = db_map["transcode_encoder"]
        if "transcode_preset" in db_map:
            settings.FFMPEG_PRESET = db_map["transcode_preset"]
        if "transcode_parallel_tiers" in db_map:
            settings.TRANSCODE_PARALLEL_TIERS = db_map["transcode_parallel_tiers"]
        if "transcode_min_size_mb" in db_map:
            settings.TRANSCODE_MIN_SIZE_MB = db_map["transcode_min_size_mb"]
        logger.info(
            f"Transcode config loaded from DB: enabled={settings.TRANSCODE_ENABLED}, "
            f"tiers={settings.TRANSCODE_TIERS}, encoder={settings.FFMPEG_ENCODER}, "
            f"min_size_mb={settings.TRANSCODE_MIN_SIZE_MB}"
        )
    except Exception as e:
        logger.warning(f"Failed to load transcode config from database: {e}")

    # Record deployment log — read build-info.json baked in by CI
    try:
        import json
        from pathlib import Path

        build_info_path = Path("/app/build-info.json")
        if build_info_path.exists():
            info = json.loads(build_info_path.read_text(encoding="utf-8"))
            sha = info.get("commit_sha")
            if sha:
                from app.db import get_async_supabase_admin

                sb = await get_async_supabase_admin()
                exists = await (
                    sb.table("deployment_logs")
                    .select("id")
                    .eq("service", "backend")
                    .eq("commit_sha", sha)
                    .limit(1)
                    .execute()
                )
                if exists.data:
                    logger.info(f"Deployment {sha} already logged, skip")
                else:
                    row = {
                        "service": info.get("service", "backend"),
                        "version": info.get("version") or "latest",
                        "commit_sha": sha,
                        "commit_count": int(info.get("commit_count") or 0),
                        "commits": info.get("commits") or [],
                        "summary": info.get("summary") or "",
                        "deployed_by": info.get("deployed_by") or "ci",
                        "status": "success",
                        "metadata": {"run_id": info.get("run_id")},
                    }
                    await sb.table("deployment_logs").insert(row).execute()
                    logger.success(
                        f"Deployment logged: {sha} ({row['commit_count']} commits)"
                    )
        else:
            logger.debug("build-info.json not found, skip deployment log")
    except Exception as e:
        logger.warning(f"Failed to record deployment log: {e}")

    yield logger.success(f"{settings.APP_NAME}启动成功")

    try:
        # 关闭 DrissionPageParser 浏览器资源

        logger.info("正在关闭抖音分析浏览器...")
        DrissionPageParser().close()
        logger.info("抖音分析浏览器已关闭")
    except Exception as e:
        logger.error(f"关闭抖音解析下载服务时出错: {str(e)}")

    try:
        await close_async_redis()
        logger.info("Async Redis connection closed")
    except Exception as e:
        logger.warning(f"Failed to close async Redis: {e}")

    logger.info(f"{settings.APP_NAME}关闭成功")


# API 分组标签元数据
tags_metadata = [
    {
        "name": "抖音采集",
        "description": "从抖音获取并解析视频信息，自动下载媒体文件。",
    },
    {
        "name": "视频管理",
        "description": "管理已采集的视频数据：查询、搜索、删除。",
    },
    {
        "name": "统计分析",
        "description": "查看视频统计数据和分析报告。",
    },
    {
        "name": "下载管理",
        "description": "管理视频下载任务：查看待下载、重试下载。",
    },
    {
        "name": "API 密钥管理",
        "description": "创建和管理 API 访问密钥。",
    },
    {
        "name": "认证",
        "description": "用户注册、登录、登出等认证操作。",
    },
    {
        "name": "Tags",
        "description": "Manage tags for organizing and categorizing videos.",
    },
    {
        "name": "Analysis",
        "description": "AI-powered video analysis: visual recognition, content understanding, and semantic search.",
    },
    {
        "name": "Search",
        "description": "Semantic search using natural language queries and vector similarity.",
    },
    {
        "name": "Collections",
        "description": "Smart collections with rule-based video grouping.",
    },
    {
        "name": "Cleanup",
        "description": "Storage cleanup suggestions based on viewing patterns and duplicates.",
    },
    {
        "name": "AI",
        "description": "AI provider settings, connection testing, and model management.",
    },
]

limiter = Limiter(key_func=get_remote_address, default_limits=["60/minute"])

app = FastAPI(
    lifespan=lifespan,
    title=settings.APP_NAME,
    description="""
## 抖音视频分析系统 API

### 认证方式

支持两种认证方式：

1. **API Key**: 在 Header 中传入 `X-API-Key: dk_xxx`
2. **JWT Token**: 在 Header 中传入 `Authorization: Bearer <token>`

### 权限说明

API Key 可以设置权限范围（Scopes），控制可访问的接口。
JWT Token 登录用户拥有全部权限。
    """,
    version="2.1.0",
    openapi_tags=tags_metadata,
)


def custom_openapi():
    """自定义 OpenAPI schema，添加认证支持"""
    if app.openapi_schema:
        return app.openapi_schema

    openapi_schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )

    # 添加安全方案
    openapi_schema["components"]["securitySchemes"] = {
        "APIKeyHeader": {
            "type": "apiKey",
            "in": "header",
            "name": "X-API-Key",
            "description": "API 密钥认证。在前端「API 密钥」页面创建密钥后使用。",
        },
        "BearerAuth": {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
            "description": "JWT Token 认证。通过 /auth/signin 登录获取。",
        },
    }

    app.openapi_schema = openapi_schema
    return app.openapi_schema


app.openapi = custom_openapi

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Global fallback for unhandled exceptions."""
    logger.error(
        f"Unhandled exception on {request.method} {request.url.path}: {exc}",
        exc_info=True,
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Ensure consistent error response format."""
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
    )


# 添加CORS中间件
# allow_origin_regex 匹配所有 localhost 端口，无需逐个配置
# allow_origins 保留生产域名列表
# Security: wildcard origins + credentials is forbidden by browsers and dangerous.
# Auto-disable credentials and warn if misconfigured, rather than silently shipping.
_cors_origins = settings.CORS_ORIGINS
_cors_credentials = settings.CORS_CREDENTIALS
if _cors_credentials and ("*" in _cors_origins or _cors_origins == ["*"]):
    logger.warning(
        "[CORS] allow_origins=['*'] is incompatible with credentials=True; "
        "disabling credentials. Set CORS_ORIGINS explicitly in production."
    )
    _cors_credentials = False

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1|0\.0\.0\.0|10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+):\d+$",
    allow_credentials=_cors_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(RequestLoggingMiddleware)

# Global exception handlers — unified ErrorResponse envelope for all errors.
register_exception_handlers(app)

app.include_router(api_router, prefix="/api/v1")
app.include_router(ws_router)

# 媒体文件服务 - 用于访问下载的视频和封面
# 使用普通路由而非 StaticFiles 子应用，确保 CORS 中间件覆盖
try:
    _media_base_path = Path(Utils.get_download_base_path()).resolve()
    _media_base_path.mkdir(parents=True, exist_ok=True)
    logger.info(f"媒体文件路由已注册: /media -> {_media_base_path}")

    async def _authenticate_media_request(
        request: Request,
        token: str | None = None,
        share_token: str | None = None,
        review_token: str | None = None,
    ) -> str | None:
        """Authenticate a media request. Returns user_id, None (share_token present), or raises 401.

        Authentication order:
        1. token query param (signed media token, preferred)
        2. share_token query param (defers auth to permission check)
        3. review_token query param (future: validate review access)
        4. media_session httpOnly cookie (legacy fallback)

        When share_token is present, returns None instead of raising 401
        so that permission check can validate the share link without login.
        """
        from app.api.media_auth import COOKIE_NAME, validate_media_cookie

        user_id = None

        if token:
            user_id = validate_media_cookie(token)
        if not user_id and review_token:
            pass  # TODO: validate review token
        if not user_id:
            cookie_value = request.cookies.get(COOKIE_NAME, "")
            if cookie_value:
                user_id = validate_media_cookie(cookie_value)

        if not user_id and not share_token:
            raise HTTPException(status_code=401, detail="Authentication required")
        return user_id

    # In-memory cache for media ID → (file_path, creator_id, team_ids) lookups
    # Avoids DB hit on every request. TTL = 5 minutes.
    import time as _time
    from typing import NamedTuple

    class _MediaCacheEntry(NamedTuple):
        file_path: str
        creator_id: str | None
        team_ids: tuple[str, ...]
        cached_at: float

    _media_path_cache: dict[tuple[str, str], _MediaCacheEntry] = {}
    _CACHE_TTL = 300  # 5 minutes

    async def _resolve_file_path(
        media_id: str, file_type: str = "file"
    ) -> tuple[str, str | None, tuple[str, ...]]:
        """Resolve a resource/media ID to a file path on disk.

        Lookup order:
        1. resources table (by id) → file_path / cover_image_path + ownership
        2. parsed_media table (by id) → download_path / cover_download_path

        Uses a 5-minute in-memory cache to avoid DB queries on every media request.
        Returns (file_path, creator_id, team_ids) or raises 404.
        """
        cache_key = (media_id, file_type)
        cached = _media_path_cache.get(cache_key)
        if cached and _time.time() - cached.cached_at < _CACHE_TTL:
            return cached.file_path, cached.creator_id, cached.team_ids
        if cached:
            del _media_path_cache[cache_key]

        from app.db.supabase_client import get_async_supabase_admin

        supabase = await get_async_supabase_admin()

        # Determine which column to query based on file_type
        resource_col = (
            "id,creator_id,file_path"
            if file_type == "file"
            else "id,creator_id,cover_image_path,thumbnail_path"
        )
        media_col = "download_path" if file_type == "file" else "cover_download_path"

        # 1. Try resources table
        try:
            res = (
                await supabase.table("resources")
                .select(resource_col)
                .eq("id", media_id)
                .maybe_single()
                .execute()
            )
            if res.data:
                result = None
                if file_type == "file" and res.data.get("file_path"):
                    result = res.data["file_path"]
                elif file_type == "cover":
                    result = res.data.get("thumbnail_path") or res.data.get(
                        "cover_image_path"
                    )
                if result:
                    creator_id = res.data.get("creator_id")
                    resource_id = res.data["id"]
                    team_ids = await _fetch_team_ids(supabase, resource_id)
                    entry = _MediaCacheEntry(result, creator_id, team_ids, _time.time())
                    _media_path_cache[cache_key] = entry
                    return entry.file_path, entry.creator_id, entry.team_ids
        except Exception as e:
            logger.warning(f"Resource lookup failed for {media_id}: {e}")

        # 2. Try parsed_media table (no ownership info — legacy)
        try:
            res = (
                await supabase.table("parsed_media")
                .select(media_col)
                .eq("id", media_id)
                .maybe_single()
                .execute()
            )
            if res.data and res.data.get(media_col):
                result = res.data[media_col]
                entry = _MediaCacheEntry(result, None, (), _time.time())
                _media_path_cache[cache_key] = entry
                return entry.file_path, entry.creator_id, entry.team_ids
        except Exception as e:
            logger.warning(f"ParsedMedia lookup failed for {media_id}: {e}")

        raise HTTPException(status_code=404, detail="Media not found")

    async def _fetch_team_ids(supabase, resource_id: str) -> tuple[str, ...]:
        """Fetch team scope IDs for a resource from resource_items."""
        try:
            items_res = (
                await supabase.table("resource_items")
                .select("scope_id")
                .eq("resource_id", resource_id)
                .eq("scope_type", "team")
                .execute()
            )
            if items_res.data:
                return tuple(
                    str(item["scope_id"])
                    for item in items_res.data
                    if item.get("scope_id")
                )
        except Exception as e:
            logger.warning(f"Team scope lookup failed for resource {resource_id}: {e}")
        return ()

    def _serve_file(file_path: str, cache_immutable: bool = False) -> FileResponse:
        """Resolve a relative file path and return a FileResponse.

        cache_immutable=True sets long-lived caching (7 days, immutable) for
        content-addressed files like thumbnails and covers that never change.
        """
        import mimetypes

        full_path = (_media_base_path / file_path).resolve()
        if not str(full_path).startswith(str(_media_base_path)):
            raise HTTPException(status_code=403, detail="Access denied")
        if not full_path.exists() or not full_path.is_file():
            raise HTTPException(status_code=404, detail="File not found")

        mime_type = (
            mimetypes.guess_type(str(full_path))[0] or "application/octet-stream"
        )
        headers = {
            "Referrer-Policy": "no-referrer",
            "Content-Disposition": "inline",
        }
        if cache_immutable:
            headers["Cache-Control"] = "public, max-age=604800, immutable"
        return FileResponse(
            str(full_path),
            media_type=mime_type,
            headers=headers,
        )

    async def _check_permissions(
        media_id: str,
        user_id: str | None,
        share_token: str | None,
        creator_id: str | None,
        team_ids: tuple[str, ...],
    ) -> None:
        """Check resource-level permissions. Raises 403 if denied.

        Uses cached ownership data from _resolve_file_path when possible,
        falls back to check_media_access for share_token and team membership.
        """
        from app.api.media_permissions import (
            _get_resource_id_for_media,
            _validate_share_token,
        )
        from app.db.supabase_client import get_async_supabase_admin as _get_admin

        # Fast path: share_token validation (never cached)
        if share_token:
            resource_id = await _get_resource_id_for_media(media_id)
            if resource_id and await _validate_share_token(share_token, resource_id):
                return
            # Invalid share token — fall through to user-based checks

        if not user_id:
            if share_token:
                raise HTTPException(
                    status_code=403, detail="Invalid or expired share link"
                )
            raise HTTPException(status_code=403, detail="Access denied")

        # Fast path: creator check using cached data
        if creator_id and str(creator_id) == str(user_id):
            return

        # No ownership info (legacy parsed_media) — allow
        if creator_id is None:
            return

        # Team membership check
        if team_ids:
            try:
                supabase = await _get_admin()
                res = (
                    await supabase.table("team_members")
                    .select("id")
                    .eq("user_id", user_id)
                    .in_("team_id", list(team_ids))
                    .limit(1)
                    .execute()
                )
                if res.data:
                    return
            except Exception as e:
                logger.error(f"Team membership check failed: {e}")

        raise HTTPException(status_code=403, detail="Access denied")

    @app.get("/media/{media_id}")
    async def serve_media_by_id(
        media_id: str,
        request: Request,
        token: str | None = None,
        share_token: str | None = None,
        review_token: str | None = None,
    ):
        """Serve media file by resource or parsed_media ID.

        URL pattern: /media/{id}?token=signed_token
        The actual file path is resolved from the database, never exposed in the URL.
        Includes resource-level permission checks.
        """
        user_id = await _authenticate_media_request(
            request, token, share_token, review_token
        )
        file_path, creator_id, team_ids = await _resolve_file_path(media_id, "file")
        await _check_permissions(media_id, user_id, share_token, creator_id, team_ids)
        return _serve_file(file_path)

    @app.get("/media/{media_id}/cover")
    async def serve_media_cover_by_id(
        media_id: str,
        request: Request,
        token: str | None = None,
        share_token: str | None = None,
        review_token: str | None = None,
    ):
        """Serve cover image by resource or parsed_media ID.

        URL pattern: /media/{id}/cover?token=signed_token
        Includes resource-level permission checks.
        """
        user_id = await _authenticate_media_request(
            request, token, share_token, review_token
        )
        file_path, creator_id, team_ids = await _resolve_file_path(media_id, "cover")
        await _check_permissions(media_id, user_id, share_token, creator_id, team_ids)
        return _serve_file(file_path, cache_immutable=True)

    @app.get("/media/{file_path:path}")
    async def serve_media_by_path(
        file_path: str,
        request: Request,
        token: str | None = None,
        share_token: str | None = None,
        review_token: str | None = None,
    ):
        """Legacy fallback: serve media files by file path.

        Handles old cached frontends that still use /media/{file_path} URLs.
        New frontends should use /media/{id} instead.
        """
        await _authenticate_media_request(request, token, share_token, review_token)
        return _serve_file(file_path)

except ValueError:
    logger.warning(
        "未配置下载路径，媒体文件路由未注册。请在设置中配置 Default Download Path。"
    )
except Exception as e:
    logger.warning(f"媒体文件路由注册失败: {e}")


@app.get("/health")
async def health_check():
    """健康检查端点"""
    return {"status": "healthy", "message": "Service is running"}


# 前端静态文件服务（Docker 部署时使用）
frontend_path = Path("/app/static")
if frontend_path.exists():
    # 挂载静态资源（JS/CSS/图片等）
    app.mount(
        "/assets",
        StaticFiles(directory=str(frontend_path / "assets")),
        name="frontend_assets",
    )
    logger.info(f"前端静态文件已挂载: /assets -> {frontend_path / 'assets'}")

    # SPA 路由：所有非 API 请求返回 index.html
    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        """SPA 路由支持：非 API 请求返回 index.html"""
        # 检查是否是静态文件
        file_path = frontend_path / full_path
        if file_path.is_file():
            return FileResponse(file_path)
        # 其他路由返回 index.html（SPA 前端路由）
        return FileResponse(frontend_path / "index.html")

else:
    # 开发模式：前端独立运行
    @app.get("/")
    async def root():
        return {"message": "MediaHub API", "docs": "/docs", "health": "/health"}


if __name__ == "__main__":
    # 使用配置文件中的主机设置，端口固定为8080（Docker标准）
    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.APP_PORT,  # 使用常量，Docker容器内部固定端口
        reload=settings.RELOAD,
    )
