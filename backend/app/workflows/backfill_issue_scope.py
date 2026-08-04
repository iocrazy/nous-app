"""backfill_issue_scope DBOS workflow — repair issues written before PR #1423.

THE BACKFILL PARADIGM (established 2026-07-17, copy this module as the
template): data backfills run as DBOS workflows, never as hand-run SQL.
That buys, for free via the ``mirror_dbos_lifecycle_to_tracking`` trigger:
a Task Center row anyone can see, phase/progress tracking, a metadata
audit of exactly what changed, and DBOS retry/error semantics. The cost is
one module + one admin endpoint — this file is the worked example.

What this instance repairs (two holes fixed forward by PR #1423):

1. **Canvas-origin issues with no scope** — ``CanvasCardMenu`` used to
   create issues with no ``team_id``/``project_id`` at all, so they never
   appear on the team-filtered list. The canvas's own project (and that
   project's team) is the ground truth; recovered via ``origin_id``
   (``canvas:{id}``).

2. **Float64-rounded team/project ids** — ``Number(teamId)`` in the UI
   rounded 19-digit Snowflakes past 2^53 before create, storing an id
   that matches no real team. The true id is recoverable when exactly ONE
   live team/project rounds to the stored value (float64 rounding is a
   pure function); ambiguous or unmatched values are reported, never
   guessed.

Design (mirrors ``storage_migration``): row-wise idempotent, no
``@DBOS.step`` checkpointing — every UPDATE re-checks its own precondition
(``team_id IS NULL`` / ``team_id = :bad``), so replaying a crashed run
just re-skips repaired rows. ``dry_run`` computes and reports everything
with zero DB mutation. Failures raise (CLAUDE.md 路线 C rule 4) — never a
returned failed dict.
"""

from __future__ import annotations

from typing import Any, Optional

from dbos import DBOS
from loguru import logger
from sqlalchemy import func, select, update
from sqlalchemy.orm import InstrumentedAttribute

from app.db.session import read_scope, write_scope
from app.models import Canvases, Issues, Projects, Teams

# Same system-batch convention as storage_migration / topic_scorer:
# task_tracking.user_id is UUID NOT NULL and no single end user owns a
# backfill.
SYSTEM_RUN_USER_ID = "00000000-0000-0000-0000-000000000000"

# Cap the per-run audit list persisted into task metadata (jsonb) — counts
# are always exact; only the id list is truncated.
_AUDIT_IDS_CAP = 200


# ── Pure helpers (unit-tested in tests/test_backfill_issue_scope.py) ────


def parse_canvas_origin(origin_id: Optional[str]) -> Optional[str]:
    """``canvas:{digits}`` → the canvas id string, else None.

    Deliberately digits-only: origin_id is free TEXT and legacy rows hold
    other shapes (routine schedule ids, ``scene:{id}``); a malformed value
    must be skipped, never CAST-crashed inside SQL.
    """
    if not origin_id or not origin_id.startswith("canvas:"):
        return None
    raw = origin_id.split(":", 1)[1]
    return raw if raw.isdigit() else None


def rounded_candidates(stored: int, real_ids: list[int]) -> list[int]:
    """Real Snowflake ids whose float64 rounding equals ``stored``.

    JS ``Number('9007199254740993')`` and Python ``float(9007199254740993)``
    perform the same IEEE-754 rounding, so ``int(float(real)) == stored``
    reproduces exactly what the buggy UI wrote. Only a single-candidate
    match is safe to repair; callers must treat 0 or 2+ as report-only.
    """
    return [rid for rid in real_ids if int(float(rid)) == stored]


# ── Workflow ────────────────────────────────────────────────────────────


async def _repair_null_scope(dry_run: bool, limit: int, result: dict[str, Any]) -> None:
    """Category 1: canvas-origin issues whose team_id is NULL."""
    async with read_scope() as session:
        rows = (
            (
                await session.execute(
                    select(Issues.id, Issues.origin_id)
                    .where(Issues.origin_id.like("canvas:%"), Issues.team_id.is_(None))
                    .order_by(Issues.id)
                    .limit(limit)
                )
            )
            .mappings()
            .all()
        )
    fixed_ids: list[int] = []
    for row in rows:
        canvas_id = parse_canvas_origin(row.get("origin_id"))
        if canvas_id is None:
            result["null_scope_malformed"] += 1
            continue
        async with read_scope() as session:
            scope = (
                (
                    await session.execute(
                        select(
                            Canvases.id.label("canvas_id"),
                            Canvases.project_id,
                            Projects.team_id,
                        )
                        .select_from(Canvases)
                        .join(Projects, Projects.id == Canvases.project_id)
                        .where(Canvases.id == int(canvas_id))
                    )
                )
                .mappings()
                .first()
            )
        if not scope or scope.get("team_id") is None:
            # Canvas deleted since, or its project has no team — nothing
            # trustworthy to write. Report, don't guess.
            result["null_scope_unresolvable"] += 1
            continue
        if dry_run:
            result["null_scope_would_fix"] += 1
            continue
        async with write_scope() as session:
            update_result = await session.execute(
                update(Issues)
                .where(Issues.id == row["id"], Issues.team_id.is_(None))
                .values(
                    team_id=scope["team_id"],
                    project_id=func.coalesce(Issues.project_id, scope["project_id"]),
                )
            )
            changed = update_result.rowcount
        if changed:
            result["null_scope_fixed"] += 1
            if len(fixed_ids) < _AUDIT_IDS_CAP:
                fixed_ids.append(row["id"])
    result["null_scope_scanned"] = len(rows)
    result["null_scope_fixed_ids"] = fixed_ids


