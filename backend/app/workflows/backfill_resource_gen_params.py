"""backfill_resource_gen_params DBOS workflow — fill ``resources.gen_params``
for PNGs uploaded before migration 440.

THE BACKFILL PARADIGM (see ``backfill_issue_scope.py``): a DBOS workflow,
``dry_run=True`` by default, row-wise idempotent, failures raise.

Task Center visibility: the template's all-zero ``SYSTEM_RUN_USER_ID``
violates ``task_tracking.user_id → auth.users`` and the row is silently
never created (2026-08-14 finding). This workflow therefore takes the
dispatching admin's ``run_user_id`` — a real auth.users row — so the run
actually shows up; it only falls back to the system id when none is given.

What it does per row (not trashed, ``gen_params IS NULL``), two sources in
order:

1. **Promoted generation** — the row was copied out of ``generated_media``
   (``promoted_resource_id``) before migration 440; the PNG itself carries no
   text chunks, the provenance (provider / model / params) is in that row.
   Normalised with the same helper the promote path uses today.
2. **PNG metadata** — image/png rows: re-run the extractor
   upload_postprocess uses, write ``gen_params`` and — under the same
   never-clobber rule — ``gen_prompt`` / ``gen_prompt_negative`` when empty.

Rows that yield nothing from either source are counted, not touched, so a
re-run re-scans them (cheap) rather than needing a "checked" marker column.
"""

from __future__ import annotations

from typing import Any, Optional

from dbos import DBOS
from loguru import logger
from sqlalchemy import or_, select, update

from app.db.scope import system_request_scope
from app.db.session import read_scope, write_scope
from app.models import Resources
from app.models.generated_media import GeneratedMedia
from app.services.library.media_storage import materialize
from app.services.prompts.origin import stamp_origin

SYSTEM_RUN_USER_ID = "00000000-0000-0000-0000-000000000000"
_AUDIT_IDS_CAP = 200


async def _extract(file_path: str) -> Optional[dict]:
    """Prompts + params for one stored PNG, or None (no metadata / unreadable)."""
    from app.services.library.png_prompt_extractor import extract_png_generation

    try:
        async with materialize(file_path) as local_path:
            gen = extract_png_generation(local_path)
    except Exception as e:  # missing file, storage hiccup — count, don't crash the run
        logger.warning(f"[backfill-gen-params] cannot read {file_path}: {e}")
        return None
    if not gen:
        return None
    return {"positive": gen.positive, "negative": gen.negative, "params": gen.params}


def extracted_from_generation(gen: Optional[dict]) -> Optional[dict]:
    """Source 1: a promoted generated_media row → the extractor's dict shape.

    Prompts are deliberately None: the promote path already copied
    ``gen_prompt`` at promotion time, and a generation row's prompt is not
    a *PNG-embedded* prompt — never-clobber must not overwrite user edits
    with it.
    """
    if not gen:
        return None
    from app.services.library.promote_generated_media_service import (
        generation_params_from_generated_media,
    )

    params = generation_params_from_generated_media(gen)
    if not params:
        return None
    return {"positive": None, "negative": None, "params": params}


def build_patch(current: dict, extracted: dict) -> dict:
    """Never-clobber merge — identical rule to upload_postprocess Phase A2."""
    patch: dict = {}
    if extracted.get("params") and not current.get("gen_params"):
        patch["gen_params"] = extracted["params"]
    if extracted.get("positive") and not (current.get("gen_prompt") or "").strip():
        patch["gen_prompt"] = extracted["positive"]
    if (
        extracted.get("negative")
        and not (current.get("gen_prompt_negative") or "").strip()
    ):
        patch["gen_prompt_negative"] = extracted["negative"]
    return stamp_origin(patch, "extracted")


