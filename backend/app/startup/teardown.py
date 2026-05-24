"""Shutdown chain — drains everything in the correct order.

Order matters: cancel background tasks → stop heartbeats / pushers /
listeners → drain DBOS workers → tear down boundary clients (SsrfProxy,
DrissionPage) → close transport pools (Redis, asyncpg).
"""

from fastapi import FastAPI
from loguru import logger

from app.core.redis import close_async_redis
from app.services.media.parsers.douyin_parse.drissionpage_parser import (
    DrissionPageParser,
)
from app.startup.agent_framework_init import (
    stop_bounds_heartbeat,
    stop_prometheus_pusher,
)
from app.startup.dbos_init import shutdown_dbos
from app.startup.lifecycle_bus import stop_lifecycle_bus
from app.startup.ssrf import stop_ssrf_proxy


async def shutdown_all(app: FastAPI) -> None:
    # Cancel still-running background bootstrap tasks first so they don't
    # race the rest of the shutdown chain. swallow_timeout so a hung task
    # can't block teardown.
    bg_registry = getattr(app.state, "bg_tasks", None)
    if bg_registry is not None:
        await bg_registry.shutdown(timeout=3.0)

    await stop_bounds_heartbeat(app)
    await stop_lifecycle_bus()
    await stop_prometheus_pusher(app)

    # Drain DBOS workers first so in-flight workflows checkpoint cleanly.
    await shutdown_dbos()

    # Stop SsrfProxy after workforce so any in-flight subprocess clients
    # (yt-dlp etc) can finish current requests through the proxy.
    await stop_ssrf_proxy(app)

    try:
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

    # Dispose the SQLAlchemy async engine (Issue #199 target layer, over
    # asyncpg → Supavisor). This is the sole asyncpg connection layer now
    # that pg_pool is retired. No-op when not created / Supavisor unset.
    try:
        from app.db.engine import dispose_engine

        await dispose_engine()
        logger.info("SQLAlchemy async engine disposed (Supavisor)")
    except Exception as e:
        logger.warning(f"Failed to dispose SQLAlchemy engine: {e}")
