"""Dispatch a canvas generation to the user's OWN daemon (C4, spec §5).

Two rules shape this module:

1. **Offline is an answer, not a wait.** If the user has no daemon
   connected, the click must fail immediately with a typed error the UI can
   phrase ("your local codex isn't connected") — never a silent hang
   (CLAUDE.md「触发路径必须类型化失败回显」).
2. **The daemon reports back over the socket**, so the dispatcher parks on a
   future keyed by job_id and the WS handler resolves it. A daemon that dies
   mid-job simply never resolves — hence the hard timeout.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, Callable, Dict, Optional, Protocol

from loguru import logger

# job_id -> future awaiting the daemon's verdict
_WAITERS: Dict[str, asyncio.Future] = {}

DEFAULT_TIMEOUT_S = 600


class DaemonOfflineError(RuntimeError):
    """The user has no daemon connected right now."""


class _Registry(Protocol):
    def is_online(self, user_id: str) -> bool: ...
    async def send_job(self, user_id: str, payload: dict) -> bool: ...


def resolve_job(job_id: str, result: dict[str, Any]) -> None:
    """Called by the WS handler when the daemon reports done/failed."""
    fut = _WAITERS.pop(job_id, None)
    if fut is None or fut.done():
        return
    fut.set_result(result)


async def dispatch_to_daemon(
    *,
    user_id: str,
    scope_id: int,
    kind: str,
    payload: dict[str, Any],
    registry: Optional[_Registry] = None,
    mint_ticket: Optional[Callable[..., Any]] = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> dict[str, Any]:
    """Send one job to the user's daemon and await its result.

    Raises ``DaemonOfflineError`` when nothing is connected, ``TimeoutError``
    when the daemon never answers, ``RuntimeError`` when it answers with a
    failure (message carries the daemon's typed code).
    """
    if registry is None:
        from app.services.codex.daemon_registry import registry as default_registry

        registry = default_registry
    if mint_ticket is None:
        from app.api.codex_daemon_router import mint_upload_ticket

        mint_ticket = mint_upload_ticket

    if not registry.is_online(user_id):
        raise DaemonOfflineError(
            "your local codex daemon is not connected — run `nous-codex run`"
        )

    job_id = str(uuid.uuid4())
    ticket = mint_ticket(user_id=user_id, scope_id=scope_id, job_id=job_id)
    if asyncio.iscoroutine(ticket):
        ticket = await ticket

    loop = asyncio.get_running_loop()
    fut: asyncio.Future = loop.create_future()
    _WAITERS[job_id] = fut

    job = {
        "type": "job",
        "job_id": job_id,
        "kind": kind,
        "payload": {**payload, "upload_ticket": ticket},
    }
    try:
        delivered = await registry.send_job(user_id, job)
        if not delivered:
            raise DaemonOfflineError("daemon disconnected before the job was sent")
        result = await asyncio.wait_for(fut, timeout=timeout_s)
    except asyncio.TimeoutError as exc:
        raise TimeoutError(
            f"local codex daemon did not answer within {int(timeout_s)}s"
        ) from exc
    finally:
        _WAITERS.pop(job_id, None)

    if result.get("error"):
        logger.info("[codex-daemon] job {} failed: {}", job_id, result["error"])
        raise RuntimeError(str(result["error"]))
    return result
