# app/celery_app.py

"""
Celery 应用初始化模块

用于创建和配置 Celery 实例，支持异步任务处理。
"""

import pkgutil

from celery import Celery
from celery.schedules import crontab

from app.core.config import settings
import app.tasks as _tasks_pkg

# 自动扫描 app/tasks/ 下所有模块，新增 task 文件无需手动注册
_task_modules = [
    f"app.tasks.{name}" for _, name, _ in pkgutil.iter_modules(_tasks_pkg.__path__)
]

# 创建 Celery 应用实例
celery_app = Celery(
    "mediahub",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=_task_modules,
)

# Celery 配置
celery_app.conf.update(
    # 序列化配置
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    # 时区配置
    timezone="Asia/Shanghai",
    enable_utc=True,
    # 任务配置
    task_track_started=True,
    task_time_limit=settings.CELERY_TASK_TIME_LIMIT,
    task_soft_time_limit=settings.CELERY_TASK_TIME_LIMIT - 30,
    # Worker 配置
    worker_prefetch_multiplier=1,  # 公平调度，每次只取一个任务
    worker_concurrency=settings.CELERY_WORKER_CONCURRENCY,
    # 结果配置
    result_expires=3600,  # 结果保留 1 小时
    # AI task queue routing
    task_routes={
        "app.tasks.ai_tasks.extract_audio_task": {"queue": "transcription"},
        "app.tasks.ai_tasks.transcribe_audio_task": {"queue": "transcription"},
        "app.tasks.ai_tasks.generate_summary_task": {"queue": "analysis"},
    },
    # 定时任务调度（Celery Beat）
    beat_schedule={
        "cleanup-temp-files-daily": {
            "task": "app.tasks.scheduled_tasks.cleanup_temp_files",
            "schedule": 86400.0,  # 每天执行一次
        },
        "retry-failed-downloads-hourly": {
            "task": "app.tasks.scheduled_tasks.retry_failed_downloads",
            "schedule": 3600.0,  # 每小时执行一次
        },
        "update-statistics-6h": {
            "task": "app.tasks.scheduled_tasks.update_statistics",
            "schedule": 21600.0,  # 每 6 小时执行一次
        },
        "update-system-status-30s": {
            "task": "app.tasks.scheduled_tasks.update_system_status",
            "schedule": 30.0,  # 每 30 秒执行一次
        },
        "reset-monthly-quotas": {
            "task": "app.tasks.scheduled_tasks.reset_monthly_quotas",
            "schedule": crontab(minute=0, hour=0, day_of_month=1),
        },
        "cleanup-trashed-resources-daily": {
            "task": "app.tasks.scheduled_tasks.cleanup_trashed_resources",
            "schedule": 86400.0,  # 每天执行一次
        },
        "cleanup-old-unified-tasks-daily": {
            "task": "app.tasks.scheduled_tasks.cleanup_old_unified_tasks",
            "schedule": 86400.0,  # 每天执行一次
        },
        "recover-stale-orchestrator-locks-hourly": {
            "task": "app.tasks.scheduled_tasks.recover_stale_orchestrator_locks",
            "schedule": 3600.0,  # Every hour
        },
    },
)

# Register signal handlers (decorators auto-connect on import)
import app.tasks.signals  # noqa: F401, E402
