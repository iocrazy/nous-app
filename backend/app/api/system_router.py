# backend/app/api/system_router.py

"""
系统状态监控 API

提供队列状态、磁盘空间、网络状态等系统信息。
"""

import os
import shutil
import time
from typing import Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from loguru import logger

from app.core.config import settings
from app.core.deps import AuthDep
from app.api.frontend_config_router import load_config

router = APIRouter(prefix="/system")

TAGS = ["系统监控"]


class QueueStatus(BaseModel):
    """队列状态"""
    active: int = 0
    pending: int = 0
    scheduled: int = 0
    status: str = "offline"  # online, offline, degraded


class StorageStatus(BaseModel):
    """存储状态"""
    total_bytes: int = 0
    used_bytes: int = 0
    free_bytes: int = 0
    percent_used: float = 0
    status: str = "unknown"  # ok, warning, critical, error
    path: str = ""


class NetworkStatus(BaseModel):
    """网络状态"""
    speed: str = "0 B/s"
    status: str = "idle"  # idle, active, error


class SystemStatusResponse(BaseModel):
    """系统状态响应"""
    queue: QueueStatus
    storage: StorageStatus
    network: NetworkStatus
    timestamp: float


# 用于跟踪网络速度的全局变量
_network_tracker = {
    "last_bytes": 0,
    "last_time": 0,
    "current_speed": 0,
}


def update_network_speed(bytes_transferred: int):
    """更新网络速度（由下载任务调用）"""
    global _network_tracker
    current_time = time.time()

    if _network_tracker["last_time"] > 0:
        time_diff = current_time - _network_tracker["last_time"]
        if time_diff > 0:
            bytes_diff = bytes_transferred - _network_tracker["last_bytes"]
            _network_tracker["current_speed"] = bytes_diff / time_diff

    _network_tracker["last_bytes"] = bytes_transferred
    _network_tracker["last_time"] = current_time


def format_speed(bytes_per_sec: float) -> str:
    """格式化速度"""
    if bytes_per_sec < 1024:
        return f"{bytes_per_sec:.0f} B/s"
    elif bytes_per_sec < 1024 * 1024:
        return f"{bytes_per_sec / 1024:.1f} KB/s"
    else:
        return f"{bytes_per_sec / (1024 * 1024):.1f} MB/s"


@router.get("/status", tags=TAGS, response_model=SystemStatusResponse)
async def get_system_status(auth: AuthDep):
    """
    获取系统状态

    返回队列、存储、网络的实时状态。

    需要认证：Bearer Token 或 API Key
    """
    try:
        # 1. 获取队列状态
        queue_status = await _get_queue_status()

        # 2. 获取存储状态
        storage_status = _get_storage_status()

        # 3. 获取网络状态
        network_status = await _get_network_status()

        return SystemStatusResponse(
            queue=queue_status,
            storage=storage_status,
            network=network_status,
            timestamp=time.time(),
        )

    except Exception as e:
        logger.error(f"获取系统状态失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取系统状态失败: {str(e)}")


# Cache for queue status to avoid frequent Celery inspect calls
_queue_cache = {
    "data": None,
    "timestamp": 0,
    "ttl": 5,  # Cache for 5 seconds
}


async def _get_queue_status() -> QueueStatus:
    """获取 Celery 队列状态（带缓存）"""
    import time
    current_time = time.time()

    # Return cached data if still valid
    if _queue_cache["data"] and (current_time - _queue_cache["timestamp"]) < _queue_cache["ttl"]:
        return _queue_cache["data"]

    try:
        from app.celery_app import celery_app

        # Use shorter timeout to avoid blocking
        inspect = celery_app.control.inspect(timeout=0.5)

        # Only ping to check if online (faster than getting all tasks)
        ping = inspect.ping()

        if not ping:
            result = QueueStatus(status="offline")
            _queue_cache["data"] = result
            _queue_cache["timestamp"] = current_time
            return result

        # Get active tasks only if online
        active = inspect.active() or {}
        reserved = inspect.reserved() or {}

        active_count = sum(len(tasks) for tasks in active.values())
        pending_count = sum(len(tasks) for tasks in reserved.values())

        result = QueueStatus(
            active=active_count,
            pending=pending_count,
            scheduled=0,  # Skip scheduled to reduce calls
            status="online",
        )

        _queue_cache["data"] = result
        _queue_cache["timestamp"] = current_time
        return result

    except Exception as e:
        logger.warning(f"获取队列状态失败: {e}")
        result = QueueStatus(status="offline")
        _queue_cache["data"] = result
        _queue_cache["timestamp"] = current_time
        return result


def _get_storage_status() -> StorageStatus:
    """获取存储状态"""
    try:
        # Use same config source as /api/v1/config endpoint for consistency
        config = load_config()
        storage_path = config.get("default_download_path") or settings.DOWNLOAD_PATH or "/app/downloads"

        if not os.path.exists(storage_path):
            return StorageStatus(
                status="error",
                path=storage_path,
            )

        # 获取磁盘使用情况
        usage = shutil.disk_usage(storage_path)
        percent_used = (usage.used / usage.total) * 100 if usage.total > 0 else 0

        # 判断状态
        if percent_used >= 95:
            status = "critical"
        elif percent_used >= 85:
            status = "warning"
        else:
            status = "ok"

        return StorageStatus(
            total_bytes=usage.total,
            used_bytes=usage.used,
            free_bytes=usage.free,
            percent_used=round(percent_used, 1),
            status=status,
            path=storage_path,
        )

    except Exception as e:
        logger.warning(f"获取存储状态失败: {e}")
        return StorageStatus(status="error")


async def _get_network_status() -> NetworkStatus:
    """获取网络/下载状态"""
    try:
        from app.celery_app import celery_app
        import json

        redis_client = celery_app.backend.client

        # 查找所有正在进行的下载进度
        progress_keys = redis_client.keys("download_progress:*")

        total_speed = 0
        active_downloads = 0

        for key in progress_keys:
            try:
                data = redis_client.get(key)
                if data:
                    progress = json.loads(data)
                    if progress.get("status") == "downloading":
                        active_downloads += 1
                        # 解析速度字符串
                        speed_str = progress.get("speed", "0 B/s")
                        total_speed += _parse_speed(speed_str)
            except Exception:
                continue

        if active_downloads > 0:
            return NetworkStatus(
                speed=format_speed(total_speed),
                status="active",
            )
        else:
            return NetworkStatus(
                speed="0 B/s",
                status="idle",
            )

    except Exception as e:
        logger.warning(f"获取网络状态失败: {e}")
        return NetworkStatus(status="error")


def _parse_speed(speed_str: str) -> float:
    """解析速度字符串为字节/秒"""
    try:
        parts = speed_str.split()
        if len(parts) != 2:
            return 0

        value = float(parts[0])
        unit = parts[1].lower()

        if "kb" in unit:
            return value * 1024
        elif "mb" in unit:
            return value * 1024 * 1024
        elif "gb" in unit:
            return value * 1024 * 1024 * 1024
        else:
            return value

    except Exception:
        return 0
