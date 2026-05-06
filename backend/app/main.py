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

from app.api import api_router
from app.api.ws_router import router as ws_router
from app.core.config import settings
from app.core.exceptions import register_exception_handlers
from app.core.redis import close_async_redis
from app.core.utils import Utils
from app.middleware.request_logging import RequestLoggingMiddleware
from app.services import dbos_orchestrator
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

    # P0-1: schema sanity probe — warn (don't block) if migrations the
    # current code depends on haven't been applied. Quick + cheap query;
    # failure here just means operator missed a `psql -f migrations/N.sql`
    # step. We log loudly so the gap is visible, but startup proceeds —
    # the affected feature paths will fail individually at first call.
    try:
        from app.db import get_async_supabase_admin

        sb = await get_async_supabase_admin()
        required_tables = ["agent_commitments"]  # extend on each migration
        for table in required_tables:
            probe = await (
                sb.table(table).select("*", count="exact").limit(0).execute()
            )
            if not hasattr(probe, "data"):
                logger.warning(
                    f"Schema probe: table '{table}' is unreachable — "
                    f"check that the corresponding migration was applied"
                )
    except Exception as e:
        # Most likely cause: PostgREST returns 42P01 when the table is missing.
        # Surface the table name so operator can grep for the migration.
        logger.warning(
            f"Schema probe failed (likely missing migration): {e}"
        )

    # Load AI Library seeds (agents + skills) from backend/seeds/.
    # Wrapped defensively: a seed failure must not block server startup.
    # Breadcrumb logs below are load-bearing for post-incident diagnosis —
    # keep the entry/exit pair even if the body is refactored.
    seeds_root = Path(__file__).resolve().parent.parent / "seeds"
    logger.info(
        f"seed_loader: entering (seeds_root={seeds_root}, "
        f"exists={seeds_root.exists()})"
    )
    try:
        from app.repositories.agent_repository import AgentRepository
        from app.repositories.skill_repository import SkillRepository
        from app.services.seed_loader import SeedLoader

        seed_loader = SeedLoader(
            agent_repo=AgentRepository(),
            skill_repo=SkillRepository(),
            seeds_root=seeds_root,
        )
        seed_results = await seed_loader.load_all()
        logger.info(f"seed_loader: exiting, results={seed_results}")
    except Exception as e:
        logger.exception(f"seed_loader: exiting with exception: {e}")

    # Record deployment log — read build-info.json baked in by CI
    try:
        import json

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

    # DBOS Orchestrator (PR-D2.2): instantiate the singleton, import workflow
    # modules so their decorators register, then launch the worker pool.
    # Failure here is non-fatal — backend keeps serving requests; only DBOS-
    # routed task_types degrade. This is intentional for the Celery → DBOS
    # migration window where 'celery' mode is the safe default.
    #
    # Sprint 5 (D10-A): MEDIAHUB_ROLE controls whether this process pulls
    # workflows from the DBOS queue. Gateway-only containers init DBOS
    # (so dispatch/enqueue still works) but skip launch (no consumption).
    # Default = combined → preserves current single-process behavior.
    from app.agent_framework import role_from_env

    process_role = role_from_env()
    app.state.process_role = process_role
    logger.info(f"Process role: {process_role.value}")

    # D10-7: install atexit + SIGINT/SIGTERM handlers that kill all
    # spawned subprocess + multiprocessing children before the parent
    # exits. Defends against orphan children holding DB connections /
    # file descriptors after pytest crash / dev script Ctrl-C.
    try:
        from app.agent_framework.process_lifecycle import install_cleanup_handlers
        install_cleanup_handlers()
        logger.info("D10-7 process cleanup handlers installed")
    except Exception as plc_exc:
        logger.warning(f"D10-7 cleanup install failed: {plc_exc}")

    # PR-D5: DBOS launch moved BEFORE workforce scheduler so the scheduler
    # can pick DbosAgentWorkforcePool when WORKFORCE_USE_DBOS_QUEUE is on.
    try:
        dbos_orchestrator.init_dbos()
        if dbos_orchestrator.is_enabled():
            # `from app import workflows` (NOT `import app.workflows`) so that
            # the `app` parameter on this function is not shadowed by a local
            # module binding. `import app.X` introduces `app` as a local in
            # the enclosing function, which would break the
            # `app.state.workforce_scheduler = ...` assignment below.
            from app import workflows  # noqa: F401 — registers @DBOS decorators

            if process_role.runs_dbos_workers:
                dbos_orchestrator.launch_dbos()
                logger.info("DBOS orchestrator launched")
            else:
                logger.info(
                    f"DBOS orchestrator initialised but launch skipped "
                    f"(role={process_role.value} — gateway dispatches only)"
                )
    except Exception as e:
        logger.error(
            f"DBOS orchestrator startup failed: {e!r} — continuing without DBOS"
        )

    # ── M3 / D5: workforce scheduler — in-process asyncio tick loop ──
    # Inbox/outbox dispatch loop. Pool selection:
    #   WORKFORCE_USE_DBOS_QUEUE=true + DBOS enabled →
    #     DbosAgentWorkforcePool (cluster-wide concurrency, partitioned
    #     per-agent serialization, durable retry)
    #   otherwise → AgentWorkerPool (in-process asyncio, M3 default)
    workforce_scheduler = None
    if not process_role.runs_inprocess_schedulers:
        logger.info(
            f"Workforce scheduler skipped (role={process_role.value} — "
            f"schedulers run on worker side only)"
        )
    else:
        try:
            from app.services.workforce.scheduler import WorkforceScheduler

            use_dbos_queue = (
                os.environ.get("WORKFORCE_USE_DBOS_QUEUE", "").lower()
                in ("1", "true", "yes")
                and dbos_orchestrator.is_enabled()
            )
            pool = None
            if use_dbos_queue:
                from app.services.workforce.dbos_pool import DbosAgentWorkforcePool

                pool = DbosAgentWorkforcePool()
                logger.info("Workforce: using DbosAgentWorkforcePool (DBOS queue)")
            else:
                logger.info("Workforce: using AgentWorkerPool (in-process)")

            workforce_scheduler = WorkforceScheduler(pool=pool)
            workforce_scheduler.start()
            app.state.workforce_scheduler = workforce_scheduler
            logger.info("Workforce scheduler started")
        except Exception as e:
            logger.warning(f"Failed to start workforce scheduler: {e}")

    # Boundary layer (B9-D/E): SsrfProxy for subprocess + browser clients
    # (yt-dlp, DrissionPage, ffmpeg). Populates settings.SSRF_PROXY_URL so
    # downstream code (ytdlp_service cmd builders) reads the actual port.
    ssrf_proxy = None
    try:
        from app.boundary import SsrfProxy

        ssrf_proxy = SsrfProxy()
        await ssrf_proxy.start()
        settings.SSRF_PROXY_URL = ssrf_proxy.url
        app.state.ssrf_proxy = ssrf_proxy
        logger.info(f"Boundary SsrfProxy started at {ssrf_proxy.url}")
    except Exception as e:
        logger.warning(f"Failed to start SsrfProxy: {e}")

    # Agent framework D10 primitives: per-process LifecycleBus + LaneQueue
    # held on app.state for any code that wants to publish events / route
    # work into a lane. Wiring callsites is per-feature follow-up.
    try:
        from app.agent_framework import (
            BoundsAdvertisement,
            BoundsRegistry,
            ContextEngineRegistry,
            LaneQueue,
            LifecycleBus,
            ModelHealthRegistry,
        )

        app.state.lifecycle_bus = LifecycleBus()
        app.state.lane_queue = LaneQueue()
        # P1-5: per-process ModelHealthRegistry. Fallback chain caller
        # (ai_library_chat_wiring) reads it from app.state when building
        # the chain, so cooled-down models are skipped on retry.
        app.state.model_health = ModelHealthRegistry()
        # Wave I (I1): per-process RootAbortRegistry. Subagent dispatches
        # register children against the parent's abort controller so root
        # cancel fans out to the entire delegation tree.
        from app.agent_framework.root_abort_registry import RootAbortRegistry
        app.state.root_abort_registry = RootAbortRegistry()

        # Wave I (I3): per-process AgentMetrics counters. Admin / health
        # endpoints read .snapshot() for ops dashboards.
        from app.agent_framework.telemetry import AgentMetrics
        app.state.agent_metrics = AgentMetrics()

        # D10-14: optional Prometheus pushgateway agent. Only fires
        # when PROMETHEUS_PUSHGATEWAY_URL env is set; default off.
        try:
            from app.agent_framework.prometheus_pusher import (
                from_env as _pp_from_env,
            )
            pusher = _pp_from_env(app.state.agent_metrics)
            if pusher is not None:
                await pusher.start()
                app.state.prometheus_pusher = pusher
        except Exception as pp_exc:
            logger.warning(f"D10-14 pusher start failed: {pp_exc}")

        # Wave G (G2): per-process HookRegistry seeded with bridge-wrapped
        # legacy hooks (BudgetGuard / CostAuditor / MemoryHarvester).
        # New hooks added later just `register()` directly.
        try:
            from app.agent_framework import (
                HookRegistry,
                wrap_legacy_post,
                wrap_legacy_pre,
            )
            from app.services.hooks.cost_auditor import CostAuditorHook
            from app.services.hooks.memory_harvester import MemoryHarvesterHook

            hook_registry = HookRegistry()
            try:
                hook_registry.register(wrap_legacy_post(CostAuditorHook()))
            except Exception as cae:
                logger.warning(f"hook register CostAuditor failed: {cae}")
            try:
                hook_registry.register(wrap_legacy_post(MemoryHarvesterHook()))
            except Exception as mhe:
                logger.warning(f"hook register MemoryHarvester failed: {mhe}")
            # BudgetGuard takes constructor args (budget_cents) — caller
            # constructs a per-run instance, not a global one. Skip here.
            app.state.hook_registry = hook_registry
            logger.info(
                f"HookRegistry seeded with {len(hook_registry)} legacy hooks"
            )
        except Exception as he:
            logger.warning(f"HookRegistry seed failed: {he}")
        # Sprint 6: per-process context-engine registry. Surfaces (chat,
        # search, storyboard) self-register their engines at startup so
        # callers can fetch by surface name.
        app.state.context_engines = ContextEngineRegistry()

        # Sprint 6.5 wire-up: register the chat context engine. Wrapped
        # PromptComposer; callers fetch via require('chat'). Search /
        # Storyboard register their own engines from feature modules.
        try:
            from app.services.chat_context_engine import ChatContextEngine

            app.state.context_engines.register(ChatContextEngine())
            logger.info("ContextEngine registered: chat")
        except Exception as ce_exc:
            logger.warning(f"ChatContextEngine registration failed: {ce_exc}")

        # Sprint 5 (D10-1) + 5.5 wire-up: every process holds a
        # BoundsRegistry. Workers self-register their REAL inventory
        # (workflow names, agent slugs, providers) so dispatch_gate
        # can fail-fast for jobs no live worker can handle.
        app.state.bounds_registry = BoundsRegistry()
        app.state.bounds_self_id = None
        if process_role.runs_dbos_workers:
            try:
                import socket

                from app.agent_framework.bounds_inventory import (
                    inventory_agent_slugs,
                    inventory_providers,
                    inventory_workflow_names,
                )
                from app.repositories.agent_repository import AgentRepository

                worker_id = f"{socket.gethostname()}-pid{os.getpid()}"

                # Workflow names: introspect the workflows pkg. Re-import
                # locally so this block doesn't depend on whether the
                # earlier DBOS init's `from app import workflows` made
                # it into this scope (it doesn't, in current Python rules
                # — names imported in conditional blocks are scope-local
                # per CPython's compile-time symbol table; see issue G2-FIX).
                workflow_names: frozenset[str] = frozenset()
                try:
                    from app import workflows as _wf  # noqa: F401
                    workflow_names = inventory_workflow_names(_wf)
                except Exception as inv_exc:
                    logger.warning(
                        f"Bounds: workflow inventory failed: {inv_exc}"
                    )

                agent_slugs = await inventory_agent_slugs(AgentRepository())
                providers = inventory_providers(settings)

                self_bound = BoundsAdvertisement(
                    worker_id=worker_id,
                    role=process_role.value,
                    workflows=workflow_names,
                    agents=agent_slugs,
                    providers=providers,
                )
                app.state.bounds_registry.register(self_bound)
                app.state.bounds_self_id = worker_id
                logger.info(
                    f"Bounds: self-registered worker_id={worker_id} "
                    f"(workflows={len(workflow_names)} agents={len(agent_slugs)} "
                    f"providers={sorted(providers)})"
                )
            except Exception as e:
                logger.warning(f"Bounds self-registration failed: {e}")

        # Sprint 5.5: dispatch gate — orchestrator consults the registry
        # before enqueueing. set_bounds_registry(None) disables.
        dbos_orchestrator.set_bounds_registry(app.state.bounds_registry)

        logger.info(
            "Agent framework primitives ready "
            "(LifecycleBus + LaneQueue + BoundsRegistry + ContextEngineRegistry)"
        )
    except Exception as e:
        logger.warning(f"Agent framework primitive setup failed: {e}")

    # Sprint 5.5 + P0-3: bounds heartbeat — refresh last_seen every 30s
    # so the registry's stale-prune (90s default) doesn't garbage-collect
    # us. If pruned anyway (clock skew, registry rebuild), reconstruct the
    # bound from cached state and re-register so we don't go silent
    # forever. Only on processes that registered themselves (workers).
    bounds_heartbeat_task = None
    if getattr(app.state, "bounds_self_id", None):
        import asyncio as _asyncio

        # Cache the bound built at startup so a re-register doesn't have
        # to re-do all the inventory I/O (DB read for agent slugs etc).
        cached_bound = next(
            (
                b
                for b in app.state.bounds_registry.live_bounds()
                if b.worker_id == app.state.bounds_self_id
            ),
            None,
        )
        app.state.bounds_self_bound = cached_bound

        async def _heartbeat() -> None:
            wid = app.state.bounds_self_id
            while True:
                try:
                    await _asyncio.sleep(30.0)
                    if not app.state.bounds_registry.heartbeat(wid):
                        # Pruned between ticks — re-register from cache so
                        # gateway's view of live workers heals next tick.
                        bound = app.state.bounds_self_bound
                        if bound is not None:
                            app.state.bounds_registry.register(bound)
                            logger.warning(
                                f"Bounds heartbeat: {wid} was pruned; "
                                "re-registered from cached bound"
                            )
                        else:
                            logger.error(
                                f"Bounds heartbeat: {wid} pruned AND no "
                                "cached bound to re-register from"
                            )
                except _asyncio.CancelledError:
                    break
                except Exception as hb_exc:
                    logger.warning(f"Bounds heartbeat tick failed: {hb_exc}")

        bounds_heartbeat_task = _asyncio.create_task(
            _heartbeat(), name="bounds-heartbeat"
        )
        app.state.bounds_heartbeat_task = bounds_heartbeat_task
        logger.info("Bounds heartbeat task started (30s tick)")

    # Event-loop-ready probe (D10-6): wait until the loop has settled
    # after DBOS/seed/workforce init before we declare startup success.
    # Avoids cold-start traffic hitting a still-loaded loop.
    try:
        from app.agent_framework import wait_for_loop_ready

        ready = await wait_for_loop_ready(
            threshold_ms=200,
            consecutive_passes=2,
            max_wait_seconds=10.0,
        )
        if ready:
            logger.info("Event loop ready (drift settled)")
        else:
            logger.warning(
                "Event loop did not settle within 10s — accepting traffic anyway"
            )
    except Exception as e:
        logger.warning(f"Event-loop-ready probe failed: {e}")

    yield logger.success(f"{settings.APP_NAME}启动成功")

    # Sprint 5.5: stop heartbeat + unregister from bounds before draining.
    if bounds_heartbeat_task is not None:
        bounds_heartbeat_task.cancel()
        try:
            await bounds_heartbeat_task
        except (BaseException,):  # noqa: BLE001 — task cancellation is expected
            pass
        if getattr(app.state, "bounds_self_id", None):
            try:
                app.state.bounds_registry.unregister(app.state.bounds_self_id)
                logger.info("Bounds: unregistered self on shutdown")
            except Exception as ub_exc:
                logger.warning(f"Bounds unregister failed: {ub_exc}")

    # D10-14: stop the Prometheus pusher before draining anything else
    # so its background loop doesn't try to push half-shutdown state.
    pusher = getattr(app.state, "prometheus_pusher", None)
    if pusher is not None:
        try:
            await pusher.stop()
            logger.info("D10-14 PrometheusPusher stopped")
        except Exception as e:
            logger.warning(f"PrometheusPusher stop raised {e!r}")

    # Drain DBOS workers first so in-flight workflows checkpoint cleanly.
    try:
        dbos_orchestrator.shutdown_dbos()
    except Exception as e:
        logger.warning(f"DBOS shutdown raised {e!r}")

    # Stop workforce scheduler before other teardown — drains in-flight
    # agent runs gracefully.
    if workforce_scheduler is not None:
        try:
            await workforce_scheduler.stop(drain_timeout=5.0)
            logger.info("Workforce scheduler stopped")
        except Exception as e:
            logger.warning(f"Workforce scheduler shutdown error: {e}")

    # Stop SsrfProxy after workforce so any in-flight subprocess clients
    # (yt-dlp etc) can finish current requests through the proxy.
    if ssrf_proxy is not None:
        try:
            await ssrf_proxy.stop()
            logger.info("Boundary SsrfProxy stopped")
        except Exception as e:
            logger.warning(f"SsrfProxy shutdown error: {e}")

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
                res = (
                    await supabase.table("resources")
                    .select("id,creator_id,file_path")
                    .eq("id", media_id)
                    .maybe_single()
                    .execute()
                )
                if res.data and res.data.get("file_path"):
                    result = res.data["file_path"]
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
