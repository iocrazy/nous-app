"""Celery monitoring API for admin dashboard."""

from __future__ import annotations

from fastapi import APIRouter
from loguru import logger

router = APIRouter()


@router.get("/workers")
async def get_celery_workers():
    """Return online Celery workers with stats."""
    from app.celery_app import celery_app

    try:
        inspect = celery_app.control.inspect(timeout=2.0)
        ping_result = inspect.ping() or {}
        stats_result = inspect.stats() or {}
        active_result = inspect.active() or {}
    except Exception as e:
        logger.warning(f"Celery inspect failed: {e}")
        return {"online": 0, "total": 0, "workers": [], "error": str(e)}

    workers = []
    for name in ping_result:
        stats = stats_result.get(name, {})
        active_tasks = active_result.get(name, [])
        pool = stats.get("pool", {})
        total = stats.get("total", {})
        processed = sum(total.values()) if isinstance(total, dict) else 0
        workers.append({
            "name": name,
            "status": "online",
            "active": len(active_tasks),
            "processed": processed,
            "concurrency": pool.get("max-concurrency"),
            "uptime": stats.get("clock", None),
        })

    return {
        "online": len(workers),
        "total": len(workers),
        "workers": workers,
    }


@router.get("/queues")
async def get_celery_queues():
    """Return Celery queue lengths from Redis."""
    from app.core.redis import get_async_redis

    queue_names = [
        "analysis",
        "celery",
        "downloads",
        "parsing",
        "scheduled",
        "transcription",
    ]

    redis = await get_async_redis()
    queues = []
    for name in queue_names:
        try:
            length = await redis.llen(name)
        except Exception:
            length = 0
        queues.append({"name": name, "messages": length})

    return {"queues": queues}