@DBOS.workflow()
async def backfill_resource_gen_params_workflow(
    dry_run: bool = True,
    limit: int = 500,
    run_user_id: Optional[str] = None,
) -> dict[str, Any]:
    """Extract generation params for PNG resources that predate migration 440."""
    from app.services.infra.unified_task_manager import get_task_manager

    manager = get_task_manager()
    task_id = DBOS.workflow_id
    owner = run_user_id or SYSTEM_RUN_USER_ID

    try:
        await manager.create(
            user_id=owner,
            task_type="backfill",
            title="Backfill: resource generation params (PNG metadata)",
            subtitle=f"dry_run={dry_run} limit={limit}",
            dbos_workflow_id=task_id,
            metadata={
                "backfill": "resource_gen_params",
                "dry_run": dry_run,
                "limit": limit,
            },
        )
    except Exception as e:
        logger.warning(
            f"[backfill-gen-params] create task_tracking failed (non-fatal): {e}"
        )
    try:
        await manager.start(task_id, user_id=owner, task_type="backfill")
    except Exception as e:
        logger.warning(f"[backfill-gen-params] start {task_id}: {e}")

    result: dict[str, Any] = {
        "dry_run": dry_run,
        "scanned": 0,
        "no_metadata": 0,
        "from_generation": 0,
        "from_png": 0,
        "would_fix": 0,
        "fixed": 0,
        "fixed_ids": [],
    }

    try:
        # Resources mixes in UserScoped(creator_id): under
        # SCOPE_ENFORCE_RESOURCES a scoped table touched with no ambient
        # scope is fail-closed (UnscopedQueryError). A backfill is
        # deliberately cross-user, so SYSTEM is the correct treatment —
        # same entry-point pattern as backfill_resource_prompt_origin.
        async with system_request_scope(
            reason=(
                "backfill resource_gen_params: system-wide extraction of "
                "generation params for rows that predate mig 440"
            )
        ):
            async with read_scope() as session:
                rows = (
                    (
                        await session.execute(
                            select(
                                Resources.id,
                                Resources.file_path,
                                Resources.mime_type,
                                Resources.gen_prompt,
                                Resources.gen_prompt_negative,
                                Resources.gen_params,
                                GeneratedMedia.model.label("gen_model"),
                                GeneratedMedia.provider.label("gen_provider"),
                                GeneratedMedia.params.label("gen_row_params"),
                            )
                            .outerjoin(
                                GeneratedMedia,
                                GeneratedMedia.promoted_resource_id == Resources.id,
                            )
                            .where(
                                Resources.gen_params.is_(None),
                                Resources.is_trashed.is_(False),
                                Resources.file_path.is_not(None),
                                or_(
                                    Resources.mime_type == "image/png",
                                    GeneratedMedia.id.is_not(None),
                                ),
                            )
                            .order_by(Resources.id)
                            .limit(limit)
                        )
                    )
                    .mappings()
                    .all()
                )
            for row in rows:
                result["scanned"] += 1
                gen_row = (
                    {
                        "model": row["gen_model"],
                        "provider": row["gen_provider"],
                        "params": row["gen_row_params"],
                    }
                    if row["gen_model"] or row["gen_provider"] or row["gen_row_params"]
                    else None
                )
                extracted = extracted_from_generation(gen_row)
                source = "from_generation"
                if not extracted and row["mime_type"] == "image/png":
                    extracted = await _extract(row["file_path"])
                    source = "from_png"
                if not extracted:
                    result["no_metadata"] += 1
                    continue
                patch = build_patch(dict(row), extracted)
                if not patch:
                    result["no_metadata"] += 1
                    continue
                if dry_run:
                    result["would_fix"] += 1
                    continue
                async with write_scope() as session:
                    await session.execute(
                        update(Resources)
                        .where(
                            Resources.id == row["id"], Resources.gen_params.is_(None)
                        )
                        .values(**patch)
                    )
                result["fixed"] += 1
                result[source] += 1
                if len(result["fixed_ids"]) < _AUDIT_IDS_CAP:
                    result["fixed_ids"].append(str(row["id"]))
    except Exception:
        try:
            await manager.patch_metadata(task_id, result)
        except Exception as e:
            logger.warning(f"[backfill-gen-params] failed-run metadata patch: {e}")
        raise

    subtitle = (
        f"dry-run: {result['would_fix']}/{result['scanned']} would get params"
        if dry_run
        else f"{result['fixed']}/{result['scanned']} rows got params"
    )
    try:
        await manager.complete(task_id, subtitle=subtitle[:200], metadata_patch=result)
    except Exception as e:
        logger.warning(f"[backfill-gen-params] complete {task_id}: {e}")
    return result
