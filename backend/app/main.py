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
from app.core.exceptions import CORS_ALLOW_ORIGIN_REGEX, register_exception_handlers
from app.core.provider_errors import register_provider_error_handlers
from app.core.utils import Utils
from app.db.schema_assertions import assert_critical_schema_on_boot
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

    # Fail-fast schema gate (ORM 2.0 §3.3): verify the live DB has the
    # tables/columns the ORM-active repos map BEFORE serving traffic. Crashes
    # boot on a confirmed mismatch (code deployed ahead of migration); the
    # not-configured / connection-error paths warn + return (DB-less local/CI
    # boot never crashes). Disable in an emergency via SCHEMA_ASSERT_ON_BOOT=false.
    await assert_critical_schema_on_boot()

    install_background_bootstrap(app)

    install_process_role(app)
    install_cleanup_handlers()
    init_dbos(app)

    # PR-D8 Phase 3: the in-process WorkforceScheduler was removed (its module
    # was deleted outright in harness 2b-2 T3) — inbox/outbox dispatch
    # is now @DBOS.scheduled in workflows/workforce_dispatch.py, imported via
    # `from app import workflows` in init_dbos on the worker/combined roles.
    # (The gateway is enqueue-only — init_dbos returns early before that import
    # and runs no schedulers; see reference_gateway_dbos_client.)

    await start_ssrf_proxy(app)
    await install_agent_primitives(app)
    install_bounds_heartbeat(app)
    await start_lifecycle_bus()
    await probe_event_loop_ready()

    # Loop-freeze detection (2026-07-06 P0): bump the healthz-lite
    # heartbeat from THIS loop every 10s. Once the loop has beaten at
    # least once, a heartbeat older than HEALTHZ_LOOP_STALE_S flips
    # /healthz/lite to 503 → autoheal restarts the container. Started
    # LAST in the startup sequence so a hung startup hook never counts
    # as "proven alive" (preserves the 2026-05-27 no-restart-storm
    # guarantee — see healthz_lite module docstring).
    async def _loop_heartbeat() -> None:
        while True:
            _healthz_lite.beat()
            await asyncio.sleep(10)

    app.state.loop_heartbeat_task = asyncio.create_task(
        _loop_heartbeat(), name="healthz-loop-heartbeat"
    )

    yield logger.success(f"{settings.APP_NAME}启动成功")

    # ── Shutdown ─────────────────────────────────────────────────
    app.state.loop_heartbeat_task.cancel()
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
#   - Cloudflare Pages preview 域名：每次部署 / 每个分支一个动态子域名
#     (<hash>.nous-app.pages.dev 和 <branch>.nous-app.pages.dev)
#     没法穷举加到 CORS_ORIGINS，所以走 regex
# allow_origins 保留生产域名列表 (prod .env: CORS_ORIGINS=["https://app.nous.ink"])
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

# Defined once in app/core/exceptions.py so the middleware and the exception
# handlers can never drift apart — see the comment there.
_CORS_ALLOW_REGEX = CORS_ALLOW_ORIGIN_REGEX

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