async def _repair_rounded(
    kind: str,
    issue_col: InstrumentedAttribute,
    ref_model: type,
    dry_run: bool,
    result: dict[str, Any],
) -> None:
    """Category 2: ids rounded through float64 — repair only exact-one matches."""
    async with read_scope() as session:
        bad_values = (
            (
                await session.execute(
                    select(issue_col.distinct()).where(
                        issue_col.isnot(None),
                        issue_col.notin_(select(ref_model.id)),
                    )
                )
            )
            .scalars()
            .all()
        )
    result[f"rounded_{kind}_distinct_bad"] = len(bad_values)
    if not bad_values:
        return
    async with read_scope() as session:
        real_ids = (await session.execute(select(ref_model.id))).scalars().all()
    repairs: list[dict[str, int]] = []
    for bad in bad_values:
        candidates = rounded_candidates(bad, list(real_ids))
        if len(candidates) != 1:
            # 0 = the row's team was deleted or never existed (orphan);
            # 2+ = two live snowflakes round to the same float (possible
            # near 2^53 boundaries). Either way: report, never guess.
            result[f"rounded_{kind}_ambiguous_or_orphan"] += 1
            continue
        if dry_run:
            result[f"rounded_{kind}_would_fix"] += 1
            continue
        async with write_scope() as session:
            update_result = await session.execute(
                update(Issues)
                .where(issue_col == bad)
                .values(**{issue_col.key: candidates[0]})
            )
            changed = update_result.rowcount
        result[f"rounded_{kind}_rows_fixed"] += changed
        if len(repairs) < _AUDIT_IDS_CAP:
            repairs.append({"from": bad, "to": candidates[0], "rows": changed})
    result[f"rounded_{kind}_repairs"] = repairs


@DBOS.workflow()
async def backfill_issue_scope_workflow(
    dry_run: bool = True,
    limit: int = 500,
) -> dict[str, Any]:
    """Repair pre-#1423 issue rows. ``dry_run=True`` (the default) only reports."""
    from app.services.infra.unified_task_manager import get_task_manager

    manager = get_task_manager()
    task_id = DBOS.workflow_id

    try:
        await manager.create(
            user_id=SYSTEM_RUN_USER_ID,
            task_type="backfill",
            title="Backfill: issue scope (pre-#1423 rows)"[:200],
            subtitle=f"dry_run={dry_run} limit={limit}",
            dbos_workflow_id=task_id,
            metadata={"backfill": "issue_scope", "dry_run": dry_run, "limit": limit},
        )
    except Exception as e:
        logger.warning(
            f"[backfill-issue-scope] create task_tracking failed (non-fatal): {e}"
        )
    try:
        await manager.start(task_id, user_id=SYSTEM_RUN_USER_ID, task_type="backfill")
    except Exception as e:
        logger.warning(f"[backfill-issue-scope] start {task_id}: {e}")

    result: dict[str, Any] = {
        "dry_run": dry_run,
        "null_scope_scanned": 0,
        "null_scope_fixed": 0,
        "null_scope_would_fix": 0,
        "null_scope_malformed": 0,
        "null_scope_unresolvable": 0,
        "rounded_team_distinct_bad": 0,
        "rounded_team_rows_fixed": 0,
        "rounded_team_would_fix": 0,
        "rounded_team_ambiguous_or_orphan": 0,
        "rounded_project_distinct_bad": 0,
        "rounded_project_rows_fixed": 0,
        "rounded_project_would_fix": 0,
        "rounded_project_ambiguous_or_orphan": 0,
    }

    try:
        await _repair_null_scope(dry_run, limit, result)
        await _repair_rounded("team", Issues.team_id, Teams, dry_run, result)
        await _repair_rounded("project", Issues.project_id, Projects, dry_run, result)
        # Visibility-only: scene-origin issues can also be scopeless (embeds
        # without route params). Counted here so the gap is measured before
        # anyone builds a scene→script→project resolver for it.
        async with read_scope() as session:
            result["scene_null_scope_count"] = (
                await session.execute(
                    select(func.count())
                    .select_from(Issues)
                    .where(Issues.origin_id.like("scene:%"), Issues.team_id.is_(None))
                )
            ).scalar_one()
    except Exception:
        # Persist whatever was counted before the crash, then raise —
        # 路线 C rule 4: the trigger writes phase=failed, we never do.
        try:
            await manager.patch_metadata(task_id, result)
        except Exception as e:
            logger.warning(f"[backfill-issue-scope] failed-run metadata patch: {e}")
        raise

    subtitle = (
        f"dry-run: {result['null_scope_would_fix']} scope + "
        f"{result['rounded_team_would_fix']}+{result['rounded_project_would_fix']} rounded"
        if dry_run
        else (
            f"{result['null_scope_fixed']} scoped, "
            f"{result['rounded_team_rows_fixed']}+{result['rounded_project_rows_fixed']} rounded rows"
        )
    )
    try:
        await manager.complete(task_id, subtitle=subtitle[:200], metadata_patch=result)
    except Exception as e:
        logger.warning(f"[backfill-issue-scope] complete {task_id}: {e}")

    return result
