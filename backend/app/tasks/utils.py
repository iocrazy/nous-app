"""Shared utilities for Celery tasks."""

import asyncio


def run_async(coro):
    """Run an async coroutine in a synchronous Celery worker context.

    Uses asyncio.run() which creates a fresh event loop per call.
    This is safe for Celery workers (each task runs in its own thread/process).
    """
    return asyncio.run(coro)
