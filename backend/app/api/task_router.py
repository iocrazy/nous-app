# backend/app/api/task_router.py

"""
任务管理路由

提供 Celery 任务状态查询和管理 API。
"""

from typing import Optional

from celery.result import AsyncResult
from fastapi import APIRouter, HTTPException, Query
from loguru import logger
from pydantic import BaseModel

from app.celery_app import celery_app
from app.core.deps import AuthDep

router = APIRouter(prefix="/tasks")

TAGS = ["任务管理"]


class TaskStatusResponse(BaseModel):
    """任务状态响应"""

    task_id: str
    status: str
    result: Optional[dict] = None
    error: Optional[str] = None
    progress: Optional[int] = None


class TaskSubmitResponse(BaseModel):
    """任务提交响应"""

    success: bool
    task_id: str
    message: str


@router.get("/{task_id}", tags=TAGS, response_model=TaskStatusResponse)
async def get_task_status(task_id: str, auth: AuthDep):
    """
    获取任务状态

    查询 Celery 任务的执行状态和结果。

    - **task_id**: 任务 ID

    状态说明:
    - PENDING: 等待执行
    - STARTED: 正在执行
    - SUCCESS: 执行成功
    - FAILURE: 执行失败
    - RETRY: 重试中
    - REVOKED: 已取消

    需要认证：Bearer Token 或 API Key
    """
    try:
        result = AsyncResult(task_id, app=celery_app)

        response = TaskStatusResponse(
            task_id=task_id,
            status=result.status,
        )

        if result.ready():
            if result.successful():
                response.result = result.result
            else:
                # 任务失败
                response.error = (
                    str(result.result) if result.result else "Unknown error"
                )

        # 如果任务包含进度信息
        if result.info and isinstance(result.info, dict):
            response.progress = result.info.get("progress")

        return response

    except Exception as e:
        logger.error(f"获取任务状态失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取任务状态失败: {str(e)}")


@router.get("/{task_id}/progress", tags=TAGS, summary="Get download progress by Celery task ID")
async def get_download_progress(task_id: str, auth: AuthDep):
    """Get real-time download progress from Redis by Celery task ID.

    Used by the parser flow where only the Celery task ID is available.
    For unified task ID lookups, use /task-manager/tasks/{id}/progress instead.
    """
    import json

    try:
        redis_client = celery_app.backend.client
        raw = redis_client.get(f"download_progress:{task_id}")

        if raw:
            data = json.loads(raw)
            return {
                "task_id": task_id,
                "status": data.get("status", "downloading"),
                "percent": data.get("percent", 0),
                "downloaded": data.get("downloaded", 0),
                "total": data.get("total", 0),
                "speed": data.get("speed", "0 B/s"),
                "error": data.get("error"),
            }

        # Fallback: check Celery result state
        result = AsyncResult(task_id, app=celery_app)
        if result.state == "SUCCESS":
            return {"task_id": task_id, "status": "completed", "percent": 100}
        elif result.state == "FAILURE":
            return {"task_id": task_id, "status": "failed", "error": str(result.result)}

        return {"task_id": task_id, "status": "pending", "percent": 0}

    except Exception as e:
        logger.error(f"Failed to get download progress: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get download progress: {e}")


@router.delete("/{task_id}", tags=TAGS)
async def cancel_task(task_id: str, auth: AuthDep):
    """
    取消任务

    取消正在执行或等待执行的任务。

    - **task_id**: 任务 ID

    需要认证：Bearer Token 或 API Key
    """
    try:
        celery_app.control.revoke(task_id, terminate=True)

        return {
            "success": True,
            "message": f"任务 {task_id} 已取消",
            "task_id": task_id,
        }

    except Exception as e:
        logger.error(f"取消任务失败: {e}")
        raise HTTPException(status_code=500, detail=f"取消任务失败: {str(e)}")


@router.get("", tags=TAGS)
async def list_active_tasks(
    auth: AuthDep,
    queue: Optional[str] = Query(None, description="按队列筛选"),
    limit: int = Query(100, ge=1, le=500),
):
    """
    获取活跃任务列表

    查询正在执行和等待执行的任务。

    - **queue**: 队列名称筛选（downloads, parsing, scheduled）
    - **limit**: 返回数量限制

    需要认证：Bearer Token 或 API Key
    """
    try:
        # 获取活跃任务
        inspect = celery_app.control.inspect()

        active = inspect.active() or {}
        reserved = inspect.reserved() or {}
        scheduled = inspect.scheduled() or {}

        tasks = []

        # 处理活跃任务
        for worker, worker_tasks in active.items():
            for task in worker_tasks:
                if queue and task.get("delivery_info", {}).get("routing_key") != queue:
                    continue
                tasks.append(
                    {
                        "task_id": task.get("id"),
                        "name": task.get("name"),
                        "status": "STARTED",
                        "worker": worker,
                        "args": task.get("args"),
                    }
                )

        # 处理预留任务
        for worker, worker_tasks in reserved.items():
            for task in worker_tasks:
                if queue and task.get("delivery_info", {}).get("routing_key") != queue:
                    continue
                tasks.append(
                    {
                        "task_id": task.get("id"),
                        "name": task.get("name"),
                        "status": "PENDING",
                        "worker": worker,
                        "args": task.get("args"),
                    }
                )

        # 处理计划任务
        for worker, worker_tasks in scheduled.items():
            for task in worker_tasks:
                tasks.append(
                    {
                        "task_id": task.get("request", {}).get("id"),
                        "name": task.get("request", {}).get("name"),
                        "status": "SCHEDULED",
                        "worker": worker,
                        "eta": task.get("eta"),
                    }
                )

        return {
            "success": True,
            "count": len(tasks[:limit]),
            "tasks": tasks[:limit],
        }

    except Exception as e:
        logger.error(f"获取活跃任务失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取活跃任务失败: {str(e)}")


@router.get("/stats/workers", tags=TAGS)
async def get_worker_stats(auth: AuthDep):
    """
    获取 Worker 统计

    查询所有 Celery Worker 的状态和统计信息。

    需要认证：Bearer Token 或 API Key
    """
    try:
        inspect = celery_app.control.inspect()

        # 获取 Worker 统计
        stats = inspect.stats() or {}
        ping = inspect.ping() or {}

        workers = []
        for worker_name, worker_stats in stats.items():
            workers.append(
                {
                    "name": worker_name,
                    "status": "online" if worker_name in ping else "offline",
                    "concurrency": worker_stats.get("pool", {}).get("max-concurrency"),
                    "processes": worker_stats.get("pool", {}).get("processes", []),
                    "total_tasks": worker_stats.get("total", {}),
                }
            )

        return {
            "success": True,
            "worker_count": len(workers),
            "workers": workers,
        }

    except Exception as e:
        logger.error(f"获取 Worker 统计失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取 Worker 统计失败: {str(e)}")


@router.get("/stats/queues", tags=TAGS)
async def get_queue_stats(auth: AuthDep):
    """
    获取队列统计

    查询各个任务队列的消息数量。

    需要认证：Bearer Token 或 API Key
    """
    try:
        # 直接从 Redis 获取队列长度
        import redis

        from app.core.config import settings

        r = redis.from_url(settings.CELERY_BROKER_URL)

        queues = ["celery", "downloads", "parsing", "scheduled"]
        queue_stats = {}

        for queue_name in queues:
            try:
                length = r.llen(queue_name)
                queue_stats[queue_name] = length
            except Exception:
                queue_stats[queue_name] = 0

        return {
            "success": True,
            "queues": queue_stats,
            "total": sum(queue_stats.values()),
        }

    except Exception as e:
        logger.error(f"获取队列统计失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取队列统计失败: {str(e)}")
