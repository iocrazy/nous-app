# app/db/supabase_client.py

"""
Supabase client module

Provides async Supabase client initialization and management for database,
auth, and storage operations.

The singleton is event-loop-aware: when the running loop changes (e.g.
successive asyncio.run() calls in Celery workers), the cached client is
automatically recreated so that its httpx transport matches the current loop.
"""

import asyncio
from typing import Optional

from loguru import logger
from supabase import AsyncClientOptions
from supabase._async.client import AsyncClient
from supabase._async.client import create_client as create_async_client

from app.core.config import settings


def _get_client_options() -> AsyncClientOptions:
    """Get Supabase async client options."""
    headers = {}
    if settings.SUPABASE_TENANT_ID:
        headers["X-Tenant-ID"] = settings.SUPABASE_TENANT_ID
        logger.debug(f"Using tenant ID: {settings.SUPABASE_TENANT_ID}")
    return AsyncClientOptions(headers=headers) if headers else AsyncClientOptions()


class AsyncSupabaseClient:
    """Event-loop-aware Supabase async client singleton.

    Tracks the event loop each client was created on. If the current running
    loop differs (common in Celery workers using asyncio.run()), the stale
    client is discarded and a fresh one is created on the new loop.
    """

    _instance: Optional[AsyncClient] = None
    _admin_instance: Optional[AsyncClient] = None
    _instance_loop_id: Optional[int] = None
    _admin_instance_loop_id: Optional[int] = None

    @classmethod
    async def _drain_old_client(cls, old: Optional[AsyncClient]) -> None:
        """Close the underlying httpx pools on a stale Supabase client.

        P2-10 / issue #21: when ``uvicorn --reload`` swaps event loops,
        the previous loop's Supabase client (held in cls._instance) was
        getting *abandoned* — replaced by reference, but its postgrest /
        gotrue / storage httpx connection pools were never closed. After
        2-3 days of reload churn, file descriptor / socket exhaustion
        manifests as the "endpoint LISTEN but every request times out"
        deadlock symptom.

        This drain hook calls aclose() on each sub-client's httpx
        session. Best-effort: any one failure logs + continues so a bad
        sub-client can't block the rest of the cleanup.
        """
        if old is None:
            return
        # Sub-clients each hold their own httpx.AsyncClient session.
        # Order chosen so the "core" (postgrest) is drained last —
        # nothing depends on its lifetime here, but keep deterministic.
        for attr in ("storage", "auth", "postgrest"):
            sub = getattr(old, attr, None)
            if sub is None:
                continue
            # postgrest exposes .aclose() directly; storage/auth wrap
            # their session and need .session.aclose()
            session_close = None
            if hasattr(sub, "aclose") and callable(sub.aclose):
                session_close = sub.aclose
            elif hasattr(sub, "session") and hasattr(sub.session, "aclose"):
                session_close = sub.session.aclose
            if session_close is None:
                continue
            try:
                await session_close()
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    f"Supabase drain: {attr}.aclose() raised {exc!r} (ignored)"
                )

    @classmethod
    async def get_client(cls) -> AsyncClient:
        """Get async Supabase client (anon key)."""
        current_loop_id = id(asyncio.get_running_loop())

        if cls._instance is None or cls._instance_loop_id != current_loop_id:
            # P2-10: drain the previous loop's client before discarding
            # the reference, otherwise its httpx pools leak across reloads.
            if cls._instance is not None:
                await cls._drain_old_client(cls._instance)

            if not settings.SUPABASE_URL or not settings.SUPABASE_ANON_KEY:
                raise ValueError(
                    "SUPABASE_URL and SUPABASE_ANON_KEY must be configured"
                )

            cls._instance = await create_async_client(
                settings.SUPABASE_URL,
                settings.SUPABASE_ANON_KEY,
                options=_get_client_options(),
            )
            cls._instance_loop_id = current_loop_id
            logger.debug("Supabase async client initialized")

        return cls._instance

    @classmethod
    async def get_admin_client(cls) -> AsyncClient:
        """Get async Supabase admin client (service_role key)."""
        current_loop_id = id(asyncio.get_running_loop())

        if (
            cls._admin_instance is None
            or cls._admin_instance_loop_id != current_loop_id
        ):
            # P2-10: drain old client before swap (see get_client docstring)
            if cls._admin_instance is not None:
                await cls._drain_old_client(cls._admin_instance)

            if not settings.SUPABASE_URL or not settings.SUPABASE_SERVICE_ROLE_KEY:
                raise ValueError(
                    "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be configured"
                )

            cls._admin_instance = await create_async_client(
                settings.SUPABASE_URL,
                settings.SUPABASE_SERVICE_ROLE_KEY,
                options=_get_client_options(),
            )
            cls._admin_instance_loop_id = current_loop_id
            logger.debug("Supabase async admin client initialized")

        return cls._admin_instance

    @classmethod
    async def close(cls):
        """Close async client connections."""
        if cls._instance:
            try:
                await cls._instance.auth.sign_out()
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"sign_out raised {exc!r} (ignored)")
            await cls._drain_old_client(cls._instance)
            cls._instance = None
            cls._instance_loop_id = None
        if cls._admin_instance:
            await cls._drain_old_client(cls._admin_instance)
            cls._admin_instance = None
            cls._admin_instance_loop_id = None
            logger.info("Supabase async clients closed")


async def get_async_supabase() -> AsyncClient:
    """Get async Supabase client."""
    return await AsyncSupabaseClient.get_client()


async def get_async_supabase_admin() -> AsyncClient:
    """Get async Supabase admin client."""
    return await AsyncSupabaseClient.get_admin_client()
