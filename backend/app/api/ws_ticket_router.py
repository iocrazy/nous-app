"""ws_ticket_router — short-lived one-shot ticket for WebSocket auth.

Why this exists
---------------
Browsers do not allow WebSocket clients to set custom headers on the
HTTP upgrade request. The standard workaround is to encode auth into
the connect URL's query string — which is exactly what mediahub does
today: `ws://host/ws/task-progress?token=<JWT>`.

That puts the JWT into:
  * Server access logs (uvicorn / nginx / load balancer)
  * Browser history / Service Worker caches
  * Frontend error reporters that capture the URL on uncaught
    exceptions (Sentry style)
  * Any reverse proxy / CDN access log on the path

A long-lived JWT in any of those locations is a **token-replay
liability**. An attacker with read access to nginx logs can replay any
JWT for its full validity period.

The ticket flow
---------------
1. Authenticated user POSTs `/api/v1/ws/ticket` with a normal Bearer
   JWT (or session cookie). Backend generates a 32-byte url-safe
   random ticket, stores it in Redis with TTL 30s, mapped to the user
   id, and returns the ticket.
2. Frontend opens the WebSocket as
   `ws://host/ws/task-progress?ticket=<short-random>`.
3. WS handler reads the ticket, looks up Redis, atomically GET+DEL
   (one-shot — replaying the same ticket fails). Resolves to user_id;
   from there the existing user-scoped pubsub subscription works.
4. Backwards-compat: the existing `?token=<JWT>` path stays for
   ~6 months while frontend migrates. Marked deprecated in the WS
   handler log.

Even if the ticket leaks (server log / Sentry), it has 30 s validity
+ one-shot use — an attacker would need to race the legitimate user.
The JWT itself never appears in any URL.

Storage choice: Redis instead of a Postgres `ws_tickets` table — this
is high-frequency, short-TTL data; Redis's EX option auto-expires
without a sweeper. Single-key lookup; no schema needed.
"""
from __future__ import annotations

import secrets
from typing import Optional

from fastapi import APIRouter, HTTPException
from loguru import logger
from pydantic import BaseModel

from app.core.deps import AuthDep
from app.core.redis import get_async_redis


router = APIRouter(prefix="/ws", tags=["WebSocket"])

TICKET_TTL_SECONDS = 30
TICKET_KEY_PREFIX = "ws_ticket:"


class TicketResponse(BaseModel):
    ticket: str
    expires_in_seconds: int


@router.post("/ticket", response_model=TicketResponse)
async def issue_ws_ticket(auth: AuthDep) -> TicketResponse:
    """Mint a one-shot WebSocket auth ticket.

    The ticket is stored in Redis as
    ``ws_ticket:<random> -> <user_id>`` with TTL 30s. The WS handler
    consumes it via GET+DEL on connect; replay attempts fail because
    the key is deleted on first use.
    """
    ticket = secrets.token_urlsafe(32)
    redis = await get_async_redis()
    try:
        await redis.set(
            f"{TICKET_KEY_PREFIX}{ticket}",
            str(auth.user_id),
            ex=TICKET_TTL_SECONDS,
        )
    except Exception as exc:
        logger.exception(f"[ws-ticket] mint failed: {exc}")
        raise HTTPException(500, "ticket store unavailable")
    return TicketResponse(
        ticket=ticket, expires_in_seconds=TICKET_TTL_SECONDS
    )


async def consume_ticket(ticket: str) -> Optional[str]:
    """Atomically GETDEL the ticket. Returns the bound user_id on
    success, or None if ticket is missing / already used / expired.

    Atomic via Redis GETDEL command (Redis 6.2+); we have 7+ in stack.
    """
    if not ticket:
        return None
    redis = await get_async_redis()
    try:
        raw = await redis.execute_command("GETDEL", f"{TICKET_KEY_PREFIX}{ticket}")
    except Exception as exc:
        logger.exception(f"[ws-ticket] consume failed: {exc}")
        return None
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode()
    return str(raw) if raw else None


__all__ = ["router", "consume_ticket", "TICKET_TTL_SECONDS"]
