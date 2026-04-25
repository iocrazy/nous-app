"""Celery beat tasks for the M2 Persistent Workforce.

Two ticks fire on the beat schedule:

    process_inbox_tick     — drain unread agent_inbox into agent_tasks
    dispatch_outbox_tick   — push undelivered agent_outbox to channels

Each tick is wrapped in a top-level Postgres advisory lock so multiple
beat workers (e.g. when scaling Celery horizontally) don't double-fire.
The migration-149 wrappers (``public.try_advisory_lock`` /
``public.advisory_unlock``) are reused — PostgREST can't call the raw
pg_catalog functions, but these wrappers expose them safely.

Per-agent correctness is enforced one level down by the state machine's
per-agent lock, so this top-level lock only prevents redundant work,
not data races. (Two ticks racing would be wasteful but not unsafe.)

If a tick crashes, Postgres releases the advisory lock at session end —
no manual cleanup needed. The next beat tick takes the lock fresh.
"""

from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger

from app.celery_app import celery_app
from app.db.supabase_client import get_async_supabase_admin
from app.services.workforce.inbox_processor import InboxProcessor
from app.services.workforce.outbox_dispatcher import OutboxDispatcher

# Distinct lock keys per task. Picked once and pinned — changing them
# mid-flight gracefully drops the old holder and re-takes under the new
# key, no migration needed. Different from the sweeper key (93_827_412_001)
# so all three locks can coexist.
INBOX_LOCK_KEY = 93_827_412_002
OUTBOX_LOCK_KEY = 93_827_412_003


async def _try_lock(client: Any, key: int) -> bool:
    try:
        result = await client.rpc("try_advisory_lock", {"lock_key": key}).execute()
        return bool(result.data) if result.data is not None else False
    except Exception as err:
        logger.warning(f"[workforce] try_advisory_lock(key={key}) failed: {err}")
        return False


async def _release_lock(client: Any, key: int) -> None:
    try:
        await client.rpc("advisory_unlock", {"lock_key": key}).execute()
    except Exception as err:
        logger.warning(f"[workforce] advisory_unlock(key={key}) failed: {err}")


# ─── inbox processor tick ────────────────────────────────────────────


async def _process_inbox_async() -> dict[str, int]:
    client = await get_async_supabase_admin()
    if not await _try_lock(client, INBOX_LOCK_KEY):
        logger.debug("[workforce.inbox] lock held — skipping tick")
        return {"skipped": 1, "agents_processed": 0, "tasks_created": 0, "errors": 0}

    try:
        processor = InboxProcessor()
        stats = await processor.tick()
        if stats.get("tasks_created") or stats.get("errors"):
            logger.info(
                f"[workforce.inbox] processed={stats['agents_processed']} "
                f"tasks_created={stats['tasks_created']} errors={stats['errors']}"
            )
        return {"skipped": 0, **stats}
    finally:
        await _release_lock(client, INBOX_LOCK_KEY)


@celery_app.task(name="app.tasks.agent_workforce_tasks.process_inbox_tick")
def process_inbox_tick() -> dict[str, int]:
    """Beat-scheduled tick: drain agent_inbox into agent_tasks."""
    return asyncio.run(_process_inbox_async())


# ─── outbox dispatcher tick ──────────────────────────────────────────


async def _dispatch_outbox_async() -> dict[str, int]:
    client = await get_async_supabase_admin()
    if not await _try_lock(client, OUTBOX_LOCK_KEY):
        logger.debug("[workforce.outbox] lock held — skipping tick")
        return {"skipped": 1, "delivered_user": 0, "delivered_agent": 0, "errors": 0}

    try:
        dispatcher = OutboxDispatcher()
        stats = await dispatcher.tick()
        delivered = stats["delivered_user"] + stats["delivered_agent"]
        if delivered or stats.get("errors"):
            logger.info(
                f"[workforce.outbox] delivered_user={stats['delivered_user']} "
                f"delivered_agent={stats['delivered_agent']} errors={stats['errors']}"
            )
        return {"skipped": 0, **stats}
    finally:
        await _release_lock(client, OUTBOX_LOCK_KEY)


@celery_app.task(name="app.tasks.agent_workforce_tasks.dispatch_outbox_tick")
def dispatch_outbox_tick() -> dict[str, int]:
    """Beat-scheduled tick: drain undelivered agent_outbox rows."""
    return asyncio.run(_dispatch_outbox_async())
