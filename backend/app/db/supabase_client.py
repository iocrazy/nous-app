# app/db/supabase_client.py

"""
Supabase client module — per-loop instances via WeakKeyDictionary.

Each running asyncio event loop gets its own ``AsyncClient`` (and its own
httpx pool). Entries are held by a ``WeakKeyDictionary`` keyed by the loop
object itself, so when a loop is garbage-collected (e.g., ``asyncio.run()``
exits) its client entry vanishes naturally.

Why this design (was an incident; do not regress)
-------------------------------------------------
The previous "single shared client + drain-on-loop-change" design worked
fine when uvicorn was the only event loop in the process. But DBOS workflows
sprinkle ``asyncio.run()`` across many call sites; each one spins up a fresh
event loop, runs a coroutine, then disposes the loop. Under the old design,
every such loop change called ``_drain_old_client(...)`` on the uvicorn
loop's cached client — that helper called ``aclose()`` on the httpx pool
*of the wrong loop*, killing fire-and-forget tasks (request logging,
progress writes) mid-flight with ``RuntimeError: Cannot send a request,
as the client has been closed.`` Failure rate observed in prod: 10-26% of
request log writes; user-facing 500s on routes that race the drain.

A per-loop ``WeakKeyDictionary`` avoids this entirely:

* Each loop only ever uses its own client; no cross-loop interference.
* When a loop dies, Python GC reclaims it and its dict entry vanishes —
  no need to call ``aclose()`` on a dead-loop client (would itself raise).
* ``id(loop)`` is **not** used as the key because Python reuses object ids
  after GC (empirically 8/10 collisions in tight ``asyncio.run()`` loops).
  The loop object itself is hashable, so ``WeakKeyDictionary[loop, client]``
  is the right primitive.

The historical motivation for the drain was a ``uvicorn --reload`` fd leak
(P2-10 / issue #21, commit 8a04d127). Prod doesn't run ``--reload`` so that
leak is dev-only; with WeakKeyDictionary the dev case also self-cleans
when the reloader replaces the worker process.
"""

import asyncio
import weakref
from typing import MutableMapping

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
    """Per-loop Supabase async client cache.

    Two dicts (anon + service-role) keyed by event loop. Entries are
    auto-evicted on loop GC.

    See module docstring for the full rationale.
    """

    # ``WeakKeyDictionary`` annotated as a generic ``MutableMapping`` so type
    # checkers don't choke on parameterised weakref types across Python
    # versions.
    _instances: MutableMapping[asyncio.AbstractEventLoop, AsyncClient] = (
        weakref.WeakKeyDictionary()
    )
    _admin_instances: MutableMapping[asyncio.AbstractEventLoop, AsyncClient] = (
        weakref.WeakKeyDictionary()
    )

    @classmethod
    async def get_client(cls) -> AsyncClient:
        """Get async Supabase client (anon key) for the current event loop."""
        loop = asyncio.get_running_loop()
        client = cls._instances.get(loop)
        if client is not None:
            return client

        if not settings.SUPABASE_URL or not settings.SUPABASE_ANON_KEY:
            raise ValueError("SUPABASE_URL and SUPABASE_ANON_KEY must be configured")

        client = await create_async_client(
            settings.SUPABASE_URL,
            settings.SUPABASE_ANON_KEY,
            options=_get_client_options(),
        )
        cls._instances[loop] = client
        logger.debug("Supabase async client initialized (per-loop)")
        return client

    @classmethod
    async def get_admin_client(cls) -> AsyncClient:
        """Get async Supabase admin client (service_role key) for the current loop."""
        loop = asyncio.get_running_loop()
        client = cls._admin_instances.get(loop)
        if client is not None:
            return client

        if not settings.SUPABASE_URL or not settings.SUPABASE_SERVICE_ROLE_KEY:
            raise ValueError(
                "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be configured"
            )

        client = await create_async_client(
            settings.SUPABASE_URL,
            settings.SUPABASE_SERVICE_ROLE_KEY,
            options=_get_client_options(),
        )
        cls._admin_instances[loop] = client
        logger.debug("Supabase async admin client initialized (per-loop)")
        return client

    @classmethod
    async def close(cls) -> None:
        """Close the current loop's clients (called from FastAPI lifespan shutdown).

        Only the current loop's clients are closed. Other loops' clients (if
        any are still alive) are owned by their respective loops and will be
        GC'd when those loops are. We never call ``aclose()`` on a client
        bound to a different loop — that's the exact bug this redesign fixes.
        """
        loop = asyncio.get_running_loop()

        client = cls._instances.pop(loop, None)
        if client is not None:
            try:
                await client.auth.sign_out()
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"sign_out raised {exc!r} (ignored)")
            await _aclose_subclients(client)

        admin_client = cls._admin_instances.pop(loop, None)
        if admin_client is not None:
            await _aclose_subclients(admin_client)
            logger.info("Supabase async clients closed (current loop)")


async def _aclose_subclients(client: AsyncClient) -> None:
    """Best-effort close of postgrest / auth / storage httpx sessions on ``client``.

    Only safe to call from the loop that owns ``client``. Errors are
    swallowed because shutdown failures shouldn't crash the process.
    """
    for attr in ("storage", "auth", "postgrest"):
        sub = getattr(client, attr, None)
        if sub is None:
            continue
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
            logger.debug(f"Supabase close: {attr}.aclose() raised {exc!r} (ignored)")


async def get_async_supabase() -> AsyncClient:
    """Get async Supabase client (anon key) for the current event loop."""
    return await AsyncSupabaseClient.get_client()


async def get_async_supabase_admin() -> AsyncClient:
    """Get async Supabase admin client (service_role key) for the current loop."""
    return await AsyncSupabaseClient.get_admin_client()
