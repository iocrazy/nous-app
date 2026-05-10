"""D10-14: background pusher for AgentMetrics → Prometheus pushgateway.

K4 added a pull endpoint (`/api/v1/admin/agent-metrics/prometheus`).
That works for typical Prometheus deploy (scrape job pulls). But some
deployments — short-lived workers, sidecars without a network path
back, batch jobs — need PUSH instead.

This module gives an opt-in asyncio task that periodically POSTs the
exporter output to a configured pushgateway URL. Default off.

Config via env:
  PROMETHEUS_PUSHGATEWAY_URL — full URL like http://localhost:9091
  PROMETHEUS_PUSH_INTERVAL_SECONDS — default 30
  PROMETHEUS_PUSH_JOB — pushgateway "job" label, default "mediahub-harness"
  PROMETHEUS_PUSH_INSTANCE — instance label, default hostname-pid
"""

from __future__ import annotations

import asyncio
import os
import socket
from typing import Any, Optional

from loguru import logger

DEFAULT_INTERVAL_SECONDS = 30
DEFAULT_JOB = "mediahub-harness"


class PrometheusPusher:
    """Background pusher. Lifecycle: instantiate at app startup, call
    start(); call stop() on shutdown.

    Idempotent: start() called twice keeps the existing task.
    """

    def __init__(
        self,
        *,
        gateway_url: str,
        metrics: Any,  # AgentMetrics instance
        interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
        job: str = DEFAULT_JOB,
        instance: Optional[str] = None,
    ) -> None:
        self.gateway_url = gateway_url.rstrip("/")
        self.metrics = metrics
        self.interval_seconds = max(5.0, float(interval_seconds))
        self.job = job
        self.instance = instance or _default_instance_id()
        self._task: Optional[asyncio.Task] = None

    def push_url(self) -> str:
        """The /metrics/job/<job>/instance/<instance> push endpoint."""
        return f"{self.gateway_url}/metrics/job/{self.job}/instance/{self.instance}"

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._loop(), name="prometheus-pusher")
        logger.info(
            f"D10-14 PrometheusPusher started → {self.push_url()} "
            f"(every {self.interval_seconds}s)"
        )

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except (asyncio.CancelledError, BaseException):  # noqa: BLE001
            pass
        self._task = None

    async def _loop(self) -> None:
        """Push every interval_seconds. Reconnect with backoff on
        push failure."""
        backoff = 1.0
        while True:
            try:
                await asyncio.sleep(self.interval_seconds)
                ok = await self._push_once()
                backoff = 1.0 if ok else min(backoff * 2.0, 60.0)
                if not ok:
                    await asyncio.sleep(backoff)
            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.debug(f"[prometheus_pusher] tick error: {exc}")
                await asyncio.sleep(min(backoff, 60.0))
                backoff = min(backoff * 2.0, 60.0)

    async def _push_once(self) -> bool:
        """One push attempt. Returns True on success, False on transport
        failure (caller backs off)."""
        try:
            import httpx

            from app.agent_framework.prometheus_exporter import render_prometheus
        except Exception:
            return False

        body = render_prometheus(self.metrics)
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(
                    self.push_url(),
                    content=body,
                    headers={
                        "Content-Type": "text/plain; version=0.0.4; charset=utf-8"
                    },
                )
                # Pushgateway returns 200/202 on success
                if 200 <= resp.status_code < 300:
                    return True
                logger.debug(f"[prometheus_pusher] push HTTP {resp.status_code}")
                return False
        except Exception as exc:
            logger.debug(f"[prometheus_pusher] push transport failed: {exc}")
            return False


def _default_instance_id() -> str:
    return f"{socket.gethostname()}-pid{os.getpid()}"


def from_env(metrics: Any) -> Optional[PrometheusPusher]:
    """Construct a pusher from PROMETHEUS_PUSHGATEWAY_URL env, or None
    if unset. Convenience for main.py wire-up."""
    url = os.environ.get("PROMETHEUS_PUSHGATEWAY_URL", "").strip()
    if not url:
        return None
    interval = float(
        os.environ.get("PROMETHEUS_PUSH_INTERVAL_SECONDS") or DEFAULT_INTERVAL_SECONDS
    )
    job = os.environ.get("PROMETHEUS_PUSH_JOB") or DEFAULT_JOB
    instance = os.environ.get("PROMETHEUS_PUSH_INSTANCE") or None
    return PrometheusPusher(
        gateway_url=url,
        metrics=metrics,
        interval_seconds=interval,
        job=job,
        instance=instance,
    )


__all__ = [
    "DEFAULT_INTERVAL_SECONDS",
    "DEFAULT_JOB",
    "PrometheusPusher",
    "from_env",
]
