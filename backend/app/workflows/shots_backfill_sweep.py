"""shots_backfill_sweep — index the backlog of videos a few at a time.

Every 10 minutes (spec 2026-09-26 §3.3): when ``ai_module.shots.backfill``
allows it (``always``, or ``local_only`` with the visual embedder on
nous-engine), dispatch up to ``backfill_batch`` ``index_shots`` runs for
videos — any user's — that have no frame vector in the VISUAL layer's space
(or were cut by an older algorithm), oldest resource first. Backpressure:
nothing new while that many are still queued / running. A network provider
also stops at ``backfill_daily_cap`` dispatches per UTC day.

路线 C：each video is still one ``index_shots`` task row with its own
typed failure; the sweeper only counts dispatches. Its bookkeeping lives in
the ``ai_module.shots.backfill_state`` setting (read by the Vectors panel).

The dispatch (``start_workflow_routed``) runs in the workflow BODY, not in
a step — same constraint as ``download.chain_followups_step``. Registered in
``_scheduled_bundle`` (worker-only role).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List

from dbos import DBOS
from loguru import logger

from app.db.scope import system_request_scope

#: One dispatch attempt's error, clipped for the state row.
_ERROR_CLIP = 200


@DBOS.step()
async def plan_shots_backfill_step() -> Dict[str, Any]:
    """Decide this tick: ``{"skip": reason}`` or ``{"rows": [...], ...}``.
    Every read (policy, provider, visual space, active count, pending page)
    happens here; nothing is dispatched."""
    # Cross-user reads (every user's videos, every user's tasks): a DBOS step
    # has no request scope, and under SCOPE_ENFORCE_RESOURCES an unscoped
    # ``resources`` read is either refused or empty — the 2026-09-26 tick read
    # 0 pending against a 1,100-video backlog. System scope names the intent.
    async with system_request_scope("shots backfill sweep: cross-user pending videos"):
        return await _plan()


async def _plan() -> Dict[str, Any]:
    from app.repositories.resource_embeddings_repository import EmbeddingStoreMissing
    from app.repositories.video_shots_repository import (
        FRAME_KIND,
        get_video_shot_embeddings_repository,
    )
    from app.services.infra.unified_task_manager import get_task_manager
    from app.services.library.shot_cut import ALGO_VERSION
    from app.services.library.shot_policy import (
        dispatch_decision,
        read_backfill_state,
    )
    from app.workflows.index_shots import TASK_TYPE

    decision = await dispatch_decision("backfill")
    state = await read_backfill_state()
    base = {
        "provider_local": decision.provider_local,
        "dispatched_today": state.dispatched_today,
    }
    if not decision.dispatch:
        return {**base, "skip": decision.reason, "detail": decision.detail}
    policy = decision.policy
    active = await get_task_manager().count_active_by_type(TASK_TYPE)
    if active >= policy.batch:
        return {**base, "skip": "backpressure", "detail": f"active={active}"}
    room = policy.batch - active
    if decision.provider_local is not True and policy.daily_cap > 0:
        left = policy.daily_cap - state.dispatched_today
        if left <= 0:
            return {**base, "skip": "daily_cap", "detail": f"cap={policy.daily_cap}"}
        room = min(room, left)
    try:
        rows, total = await get_video_shot_embeddings_repository().pending_all(
            space_id=decision.space_id,
            kind=FRAME_KIND,
            algo_version=ALGO_VERSION,
            limit=room,
        )
    except EmbeddingStoreMissing as e:
        return {**base, "skip": "store_missing", "detail": str(e)[:_ERROR_CLIP]}
    if not rows:
        return {**base, "skip": "nothing_pending", "pending_total": total}
    return {
        **base,
        "pending_total": total,
        "space_id": decision.space_id,
        "rows": [
            {
                "resource_id": str(r.resource_id),
                "user_id": r.user_id,
                "title": r.title,
                "reason": r.reason,
            }
            for r in rows
        ],
    }


@DBOS.step()
async def record_shots_backfill_step(
    *, dispatched: int, error: str | None, skip: str | None
) -> None:
    """Roll the day, add this tick's dispatches, stamp the tick."""
    from app.services.library.shot_policy import (
        BackfillState,
        now_utc_iso,
        read_backfill_state,
        write_backfill_state,
    )

    state = await read_backfill_state()
    await write_backfill_state(
        BackfillState(
            day=state.day,
            dispatched_today=state.dispatched_today + dispatched,
            last_tick=now_utc_iso(),
            last_error=error,
            last_skip=skip,
        )
    )


async def _dispatch_rows(rows: List[Dict[str, Any]]) -> tuple[int, str | None]:
    """Dispatch each row; one failure does not stop the rest. Returns
    ``(dispatched, last error)``."""
    from app.services.library.shot_dispatch import dispatch_index_shots

    dispatched, error = 0, None
    for row in rows:
        try:
            await dispatch_index_shots(
                user_id=row["user_id"],
                resource_id=row["resource_id"],
                title=row["title"],
                flow_id=None,
            )
            dispatched += 1
        except Exception as e:  # noqa: BLE001 — recorded, the loop goes on
            error = f"{row['resource_id']}: {type(e).__name__}: {e}"[:_ERROR_CLIP]
            logger.error(f"[shots_backfill] dispatch {row['resource_id']}: {e}")
    return dispatched, error


@DBOS.scheduled("*/10 * * * *")  # every 10 minutes
@DBOS.workflow()
async def shots_backfill_sweep_workflow(
    scheduled_time: datetime, actual_time: datetime
) -> None:
    """One tick: plan (step) → dispatch (body) → record (step)."""
    plan = await plan_shots_backfill_step()
    dispatched, error = await _dispatch_rows(plan.get("rows") or [])
    await record_shots_backfill_step(
        dispatched=dispatched, error=error, skip=plan.get("skip")
    )
    if dispatched or error:
        logger.info(
            f"[shots_backfill] tick: dispatched={dispatched} "
            f"pending_total={plan.get('pending_total')} error={error}"
        )


__all__ = [
    "plan_shots_backfill_step",
    "record_shots_backfill_step",
    "shots_backfill_sweep_workflow",
]
