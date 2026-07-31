"""autopilot_tick — the M4 Autopilot engine (design spec §2, task O2).

Thin `@DBOS.workflow()` shell over `_autopilot_tick_impl`, mirroring the
`stage_hook_dispatch` / `_stage_hook_dispatch_impl` split (module docstring
of ``app.workflows.stage_hook``): the impl is a plain undecorated async
function tests can drive directly, no DBOS runtime needed.

Trigger points (all best-effort, never block the caller — see
``enqueue_autopilot_tick``):
  1. A node's mirror issue reaches ``done`` (``issue_repository.
     _fire_stage_node_sync``).
  2. A group arrives — forward/back advance (``advance_service.
     execute_advance``) or project creation (``instantiation.
     instantiate_project_workflow``).
  3. The 5-minute scheduled sweep (``app.workflows.
     autopilot_sweep.autopilot_sweep_workflow``) — the backstop for a tick
     that never got enqueued (a missed hook, a worker restart mid-flight).

Tick body (spec §2, re-checked from scratch every call — NO cross-call
memo/lock; correctness comes from re-reading current state at each step, the
same idempotency discipline ``stage_hook`` uses):
  1. ``projects.autopilot_enabled`` off → return (framework-wide off switch,
     spec §1: default TRUE, so merging this PR is zero-behavior-change only
     because every node's own `events.auto_start` still defaults false).
  2. Auto-start pass: every non-terminal, non-skipped node with
     ``events.auto_start`` whose dependencies are ALL actually done/skipped
     (no co-arrival/closing-group exemptions here — unlike a group's forward
     advance, a start-ahead node has no "arriving sibling group" to exempt;
     see ``_unmet_dependency_names``'s own docstring on the two exemptions
     that do NOT apply to this standalone-node call) gets started via
     ``node_start.start_node_now`` — dispatched if agent-owned and under
     today's quota, else routed to the M3 confirm-gate "upshot" path.
  3. Cascade pass: loop ``execute_advance`` (the SAME predicate `preview`/
     `execute` share — #1400, never a parallel advance path) until it either
     can't advance any further or hits a real gate (review/deliverable/
     form/deps) — notified ONCE per (node, reason) via a metadata stamp,
     never re-notified while the same block persists across ticks.
  4. Steps 2 and 3 repeat until neither changes anything: a cascade that
     opens a new group can make that group's own ``auto_start`` nodes
     eligible, and no nested tick will notice (the re-entrancy guard
     suppresses ``execute_advance``'s tail enqueue during a cascade). See
     ``_autopilot_tick_impl``.

Hard line: the review gate is NEVER touched here. Nothing in this module (or
in the ``execute_advance`` it calls) can move a node ``in_review`` → ``done``
— that transition is human/manager-only (``issues_router``'s owner-review
guard). Autopilot only ever starts work earlier; it never finishes it.
"""

from __future__ import annotations

import contextvars
import time
from typing import Any, Dict, List, Optional

from dbos import DBOS
from loguru import logger

from app.schemas.workflow import (
    BLOCK_DELIVERABLE_MISSING,
    BLOCK_DEPS_PENDING,
    BLOCK_FORM_INCOMPLETE,
    BLOCK_NOT_MANAGER_OR_EDITOR,
    BLOCK_REVIEW_PENDING,
)

# Reused verbatim (never re-implemented) — brief's explicit "REUSE, don't
# reimplement" instruction for the dependency-satisfaction predicate.
from app.services.workflow.advance_service import _unmet_dependency_names
from app.services.workflow.node_start import start_node_now

_DEFAULT_DAILY_AUTO_RUNS = 20
_QUOTA_CACHE_TTL_S = 60.0
_quota_cache_value: Optional[int] = None
_quota_cache_at: float = 0.0

# blocked_reason values worth a human notification — BLOCK_NO_NEXT (workflow
# simply ended) and BLOCK_NOT_MANAGER_OR_EDITOR (no eligible actor to advance
# on) are both silent no-ops, not gates a human needs to go unblock.
_CASCADE_NOTIFY_REASONS = frozenset(
    {
        BLOCK_REVIEW_PENDING,
        BLOCK_DELIVERABLE_MISSING,
        BLOCK_FORM_INCOMPLETE,
        BLOCK_DEPS_PENDING,
    }
)

