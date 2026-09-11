"""The one builder of an issue's deep link (3a Task 3b).

The frontend route is ``/team/{team_id}/todolist/{issue_key}`` — keyed by the
issue KEY (``MH-n``) inside a team, never by the snowflake id. Two readers print
this URL today:

* the Generated inbox card's source line (``services/library/generated_source``)
* every version of a lineage response (``services/deliverables/lineage_view``)

They describe the same row from two directions, so they must print the same
string. A second builder would drift by a query string and nothing would fail —
the link would simply land at the top of the issue instead of on the step that
produced the thing you clicked from.

**It refuses more often than it builds.** No team or no key → ``None``, and the
caller renders a disabled control. That is the honest answer: a URL assembled
from ``issue_id`` alone resolves to nothing, and a dead link reads worse than a
button that says it has nowhere to go.
"""

from __future__ import annotations

from typing import Any, Optional


def issue_deep_link(
    *,
    team_id: Any,
    issue_key: Any,
    step: Optional[int] = None,
    turn: Optional[int] = None,
) -> Optional[str]:
    """``/team/{team_id}/todolist/{issue_key}`` (+ ``?step=&turn=`` when known).

    ``team_id`` is accepted as ``Any`` on purpose: ``issues.team_id`` comes
    back a native int from the issue repository and a string from the
    deliverables join, and both must format to the same URL.

    ``step`` is compared against ``None``, not truth-tested — steps are
    0-based, so ``if step:`` would silently drop every link to a run's first
    step. ``turn`` is read the same way, for the same reason.

    **``turn`` only rides along with a step.** The trajectory keys its nodes by
    the PAIR ``(turn, step)``, so a run that took three turns draws three
    "step 2" nodes and the step alone lands on whichever the page finds first
    (3a Task 8b). Alone, though, a turn names no position — emitting it would
    leave a query key the page can only ignore, so it is dropped with the step.
    """
    if not team_id or not issue_key:
        return None
    url = f"/team/{team_id}/todolist/{issue_key}"
    if step is not None:
        url += f"?step={step}"
        if turn is not None:
            url += f"&turn={turn}"
    return url


__all__ = ["issue_deep_link"]
