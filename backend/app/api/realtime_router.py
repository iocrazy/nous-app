"""SSE (Server-Sent Events) endpoint for realtime updates.

Replaces direct Supabase Realtime connections - frontend only connects to FastAPI.
"""

import asyncio
import json
from typing import AsyncGenerator

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from loguru import logger

from app.core.deps import OptionalAuthDep
from app.db.supabase_client import get_async_supabase_admin

router = APIRouter(prefix="/realtime", tags=["Realtime"])


class RealtimeManager:
    """Manages Supabase Realtime subscriptions and SSE clients."""

    def __init__(self):
        self.clients: dict[str, asyncio.Queue] = {}  # user_id -> event queue
        self.subscriptions: dict[str, any] = {}  # user_id -> supabase channel

    async def subscribe_user(self, user_id: str) -> asyncio.Queue:
        """Create a queue for user and subscribe to Supabase Realtime."""
        if user_id in self.clients:
            # Return existing queue
            return self.clients[user_id]

        queue: asyncio.Queue = asyncio.Queue()
        self.clients[user_id] = queue

        # Start Supabase subscription for this user
        await self._setup_supabase_subscription(user_id, queue)

        return queue

    async def unsubscribe_user(self, user_id: str):
        """Remove user subscription."""
        if user_id in self.clients:
            del self.clients[user_id]

        if user_id in self.subscriptions:
            try:
                channel = self.subscriptions[user_id]
                client = await get_async_supabase_admin()
                await client.remove_channel(channel)
            except Exception as e:
                logger.warning(f"Error removing channel for user {user_id}: {e}")
            del self.subscriptions[user_id]

    async def _setup_supabase_subscription(self, user_id: str, queue: asyncio.Queue):
        """Setup Supabase Realtime subscription for a user."""
        try:
            client = await get_async_supabase_admin()

            # Subscribe to parsed_media changes for this user
            channel = client.channel(f"user_{user_id}_realtime")

            async def on_video_change(payload):
                """Handle video table changes."""
                event_type = payload.get("eventType", "unknown")
                new_record = payload.get("new", {})
                old_record = payload.get("old", {})

                # Only send events for this user's data
                record_user_id = new_record.get("user_id") or old_record.get("user_id")
                if record_user_id != user_id:
                    return

                event = {
                    "type": "video",
                    "event": event_type.lower(),
                    "data": new_record if event_type != "DELETE" else old_record,
                }
                await queue.put(event)

            async def on_collection_change(payload):
                """Handle collection_videos changes."""
                event_type = payload.get("eventType", "unknown")
                new_record = payload.get("new", {})
                old_record = payload.get("old", {})

                event = {
                    "type": "collection_video",
                    "event": event_type.lower(),
                    "data": new_record if event_type != "DELETE" else old_record,
                }
                await queue.put(event)

            async def on_tag_change(payload):
                """Handle video_tags changes."""
                event_type = payload.get("eventType", "unknown")
                new_record = payload.get("new", {})
                old_record = payload.get("old", {})

                event = {
                    "type": "video_tag",
                    "event": event_type.lower(),
                    "data": new_record if event_type != "DELETE" else old_record,
                }
                await queue.put(event)

            # Subscribe to tables
            channel.on_postgres_changes(
                event="*",
                schema="public",
                table="parsed_media",
                callback=on_video_change,
            ).on_postgres_changes(
                event="*",
                schema="public",
                table="video_collections",
                callback=on_collection_change,
            ).on_postgres_changes(
                event="*", schema="public", table="video_tags", callback=on_tag_change
            )

            await channel.subscribe()
            self.subscriptions[user_id] = channel
            logger.info(f"Subscribed user {user_id} to realtime updates")

        except Exception as e:
            logger.error(
                f"Failed to setup Supabase subscription for user {user_id}: {e}"
            )
            # Send error event to client
            await queue.put(
                {
                    "type": "error",
                    "event": "subscription_failed",
                    "data": {"message": str(e)},
                }
            )


# Global realtime manager instance
realtime_manager = RealtimeManager()


async def event_generator(user_id: str, request: Request) -> AsyncGenerator[str, None]:
    """Generate SSE events for a user."""
    queue = await realtime_manager.subscribe_user(user_id)

    try:
        # Send initial connection event
        yield f"data: {json.dumps({'type': 'connected', 'event': 'open', 'data': {}})}\n\n"

        # Send heartbeat every 30 seconds to keep connection alive
        heartbeat_interval = 30
        last_heartbeat = asyncio.get_event_loop().time()

        while True:
            # Check if client disconnected
            if await request.is_disconnected():
                break

            try:
                # Wait for event with timeout for heartbeat
                event = await asyncio.wait_for(queue.get(), timeout=heartbeat_interval)
                yield f"data: {json.dumps(event)}\n\n"
            except asyncio.TimeoutError:
                # Send heartbeat
                current_time = asyncio.get_event_loop().time()
                if current_time - last_heartbeat >= heartbeat_interval:
                    yield f"data: {json.dumps({'type': 'heartbeat', 'event': 'ping', 'data': {}})}\n\n"
                    last_heartbeat = current_time

    except asyncio.CancelledError:
        pass
    finally:
        await realtime_manager.unsubscribe_user(user_id)
        logger.info(f"User {user_id} disconnected from realtime")


@router.get("/subscribe")
async def subscribe_to_realtime(
    request: Request,
    ticket: str = None,
    token: str = None,
    auth: OptionalAuthDep = None,
):
    """
    Subscribe to realtime updates via Server-Sent Events.

    Authentication (preferred): one-shot 30s ticket from POST
    `/api/v1/ws/ticket`, passed as `?ticket=<random>`. The JWT itself
    never enters the URL (which would otherwise leak into nginx /
    uvicorn / Sentry access logs and the Referer header).

    Authentication (deprecated): `?token=<JWT>`. Still accepted while
    legacy clients migrate; logs a WARNING on every use because the JWT
    is now in the URL and therefore in access logs.

    Events:
    - video, collection_video, video_tag — DB changes
    - heartbeat — keep-alive every 30 s
    - connected — initial ack
    - error — subscription errors
    """
    # Auth chain: ticket (preferred) → header (Authorization) → token (warn).
    user_id = None
    if ticket:
        from app.api.ws_ticket_router import consume_ticket

        user_id = await consume_ticket(ticket)
    elif auth and auth.user_id:
        user_id = auth.user_id
    elif token:
        logger.warning(
            "[realtime/sse] DEPRECATED ?token= auth — JWT was just leaked to "
            "access logs. Client should migrate to ?ticket= "
            "(POST /api/v1/ws/ticket)."
        )
        from app.services.infra.supabase_auth_service import SupabaseAuthService

        auth_service = SupabaseAuthService()
        try:
            user = await auth_service.get_user(token)
            if user:
                user_id = user.get("id")
        except Exception as e:
            logger.warning(f"Token verification failed: {e}")

    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required"
        )

    return StreamingResponse(
        event_generator(user_id, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )
