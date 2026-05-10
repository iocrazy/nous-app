"""Episodic memory threading — group related memories.

Phase M (M3.A). Today retrieved memories arrive as independent rows
ordered by cosine similarity. But many facts come in coherent
"threads": ("user mentioned bug X" + "we tried fix A" + "fix A failed
because Y" + "switched to fix B") all belong together — losing one
loses the context.

This module gives:
  - assign_thread_for(): writer-time decision — same session_id +
    near-temporal proximity = same thread
  - load_thread(): retriever-time roll-up — given a hit, pull all
    siblings in same thread

Pure functions; DB calls injected via repo callable so module is
testable.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Awaitable, Callable, Optional
from uuid import UUID, uuid4

# Two memories share a thread if same session AND created within
# this window of each other. 30 min covers a typical sustained
# discussion; longer-gap memories are different threads.
DEFAULT_THREAD_WINDOW_MINUTES = 30


async def assign_thread_for(
    *,
    agent_id: str,
    user_id: str,
    session_id: Optional[str],
    created_at: datetime,
    fetch_recent_in_session: Callable[
        [str, str, Optional[str], int], Awaitable[list[dict]]
    ],
    window_minutes: int = DEFAULT_THREAD_WINDOW_MINUTES,
) -> Optional[UUID]:
    """Decide which thread_id this NEW memory belongs to.

    Rules:
      - No session_id (e.g. cross-session active_remember) → no thread
      - Find most recent memory in same (agent, user, session) that has
        a thread_id AND was created within window_minutes
      - If found: reuse its thread_id
      - Else: mint a fresh UUID

    ``fetch_recent_in_session`` injects DB access:
      (agent_id, user_id, session_id, limit) → list of recent rows
      with at least {created_at, thread_id}.
    """
    if not session_id:
        return None

    try:
        recent = await fetch_recent_in_session(agent_id, user_id, session_id, 10)
    except Exception:
        return uuid4()  # best-effort: don't block write on lookup failure

    cutoff = created_at - timedelta(minutes=window_minutes)
    for row in recent or []:
        row_created = row.get("created_at")
        row_thread = row.get("thread_id")
        if not row_thread or not row_created:
            continue
        # Coerce timestamp
        if isinstance(row_created, str):
            try:
                row_created = datetime.fromisoformat(row_created.replace("Z", "+00:00"))
            except ValueError:
                continue
        # Compare in same tz space — strip tz from both for safety
        a = created_at.replace(tzinfo=None) if created_at.tzinfo else created_at
        b = row_created.replace(tzinfo=None) if row_created.tzinfo else row_created
        if b >= cutoff.replace(tzinfo=None):
            try:
                return UUID(str(row_thread))
            except (ValueError, TypeError):
                continue

    return uuid4()


async def load_thread_for_memory(
    *,
    memory_id: str,
    fetch_thread: Callable[[str], Awaitable[list[dict]]],
    max_siblings: int = 10,
) -> list[dict]:
    """Given a memory_id, fetch all siblings in the same thread.

    Used by the retriever to roll up cosine-matched memories — if a
    user asks about "the deployment failure", the matched memory + its
    thread siblings together carry the full episode.

    Returns at most ``max_siblings + 1`` rows (target + siblings),
    ordered by created_at ASC (chronological recall reads naturally).
    """
    try:
        rows = await fetch_thread(memory_id)
    except Exception:
        return []
    return (rows or [])[: max_siblings + 1]


__all__ = [
    "DEFAULT_THREAD_WINDOW_MINUTES",
    "assign_thread_for",
    "load_thread_for_memory",
]
