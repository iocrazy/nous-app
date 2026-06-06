"""Regression tests for ambient-scope propagation through the sync→async
bridges (A2 pass 4a).

When ``SCOPE_ENFORCE_RESOURCES`` flips on, every ORM query touching
``resources`` must observe the ambient ``Scope`` set at the request/task
entry boundary (the ``_scope`` ContextVar). Pass 4b sets that scope at async
boundaries that then call DOWNSTREAM sync helpers, which bounce work onto a
fresh thread/loop via ``run_async``. A raw thread hop starts the worker with a
FRESH default context, so without care the ambient ``_scope`` would evaporate
before reaching the resource repo call → fail-closed ``UnscopedQueryError``
(flag on) or a silent cross-tenant gap.

These tests use the PUBLIC scope API only (``Scope`` / ``request_scope`` /
``current_scope``) and pin the two bridge helpers' behaviour:

  * ``app.tasks.utils.run_async`` — branch 2 (running loop → ThreadPoolExecutor)
    is the load-bearing fix; branch 1 (no running loop → ``asyncio.run``) already
    propagated and is documented here.
  * ``app.services.media.parsers.parse_helpers._run_async`` — bare
    ``asyncio.run`` with no thread hop; documents whether the explicit
    copy_context wrap is load-bearing or a no-op.
"""

from __future__ import annotations

import asyncio

from app.db.scope import Scope, current_scope, request_scope
from app.services.media.parsers.parse_helpers import _run_async
from app.tasks.utils import run_async


async def _record_scope():
    """Coroutine that observes the ambient scope visible to it and returns the
    ``user_id`` (or ``None`` if no scope is bound)."""
    await asyncio.sleep(0)
    scope = current_scope()
    return getattr(scope, "user_id", None)


async def test_run_async_branch2_propagates_scope_through_thread_hop():
    """run_async BRANCH 2 (running loop present → ThreadPoolExecutor worker).

    Being inside pytest-asyncio's running loop forces branch 2: run_async
    bounces the coroutine to a worker thread that gets its own fresh loop.
    The worker thread starts with a fresh default context, so the ambient
    ``_scope`` only survives because run_async runs the worker under a COPY of
    the caller's context.

    Call run_async DIRECTLY (synchronously) inside the running loop — do NOT
    use ``asyncio.to_thread`` to reach it, which would land on a loop-LESS
    worker thread and route through branch 1 (asyncio.run), masking the
    regression.
    """
    async with request_scope(Scope(user_id="u-123")):
        recorded = run_async(_record_scope())
    assert recorded == "u-123"


def test_run_async_branch1_propagates_scope_no_running_loop():
    """run_async BRANCH 1 (no running loop → asyncio.run).

    A plain sync test: no event loop is running on this thread, so run_async
    takes the ``asyncio.run`` path. ``asyncio.run`` creates its Task via
    ``loop.create_task``, which copies THIS thread's current context, so a
    scope set on the calling thread already propagates. Documents the
    already-working path.
    """
    from app.db.scope import _scope  # public API has no sync setter

    tok = _scope.set(Scope(user_id="u-sync"))
    try:
        recorded = run_async(_record_scope())
    finally:
        _scope.reset(tok)
    assert recorded == "u-sync"


def test_parse_helpers_run_async_propagates_scope():
    """parse_helpers._run_async — bare ``asyncio.run`` (no thread hop).

    Set the scope on the calling thread, call ``_run_async`` directly, assert
    it propagates. ``asyncio.run`` copies the calling thread's current context,
    so the scope reaches the coroutine. Combined with the analysis in the task
    report this documents whether the explicit copy_context wrap is load-bearing
    or a documented no-op.
    """
    from app.db.scope import _scope  # public API has no sync setter

    tok = _scope.set(Scope(user_id="u-parse"))
    try:
        recorded = _run_async(_record_scope())
    finally:
        _scope.reset(tok)
    assert recorded == "u-parse"
