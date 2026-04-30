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
async def ws_task_progress(websocket: WebSocket, token: str = Query(...)):
    """WebSocket endpoint that streams task progress from Redis pub/sub.

    Connect: ``ws(s)://<host>/ws/task-progress?token=<jwt>``

    Messages are JSON objects with fields:
    ``unified_task_id``, ``dbos_workflow_id``, ``status``, ``percent``,
    ``speed``, ``downloaded``, ``total``, and optionally ``error``.
    """
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
