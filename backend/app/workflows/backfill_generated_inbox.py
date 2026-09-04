"""backfill_generated_inbox — legacy chat uploads join the Generated inbox.

THE BACKFILL PARADIGM (see ``backfill_issue_scope.py``): a DBOS workflow,
``dry_run=True`` by default, row-wise idempotent, failures raise, Task Center
visible, **DB work under an explicit system scope**. The planner is pure so
every decision it makes is testable without a database.

Two independent halves, reported separately (a run can be entirely one of
them, and collapsing the counts would hide that):

1. **register** — every resource sitting in a scope's chat-uploads folder
   (``chat_upload.chat_uploads_folder_criteria()``) that has no
   ``generated_media`` row pointing at it gets one: ``origin_kind='chat_upload'``,
   ``review_state='saved'``, ``promoted_resource_id`` = the resource,
   ``file_path`` = the resource's own path. **No blob is copied** — the row
   registers bytes that already exist.
2. **mark in_assets** — a ``generated_media`` row whose promoted resource is
   attached to an asset (``asset_files``) is, by definition, in the asset
   library; its inbox state should say so.

``dry_run=False`` is cheap to reach for here because both halves are narrow and
reversible in kind: one inserts rows that point at files that already exist
(no blob is written, nothing is moved), the other flips a display state for
rows whose attachment is a fact recorded in another table. Neither half can
lose data that the run did not itself create.

Task Center: the all-zero ``SYSTEM_RUN_USER_ID`` violates
``task_tracking.user_id → auth.users`` and the row is silently never created
(2026-08-14 finding), so the dispatching admin's ``run_user_id`` is what makes
the run visible.

No ``limit``: the plan is a global reconciliation of two whole sets, and a
prefix scan would report "already registered" counts that are simply wrong.
``workflow_kwargs`` in the admin router only passes ``limit`` to workflows
that declare one.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple, TypedDict

from dbos import DBOS
from loguru import logger
from sqlalchemy import select
from sqlalchemy import update as sa_update

from app.db.scope import system_request_scope
from app.db.session import read_scope, write_scope
from app.models import (
    AssetFiles,
    Assets,
    Folders,
    GeneratedMedia,
    ResourceItems,
    Resources,
)
from app.services.library.chat_upload import (
    chat_uploads_folder_criteria,
    media_kind_for_mime,
)

SYSTEM_RUN_USER_ID = "00000000-0000-0000-0000-000000000000"

# Cap the audit id lists carried in the run's report (jsonb). Counts are always
# exact — compare a list's length against its count to see if it was truncated.
_AUDIT_IDS_CAP = 200

# States that must not be flipped to ``in_assets``:
#   in_assets — already there (re-running must be a no-op)
#   deleted   — a user's decision about the inbox card. Attaching the promoted
#               resource to an asset is somebody else's action; un-dismissing
#               the card behind their back is not this backfill's call.
_NOT_MARKABLE = ("in_assets", "deleted")


# What ``_load_inputs`` hands the planner: (temp resources, generated_media
# rows keyed by promoted_resource_id, resource ids that have an asset_files row).
PlannerInputs = Tuple[List[Dict[str, Any]], Dict[int, Dict[str, Any]], Set[int]]


def _resources_with_asset_files_stmt():
    """Resource ids that are attached to a LIVE asset.

    Pure + module-level so the join can be asserted on compiled SQL (same
    stance as ``generated_media_repository._inbox_filters``): a soft-deleted
    asset KEEPS its ``asset_files`` rows, so without the join to ``assets``
    this query still returns rows and the backfill quietly flips resources
    attached only to a deleted asset to ``in_assets`` — a wrong answer that
    produces no error anywhere.
    """
    return (
        select(AssetFiles.resource_id)
        .join(Assets, Assets.id == AssetFiles.asset_id)
        .where(Assets.deleted_at.is_(None))
        .distinct()
    )


def _chat_upload_resources_stmt():
    """Live resources filed in a scope's chat-uploads folder.

    Pure + module-level for the same reason as
    ``_resources_with_asset_files_stmt`` above: the folder predicate is the
    whole correctness of the register half, and it is only checkable on the
    compiled SQL. Dropping the legacy arm (or the ``system_key IS NULL`` guard
    inside it) leaves a query that still runs and still returns rows — it just
    returns the wrong set, and reports the shortfall as "already registered".
    """
    return (
        select(
            Resources.id,
            ResourceItems.scope_id,
            Resources.creator_id,
            Resources.file_path,
            Resources.mime_type,
        )
        .select_from(Resources)
        .join(ResourceItems, ResourceItems.resource_id == Resources.id)
        .join(Folders, Folders.id == ResourceItems.folder_id)
        .where(
            chat_uploads_folder_criteria(),
            Folders.is_trashed.is_(False),
            Resources.is_trashed.is_(False),
            Resources.file_path.is_not(None),
        )
        .order_by(Resources.id)
    )


class InboxPlan(TypedDict):
    to_register: List[Dict[str, Any]]
    to_mark_in_assets: List[int]
    counts: Dict[str, int]


def plan_inbox_backfill(
    temp_resources: List[Dict[str, Any]],
    gen_by_resource: Dict[int, Dict[str, Any]],
    promoted_with_asset_files: Set[int],
) -> InboxPlan:
    """Pure: decide what to register and what to mark, from three inputs.

    * ``temp_resources`` — rows in any ``temp`` folder (id, scope_id,
      creator_id, file_path, mime_type).
    * ``gen_by_resource`` — existing ``generated_media`` rows keyed by
      ``promoted_resource_id`` (the same key the write path is idempotent on).
    * ``promoted_with_asset_files`` — promoted resource ids that have an
      ``asset_files`` row.

    The two halves are independent: a resource can be in neither, either, or
    (across a run) both — registered now, marked on the next run once someone
    attaches it.
    """
    to_register = [r for r in temp_resources if int(r["id"]) not in gen_by_resource]
    to_mark = [
        int(gen["id"])
        for resource_id, gen in gen_by_resource.items()
        if int(resource_id) in promoted_with_asset_files
        and str(gen.get("review_state") or "") not in _NOT_MARKABLE
    ]
    return {
        "to_register": to_register,
        "to_mark_in_assets": to_mark,
        "counts": {
            "temp_resources": len(temp_resources),
            "already_registered": len(temp_resources) - len(to_register),
            "to_register": len(to_register),
            "to_mark_in_assets": len(to_mark),
        },
    }


async def _load_inputs() -> PlannerInputs:
    """The three planner inputs, read in one place (ORM).

    ``folder_id`` lives on ``resource_items``, not on ``resources`` — the
    chat-uploads-folder membership is a two-hop join. That folder is matched by
    the SHARED criterion from ``chat_upload``: the keyed
    ``system_key='chat_uploads'`` folder OR a not-yet-adopted legacy ``temp``
    one. Both arms are needed, and the legacy arm is the load-bearing one right
    now: a scope gets adopted only when someone uploads to it (migration 450,
    which adopts the rest, ships in a later PR), so most scopes still hold an
    unkeyed ``temp`` folder. Keying only on ``system_key`` would report an
    empty plan and call it reconciled.

    Trashed resources and trashed folders are excluded: an inbox row for a file
    on its way to deletion is noise (temp clean-up is manual —
    ``POST /api/v1/generated/cleanup``; the TTL sweeper was retired in P6).
    ``file_path IS NULL`` rows are excluded because
    ``generated_media.file_path`` is NOT NULL.
    """
    async with read_scope() as session:
        temp_rows = (
            (await session.execute(_chat_upload_resources_stmt())).mappings().all()
        )
        gen_rows = (
            (
                await session.execute(
                    select(
                        GeneratedMedia.id,
                        GeneratedMedia.scope_id,
                        GeneratedMedia.promoted_resource_id,
                        GeneratedMedia.review_state,
                    )
                    .where(GeneratedMedia.promoted_resource_id.is_not(None))
                    .order_by(GeneratedMedia.id)
                )
            )
            .mappings()
            .all()
        )
        asset_file_resource_ids = (
            (await session.execute(_resources_with_asset_files_stmt())).scalars().all()
        )

    # De-duplicated by resource id: the join is through ``resource_items``,
    # and one resource can have several rows there (it can sit in a temp
    # folder in more than one scope). Two identical entries would not create
    # two generated_media rows (the repo's select-then-insert stops that) but
    # WOULD double-count the run's report.
    seen: Set[int] = set()
    temp_resources: List[Dict[str, Any]] = []
    for r in temp_rows:
        rid = int(r["id"])
        if rid in seen:
            continue
        seen.add(rid)
        temp_resources.append(
            {
                "id": rid,
                "scope_id": int(r["scope_id"]),
                "creator_id": str(r["creator_id"]),
                "file_path": r["file_path"],
                "mime_type": r["mime_type"],
            }
        )
    # Lowest id wins per resource (hence the ORDER BY + setdefault above):
    # promoted_resource_id has no unique index, so a legacy promote can have
    # left a second row, and the planner must name the SAME row that
    # ``insert_registered_resource`` treats as the one that already exists.
    gen_by_resource: Dict[int, Dict[str, Any]] = {}
    for g in gen_rows:
        gen_by_resource.setdefault(
            int(g["promoted_resource_id"]),
            {
                "id": int(g["id"]),
                "scope_id": int(g["scope_id"]),
                "review_state": g["review_state"],
            },
        )
    return temp_resources, gen_by_resource, set(int(x) for x in asset_file_resource_ids)


async def _apply(plan: InboxPlan, run_user_id: str) -> Dict[str, Any]:
    """Write the plan. Per-row transactions; every write is idempotent.

    ``run_user_id`` is deliberately NOT the ``creator_id`` of a registered
    row: the row describes the user who uploaded the file, not the admin who
    dispatched the reconciliation.
    """
    from app.repositories.generated_media_repository import GeneratedMediaRepository

    repo = GeneratedMediaRepository()
    registered = 0
    marked = 0
    registered_ids: List[str] = []
    marked_ids: List[str] = []

    for row in plan["to_register"]:
        # Idempotent by promoted_resource_id inside the repo (select-then-insert).
        out = await repo.insert_registered_resource(
            scope_id=int(row["scope_id"]),
            creator_id=str(row["creator_id"]),
            resource_id=int(row["id"]),
            file_path=row["file_path"],
            mime=row.get("mime_type"),
            media_kind=media_kind_for_mime(row.get("mime_type")),
            conversation_id=None,
            origin_kind="chat_upload",
        )
        registered += 1
        if len(registered_ids) < _AUDIT_IDS_CAP:
            registered_ids.append(str(out.get("id")))

    for gen_id in plan["to_mark_in_assets"]:
        async with write_scope() as session:
            # The state predicate makes the UPDATE itself idempotent, so a
            # re-run (or a concurrent flip) is a no-op rather than a rewrite.
            result = await session.execute(
                sa_update(GeneratedMedia)
                .where(GeneratedMedia.id == int(gen_id))
                .where(GeneratedMedia.review_state.not_in(_NOT_MARKABLE))
                .values(review_state="in_assets")
            )
            # Read inside the transaction — a CursorResult's rowcount is not
            # a promise once the connection is back in the pool.
            changed = result.rowcount
        if changed:
            marked += 1
            if len(marked_ids) < _AUDIT_IDS_CAP:
                marked_ids.append(str(gen_id))

    return {
        "registered": registered,
        "marked_in_assets": marked,
        "registered_ids": registered_ids,
        "marked_ids": marked_ids,
    }


@DBOS.workflow()
async def backfill_generated_inbox(
    dry_run: bool = True, run_user_id: Optional[str] = None
) -> Dict[str, Any]:
    """Register legacy temp uploads into the inbox; mark attached rows in_assets."""
    from app.services.infra.unified_task_manager import get_task_manager

    manager = get_task_manager()
    task_id = DBOS.workflow_id
    owner = run_user_id or SYSTEM_RUN_USER_ID

    try:
        await manager.create(
            user_id=owner,
            task_type="backfill",
            title="Backfill: chat uploads → Generated inbox",
            subtitle=f"dry_run={dry_run}",
            dbos_workflow_id=task_id,
            metadata={"backfill": "generated_inbox", "dry_run": dry_run},
        )
    except Exception as e:
        logger.warning(f"[backfill-inbox] create task_tracking failed (non-fatal): {e}")
    try:
        await manager.start(task_id, user_id=owner, task_type="backfill")
    except Exception as e:
        logger.warning(f"[backfill-inbox] start {task_id}: {e}")

    out: Dict[str, Any] = {"dry_run": dry_run}
    try:
        # Both DB halves run under an explicit system scope: ``Resources`` is a
        # ``UserScoped`` model and production has ``SCOPE_ENFORCE_RESOURCES=True``,
        # so an unscoped SELECT raises ``UnscopedQueryError`` — the whole run
        # fails before it reads a single row. A reconciliation is global by
        # definition, so the scope is ``system``, not a user's. Entered inside
        # the async body so the ContextVar lands in this workflow's event loop
        # (same stance as ``scheduled_cleanup.cleanup_trashed_resources_step``).
        # The Task Center ``manager.*`` calls stay outside — they touch
        # ``task_tracking``, which is not scope-enforced.
        async with system_request_scope(reason="backfill-generated-inbox"):
            temp_resources, gen_by_resource, with_asset_files = await _load_inputs()
            plan = plan_inbox_backfill(
                temp_resources, gen_by_resource, with_asset_files
            )
            logger.info(
                "[backfill-inbox] plan counts={} dry_run={}", plan["counts"], dry_run
            )
            out["counts"] = plan["counts"]
            if not dry_run:
                out["applied"] = await _apply(plan, owner)
    except Exception:
        # Persist whatever was computed before the crash, then raise —
        # 路线 C rule 4: the trigger writes phase=failed, we never do.
        try:
            await manager.patch_metadata(task_id, out)
        except Exception as e:
            logger.warning(f"[backfill-inbox] failed-run metadata patch: {e}")
        raise

    counts = out["counts"]
    subtitle = (
        f"dry-run: register {counts['to_register']} temp uploads, "
        f"mark {counts['to_mark_in_assets']} in_assets "
        f"({counts['already_registered']} already registered)"
        if dry_run
        else (
            f"registered {out['applied']['registered']} temp uploads, "
            f"marked {out['applied']['marked_in_assets']} in_assets "
            f"({counts['already_registered']} already registered)"
        )
    )
    try:
        await manager.complete(task_id, subtitle=subtitle[:200], metadata_patch=out)
    except Exception as e:
        logger.warning(f"[backfill-inbox] complete {task_id}: {e}")
    return out