# Provider-specific typed handlers (AllModelsFailed / LLMCallError) — classify
# via error_catalog and answer with a stable code instead of falling into the
# generic 500 internal_error above. See app/core/provider_errors.py.
register_provider_error_handlers(app)

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
    # Avoids DB hit on every request. TTL = 5 minutes. Lives in a dedicated
    # module so version-content write paths can invalidate a stale entry
    # (resources_service.overwrite_version_content / upload_new_version /
    # set_current_version) — otherwise an edit isn't visible via /media/{id}
    # until the TTL lapses.
    from app.services.media import media_path_cache

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
        cached = media_path_cache.get(media_id, file_type)
        if cached is not None:
            return cached.file_path, cached.creator_id, cached.team_ids

        # ORM session over the SQLAlchemy engine (Issue #199 → Phase A raw-SQL
        # -to-ORM migration, docs/decisions/2026-08-04-raw-sql-to-orm-full-
        # migration.md) — replaces both the supabase-py path whose
        # maybe_single().execute() returned None (→ "'NoneType' object has no
        # attribute 'data'") when the per-loop client was in a bad state, and
        # the raw db_engine.fetch_one() text() SQL this superseded. The engine
        # has no per-loop client.
        from contextlib import nullcontext

        from sqlalchemy import select

        from app.db.scope import is_enforced, system_request_scope
        from app.db.session import read_scope
        from app.models import ParsedMedia, Resources

        # Determine which column to query based on file_type. Cover /
        # thumbnail are shared assets — they live on parsed_media only.
        # Skip the resources lookup entirely for ``file_type='cover'``
        # and go straight to parsed_media. The ``file`` branch still
        # checks resources first because per-user uploads have their own
        # ``file_path`` on resources.
        media_col = "download_path" if file_type == "file" else "cover_download_path"

        # 1. resources table — only meaningful for per-user file lookups.
        # Original SQL (kept for reference — same columns, same predicate):
        #   SELECT id, creator_id, file_path FROM public.resources WHERE id = :id
        #
        # This is a deliberately cross-user lookup: ownership (creator_id) is
        # exactly what we're trying to determine here, BEFORE
        # _check_permissions can run — there is no caller identity yet to open
        # a user_session with (share-token / anonymous requests hit this too).
        # Resources carries UserScoped(creator_id). ``SCOPE_ENFORCE_RESOURCES``
        # DEFAULTS to false in code, but production sets it true via
        # ``secrets/backend.env`` (outside this repo tree — CLAUDE.md's
        # 部署陷阱: env overrides config.yml). So in production this
        # ``system_request_scope`` wrap is LOAD-BEARING, not decorative:
        # without it, the do_orm_execute choke point sees a scoped table
        # (Resources) touched with no ambient scope and fail-closed raises
        # ``UnscopedQueryError`` on the very first request — a 500, not a
        # silent no-op. Gated on ``is_enforced`` (not unconditional) purely
        # to stay byte-for-byte legacy in environments where the flag really
        # is off (e.g. this repo's own local/test default).
        if file_type == "file":
            try:
                scope_cm = (
                    system_request_scope(
                        reason="media-serve-resolve-file-path: ownership "
                        "unknown until this lookup runs; _check_permissions "
                        "governs access"
                    )
                    if is_enforced("resources")
                    else nullcontext()
                )
                async with scope_cm:
                    async with read_scope() as session:
                        row = (
                            (
                                await session.execute(
                                    select(
                                        Resources.id,
                                        Resources.creator_id,
                                        Resources.file_path,
                                    ).where(Resources.id == int(media_id))
                                )
                            )
                            .mappings()
                            .first()
                        )
                if row and row.get("file_path"):
                    result = row["file_path"]
                    creator_id = row.get("creator_id")
                    resource_id = row["id"]
                    team_ids = await _fetch_team_ids(resource_id)
                    entry = media_path_cache.put(
                        media_id, file_type, result, creator_id, team_ids
                    )
                    return entry.file_path, entry.creator_id, entry.team_ids
            except Exception as e:
                logger.warning(f"Resource lookup failed for {media_id}: {e}")

        # 2. Try parsed_media table (no ownership info — legacy)
        # Original SQL: SELECT {media_col} FROM public.parsed_media WHERE id = :id
        # parsed_media has no scope mixin — no ambient scope is required.
        try:
            col = getattr(ParsedMedia, media_col)
            async with read_scope() as session:
                row = (
                    (
                        await session.execute(
                            select(col).where(ParsedMedia.id == int(media_id))
                        )
                    )
                    .mappings()
                    .first()
                )
            if row and row.get(media_col):
                result = row[media_col]
                entry = media_path_cache.put(media_id, file_type, result, None, ())
                return entry.file_path, entry.creator_id, entry.team_ids
        except Exception as e:
            logger.warning(f"ParsedMedia lookup failed for {media_id}: {e}")

        raise HTTPException(status_code=404, detail="Media not found")

    async def _fetch_team_ids(resource_id) -> tuple[str, ...]:
        """Fetch scope IDs for a resource from resource_items (ORM session).

        PR-E 4c: scope_id is always a teams.id snowflake post PR-C and authz is
        purely team_members membership, so we no longer filter on
        scope_type='team' — personal teams have only their owner as a member,
        so including their scope_ids can't over-authorize anyone.

        Original SQL:
          SELECT scope_id FROM public.resource_items WHERE resource_id = :rid
        resource_items has no scope mixin — no ambient scope is required.
        """
        from sqlalchemy import select

        from app.db.session import read_scope
        from app.models import ResourceItems

        try:
            async with read_scope() as session:
                rows = (
                    await session.execute(
                        select(ResourceItems.scope_id).where(
                            ResourceItems.resource_id == resource_id
                        )
                    )
                ).all()
            return tuple(str(r[0]) for r in rows if r[0])
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
                from sqlalchemy import select

                from app.db.session import read_scope
                from app.models import TeamMembers

                async with read_scope() as session:
                    member = (
                        await session.execute(
                            select(TeamMembers.team_id)
                            .where(TeamMembers.user_id == user_id)
                            .where(
                                TeamMembers.team_id.in_([int(str(t)) for t in team_ids])
                            )
                            .limit(1)
                        )
                    ).first()
                if member is not None:
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

        # Storage unification: migrated/new uploads carry an sb:// object-store
        # path — _serve_file is filesystem-only and would 404 them (the exact
        # detail-page "broken original" symptom; the versions/file routes got
        # this dual-track in PR-2, this catch-all was missed). Legacy fs rows
        # keep the original FileResponse path byte-for-byte.
        from app.services.library.media_storage import resolve_media_source

        if resolve_media_source(file_path).is_object_store:
            import mimetypes

            from app.services.library.media_serving import serve_stored_file

            return await serve_stored_file(
                file_path,
                mime=mimetypes.guess_type(file_path)[0] or "application/octet-stream",
                request=request,
                extra_headers={"Referrer-Policy": "no-referrer"},
            )
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

        Storage unification: mirrors serve_media_by_id — a migrated
        cover_download_path carries an sb:// object-store path, which
        _serve_file (filesystem-only) would 404 on. Legacy fs rows keep the
        original _serve_file call byte-for-byte.
        """
        user_id = await _authenticate_media_request(
            request, token, share_token, review_token
        )
        file_path, creator_id, team_ids = await _resolve_file_path(media_id, "cover")
        await _check_permissions(media_id, user_id, share_token, creator_id, team_ids)

        from app.services.library.media_storage import resolve_media_source

        if resolve_media_source(file_path).is_object_store:
            from app.services.library.media_serving import serve_stored_file

            return await serve_stored_file(
                file_path,
                mime="image/jpeg",
                request=request,
                extra_headers={"Cache-Control": "public, max-age=31536000, immutable"},
            )
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
        return {"message": "Nous API", "docs": "/docs", "health": "/health"}


if __name__ == "__main__":
    # 使用配置文件中的主机设置，端口固定为8080（Docker标准）
    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.APP_PORT,  # 使用常量，Docker容器内部固定端口
        reload=settings.RELOAD,
    )
