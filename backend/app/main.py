import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.agent_framework import role_from_env
from app.api import api_router
from app.api.lifespan_router import router as lifespan_router
from app.api.ws_router import router as ws_router
from app.core.config import settings
from app.core.exceptions import register_exception_handlers
from app.core.utils import Utils
from app.middleware.request_logging import RequestLoggingMiddleware
from app.startup import healthz_lite as _healthz_lite
from app.startup.agent_framework_init import (
    install_agent_primitives,
    install_bounds_heartbeat,
)
from app.startup.bootstrap import install_background_bootstrap
from app.startup.dbos_init import (
    init_dbos,
    install_cleanup_handlers,
    install_process_role,
)
from app.startup.event_loop_probe import probe_event_loop_ready
from app.startup.lifecycle_bus import start_lifecycle_bus
from app.startup.ssrf import start_ssrf_proxy
from app.startup.teardown import shutdown_all
from app.startup.transcode_config import load_persisted_transcode_settings

# 在应用启动前设置日志
Utils.setup_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Orchestrate startup → yield → shutdown.

    Each phase delegates to a focused helper in ``app.startup.*``.
    Behaviour matches the original monolithic block one-for-one.
    """
    # ── Startup ──────────────────────────────────────────────────
    # P2 (2026-05-27): start healthz_lite FIRST so docker has an honest
    # liveness signal even if any later startup hook blocks. Bound to
    # port 8090 (override via HEALTHZ_LITE_PORT env). Runs on its own
    # daemon thread, independent of the FastAPI event loop.
    _hl_port = int(os.environ.get("HEALTHZ_LITE_PORT", "8090"))
    app.state.healthz_lite_server = _healthz_lite.start(port=_hl_port)

    await load_persisted_transcode_settings()
    install_background_bootstrap(app)

    install_process_role(app)
    install_cleanup_handlers()
    init_dbos(app)

    # PR-D8 Phase 3: WorkforceScheduler removed — inbox/outbox dispatch
    # is now @DBOS.scheduled in workflows/workforce_dispatch.py, imported via
    # `from app import workflows` in init_dbos on the worker/combined roles.
    # (The gateway is enqueue-only — init_dbos returns early before that import
    # and runs no schedulers; see reference_gateway_dbos_client.)

    await start_ssrf_proxy(app)
    await install_agent_primitives(app)
    install_bounds_heartbeat(app)
    await start_lifecycle_bus()
    await probe_event_loop_ready()

    yield logger.success(f"{settings.APP_NAME}启动成功")

    # ── Shutdown ─────────────────────────────────────────────────
    await shutdown_all(app)
    try:
        # HTTPServer.shutdown() blocks up to ~500ms waiting on serve_forever's
        # poll loop — dispatch to executor so it doesn't stall the asyncio loop.
        await asyncio.get_event_loop().run_in_executor(
            None, _healthz_lite.stop, app.state.healthz_lite_server
        )
    except Exception as _hl_exc:
        logger.warning(f"[healthz-lite] stop failed: {_hl_exc!r}")
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


# Exception handlers for Exception/HTTPException/RequestValidationError/AppError
# are registered below via ``register_exception_handlers(app)`` (see
# ``app/core/exceptions.py``). Those handlers emit a unified ErrorResponse
# envelope AND attach CORS headers on cross-origin error responses — something
# CORSMiddleware cannot do because FastAPI exception handlers bypass the
# user middleware stack.


# 添加CORS中间件
# allow_origin_regex 同时覆盖:
#   - localhost / 内网 IP 任意端口（本地开发）
#   - Vercel preview 域名：每个 PR 一个动态子域名
#     (mediahub-git-<branch>-heygos-projects.vercel.app 和
#      mediahub-<hash>-heygos-projects.vercel.app)
#     没法穷举加到 CORS_ORIGINS，所以走 regex
# allow_origins 保留生产域名列表 (prod .env: CORS_ORIGINS=["https://mediahub.heygo.cn"])
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

_CORS_ALLOW_REGEX = (
    # localhost / RFC1918 dev hosts on any port
    r"^https?://(localhost|127\.0\.0\.1|0\.0\.0\.0|10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+):\d+$"
    # Vercel preview for this project (git branch or immutable deployment URL)
    r"|^https://mediahub-(git-)?[a-z0-9-]+-heygos-projects\.vercel\.app$"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_origin_regex=_CORS_ALLOW_REGEX,
    allow_credentials=_cors_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(RequestLoggingMiddleware)

# Global exception handlers — unified ErrorResponse envelope for all errors.
register_exception_handlers(app)

# Role-aware router mount (Sprint 5 D10-A wired up here).
#
# MEDIAHUB_ROLE controls what HTTP surface this process exposes:
#   gateway/combined → full /api/v1 + /ws (HTTP API + websocket)
#   worker           → only /api/v1/healthz + /api/v1/readyz (probes)
#
# `lifespan_router` is mounted unconditionally so worker pods/processes
# can be probed by k8s / dev-backend.sh / autoheal even though they
# don't serve the public API. Module-level role read (env-only, no I/O)
# matches the lifespan-side gating that decides whether to launch DBOS
# workers, so HTTP surface and worker behavior stay in sync.
_module_role = role_from_env()
app.include_router(lifespan_router, prefix="/api/v1")
if _module_role.serves_http_api:
    app.include_router(api_router, prefix="/api/v1")
    app.include_router(ws_router)
    logger.info(f"HTTP routes mounted: full API surface (role={_module_role.value})")
else:
    logger.info(
        f"HTTP routes mounted: probes only (role={_module_role.value}) — "
        f"public API surface skipped"
    )

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
            user_id = await validate_media_cookie(token)
        if not user_id and review_token:
            pass  # TODO: validate review token
        if not user_id:
            cookie_value = request.cookies.get(COOKIE_NAME, "")
            if cookie_value:
                user_id = await validate_media_cookie(cookie_value)

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

        # Direct PG via the SQLAlchemy engine (Issue #199) — replaces the
        # supabase-py path whose maybe_single().execute() returned None (→
        # "'NoneType' object has no attribute 'data'") when the per-loop
        # client was in a bad state. The engine has no per-loop client.
        from app.db import engine as db_engine

        # Determine which column to query based on file_type. Cover /
        # thumbnail are shared assets — they live on parsed_media only.
        # Skip the resources lookup entirely for ``file_type='cover'``
        # and go straight to parsed_media. The ``file`` branch still
        # checks resources first because per-user uploads have their own
        # ``file_path`` on resources.
        media_col = "download_path" if file_type == "file" else "cover_download_path"

        # 1. resources table — only meaningful for per-user file lookups.
        if file_type == "file":
            try:
                row = await db_engine.fetch_one(
                    "SELECT id, creator_id, file_path FROM public.resources "
                    "WHERE id = :id",
                    {"id": int(media_id)},
                )
                if row and row.get("file_path"):
                    result = row["file_path"]
                    creator_id = row.get("creator_id")
                    resource_id = row["id"]
                    team_ids = await _fetch_team_ids(resource_id)
                    entry = _MediaCacheEntry(result, creator_id, team_ids, _time.time())
                    _media_path_cache[cache_key] = entry
                    return entry.file_path, entry.creator_id, entry.team_ids
            except Exception as e:
                logger.warning(f"Resource lookup failed for {media_id}: {e}")

        # 2. Try parsed_media table (no ownership info — legacy)
        try:
            # media_col is one of two hardcoded column names (not user input).
            row = await db_engine.fetch_one(
                f"SELECT {media_col} FROM public.parsed_media WHERE id = :id",
                {"id": int(media_id)},
            )
            if row and row.get(media_col):
                result = row[media_col]
                entry = _MediaCacheEntry(result, None, (), _time.time())
                _media_path_cache[cache_key] = entry
                return entry.file_path, entry.creator_id, entry.team_ids
        except Exception as e:
            logger.warning(f"ParsedMedia lookup failed for {media_id}: {e}")

        raise HTTPException(status_code=404, detail="Media not found")

    async def _fetch_team_ids(resource_id) -> tuple[str, ...]:
        """Fetch scope IDs for a resource from resource_items (engine).

        PR-E 4c: scope_id is always a teams.id snowflake post PR-C and authz is
        purely team_members membership, so we no longer filter on
        scope_type='team' — personal teams have only their owner as a member,
        so including their scope_ids can't over-authorize anyone.
        """
        from app.db import engine as db_engine

        try:
            rows = await db_engine.fetch_all(
                "SELECT scope_id FROM public.resource_items "
                "WHERE resource_id = :rid",
                {"rid": resource_id},
            )
            return tuple(str(r["scope_id"]) for r in rows if r.get("scope_id"))
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


@app.get("/api/version")
async def api_version():
    """Build identity for deploy-verify.

    CI's deploy-backend workflow polls this after the Watchtower webhook
    and asserts the response carries the just-built `commit_sha`. Without
    this check, a Watchtower webhook that 504s but is still recorded as
    "✅ triggered" silently leaves prod on the previous image — exactly
    the failure mode that wasted hours on 2026-05-12.
    """
    import json
    from pathlib import Path

    build_info_path = Path("/app/build-info.json")
    if not build_info_path.exists():
        return {"commit_sha": None, "available": False}
    try:
        info = json.loads(build_info_path.read_text(encoding="utf-8"))
        return {
            "commit_sha": info.get("commit_sha"),
            "commit_count": info.get("commit_count"),
            "service": info.get("service", "backend"),
            "version": info.get("version") or "latest",
            "available": True,
        }
    except Exception:
        return {"commit_sha": None, "available": False}


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
