from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

from app.api import api_router
from app.api.frontend_config_router import load_config as load_frontend_config
from app.middleware.request_logging import RequestLoggingMiddleware
from app.api.ws_router import router as ws_router
from app.core.config import settings
from app.core.redis import close_async_redis
from app.core.utils import Utils
from app.services.douyin_analysis import DouyinAnalysis

# 在应用启动前设置日志
Utils.setup_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):

    # app.state.redis = await init_redis() # 在启动时初始化redis

    # Load persisted transcode settings from frontend_config.yml
    try:
        config = load_frontend_config()
        transcode = config.get("transcode", {})
        if transcode.get("encoder"):
            settings.FFMPEG_ENCODER = transcode["encoder"]
        if transcode.get("preset"):
            settings.FFMPEG_PRESET = transcode["preset"]
        if "parallel_tiers" in transcode:
            settings.TRANSCODE_PARALLEL_TIERS = transcode["parallel_tiers"]
        if "min_size_mb" in transcode:
            settings.TRANSCODE_MIN_SIZE_MB = transcode["min_size_mb"]
        logger.info(
            f"Transcode config loaded: encoder={settings.FFMPEG_ENCODER}, "
            f"preset={settings.FFMPEG_PRESET}, parallel={settings.TRANSCODE_PARALLEL_TIERS}"
        )
    except Exception as e:
        logger.warning(f"Failed to load transcode config from frontend_config.yml: {e}")

    yield logger.success(f"{settings.APP_NAME}启动成功")

    try:
        # 关闭 db 数据库引擎

        # # 关闭 redis 连接
        # if hasattr(app.state, "redis") and app.state.redis:
        #     logger.info("正在关闭Redis连接...")
        #     await app.state.redis.close()
        #     logger.info("Redis连接已关闭")

        # 关闭 DouyinAnalysis 浏览器资源

        logger.info("正在关闭抖音分析浏览器...")
        DouyinAnalysis().close()
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

# 添加CORS中间件
# allow_origin_regex 匹配所有 localhost 端口，无需逐个配置
# allow_origins 保留生产域名列表
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_origin_regex=r"^http://localhost:\d+$",
    allow_credentials=settings.CORS_CREDENTIALS,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(RequestLoggingMiddleware)

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
    ) -> str:
        """Authenticate a media request. Returns user_id or raises 401.

        Authentication order:
        1. token query param (signed media token, preferred)
        2. share_token query param (future: validate sharing link)
        3. review_token query param (future: validate review access)
        4. media_session httpOnly cookie (legacy fallback)
        """
        from app.api.media_auth import COOKIE_NAME, validate_media_cookie

        user_id = None

        if token:
            user_id = validate_media_cookie(token)
        if not user_id and share_token:
            pass  # TODO: validate share token
        if not user_id and review_token:
            pass  # TODO: validate review token
        if not user_id:
            cookie_value = request.cookies.get(COOKIE_NAME, "")
            if cookie_value:
                user_id = validate_media_cookie(cookie_value)

        if not user_id:
            raise HTTPException(status_code=401, detail="Authentication required")
        return user_id

    async def _resolve_file_path(media_id: str, file_type: str = "file") -> str:
        """Resolve a resource/media ID to a file path on disk.

        Lookup order:
        1. resources table (by id) → file_path / cover_image_path
        2. parsed_media table (by id) → download_path / cover_download_path

        Returns the relative file path or raises 404.
        """
        from app.db.supabase_client import get_async_supabase_admin

        supabase = await get_async_supabase_admin()

        # Determine which column to query based on file_type
        resource_col = "file_path" if file_type == "file" else "cover_image_path,thumbnail_path"
        media_col = "download_path" if file_type == "file" else "cover_download_path"

        # 1. Try resources table
        try:
            res = await supabase.table("resources").select(resource_col).eq("id", media_id).maybe_single().execute()
            if res.data:
                if file_type == "file" and res.data.get("file_path"):
                    return res.data["file_path"]
                elif file_type == "cover":
                    path = res.data.get("cover_image_path") or res.data.get("thumbnail_path")
                    if path:
                        return path
        except Exception:
            pass  # ID format mismatch or DB error, try parsed_media

        # 2. Try parsed_media table
        try:
            res = await supabase.table("parsed_media").select(media_col).eq("id", media_id).maybe_single().execute()
            if res.data and res.data.get(media_col):
                return res.data[media_col]
        except Exception:
            pass

        raise HTTPException(status_code=404, detail="Media not found")

    def _serve_file(file_path: str) -> FileResponse:
        """Resolve a relative file path and return a FileResponse."""
        import mimetypes

        full_path = (_media_base_path / file_path).resolve()
        if not str(full_path).startswith(str(_media_base_path)):
            raise HTTPException(status_code=403, detail="Access denied")
        if not full_path.exists() or not full_path.is_file():
            raise HTTPException(status_code=404, detail="File not found")

        mime_type = mimetypes.guess_type(str(full_path))[0] or "application/octet-stream"
        return FileResponse(
            str(full_path),
            media_type=mime_type,
            headers={
                "Referrer-Policy": "no-referrer",
                "Content-Disposition": "inline",
            },
        )

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
        """
        await _authenticate_media_request(request, token, share_token, review_token)
        file_path = await _resolve_file_path(media_id, "file")
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
        """
        await _authenticate_media_request(request, token, share_token, review_token)
        file_path = await _resolve_file_path(media_id, "cover")
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
