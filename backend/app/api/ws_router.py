# app/api/ws_router.py

"""
WebSocket endpoint for real-time task progress.

Subscribes to Redis pub/sub channel ``task_progress:{user_id}``
and pushes progress messages to the connected client.
"""

import json

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from loguru import logger

from app.core.redis import get_async_redis
from app.db.supabase_client import get_async_supabase

router = APIRouter()


async def _authenticate_ws(token: str) -> str | None:
    """Validate JWT token and return user_id, or None on failure."""
    try:
        client = await get_async_supabase()
        user_response = await client.auth.get_user(token)
        if user_response and user_response.user:
            return str(user_response.user.id)
    except Exception as e:
        logger.debug(f"[WS] Auth failed: {e}")
    return None


@router.websocket("/ws/task-progress")
async def ws_task_progress(
    websocket: WebSocket,
    ticket: str | None = Query(None),
    token: str | None = Query(None),
):
    """WebSocket endpoint that streams task progress from Redis pub/sub.

    Auth (preferred): ``?ticket=<short-random>``
        One-shot 30s-valid handle minted by ``POST /api/v1/ws/ticket``.
        Consumed via Redis GETDEL so the same ticket can't be replayed.
        The JWT never appears in the URL → server access logs / browser
        history / Sentry breadcrumbs don't leak it. See A7 of the A-route
        plan and ``app/api/ws_ticket_router.py``.

    Auth (deprecated, 6-month migration window): ``?token=<jwt>``
        Kept for backwards compatibility while frontend migrates. New
        clients should use ticket only. Server access logs DO contain
        the JWT for this path — treat as a known liability until the
        flag day.

    Messages are JSON objects with fields:
    ``unified_task_id``, ``dbos_workflow_id``, ``status``, ``percent``,
    ``speed``, ``downloaded``, ``total``, and optionally ``error``.
    """
    user_id: str | None = None
    if ticket:
        from app.api.ws_ticket_router import consume_ticket

        user_id = await consume_ticket(ticket)
    elif token:
        logger.debug(
            "[WS] using legacy ?token= auth — client should migrate to ?ticket="
        )
        user_id = await _authenticate_ws(token)

    if not user_id:
        await websocket.close(code=4001, reason="Authentication failed")
        return

    await websocket.accept()
    logger.info(f"[WS] Client connected: user={user_id[:8]}...")

    redis = await get_async_redis()
    pubsub = redis.pubsub()
    channel = f"task_progress:{user_id}"

    try:
        await pubsub.subscribe(channel)
        logger.debug(f"[WS] Subscribed to {channel}")

        async for message in pubsub.listen():
            if message["type"] != "message":
                continue
            try:
                data = message["data"]
                if isinstance(data, bytes):
                    data = data.decode("utf-8")
                payload = json.loads(data)
                await websocket.send_json(payload)
            except WebSocketDisconnect:
                break
            except Exception as e:
                logger.debug(f"[WS] Send error: {e}")
                break

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.debug(f"[WS] Connection error: {e}")
    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.aclose()
        logger.info(f"[WS] Client disconnected: user={user_id[:8]}...")
