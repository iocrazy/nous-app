"""Run 撤销执行（IO 层）：逐 shot CAS 删/复原 + scene 选择性回滚 + B4 scene 级回流
(spec §4.4). Consumes the pure planning logic in ``run_undo_logic`` (Task 3).

Not an agent-tool path: the caller is the human-triggered
``/runs/{run_id}/undo`` REST endpoint (Task 5), which has already checked
``agent_runs.user_id`` ownership and CAS'd ``undone_at`` for idempotency.
Every id this module touches comes off the server's own ledgers
(``script_shot_ops`` / ``script_ops``) via the run id — it never accepts a
model- or user-supplied scene/shot id, which is why it is exempt from the
scope_resolver choke point (see the two allowlist entries this task adds to
``tests/test_scope_resolver_single_choke_point.py``).
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import delete, func, select, update

from app.db.session import read_scope, write_scope
from app.models.scripts import ScriptOps, ScriptShotOps, ScriptShots
from app.repositories.script_scene_repository import (
    VersionConflict,
    get_script_scene_repository,
)
from app.services.ai.undo.run_undo_logic import (
    ShotUndoPlan,
    merge_shot_ops,
    scene_undo_plan,
)
from app.services.workflow.surface_completion import fire_surface_sync_for_scene

logger = logging.getLogger(__name__)


async def execute_undo(run_id: int) -> dict[str, Any]:
    """Execute the full undo for a run (permission/idempotency are the
    caller's job — see module docstring). Returns spec §4.4's report body
    (no ``status`` field; the endpoint adds that):
    ``{"shots_deleted": int, "shots_reverted": int,
       "scene_elements_reverted": int, "skipped": [...]}``
    with ``reason`` in ``skipped`` one of ``edited_after_run`` /
    ``rendered`` / ``version_conflict``, and every id a ``str`` (snowflake
    precision discipline).
    """
    report: dict[str, Any] = {
        "shots_deleted": 0,
        "shots_reverted": 0,
        "scene_elements_reverted": 0,
        "skipped": [],
    }
    await _undo_shots(run_id, report)
    await _undo_scenes(run_id, report)
    return report


async def _undo_shots(run_id: int, report: dict[str, Any]) -> None:
    async with read_scope() as session:
        result = await session.execute(
            select(ScriptShotOps)
            .where(ScriptShotOps.run_id == run_id)
            .order_by(ScriptShotOps.created_at.asc(), ScriptShotOps.id.asc())
        )
        rows = [
            {
                "id": r.id,
                "shot_id": r.shot_id,
                "scene_id": r.scene_id,
                "action": r.action,
                "before_json": r.before_json,
                "after_json": r.after_json,
                "created_at": r.created_at,
            }
            for r in result.scalars().all()
        ]

    plans = merge_shot_ops(rows)
    for plan in plans:
        # Per-plan isolation: each plan gets its own write_scope transaction,
        # and an unexpected failure on one plan must never lose the report
        # for the others already accumulated.
        try:
            if plan.kind == "delete":
                await _undo_delete_shot(plan, report)
            else:
                await _undo_revert_shot(plan, report)
        except Exception:  # noqa: BLE001
            logger.error(
                "run %s undo: shot %s unexpected failure",
                run_id,
                plan.shot_id,
                exc_info=True,
            )
            report["skipped"].append(
                {
                    "kind": "shot",
                    "id": str(plan.shot_id),
                    "reason": "edited_after_run",
                }
            )


def _expected_conds(plan: ShotUndoPlan) -> list:
    conds = []
    for field, value in plan.expected.items():
        col = getattr(ScriptShots, field)
        conds.append(col.is_(None) if value is None else col == value)
    return conds


async def _undo_delete_shot(plan: ShotUndoPlan, report: dict[str, Any]) -> None:
    conds = [
        ScriptShots.id == plan.shot_id,
        ScriptShots.status == "empty",
        func.coalesce(ScriptShots.image_url, "") == "",
        func.coalesce(ScriptShots.thumbnail_url, "") == "",
        func.coalesce(ScriptShots.video_url, "") == "",
        *_expected_conds(plan),
    ]
    deleted = False
    reason = None
    async with write_scope() as session:
        result = await session.execute(delete(ScriptShots).where(*conds))
        deleted = result.rowcount > 0
        if not deleted:
            # Fetch AFTER the failed CAS (not before) — the miss reason must
            # reflect the row's state at the moment the delete was rejected.
            fetch = await session.execute(
                select(ScriptShots).where(ScriptShots.id == plan.shot_id)
            )
            row = fetch.scalars().first()
            if row is None:
                reason = "edited_after_run"
            elif (
                row.status != "empty"
                or row.image_url
                or row.thumbnail_url
                or row.video_url
            ):
                reason = "rendered"
            else:
                reason = "edited_after_run"

    if deleted:
        report["shots_deleted"] += 1
        # Scene version, never the shot version: the card is gone, so the
        # shot-dimension `_scope_for_shot` JOIN would resolve to nothing and
        # the sync would silently no-op (fire_* never raises, so it wouldn't
        # even surface as an error).
        await fire_surface_sync_for_scene(str(plan.scene_id), surfaces=("storyboard",))
    else:
        report["skipped"].append(
            {"kind": "shot", "id": str(plan.shot_id), "reason": reason}
        )


async def _undo_revert_shot(plan: ShotUndoPlan, report: dict[str, Any]) -> None:
    conds = [ScriptShots.id == plan.shot_id, *_expected_conds(plan)]
    async with write_scope() as session:
        result = await session.execute(
            update(ScriptShots).where(*conds).values(**plan.restore)
        )
        reverted = result.rowcount > 0

    # revert 不触回流：涉及字段全是创作参数（shot_type/camera_*/...），不影响
    # done 判据。
    if reverted:
        report["shots_reverted"] += 1
    else:
        report["skipped"].append(
            {"kind": "shot", "id": str(plan.shot_id), "reason": "edited_after_run"}
        )


async def _undo_scenes(run_id: int, report: dict[str, Any]) -> None:
    actor = f"agent:{run_id}"
    async with read_scope() as session:
        result = await session.execute(
            select(ScriptOps.scene_id).where(ScriptOps.actor == actor).distinct()
        )
        scene_ids = [row[0] for row in result.all()]

    repo = get_script_scene_repository()
    for scene_id in scene_ids:
        try:
            ops_rows = await repo.list_ops_by_scene(str(scene_id))
            plan = scene_undo_plan(scene_id, ops_rows, actor)
            if plan.inverse_ops:
                await repo.apply_element_ops(
                    str(scene_id),
                    plan.inverse_ops,
                    expected_version=plan.expected_version,
                    actor=f"undo:{run_id}",
                )
                report["scene_elements_reverted"] += len(plan.undone_element_ids)
            for eid in plan.skipped_element_ids:
                report["skipped"].append(
                    {"kind": "scene_element", "id": eid, "reason": "edited_after_run"}
                )
        except VersionConflict:
            # At-most-once, same as agent writes: no retry on a lost race.
            report["skipped"].append(
                {"kind": "scene", "id": str(scene_id), "reason": "version_conflict"}
            )
        except Exception:  # noqa: BLE001
            logger.error(
                "run %s undo: scene %s unexpected failure",
                run_id,
                scene_id,
                exc_info=True,
            )
            report["skipped"].append(
                {"kind": "scene", "id": str(scene_id), "reason": "edited_after_run"}
            )
