"""lane_dispatch — helper for routing work into the correct LaneQueue.

Today mediahub dispatch is direct: API handler → service → workflow,
no priority discrimination. A flood of background AI summary work can
starve a user clicking "fetch this video".

This helper wraps ``app.state.lane_queue.submit(lane, awaitable)`` with
a ``classify_lane()`` shortcut so callsites stay readable:

    # Before
    result = await my_workflow_call()

    # After
    from app.agent_framework.lane_dispatch import dispatch_in_lane, Origin

    result = await dispatch_in_lane(
        my_workflow_call(),
        origin=Origin.USER_CLICK,
    )

Wire-up sites (integration is per-feature; this commit only ships
the helper):

| Callsite                                  | Lane to use                      |
|-------------------------------------------|----------------------------------|
| media_fetch_router.fetch_video            | Origin.USER_CLICK → Lane.USER    |
| sb_ai_router /storyboard/*                | Origin.USER_CLICK → Lane.USER    |
| scheduled_recovery / scheduled_quotas     | Origin.SCHEDULED → Lane.SCHEDULED|
| ai_summary / analyze workflows            | Origin.BACKGROUND → Lane.BACKGROUND |
| AgentRunner delegate (workforce)          | Origin.SUBAGENT → Lane.SUBAGENT  |

The integration is deferred per-callsite because workforce/ (2062 lines)
needs careful refactor to slot lanes into the existing scheduler /
inbox / outbox model. This helper unblocks the integration when ready.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Awaitable, Optional

from app.agent_framework.lane_queue import Lane, LaneQueue


class Origin(str, Enum):
    """Where the work came from. Used to pick the right Lane."""

    USER_CLICK = "user_click"  # API hit from frontend interaction
    USER_API = "user_api"  # API hit from external automation
    BACKGROUND = "background"  # AI workflow chain
    SCHEDULED = "scheduled"  # cron job / sweeper
    SUBAGENT = "subagent"  # AgentRunner internal delegate


_ORIGIN_TO_LANE: dict[Origin, Lane] = {
    Origin.USER_CLICK: Lane.USER,
    Origin.USER_API: Lane.USER,  # treat API calls same priority as click
    Origin.BACKGROUND: Lane.BACKGROUND,
    Origin.SCHEDULED: Lane.SCHEDULED,
    Origin.SUBAGENT: Lane.SUBAGENT,
}


def classify_lane(origin: Origin) -> Lane:
    """Return the Lane for a given Origin. Static mapping for now;
    future could add policy (e.g. premium users always USER, free
    users BACKGROUND)."""
    return _ORIGIN_TO_LANE[origin]


async def dispatch_in_lane(
    awaitable: Awaitable[Any],
    *,
    origin: Origin,
    queue: Optional[LaneQueue] = None,
) -> Any:
    """Submit ``awaitable`` to the lane derived from ``origin``.

    ``queue`` defaults to the per-process queue on
    ``app.state.lane_queue`` (set up in app.main lifespan). Tests can
    pass an explicit queue.

    If no queue available (cold start), runs awaitable directly without
    lane dispatch — degrades gracefully rather than blocking startup.
    """
    if queue is None:
        try:
            from app.main import app as _app

            queue = getattr(_app.state, "lane_queue", None)
        except (ImportError, RuntimeError):
            queue = None

    if queue is None:
        # Pre-lifespan or no queue configured — run directly
        return await awaitable

    lane = classify_lane(origin)
    return await queue.submit(lane, awaitable)
