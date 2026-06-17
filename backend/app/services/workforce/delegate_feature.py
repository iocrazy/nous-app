"""Feature gate for the Workforce Delegate tool (harness audit #4).

The Delegate → agent_inbox → workforce-queue → agent_workforce_workflow
execution chain is built and registered, but NOT fully wired: the
DBOS-scheduled inbox processor runs without a dispatcher
(``workforce_dispatch.py`` constructs ``InboxProcessor()`` with no dispatcher),
so a delegated task is created in ``task_tracking`` and then orphaned —
never enqueued, never executed, and an ``await=true`` Delegate call times out
forever.

Until that last wire is connected, advertising Delegate to the LLM is a silent
footgun: a user-facing agent can call it, get ``status="queued"``, and the work
never runs. This flag keeps the front door shut so the UI is honest — off
(default) means the tool is not advertised AND ``execute()`` fail-closes with a
clear message.

Flip ``FEATURE_WORKFORCE_DELEGATE`` truthy only once the execution path is
actually wired end-to-end:
  1. give the scheduled InboxProcessor a ``DbosAgentWorkforcePool`` dispatcher,
  2. enqueue the workflow from the workflow body, NOT inside the ``@DBOS.step``
     (CLAUDE.md route C: never dispatch a workflow from within a step),
  3. make ``DbosAgentWorkforcePool.inflight_count`` decrement on terminal state,
  4. decide whether to wire or delete the dead ``claim_next_queued`` CAS guard.
"""

from __future__ import annotations

import os

_TRUTHY = {"1", "true", "yes", "on"}


def delegate_feature_enabled() -> bool:
    """True only when ``FEATURE_WORKFORCE_DELEGATE`` is explicitly enabled."""
    return os.getenv("FEATURE_WORKFORCE_DELEGATE", "").strip().lower() in _TRUTHY


__all__ = ["delegate_feature_enabled"]
