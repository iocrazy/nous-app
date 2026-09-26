"""Why a sub-agent settled — one closed vocabulary for every producer (fh4 E2).

A child's result is written by up to four producers: the synchronous Task
path, the background worker, a replayed worker step that finds the child
already ran, and the stale-task reaper closing a child whose worker is gone.
``status`` says WHAT the result is. ``settle_reason`` says WHO ended it, and
the two are orthogonal: a ``failed`` child can have failed on its own
(``producer``) or have been lost with its worker (``lost``), and the parent
model should be able to tell a bad answer from no answer.

Values
======
* ``producer`` — the child ran to its own end: success, failure, budget stop,
  or a crash inside the child. The result is the child's.
* ``kill`` — the child was stopped from outside (issue cancel, root abort,
  CancelHook). Its envelope status is ``cancelled``.
* ``teardown`` — the worker running it was shut down on purpose (the run row
  carries ``error_code='worker_shutdown'``, fh2 T3). Closed by the reaper.
* ``lost`` — the worker died, or DBOS no longer owns its workflow. Closed by
  the reaper.

Carried on ``subagent_done`` (all three spawn paths, including Delegate's
``task_result``) and on the ``subagent_result`` inbox content (the two Task
paths only; Delegate results do not enter the parent run's inbox, by design).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Optional

from app.repositories.agent_runs_repository import WORKER_SHUTDOWN_ERROR_CODE


class SettleReason(StrEnum):
    PRODUCER = "producer"
    KILL = "kill"
    TEARDOWN = "teardown"
    LOST = "lost"


def settle_reason_for_envelope(status: Optional[str]) -> SettleReason:
    """The reason for a result the child's own code path produced. Only a
    cancel is someone else's doing; everything else, a crash included, is the
    child's."""
    return SettleReason.KILL if status == "cancelled" else SettleReason.PRODUCER


def settle_reason_for_lost_run(run: Optional[dict[str, Any]]) -> SettleReason:
    """The reason the reaper files. A deliberate worker shutdown stamps the run
    with ``worker_shutdown``; anything else the reaper closes was lost."""
    if (run or {}).get("error_code") == WORKER_SHUTDOWN_ERROR_CODE:
        return SettleReason.TEARDOWN
    return SettleReason.LOST


def subagent_result_dedupe_key(task_id: str) -> str:
    """One live ``subagent_result`` per background task, whoever writes it.

    The worker and the reaper both use it, so a replayed step and a reaper
    racing a dying worker converge on the first writer's row (mig 462's unique
    index makes that a constraint, not a lookup)."""
    return f"subagent-result-{task_id}"


__all__ = [
    "SettleReason",
    "settle_reason_for_envelope",
    "settle_reason_for_lost_run",
    "subagent_result_dedupe_key",
]