_CASCADE_BLOCKED_STAMP_KEY = "autopilot_cascade_blocked_reason"

_BLOCK_LABEL: Dict[str, str] = {
    BLOCK_REVIEW_PENDING: "waiting on review",
    BLOCK_DELIVERABLE_MISSING: "waiting on a deliverable file",
    BLOCK_FORM_INCOMPLETE: "waiting on required form fields",
    BLOCK_DEPS_PENDING: "waiting on a dependency",
}

# Cascade bound — a real workflow has a handful of groups; this only guards
# against a pathological/cyclic template turning the loop unbounded.
_MAX_CASCADE_STEPS = 50

# Bound on the tick's (auto-start → cascade) fixpoint loop. Real usage settles
# in two iterations (one that does work, one that confirms there is no more);
# anything beyond that is a pathological template, so cap and flag it rather
# than spin.
_MAX_TICK_PASSES = 10

# Re-entrancy guard (brief: "级联推进...幂等防重入"). ``_cascade_pass`` calls
# ``execute_advance`` directly (in-process, same task) rather than enqueueing
# a fresh tick per step — but ``execute_advance`` ALSO enqueues a tick on
# every successful forward arrival (so a MANUAL/router-triggered advance
# still kicks the engine). Without this guard, each of the cascade's own
# internal ``execute_advance`` calls would enqueue a REDUNDANT extra tick —
# not incorrect (every one of those extra ticks is idempotent-cheap-no-op),
# just a wasteful queue storm proportional to cascade depth. Set for the
# duration of ``_cascade_pass``'s loop; ``advance_service``'s enqueue call
# checks it and skips while it's active — the ORIGINAL tick's own loop is
# already handling every step, so nothing is lost by skipping.
_cascade_active: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "autopilot_cascade_active", default=False
)


def cascade_in_progress() -> bool:
    """True while THIS task is inside ``_cascade_pass``'s advance loop —
    see the re-entrancy-guard note above. Consulted by
    ``advance_service``'s post-arrival tick-enqueue call."""
    return _cascade_active.get()


async def _daily_auto_runs_limit() -> int:
    """The project-agnostic daily auto-dispatch quota (design spec §1:
    ``system_settings['workflow_autopilot'] = {"daily_auto_runs": 20}``),
    60s-cached (spec §1) so a hot admin edit takes effect quickly without
    hitting the DB on every candidate node. Any read failure falls back to
    the last good cached value, else the hardcoded default — a settings
    hiccup must never block autopilot from working at all."""
    global _quota_cache_value, _quota_cache_at

    now = time.time()
    if _quota_cache_value is not None and (now - _quota_cache_at) < _QUOTA_CACHE_TTL_S:
        return _quota_cache_value

    limit = (
        _quota_cache_value
        if _quota_cache_value is not None
        else (_DEFAULT_DAILY_AUTO_RUNS)
    )
    try:
        from sqlalchemy import select

        from app.db.session import read_scope
        from app.models import SystemSettings

        async with read_scope() as session:
            row = (
                await session.execute(
                    select(SystemSettings.value)
                    .where(SystemSettings.key == "workflow_autopilot")
                    .limit(1)
                )
            ).first()
        if row is not None and isinstance(row[0], dict):
            limit = int(row[0].get("daily_auto_runs", _DEFAULT_DAILY_AUTO_RUNS))
        elif row is None:
            limit = _DEFAULT_DAILY_AUTO_RUNS
    except Exception as exc:  # noqa: BLE001 — degrade to cached/default, never raise
        logger.warning(f"[autopilot] daily_auto_runs read failed: {exc!r}")

    _quota_cache_value = limit
    _quota_cache_at = now
    return limit


