# app/celery_app.py

"""
Celery 应用初始化模块

用于创建和配置 Celery 实例，支持异步任务处理。
"""

import pkgutil

from celery import Celery
from celery.signals import beat_init, worker_process_init

import app.tasks as _tasks_pkg
from app.core.config import settings


@worker_process_init.connect
def _init_worker_logging(**kwargs):
    """Initialize loguru for Celery worker processes."""
    from app.core.utils import Utils

    Utils.setup_logging("celery")


@beat_init.connect
def _init_beat_logging(**kwargs):
    """Initialize loguru for Celery Beat scheduler process."""
    from app.core.utils import Utils

    Utils.setup_logging("celery-beat")


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
    # Ack after the task finishes, not when the worker picks it up. Without
    # this, a worker restart mid-task (deploys, OOM) silently drops the
    # message — the reaper then marks the unified_task "Stale task timeout"
    # minutes later. Our tasks are idempotent (they re-check DB state before
    # writing), so re-delivery on crash is safe.
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    # Worker 配置
    worker_prefetch_multiplier=1,  # 公平调度，每次只取一个任务
    worker_concurrency=settings.CELERY_WORKER_CONCURRENCY,
    worker_hijack_root_logger=False,  # Don't override loguru setup
    worker_redirect_stdouts=False,  # Don't redirect stderr (prevents duplicate logs)
    # 结果配置
    result_expires=3600,  # 结果保留 1 小时
    # AI task queue routing
    task_routes={
        "app.tasks.ai_tasks.extract_audio_task": {"queue": "transcription"},
        "app.tasks.ai_tasks.transcribe_audio_task": {"queue": "transcription"},
        "app.tasks.ai_tasks.generate_summary_task": {"queue": "analysis"},
    },
    # 定时任务调度（Celery Beat）— PR-D7: ALL ENTRIES REMOVED.
    #
    # Each scheduled job has a DBOS @scheduled equivalent under
    # `backend/app/workflows/scheduled_*.py` (PR-D3c2) plus
    # `agent_runs_sweeper.py` (PR-D3c). Running both side-by-side
    # would double-execute write tasks (e.g. grant_daily_free_points
    # would credit users twice per day).
    #
    # The legacy task FUNCTIONS in `app/tasks/scheduled_tasks.py` and
    # `app/tasks/agent_runs_sweeper.py` are kept for now — other code
    # paths invoke them programmatically. The schedule entry was the
    # "what fires automatically" config; removing it switches the cron
    # source-of-truth to DBOS without breaking direct callers.
    #
    # If celery-beat is still running in your env, this means: it has
    # nothing to fire. The deployment can drop the celery-beat process
    # entirely (see docker-compose teardown notes in D7 README).
    beat_schedule={},
    # M2/M3 note: workforce inbox/outbox dispatch moved out of Celery
    # beat in M3 — see app/services/workforce/scheduler.py. Lives in
    # the FastAPI lifespan as an in-process asyncio loop with the
    # AgentWorkerPool (or DbosAgentWorkforcePool when D5 flag is on).
)

# Register signal handlers (decorators auto-connect on import)
import app.tasks.signals  # noqa: F401, E402
