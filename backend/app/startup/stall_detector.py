"""Gateway-side worker-stall detector → Discord alert.

Why this is NOT a @DBOS.scheduled workflow
------------------------------------------
The failure it watches for is a DBOS dequeue stall (worker on a stale
``application_version`` can't pull the gateway's enqueued work, or a
global-concurrency lock backs off — see bug_worker_down_after_deploy).
A @DBOS.scheduled tick would be enqueued into the very queue that's
stalled, so the detector would stall with it. Instead this is a plain
asyncio loop that runs in the GATEWAY process (``serves_http_api``):
the gateway stays healthy during a worker stall, so it can observe the
backlog and alert. Pure asyncio means it keeps ticking regardless of
DBOS queue health.

Signal: ``queued >= QUEUE_MIN`` AND ``running == 0`` sustained for
``STALL_TICKS`` consecutive checks → one Discord alert (debounced until
recovery). Tunables via env; Discord is a no-op when DISCORD_WEBHOOK_URL
is unset, so this is safe to ship before the webhook is configured.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass

import httpx
from loguru import logger


@dataclass(frozen=True)
class StallState:
    """Immutable detector state, threaded through each tick."""

    streak: int = 0
    alerting: bool = False


def stall_step(
    queued: int,
    running: int,
    state: StallState,
    *,
    queue_min: int,
    stall_ticks: int,
) -> tuple[StallState, str | None]:
    """Pure transition: given current counts + prior state, return the next
    state and an action (``"alert"`` | ``"recover"`` | ``None``).

    Debounced: ``"alert"`` fires once when the streak first crosses the
    threshold; ``"recover"`` fires once when a stall clears while alerting.
    """
    stalled = queued >= queue_min and running == 0
    if stalled:
        streak = state.streak + 1
        if streak >= stall_ticks and not state.alerting:
            return StallState(streak, True), "alert"
        return StallState(streak, state.alerting), None
    if state.alerting:
        return StallState(0, False), "recover"
    return StallState(0, False), None


# ── DB query (sync psycopg off the event loop, mirrors the reaper) ──────


def _query_counts() -> tuple[int, int]:
    """Return ``(queued, running)`` from task_tracking. Sync psycopg so the
    caller offloads via ``asyncio.to_thread``. Returns ``(-1, -1)`` if the
    DSN is unset or the query errors (treated as 'unknown', never a stall)."""
    dsn = os.environ.get("DBOS_DATABASE_URL")
    if not dsn:
        return (-1, -1)
    import psycopg

    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT "
                "  count(*) FILTER (WHERE phase IN ('queued','pending')), "
                "  count(*) FILTER (WHERE phase IN ('in_progress','processing')) "
                "FROM public.task_tracking"
            )
            row = cur.fetchone()
            return (int(row[0]), int(row[1])) if row else (0, 0)


async def _send_discord(content: str) -> None:
    """POST to DISCORD_WEBHOOK_URL if set; otherwise log only."""
    url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not url:
        logger.warning(f"[stall-detector] (no DISCORD_WEBHOOK_URL) {content}")
        return
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(url, json={"content": content})
    except Exception as exc:  # noqa: BLE001 — alerting must never crash the loop
        logger.error(f"[stall-detector] Discord post failed: {exc!r}")


async def _bg_stall_detector() -> None:
    """Poll for the worker-stall signature and alert on transitions."""
    interval = int(os.environ.get("STALL_CHECK_INTERVAL_SECONDS", "120"))
    queue_min = int(os.environ.get("STALL_QUEUE_MIN", "5"))
    stall_ticks = int(os.environ.get("STALL_TICKS", "3"))
    state = StallState()
    logger.info(
        f"[stall-detector] armed (interval={interval}s queue_min={queue_min} "
        f"ticks={stall_ticks})"
    )
    while True:
        try:
            await asyncio.sleep(interval)
            queued, running = await asyncio.to_thread(_query_counts)
            if queued < 0:  # unknown (no DSN / query error) — skip this tick
                continue
            state, action = stall_step(
                queued, running, state, queue_min=queue_min, stall_ticks=stall_ticks
            )
            if action == "alert":
                await _send_discord(
                    f"⚠️ **Worker stall**: {queued} tasks queued, 0 running for "
                    f"~{interval * stall_ticks // 60}min. Likely worker version "
                    f"drift or queue lock — check `mediahub-app-worker` vs "
                    f"`mediahub-app-backend` image. Manual: `cd "
                    f"/volume1/docker/mediahub/docker && sudo "
                    f"/usr/local/bin/docker-compose up -d --force-recreate "
                    f"mediahub-worker`"
                )
            elif action == "recover":
                await _send_discord(
                    f"✅ **Worker recovered**: queue draining again "
                    f"(queued={queued}, running={running})."
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — a bad tick must not kill the loop
            logger.error(f"[stall-detector] tick failed: {exc!r}")
