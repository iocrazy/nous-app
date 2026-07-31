"""autopilot_sweep — 5-minute backstop sweep for the M4 Autopilot engine
(design spec §2 trigger 3, task O2).

The three arrival/completion hooks (``issue_repository._fire_stage_node_sync``,
``advance_service.execute_advance``, ``instantiation.
instantiate_project_workflow``) enqueue ``autopilot_tick`` best-effort — if one
of those enqueues is lost (a worker restart mid-flight, a transient DB hiccup
on the enqueue call itself), the affected project's ``auto_start`` nodes would
otherwise sit forever. This sweep is the backstop: every 5 minutes, find every
``autopilot_enabled`` project that currently has an eligible (pending,
non-skipped, ``events.auto_start``) node and re-enqueue its tick — the tick
itself re-checks everything from scratch, so a redundant sweep-triggered tick
for a project whose hooks already handled it is simply a fast no-op.

Registered in ``_scheduled_bundle`` (worker-only role — the gateway must NOT
import this module, same rationale as every other ``@DBOS.scheduled`` sweeper
in this package, see that bundle's docstring).
"""

from __future__ import annotations

from datetime import datetime

from dbos import DBOS
from loguru import logger

# Bounded per tick so one runaway sweep can't fan out unbounded work — a
# handful of concurrently-active auto-start projects is the realistic
# ceiling; this is a generous multiple of that.
_BATCH_SIZE = 200

_ELIGIBLE_PROJECTS_SQL = """
SELECT DISTINCT p.id
FROM public.projects p
JOIN public.project_stage_nodes n ON n.project_id = p.id
WHERE p.autopilot_enabled = true
  AND n.status = 'pending'
  AND n.skipped = false
  AND n.events->>'auto_start' = 'true'
LIMIT :limit
"""
# Plain string equality, NOT ``(n.events->>'auto_start')::boolean`` (review
# adjacent-minor fix): a ``::boolean`` cast RAISES on any row whose value
# isn't one of Postgres's recognized boolean literals, and — unlike a
# per-row filter mismatch — that raise aborts the ENTIRE query, so one dirty
# row (a hand-edited events blob, a future migration bug) would take down
# the global sweep for every OTHER project too. Pydantic's
# ``WorkflowNodeEvents.auto_start: bool`` always serializes as the JSON
# literal ``true``/``false``, so string equality never loses a real match —
# it degrades a bad row to "not eligible" instead of a global 500.


@DBOS.step()
async def sweep_autopilot_projects_step() -> dict:
    """Find + re-enqueue a tick for every eligible project. Returns
    ``{"projects": N}`` for the tick log. Gracefully skips when Supavisor
    isn't configured (dev/CI) instead of erroring every 5 minutes."""
    from app.db import engine as db_engine

    if not db_engine.is_configured():
        return {"projects": 0}

    try:
        rows = await db_engine.fetch_all(_ELIGIBLE_PROJECTS_SQL, {"limit": _BATCH_SIZE})
    except Exception as exc:  # noqa: BLE001 — a scan failure must not crash the sweep
        logger.warning(f"[autopilot_sweep] eligible-project scan failed: {exc!r}")
        return {"projects": 0}

    project_ids = [str(r["id"]) for r in rows or []]
    for project_id in project_ids:
        try:
            from app.workflows.autopilot import enqueue_autopilot_tick

            await enqueue_autopilot_tick(project_id)
        except Exception as exc:  # noqa: BLE001 — one project's enqueue failure
            # must never stop the sweep from covering the rest.
            logger.warning(
                f"[autopilot_sweep] enqueue failed for project {project_id}: {exc!r}"
            )
    return {"projects": len(project_ids)}


@DBOS.scheduled("*/5 * * * *")  # every 5 minutes
@DBOS.workflow()
async def autopilot_sweep_workflow(
    scheduled_time: datetime, actual_time: datetime
) -> None:
    """One sweep tick. DBOS dedups via the standard `sched-<name>-<iso>`
    workflow_id, so cluster-wide only one worker fires per scheduled time."""
    counters = await sweep_autopilot_projects_step()
    if counters.get("projects"):
        logger.info(f"[autopilot_sweep] tick: {counters}")
