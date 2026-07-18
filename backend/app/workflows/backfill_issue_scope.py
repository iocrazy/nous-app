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

from app.db import engine as db_engine

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


# ── SQL ─────────────────────────────────────────────────────────────────

_CANVAS_NULL_SCOPE_SQL = """
SELECT id, origin_id
FROM public.issues
WHERE origin_id LIKE 'canvas:%'
  AND team_id IS NULL
ORDER BY id
LIMIT :limit
"""

_CANVAS_SCOPE_LOOKUP_SQL = """
SELECT c.id AS canvas_id, c.project_id, p.team_id
FROM public.canvases c
JOIN public.projects p ON p.id = c.project_id
WHERE c.id = :canvas_id
"""

_FIX_NULL_SCOPE_SQL = """
UPDATE public.issues
SET team_id = :team_id,
    project_id = COALESCE(project_id, :project_id)
WHERE id = :id AND team_id IS NULL
"""

_SCENE_NULL_SCOPE_COUNT_SQL = """
SELECT COUNT(*) FROM public.issues
WHERE origin_id LIKE 'scene:%' AND team_id IS NULL
"""

_BAD_TEAM_IDS_SQL = """
SELECT DISTINCT team_id FROM public.issues
WHERE team_id IS NOT NULL
  AND team_id NOT IN (SELECT id FROM public.teams)
"""

_ALL_TEAM_IDS_SQL = "SELECT id FROM public.teams"

_FIX_ROUNDED_TEAM_SQL = """
UPDATE public.issues SET team_id = :good WHERE team_id = :bad
"""

_BAD_PROJECT_IDS_SQL = """
SELECT DISTINCT project_id FROM public.issues
WHERE project_id IS NOT NULL
  AND project_id NOT IN (SELECT id FROM public.projects)
"""

_ALL_PROJECT_IDS_SQL = "SELECT id FROM public.projects"

_FIX_ROUNDED_PROJECT_SQL = """
UPDATE public.issues SET project_id = :good WHERE project_id = :bad
"""


# ── Workflow ────────────────────────────────────────────────────────────


async def _repair_null_scope(dry_run: bool, limit: int, result: dict[str, Any]) -> None:
    """Category 1: canvas-origin issues whose team_id is NULL."""
    rows = await db_engine.fetch_all(_CANVAS_NULL_SCOPE_SQL, {"limit": limit})
    fixed_ids: list[int] = []
    for row in rows:
        canvas_id = parse_canvas_origin(row.get("origin_id"))
        if canvas_id is None:
            result["null_scope_malformed"] += 1
            continue
        scope = await db_engine.fetch_one(
            _CANVAS_SCOPE_LOOKUP_SQL, {"canvas_id": int(canvas_id)}
        )
        if not scope or scope.get("team_id") is None:
            # Canvas deleted since, or its project has no team — nothing
            # trustworthy to write. Report, don't guess.
            result["null_scope_unresolvable"] += 1
            continue
        if dry_run:
            result["null_scope_would_fix"] += 1
            continue
        changed = await db_engine.execute(
            _FIX_NULL_SCOPE_SQL,
            {
                "id": row["id"],
                "team_id": scope["team_id"],
                "project_id": scope["project_id"],
            },
        )
        if changed:
            result["null_scope_fixed"] += 1
            if len(fixed_ids) < _AUDIT_IDS_CAP:
                fixed_ids.append(row["id"])
    result["null_scope_scanned"] = len(rows)
    result["null_scope_fixed_ids"] = fixed_ids


async def _repair_rounded(
    kind: str,
    bad_sql: str,
    all_sql: str,
    fix_sql: str,
    dry_run: bool,
    result: dict[str, Any],
) -> None:
    """Category 2: ids rounded through float64 — repair only exact-one matches."""
    bad_rows = await db_engine.fetch_all(bad_sql)
    key = "team_id" if kind == "team" else "project_id"
    bad_values = [r[key] for r in bad_rows]
    result[f"rounded_{kind}_distinct_bad"] = len(bad_values)
    if not bad_values:
        return
    real_ids = [r["id"] for r in await db_engine.fetch_all(all_sql)]
    repairs: list[dict[str, int]] = []
    for bad in bad_values:
        candidates = rounded_candidates(bad, real_ids)
        if len(candidates) != 1:
            # 0 = the row's team was deleted or never existed (orphan);
            # 2+ = two live snowflakes round to the same float (possible
            # near 2^53 boundaries). Either way: report, never guess.
            result[f"rounded_{kind}_ambiguous_or_orphan"] += 1
            continue
        if dry_run:
            result[f"rounded_{kind}_would_fix"] += 1
            continue
        changed = await db_engine.execute(fix_sql, {"good": candidates[0], "bad": bad})
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
        await _repair_rounded(
            "team",
            _BAD_TEAM_IDS_SQL,
            _ALL_TEAM_IDS_SQL,
            _FIX_ROUNDED_TEAM_SQL,
            dry_run,
            result,
        )
        await _repair_rounded(
            "project",
            _BAD_PROJECT_IDS_SQL,
            _ALL_PROJECT_IDS_SQL,
            _FIX_ROUNDED_PROJECT_SQL,
            dry_run,
            result,
        )
        # Visibility-only: scene-origin issues can also be scopeless (embeds
        # without route params). Counted here so the gap is measured before
        # anyone builds a scene→script→project resolver for it.
        result["scene_null_scope_count"] = await db_engine.fetch_val(
            _SCENE_NULL_SCOPE_COUNT_SQL
        )
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
