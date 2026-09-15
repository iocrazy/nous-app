"""backfill_parsed_media_canonical_url — fill the mig 471 dedup key.

THE BACKFILL PARADIGM (see ``backfill_resource_gen_params.py``): DBOS
workflow, ``dry_run=True`` by default, row-wise idempotent (only rows with
``canonical_url IS NULL`` are touched), failures raise.

Why a backfill at all: every reader pairs ``canonical_url`` with the legacy
``original_url`` equality, so a NULL degrades to the old exact-match
behaviour — correct, but exactly the behaviour that let a bilibili video the
user had owned for a week read as "never downloaded" (its ``spm_id_from``
tracking parameter differed between the two submits). Rows only earn a key by
being re-parsed, and a re-parse is the event we are trying to prevent, so
without this backfill the fix would not apply to anything already in the
library.

The key is computed by :func:`app.utils.url_canonical.
canonical_url`, never by a SQL re-implementation — two implementations of a
dedup key drift silently, and the only symptom is a redundant download.
"""

from __future__ import annotations

from typing import Any, Optional

from dbos import DBOS
from loguru import logger
from sqlalchemy import select, update

from app.db.session import read_scope, write_scope
from app.models import ParsedMedia
from app.utils.url_canonical import canonical_url

SYSTEM_RUN_USER_ID = "00000000-0000-0000-0000-000000000000"
_AUDIT_IDS_CAP = 200


@DBOS.workflow()
async def backfill_parsed_media_canonical_url_workflow(
    dry_run: bool = True,
    limit: int = 2000,
    run_user_id: Optional[str] = None,
) -> dict[str, Any]:
    """Populate ``parsed_media.canonical_url`` for rows that predate mig 471."""
    from app.services.infra.unified_task_manager import get_task_manager

    manager = get_task_manager()
    task_id = DBOS.workflow_id
    owner = run_user_id or SYSTEM_RUN_USER_ID

    try:
        await manager.create(
            user_id=owner,
            task_type="backfill",
            title="Backfill: parsed_media canonical_url (mig 471)",
            subtitle=f"dry_run={dry_run} limit={limit}",
            dbos_workflow_id=task_id,
            metadata={
                "backfill": "parsed_media_canonical_url",
                "dry_run": dry_run,
                "limit": limit,
            },
        )
    except Exception as e:
        logger.warning(
            f"[backfill-canonical-url] create task_tracking failed (non-fatal): {e}"
        )
    try:
        await manager.start(task_id, user_id=owner, task_type="backfill")
    except Exception as e:
        logger.warning(f"[backfill-canonical-url] start {task_id}: {e}")

    result: dict[str, Any] = {
        "dry_run": dry_run,
        "scanned": 0,
        "would_fix": 0,
        "fixed": 0,
        # Rows whose original_url canonicalises to "" (empty / unparseable).
        # Reported separately so the residual `canonical_url IS NULL` count
        # after a live run has an explanation instead of reading as a gap.
        "skipped_unusable_url": 0,
        "hit_limit": False,
        "fixed_ids": [],
    }

    try:
        # parsed_media is NOT a scoped table (no UserScoped mixin), so no
        # ambient scope is required here — unlike the resources-side
        # backfills, which need system_request_scope.
        async with read_scope() as session:
            rows = (
                (
                    await session.execute(
                        select(ParsedMedia.id, ParsedMedia.original_url)
                        .where(ParsedMedia.canonical_url.is_(None))
                        .order_by(ParsedMedia.id)
                        .limit(limit)
                    )
                )
                .mappings()
                .all()
            )

        for row in rows:
            result["scanned"] += 1
            key = canonical_url(str(row["original_url"] or ""))
            if not key:
                result["skipped_unusable_url"] += 1
                continue
            if dry_run:
                result["would_fix"] += 1
                continue
            async with write_scope() as session:
                await session.execute(
                    update(ParsedMedia)
                    .where(
                        ParsedMedia.id == row["id"],
                        ParsedMedia.canonical_url.is_(None),
                    )
                    .values(canonical_url=key)
                )
            result["fixed"] += 1
            if len(result["fixed_ids"]) < _AUDIT_IDS_CAP:
                result["fixed_ids"].append(str(row["id"]))
    except Exception:
        try:
            await manager.patch_metadata(task_id, result)
        except Exception as e:  # keep the original failure the visible one
            logger.warning(f"[backfill-canonical-url] failed-run metadata patch: {e}")
        raise

    # Reported independently of the other counters: a run can be both
    # complete-looking and truncated.
    result["hit_limit"] = len(rows) >= limit

    subtitle = (
        f"dry-run: {result['would_fix']}/{result['scanned']} would get a key"
        if dry_run
        else f"{result['fixed']}/{result['scanned']} rows got a key"
    )
    try:
        await manager.complete(task_id, subtitle=subtitle[:200], metadata_patch=result)
    except Exception as e:
        logger.warning(f"[backfill-canonical-url] complete {task_id}: {e}")
    return result
