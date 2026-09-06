"""backfill_resource_prompt_origin — label rows that predate mig 455.

THE BACKFILL PARADIGM (see ``backfill_resource_gen_params.py``): DBOS workflow,
``dry_run=True`` by default, row-wise idempotent (only rows with
``prompt_origin IS NULL`` are touched), failures raise. Takes the dispatching
admin's ``run_user_id`` so the run shows in Task Center — the template's
all-zero system id violates ``task_tracking.user_id → auth.users`` and the row
is silently never created.

Rule per row: :func:`app.services.prompts.origin.derive_origin` (spec §3.2).
Rows it returns ``None`` for carry no prompt text and are left NULL.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from dbos import DBOS
from loguru import logger
from sqlalchemy import select, update

from app.db.scope import system_request_scope
from app.db.session import read_scope, write_scope
from app.models import Resources
from app.repositories.media_repository import has_prompt_expr
from app.services.prompts.origin import derive_origin

SYSTEM_RUN_USER_ID = "00000000-0000-0000-0000-000000000000"
_AUDIT_IDS_CAP = 200


def plan_row(row: Mapping[str, Any]) -> Optional[str]:
    """The origin this row gets, or None (no prompt text → stays NULL)."""
    return derive_origin(row)


@DBOS.workflow()
async def backfill_resource_prompt_origin_workflow(
    dry_run: bool = True,
    limit: int = 2000,
    run_user_id: Optional[str] = None,
) -> dict[str, Any]:
    """Label ``resources.prompt_origin`` for prompt rows that predate mig 455."""
    from app.services.infra.unified_task_manager import get_task_manager

    manager = get_task_manager()
    task_id = DBOS.workflow_id
    owner = run_user_id or SYSTEM_RUN_USER_ID

    try:
        await manager.create(
            user_id=owner,
            task_type="backfill",
            title="Backfill: resource prompt origin (mig 455)",
            subtitle=f"dry_run={dry_run} limit={limit}",
            dbos_workflow_id=task_id,
            metadata={
                "backfill": "resource_prompt_origin",
                "dry_run": dry_run,
                "limit": limit,
            },
        )
    except Exception as e:
        logger.warning(
            f"[backfill-prompt-origin] create task_tracking failed (non-fatal): {e}"
        )
    try:
        await manager.start(task_id, user_id=owner, task_type="backfill")
    except Exception as e:
        logger.warning(f"[backfill-prompt-origin] start {task_id}: {e}")

    result: dict[str, Any] = {
        "dry_run": dry_run,
        "scanned": 0,
        "would_fix": 0,
        "fixed": 0,
        "by_origin": {"typed": 0, "extracted": 0, "captioned": 0},
        "fixed_ids": [],
    }

    try:
        # Resources mixes in UserScoped(creator_id): under
        # SCOPE_ENFORCE_RESOURCES a scoped table touched with no ambient
        # scope is fail-closed (UnscopedQueryError). A backfill is
        # deliberately cross-user, so SYSTEM is the correct treatment —
        # same entry-point pattern as thumbnail_workflow.
        async with system_request_scope(
            reason=(
                "backfill resource_prompt_origin: system-wide labelling of "
                "rows that predate mig 455"
            )
        ):
            async with read_scope() as session:
                rows = (
                    (
                        await session.execute(
                            select(
                                Resources.id,
                                Resources.gen_prompt,
                                Resources.gen_prompt_zh,
                                Resources.gen_prompt_json,
                                Resources.gen_params,
                                Resources.slide_prompts,
                            )
                            .where(Resources.prompt_origin.is_(None), has_prompt_expr())
                            .order_by(Resources.id)
                            .limit(limit)
                        )
                    )
                    .mappings()
                    .all()
                )
            for row in rows:
                result["scanned"] += 1
                origin = plan_row(row)
                if origin is None:
                    continue
                result["by_origin"][origin] += 1
                if dry_run:
                    result["would_fix"] += 1
                    continue
                async with write_scope() as session:
                    await session.execute(
                        update(Resources)
                        .where(
                            Resources.id == row["id"],
                            Resources.prompt_origin.is_(None),
                        )
                        .values(prompt_origin=origin)
                    )
                result["fixed"] += 1
                if len(result["fixed_ids"]) < _AUDIT_IDS_CAP:
                    result["fixed_ids"].append(str(row["id"]))
    except Exception:
        try:
            await manager.patch_metadata(task_id, result)
        except Exception as e:  # keep the original failure the visible one
            logger.warning(f"[backfill-prompt-origin] failed-run metadata patch: {e}")
        raise

    subtitle = (
        f"dry-run: {result['would_fix']}/{result['scanned']} would get an origin"
        if dry_run
        else f"{result['fixed']}/{result['scanned']} rows got an origin"
    )
    try:
        await manager.complete(task_id, subtitle=subtitle[:200], metadata_patch=result)
    except Exception as e:
        logger.warning(f"[backfill-prompt-origin] complete {task_id}: {e}")
    return result
