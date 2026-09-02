"""backfill_canvas_upload_roles DBOS workflow — classify the canvas_upload
rows that predate ``params.role``.

THE BACKFILL PARADIGM (see ``backfill_issue_scope.py``): a DBOS workflow,
``dry_run=True`` by default, row-wise idempotent, failures raise.

Task Center visibility: the template's all-zero ``SYSTEM_RUN_USER_ID``
violates ``task_tracking.user_id → auth.users`` and the row is silently never
created (2026-08-14 finding). This workflow therefore takes the dispatching
admin's ``run_user_id`` — a real auth.users row — so the run actually shows up.

WHAT IT CAN AND CANNOT CLASSIFY
-------------------------------

Only ``mask`` and ``brush``, and only from ``params.filename``. The canvas
editors construct those uploads themselves with a hard-coded name —
``OutputNodeView.handleMaskCommit`` sends ``mask.png``,
``handleBrushCommit`` sends ``brush.png`` — so the filename is a literal this
codebase emits, not something a user typed. That is what makes matching on it
a fact rather than a guess.

It deliberately does NOT try to recover the third intermediate,
``reference``: ``import-from-resource`` wrote no params at all, so an old
transcoded reference is byte-for-byte indistinguishable from a genuine
drag-and-drop upload with no filename. Guessing there would delete a user's
own file from their inbox to tidy up ours. Those rows stay VISIBLE and are
removable through the inbox's existing Clean Up dialog.

A user who happens to have uploaded a file literally named ``mask.png`` is
therefore the one false positive this can produce. The cost is that one card
moves behind the "Intermediate Inputs" filter; nothing is deleted, and
re-running with a corrected map would move it back.
"""

from __future__ import annotations

from typing import Any, Optional

from dbos import DBOS
from loguru import logger
from sqlalchemy import select, update

from app.db.session import read_scope, write_scope
from app.models.generated_media import GeneratedMedia
from app.services.library.generated_roles import LEGACY_FILENAME_ROLES, ROLE_KEY

SYSTEM_RUN_USER_ID = "00000000-0000-0000-0000-000000000000"
_AUDIT_IDS_CAP = 200


def role_for_filename(filename: Optional[str]) -> Optional[str]:
    """The role a legacy row's ``params.filename`` proves, or None.

    Exact match, case-sensitive, on the literals the editors emit. A
    ``startswith``/``contains`` rule would sweep in a user's ``mask.png.bak``
    or ``face-mask.png`` — files a person named, which prove nothing.
    """
    if not filename:
        return None
    return LEGACY_FILENAME_ROLES.get(filename)


@DBOS.workflow()
async def backfill_canvas_upload_roles_workflow(
    dry_run: bool = True,
    limit: int = 500,
    run_user_id: Optional[str] = None,
) -> dict[str, Any]:
    """Stamp ``params.role`` on pre-role canvas_upload masks and brush bakes."""
    from app.services.infra.unified_task_manager import get_task_manager

    manager = get_task_manager()
    task_id = DBOS.workflow_id
    owner = run_user_id or SYSTEM_RUN_USER_ID

    try:
        await manager.create(
            user_id=owner,
            task_type="backfill",
            title="Backfill: canvas_upload roles (masks / brush bakes)",
            subtitle=f"dry_run={dry_run} limit={limit}",
            dbos_workflow_id=task_id,
            metadata={
                "backfill": "canvas_upload_roles",
                "dry_run": dry_run,
                "limit": limit,
            },
        )
    except Exception as e:
        logger.warning(
            f"[backfill-upload-roles] create task_tracking failed (non-fatal): {e}"
        )
    try:
        await manager.start(task_id, user_id=owner, task_type="backfill")
    except Exception as e:
        logger.warning(f"[backfill-upload-roles] start {task_id}: {e}")

    result: dict[str, Any] = {
        "dry_run": dry_run,
        "scanned": 0,
        "unclassifiable": 0,
        "would_fix": 0,
        "fixed": 0,
        "by_role": {},
        "fixed_ids": [],
    }

    try:
        async with read_scope() as session:
            rows = (
                (
                    await session.execute(
                        select(
                            GeneratedMedia.id,
                            GeneratedMedia.params,
                        )
                        .where(
                            GeneratedMedia.origin_kind == "canvas_upload",
                            # Already classified rows are skipped, which is
                            # what makes a re-run cheap AND keeps a corrected
                            # hand-fix from being reverted by the next run.
                            GeneratedMedia.params[ROLE_KEY].astext.is_(None),
                        )
                        .order_by(GeneratedMedia.id)
                        .limit(limit)
                    )
                )
                .mappings()
                .all()
            )

        for row in rows:
            result["scanned"] += 1
            params = dict(row["params"] or {})
            role = role_for_filename(params.get("filename"))
            if role is None:
                result["unclassifiable"] += 1
                continue
            if dry_run:
                result["would_fix"] += 1
                result["by_role"][role] = result["by_role"].get(role, 0) + 1
                continue
            async with write_scope() as session:
                await session.execute(
                    update(GeneratedMedia)
                    .where(
                        GeneratedMedia.id == row["id"],
                        GeneratedMedia.params[ROLE_KEY].astext.is_(None),
                    )
                    .values(params={**params, ROLE_KEY: role})
                )
            result["fixed"] += 1
            result["by_role"][role] = result["by_role"].get(role, 0) + 1
            if len(result["fixed_ids"]) < _AUDIT_IDS_CAP:
                result["fixed_ids"].append(str(row["id"]))
    except Exception:
        try:
            await manager.patch_metadata(task_id, result)
        except Exception as e:
            logger.warning(f"[backfill-upload-roles] failed-run metadata patch: {e}")
        raise

    subtitle = (
        f"dry-run: {result['would_fix']}/{result['scanned']} would be classified"
        if dry_run
        else f"{result['fixed']}/{result['scanned']} rows classified"
    )
    try:
        await manager.complete(task_id, subtitle=subtitle[:200], metadata_patch=result)
    except Exception as e:
        logger.warning(f"[backfill-upload-roles] complete {task_id}: {e}")
    return result