def _eligible_auto_start_candidates(
    nodes: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Every non-skipped, not-yet-started node with ``events.auto_start``,
    ordered by ``sort_order`` (stable, matches the Stage Board's node order)."""
    ordered = sorted(nodes, key=lambda n: n.get("sort_order", 0))
    return [
        n
        for n in ordered
        if not n.get("skipped")
        and n.get("status") == "pending"
        and (n.get("events") or {}).get("auto_start")
    ]


async def _auto_start_pass(project_id: str, project: Dict[str, Any]) -> None:
    """Spec §2 step 2. Re-fetches the live node list fresh (idempotency: a
    node another tick already started is no longer ``status == 'pending'``,
    so it silently drops out of the candidate list on the next call)."""
    from app.repositories.project_stage_nodes_repository import (
        get_project_stage_nodes_repository,
    )

    nodes_repo = get_project_stage_nodes_repository()
    nodes = await nodes_repo.list_nodes(str(project_id))
    node_by_id = {str(n["id"]): n for n in nodes}
    candidates = _eligible_auto_start_candidates(nodes)
    if not candidates:
        return

    from app.repositories.agent_runs_repository import get_agent_runs_repository

    quota_limit = await _daily_auto_runs_limit()
    used = await get_agent_runs_repository().count_auto_dispatches_today(
        str(project_id)
    )

    for node in candidates:
        # A start-ahead node's deps must ACTUALLY be done/skipped — no
        # co-arrival/closing-group exemptions (those exist for a GROUP's
        # forward advance, not a standalone future node; see
        # _unmet_dependency_names's docstring).
        unmet = _unmet_dependency_names([node], node_by_id, exempt_ids=set())
        if unmet:
            continue

        dispatch = False
        prepare_title: Optional[str] = None
        if node.get("owner_agent_id"):
            if used < quota_limit:
                dispatch = True
                used += 1  # in-process running count — the agent_runs row
                # this dispatch produces is written asynchronously by the
                # DBOS workflow it kicks off, so a same-tick re-query of the
                # DB would race it; counting in-process keeps the 20→21
                # boundary correct within one tick regardless of that lag.
            else:
                node_name = node.get("name") or "Stage"
                prepare_title = f'Autopilot paused: daily limit reached — "{node_name}"'

        try:
            await start_node_now(
                str(project_id),
                node,
                actor_user_id=None,
                dispatch=dispatch,
                dispatch_auto=True,
                prepare_title=prepare_title,
            )
        except Exception as exc:  # noqa: BLE001 — one node's failure never
            # blocks the rest of the pass or the tick that called it.
            logger.warning(
                f"[autopilot] auto-start failed for project {project_id} "
                f"node {node.get('id')}: {exc!r}"
            )


def _recipients(node: Dict[str, Any]) -> set[str]:
    recipients: set[str] = set()
    owner_user_id = node.get("owner_user_id")
    if owner_user_id:
        recipients.add(str(owner_user_id))
    for member in node.get("members") or []:
        member_user_id = member.get("user_id")
        if member_user_id:
            recipients.add(str(member_user_id))
    return recipients


async def _notify_cascade_blocked_once(
    project_id: str, project: Dict[str, Any], preview: Any
) -> None:
    """Notify the blocked group's owner/members ONCE per (node, reason) —
    a metadata stamp (mirroring ``stage_hook``'s ``run_prepared_at`` idiom)
    dedupes across repeated ticks while the same block persists. A LATER
    block for a DIFFERENT reason on the same node still notifies (the stamp
    compares the reason value, not just presence).

    Anchor node: ``preview.closing[0]`` — the group that's DONE with its own
    work but can't hand off. For REVIEW_PENDING/DELIVERABLE_MISSING/
    FORM_INCOMPLETE this is also the node the gate is actually about (those
    three gate the CLOSING group's own completion). For DEPS_PENDING it
    gates the NEXT group's readiness instead (``AdvancePreview`` carries no
    node-ref list for that side, only ``waiting_on`` name strings) — the
    closing group's owner is still the right person to tell "your next step
    can't start yet", so the anchor/recipients stay the same; only the
    copy differs (naming the actual waiting-on dependency, not the anchor).

    Review fix I3: the notify() call is tagged with the anchor node's mirror
    issue (``link_kind="issue"``/``link_id=identifier``, same as
    ``stage_hook._notify_prepared``) — NOT link-less. ``notify()`` itself
    dedupes on ``(user_id, kind, link_kind, link_id)`` within a 10-minute
    window; a link-less call would collide with ANY other link-less
    ``workflow_stage`` notification (e.g. a cascade block on a different
    node/project for the same recipient) and get silently swallowed by that
    UNRELATED dedupe — while this function had already written its own
    "notified" stamp, so the real event would never be retried. Tagging the
    link makes the dedupe key specific to THIS node's event, matching the
    stamp's own specificity.
    """
    if not preview.closing:
        return
    from app.repositories.project_stage_nodes_repository import (
        get_project_stage_nodes_repository,
    )
    from app.services.notifications import notify
    from app.services.workflow.advance_service import _first_issue_identifier

    nodes_repo = get_project_stage_nodes_repository()
    node_ref = preview.closing[0]
    node = await nodes_repo.get_node(node_ref.node_id, str(project_id))
    if node is None:
        return

    metadata = node.get("metadata") or {}
    if metadata.get(_CASCADE_BLOCKED_STAMP_KEY) == preview.blocked_reason:
        return  # already notified for this exact block — idempotent

    recipients = _recipients(node)
    if recipients:
        project_name = project.get("name") or "Project"
        node_name = node.get("name") or "Stage"
        label = _BLOCK_LABEL.get(preview.blocked_reason, preview.blocked_reason)
        if preview.blocked_reason == BLOCK_DEPS_PENDING and preview.waiting_on:
            label = f"waiting on: {', '.join(preview.waiting_on)}"
        title = f'Autopilot paused — "{node_name}" {label} — {project_name}'
        identifier = await _first_issue_identifier(str(project_id), node_ref.node_id)
        for user_id in recipients:
            await notify(
                user_id,
                "workflow_stage",
                title,
                body=None,
                link_kind="issue" if identifier else None,
                link_id=identifier,
            )

    await nodes_repo.set_node_metadata(
        node_ref.node_id, {_CASCADE_BLOCKED_STAMP_KEY: preview.blocked_reason}
    )


async def _cascade_pass(project_id: str, project: Dict[str, Any]) -> bool:
    """Spec §2 step 3. Loops ``execute_advance`` — the SAME predicate
    ``compute_advance_preview``/``execute_advance`` share (#1400) — until
    blocked or there's nothing left to advance into. Never a parallel
    advance path.

    The acting user for this system-driven advance is the project owner
    (there is no human actor for an automated cascade) — same "acts on the
    owner's behalf" convention W3c's rule_owner attribution uses elsewhere.
    No owner on the project row → nothing this tick can safely advance.

    Returns True when at least one group actually advanced, so
    ``_autopilot_tick_impl`` knows to re-run the auto-start pass over the
    group this cascade just opened (see its own docstring). The ceiling-
    exhaustion path below returns False on purpose: that case is already
    flagged as pathological, and letting the caller loop it again would only
    multiply the damage.
    """
    from app.services.workflow.advance_service import execute_advance

    owner_id = project.get("owner_id")
    if not owner_id:
        return False
    actor_user_id = str(owner_id)

    advanced = False
    token = _cascade_active.set(True)
    try:
        for _ in range(_MAX_CASCADE_STEPS):
            try:
                preview = await execute_advance(
                    str(project_id), actor_user_id, "forward"
                )
            except Exception as exc:  # noqa: BLE001 — cascade is best-effort
                logger.warning(
                    f"[autopilot] cascade advance failed for project "
                    f"{project_id}: {exc!r}"
                )
                return advanced
            if preview.will_advance:
                advanced = True
                continue  # a new group just arrived — re-evaluate it too
            if preview.blocked_reason == BLOCK_NOT_MANAGER_OR_EDITOR:
                # Review fix I5: this stalls the cascade silently (no
                # notification — there's no human action to point at, the
                # actor itself has no role) but was ALSO silent in the logs,
                # making a permanently-stuck project's autopilot invisible.
                # The underlying gap (resolve_effective_role has no owner-
                # special-case for a personal/team_id-null project — see
                # task-O2-report.md Concerns) is NOT fixed here — out of
                # scope for this guardrail pass — just made discoverable.
                logger.warning(
                    f"[autopilot] cascade stalled for project {project_id}: "
                    "the acting owner has no resolvable manager/editor role "
                    "(BLOCK_NOT_MANAGER_OR_EDITOR) — autopilot cannot advance "
                    "this project until that's fixed"
                )
            elif preview.blocked_reason in _CASCADE_NOTIFY_REASONS:
                try:
                    await _notify_cascade_blocked_once(project_id, project, preview)
                except Exception as exc:  # noqa: BLE001 — notify is enrichment only
                    logger.warning(
                        f"[autopilot] cascade-blocked notify failed for project "
                        f"{project_id}: {exc!r}"
                    )
            return advanced
        else:
            # Loop exhausted _MAX_CASCADE_STEPS without ever blocking — every
            # single step advanced. A real template has a handful of groups;
            # this many CONSECUTIVE successful advances in one tick is almost
            # certainly a cyclic/pathological template, not real usage
            # (adjacent minor fix — flagged, not auto-remediated).
            logger.warning(
                f"[autopilot] cascade for project {project_id} hit the "
                f"{_MAX_CASCADE_STEPS}-step ceiling without ever blocking — "
                "possible cyclic/pathological workflow template"
            )
            return False
    finally:
        _cascade_active.reset(token)


async def _autopilot_tick_impl(project_id: str) -> None:
    """One tick: re-checks ``autopilot_enabled`` fresh, then runs the
    (auto-start pass → cascade pass) sequence to a fixpoint. Fully idempotent
    — safe to call any number of times for the same project, concurrently or
    not.

    Why a LOOP and not one pass of each: a cascade that opens a new group can
    itself make that group's ``auto_start`` nodes eligible, and nothing else
    will notice within this tick. ``execute_advance``'s own tail enqueue is
    (correctly) suppressed by the ``cascade_in_progress()`` re-entrancy guard,
    so no nested tick re-checks the newly-opened group; the node then sits
    ``pending`` until an unrelated later trigger happens to fire. Re-running
    the auto-start pass after every cascade that actually advanced closes that
    gap in-tick. Each pass re-reads live state, so a run that changes nothing
    converges immediately (the common case is exactly two iterations: work,
    then a no-op confirmation).
    """
    from app.repositories.projects_repository import get_projects_repository

    project = await get_projects_repository().get_project_by_id(int(str(project_id)))
    if project is None:
        return
    if not project.get("autopilot_enabled", True):
        return

    for _ in range(_MAX_TICK_PASSES):
        await _auto_start_pass(project_id, project)
        if not await _cascade_pass(project_id, project):
            return
    logger.warning(
        f"[autopilot] tick for project {project_id} hit the "
        f"{_MAX_TICK_PASSES}-pass ceiling without reaching a fixpoint — "
        "possible cyclic/pathological workflow template"
    )


@DBOS.workflow()
async def autopilot_tick(project_id: str) -> None:
    """Thin DBOS shell over ``_autopilot_tick_impl`` — see module docstring."""
    await _autopilot_tick_impl(project_id)


async def enqueue_autopilot_tick(project_id: str) -> None:
    """Best-effort enqueue of ``autopilot_tick`` for one project.

    Called from every arrival/completion hook (``issue_repository.
    _fire_stage_node_sync``, ``advance_service.execute_advance``,
    ``instantiation.instantiate_project_workflow``) plus the 5-minute
    scheduled sweep. Deliberately NOT given a pinned/deterministic
    ``workflow_id`` (unlike ``stage_hook_dispatch``'s per-node pin) — a tick
    takes no node-specific argument to key a dedup id off, and EVERY new
    trigger must actually re-run (state may have changed since the last
    tick); idempotency instead comes from the impl re-reading current state
    at each step. Never raises — an enqueue failure must never block the
    arrival/completion flow that triggered it.
    """
    try:
        from app.services.infra.dbos_orchestrator import start_workflow_routed

        await start_workflow_routed(
            "autopilot_tick",
            dbos_workflow_callable=autopilot_tick,
            dbos_workflow_kwargs={"project_id": str(project_id)},
        )
    except Exception as exc:  # noqa: BLE001 — best-effort, never blocks the caller
        logger.warning(f"[autopilot] enqueue failed for project {project_id}: {exc!r}")
