"""Dispatch a canvas generation to the user's OWN daemon (C4, spec §5).

Cross-container by construction: the daemon's socket lives in the *gateway*
process while generation workflows run in the *worker* container, so every
leg rides Redis —

    worker ──publish codex_jobs:<user>──► gateway (socket holder) ──WS──► daemon
    worker ◄─subscribe codex_results:<job_id>── gateway ◄──WS── daemon

Two rules shape this module:

1. **Offline is an answer, not a wait.** No live presence marker → typed
   ``DaemonOfflineError`` at click time, never a silent hang.
2. **A daemon that dies mid-job never publishes a result** — hence the hard
   timeout on the results subscription.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, Awaitable, Callable, Optional, Protocol

from loguru import logger

from app.services.codex.daemon_version import (
    reported_daemon_version,
    version_at_least,
)

DEFAULT_TIMEOUT_S = 600

# Image/video jobs on the codex engine need a daemon that forwards --quality
# (0.4.0). Below that, `quality` is silently discarded one layer down — the
# exact fake switch P4 removed from the UI — so the honest answer is a typed
# refusal that tells the user how to update, not a quiet degrade.
MIN_IMAGE_DAEMON_VERSION = "0.4.0"

# The refusal has to survive the trip to the user, and be followable once it
# gets there. Two constraints shape the string below, both learned the hard way:
#
# 1. ASCII ONLY. This message ends up in `dbos.workflow_status.error` as a
#    pickle, and `public.dbos_error_to_text()` (migration 219) escape-renders
#    that pickle, turns every byte >= 0x80 into a delimiter, and keeps only the
#    LONGEST surviving chunk. One em-dash therefore silently deletes whichever
#    half of the sentence is shorter. Measured against the live nous-db: with an
#    em-dash the reported version and the minimum were both dropped, leaving
#    only the tail. Do not "prettify" this punctuation back —
#    ``test_refusal_message_is_pure_ascii`` fails if anyone does.
# 2. It must name a command that actually works for the person reading it.
#    A bare "re-run install.sh" does not: that path demands a pairing code
#    (install.sh's pair block) and dies without one, and the reader is already
#    paired by definition. `--update` is the route that keeps their token.
_UPDATE_COMMAND = (
    "curl -fsSL https://cn.nous.ink:88/api/v1/codex-daemon/dist/install.sh"
    " | sh -s -- --update"
)


class DaemonOfflineError(RuntimeError):
    """The user has no daemon connected right now."""


class DaemonUpdateRequiredError(RuntimeError):
    """The connected daemon is too old for this job; message says how to update."""


class DaemonJobFailedError(RuntimeError):
    """The daemon ran the job and reported a failure.

    ``str(self)`` is deliberately the same ``"<code>: <message>"`` string the
    plain ``RuntimeError`` used to carry — ``codex.errors.from_daemon_error``
    splits on it, so changing the message would silently collapse every typed
    codex-local failure into the generic ``codex_failed``.

    ``code`` / ``detail`` default to empty because pickle reconstructs an
    exception by calling ``cls(*self.args)``, and DBOS pickles whatever a
    workflow raises. Nothing relies on them surviving that trip: callers
    translate via ``describe_generation_failure`` while still in-process, and what
    crosses the DBOS boundary afterwards is a plain ``RuntimeError``.
    """

    def __init__(self, message: str, code: str = "", detail: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.detail = detail


class DaemonTransport(Protocol):
    async def is_online(self, user_id: str) -> bool: ...
    async def send_job(self, user_id: str, job: dict) -> None: ...
    async def wait_result(self, job_id: str, timeout_s: float) -> dict: ...


class RedisDaemonTransport:
    """Production transport: presence + pub/sub via the shared Redis."""

    async def is_online(self, user_id: str) -> bool:
        from app.services.codex import daemon_presence

        return await daemon_presence.is_online_anywhere(user_id)

    async def send_job(self, user_id: str, job: dict) -> None:
        from app.services.codex import daemon_presence

        await daemon_presence.publish_job(user_id, job)

    async def wait_result(self, job_id: str, timeout_s: float) -> dict:
        from app.core.redis import get_async_redis
        from app.services.codex.daemon_presence import RESULTS_CHANNEL_PREFIX

        redis = await get_async_redis()
        pubsub = redis.pubsub()
        channel = f"{RESULTS_CHANNEL_PREFIX}{job_id}"
        await pubsub.subscribe(channel)
        try:
            loop = asyncio.get_running_loop()
            deadline = loop.time() + timeout_s
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise asyncio.TimeoutError()
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True,
                    timeout=min(remaining, 5.0),
                )
                if message is None:
                    continue
                data = message.get("data")
                if isinstance(data, bytes):
                    data = data.decode()
                try:
                    return json.loads(data)
                except Exception:
                    continue
        finally:
            try:
                await pubsub.unsubscribe(channel)
                await pubsub.aclose()
            except Exception:
                pass


async def dispatch_to_daemon(
    *,
    user_id: str,
    scope_id: int,
    kind: str,
    payload: dict[str, Any],
    transport: Optional[DaemonTransport] = None,
    mint_ticket: Optional[Callable[..., Any]] = None,
    attribution: Optional[dict[str, Any]] = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    daemon_version: Optional[Callable[[str], Awaitable[Optional[str]]]] = None,
) -> dict[str, Any]:
    """Send one job to the user's daemon and await its result.

    Raises ``DaemonOfflineError`` when nothing is connected,
    ``DaemonUpdateRequiredError`` when the connected daemon is older than this
    job needs, ``TimeoutError`` when the daemon never answers, ``RuntimeError``
    when it answers with a failure (message carries the daemon's typed code).

    ``attribution`` is the job's generation context. It goes on the TICKET,
    not into the job the daemon receives: the daemon never needs it, and the
    upload endpoint — which is where the product becomes a row — is the only
    place that does.
    """
    if transport is None:
        transport = RedisDaemonTransport()
    if mint_ticket is None:
        from app.api.codex_daemon_router import mint_upload_ticket

        mint_ticket = mint_upload_ticket

    if not await transport.is_online(user_id):
        raise DaemonOfflineError(
            "your local codex daemon is not connected — run `nous-codex run`"
        )

    if payload.get("engine") == "codex" and kind in ("image", "video"):
        resolver = daemon_version or reported_daemon_version
        reported = await resolver(user_id)
        # None = could not find out (offline / presence-vs-table disagreement):
        # not a verdict — skip, and let a later step raise the truthful error.
        if reported is not None and not version_at_least(
            reported, MIN_IMAGE_DAEMON_VERSION
        ):
            raise DaemonUpdateRequiredError(
                f"your local codex daemon is {reported}; image generation needs "
                f">= {MIN_IMAGE_DAEMON_VERSION}. No pairing code needed - update "
                f"it in place with: {_UPDATE_COMMAND} "
                "(on Windows, run install.ps1 with -Update instead; see "
                "tools/codex-daemon/README.md, section Upgrading)"
            )

    job_id = str(uuid.uuid4())
    ticket = mint_ticket(
        user_id=user_id, scope_id=scope_id, job_id=job_id, attribution=attribution
    )
    if asyncio.iscoroutine(ticket):
        ticket = await ticket

    job = {
        "type": "job",
        "job_id": job_id,
        "kind": kind,
        "payload": {**payload, "upload_ticket": ticket},
    }
    # The waiter owns its own subscription; start it BEFORE publishing so the
    # daemon's (fast) answer can never race past an unsubscribed channel.
    waiter = asyncio.create_task(transport.wait_result(job_id, timeout_s))
    await asyncio.sleep(0)
    try:
        await transport.send_job(user_id, job)
        result = await waiter
    except asyncio.TimeoutError as exc:
        raise TimeoutError(
            f"local codex daemon did not answer within {int(timeout_s)}s"
        ) from exc
    finally:
        if not waiter.done():
            waiter.cancel()

    if result.get("error"):
        logger.info("[codex-daemon] job {} failed: {}", job_id, result["error"])
        raw = str(result["error"])
        # The code is the leading token of the daemon's own message, the same
        # split ``errors.from_daemon_error`` makes. Read here too so callers
        # that want the verdict do not have to re-parse prose.
        code = raw.partition(":")[0].strip()
        detail = result.get("error_detail")
        raise DaemonJobFailedError(
            raw, code=code, detail=detail if isinstance(detail, str) else ""
        )
    return result
