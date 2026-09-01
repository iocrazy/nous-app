"""backfill_assets_from_project_entities — project_characters + project_lib_entities
→ team-scoped assets (spec §4). THE BACKFILL PARADIGM (backfill_issue_scope.py):
DBOS workflow, ``dry_run=True`` by default, idempotent, failures raise.

P0 shipped the PLANNER and the dry-run path only; execution was blocked by a
``_reject_execution_until_p3()`` guard because ``_apply`` / ``_reconcile`` had
never run against a database. P3 removes that guard: both paths are now
exercised against a real PostgreSQL by
``tests/db/test_assets_migration_integration.py``. ``dry_run=True`` is still the
default and still the way to review the merge list first — same-name-same-type
rows inside one team MERGE into one asset (the first cross-project reuse win,
and the one place a wrong merge would hurt).

ADOPTION, and why reconciliation must share its definition of "done":
``_apply`` claims an existing same-name asset **whatever its source** — a
migrated row on a re-run, but also one a user created by hand. That merge is the
point (the legacy row and the hand-made asset are the same character). It also
means "how many assets did this produce" cannot be answered by counting
``source='migrated'``: a run that adopted N user-created assets would under-report
by exactly N and read as a failure. ``_reconcile`` therefore asks the same
question ``_apply`` asks — does a live asset exist at ``(scope_id, asset_type,
lower(name))`` — for every planned key, and counts only the project refs the plan
actually asked for (an adopted asset may carry older refs to projects outside
this plan; those are not this run's business).

Task Center: pass the dispatching admin's ``run_user_id`` (a real auth.users
row) so the run shows up; the all-zero system id never creates a row.

No ``limit`` parameter, unlike the other backfills: this one plans a *global*
grouping, so scanning a prefix of the rows would emit a plan whose merge groups
are wrong (two rows that merge could straddle the cutoff). ``workflow_kwargs``
in the admin router therefore only passes ``limit`` to workflows that take one.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import (
    AbstractSet,
    Any,
    Dict,
    List,
    Optional,
    Sequence,
    Set,
    Tuple,
    TypedDict,
)

from dbos import DBOS
from loguru import logger
from sqlalchemy import func, select, tuple_

from app.db.session import read_scope, write_scope
from app.models import (
    AssetLoadouts,
    AssetProjectRefs,
    Assets,
    ProjectCharacters,
    ProjectLibEntities,
    Projects,
)

SYSTEM_RUN_USER_ID = "00000000-0000-0000-0000-000000000000"

# Cap the merge list carried in the run's report/metadata (jsonb). Counts are
# always exact — compare ``counts["merges"]`` against the list length to see
# whether the review list was truncated.
_MERGES_CAP = 200

# Cap the missing-key lists the reconciliation report carries. Exact counts sit
# beside them (``*_missing_count`` + ``*_missing_truncated``), so a truncated
# list is never mistaken for the whole story.
_MISSING_CAP = 20

# Batch sizes for the reconciliation lookups. The plan is global (no ``limit``),
# so a large workspace can hold thousands of keys — one IN list per scope would
# be a single enormous statement.
_KEY_CHUNK = 500
_ID_CHUNK = 1000

LegacyRef = Tuple[str, int]


class PlannedAsset(TypedDict):
    scope_id: int
    asset_type: str
    name: str
    role_tag: str
    description: str
    tags: dict
    cover_url: Optional[str]
    source: str
    project_ids: List[int]
    legacy: List[LegacyRef]


class Merge(TypedDict):
    scope_id: int
    asset_type: str
    name: str
    legacy: List[LegacyRef]


class MigrationPlan(TypedDict):
    assets: List[PlannedAsset]
    merges: List[Merge]
    counts: Dict[str, int]


def _key(scope_id: int, asset_type: str, name: str) -> Tuple[int, str, str]:
    return (scope_id, asset_type, name.strip().lower())


def plan_migration(
    characters: List[Dict[str, Any]],
    entities: List[Dict[str, Any]],
    project_team: Dict[int, int],
    personal_project_ids: AbstractSet[int] = frozenset(),
) -> MigrationPlan:
    """Pure: group legacy rows by (team, type, lower(name)); merge duplicates.

    Merge policy: first row's display name, longest description, union of tags
    (first-wins per key), first non-empty role_tag, first non-null cover, all
    project ids, all legacy ids.

    Two skip buckets, deliberately distinct: ``skipped_personal_project`` are
    rows whose project has ``team_id IS NULL`` (a real project with no team —
    P3 decides how those map to a personal team), ``skipped_unknown_project``
    are rows whose project id resolves to nothing at all.
    """
    groups: "OrderedDict[Tuple[int, str, str], PlannedAsset]" = OrderedDict()
    skipped_unknown = 0
    skipped_personal = 0

    def _fold(
        row: Dict[str, Any],
        asset_type: str,
        role_tag: str,
        cover: Optional[str],
        table: str,
    ) -> None:
        nonlocal skipped_unknown, skipped_personal
        pid = int(row["project_id"])
        if pid in personal_project_ids:
            skipped_personal += 1
            return
        team = project_team.get(pid)
        if team is None:
            skipped_unknown += 1
            return
        k = _key(team, asset_type, row["name"])
        legacy: LegacyRef = (table, int(row["id"]))
        if k not in groups:
            groups[k] = {
                "scope_id": team,
                "asset_type": asset_type,
                "name": row["name"].strip(),
                "role_tag": role_tag or "",
                "description": row.get("description") or "",
                "tags": dict(row.get("tags") or {}),
                "cover_url": cover,
                "source": "migrated",
                "project_ids": [pid],
                "legacy": [legacy],
            }
            return
        g = groups[k]
        if pid not in g["project_ids"]:
            g["project_ids"].append(pid)
        g["legacy"].append(legacy)
        if len(row.get("description") or "") > len(g["description"]):
            g["description"] = row["description"]
        for tk, tv in (row.get("tags") or {}).items():
            g["tags"].setdefault(tk, tv)
        if g["cover_url"] is None and cover:
            g["cover_url"] = cover
        if not g["role_tag"] and role_tag:
            g["role_tag"] = role_tag

    for c in characters:
        _fold(
            c,
            "character",
            c.get("role_tag") or "",
            c.get("portrait_url"),
            "project_characters",
        )
    for e in entities:
        _fold(
            e,
            e["entity_type"],
            e.get("badge_tag") or "",
            e.get("cover_url"),
            "project_lib_entities",
        )

    assets = list(groups.values())
    merges: List[Merge] = [
        {
            "scope_id": a["scope_id"],
            "asset_type": a["asset_type"],
            "name": a["name"],
            "legacy": a["legacy"],
        }
        for a in assets
        if len(a["legacy"]) > 1
    ]
    return {
        "assets": assets,
        "merges": merges,
        "counts": {
            "characters": len(characters),
            "entities": len(entities),
            "assets": len(assets),
            "merges": len(merges),
            "skipped_unknown_project": skipped_unknown,
            "skipped_personal_project": skipped_personal,
        },
    }


async def _load_inputs() -> Tuple[List[dict], List[dict], Dict[int, int], Set[int]]:
    def _rows(objs) -> List[dict]:
        return [{c.name: getattr(o, c.name) for c in o.__table__.columns} for o in objs]

    async with read_scope() as session:
        chars = _rows(
            (await session.execute(select(ProjectCharacters))).scalars().all()
        )
        ents = _rows(
            (await session.execute(select(ProjectLibEntities))).scalars().all()
        )
        projs = (await session.execute(select(Projects.id, Projects.team_id))).all()
    project_team: Dict[int, int] = {}
    personal_project_ids: Set[int] = set()
    for pid, team_id in projs:
        if team_id is None:
            personal_project_ids.add(int(pid))
        else:
            project_team[int(pid)] = int(team_id)
    return chars, ents, project_team, personal_project_ids


def reconcile_counts(
    expected_assets: int,
    actual_assets: int,
    expected_refs: int,
    actual_refs: int,
    missing_assets: Sequence[Dict[str, Any]] = (),
    missing_refs: Sequence[Dict[str, Any]] = (),
) -> None:
    """Pure: raise unless the database agrees with the plan (spec §4).

    Kept separate from the queries so the arithmetic is testable, and stated as
    a comparison against *database* counts on purpose: the previous version
    compared ``created + existing`` against ``len(plan["assets"])``, which the
    apply loop makes true by construction — a check that cannot fail is not a
    check.

    ``missing_assets`` / ``missing_refs`` are for the message only. A bare
    "expected=41 actual=40" cannot tell an operator WHICH key failed, and the
    keys are what they need to go look at; they are already capped by
    :func:`_reconcile` before they get here.
    """
    if expected_assets == actual_assets and expected_refs == actual_refs:
        return
    detail = ""
    if missing_assets:
        detail += f"; missing assets (up to {_MISSING_CAP}): {list(missing_assets)}"
    if missing_refs:
        detail += f"; missing project refs (up to {_MISSING_CAP}): {list(missing_refs)}"
    raise RuntimeError(
        "[backfill-assets] reconciliation failed: "
        f"assets expected={expected_assets} actual={actual_assets}; "
        f"project_refs expected={expected_refs} actual={actual_refs}" + detail
    )


def _chunks(items: List[Any], size: int) -> List[List[Any]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


async def _reconcile(plan: MigrationPlan) -> Dict[str, Any]:
    """Query-only: report what the DB actually holds for THIS plan's keys.

    Deliberately does not raise. The workflow stores this report in the run's
    metadata *before* calling :func:`reconcile_counts`, so a failed
    reconciliation leaves behind the keys that are missing rather than only a
    count that disagrees.

    Existence is asked exactly the way ``_apply`` asks it — a live asset at
    ``(scope_id, asset_type, lower(name))``, **no source filter**. Filtering on
    ``source='migrated'`` (the pre-P3 form) contradicted ``_apply``'s deliberate
    adoption of same-name user-created assets: every adopted asset would be
    counted as absent, so a correct run reported failure.

    Project refs are counted per planned ``(asset_id, project_id)`` pair, not as
    "all refs of these assets": an adopted asset can already carry refs to
    projects this plan never mentions, and those must not inflate the actual
    count past the expected one.

    Re-running the same plan keeps this true: every planned asset exists exactly
    once (the idempotency lookup finds it) and every planned project ref exists
    exactly once (``AssetProjectRefs`` is PK'd on ``(asset_id, project_id)``).
    """
    expected_assets = plan["counts"]["assets"]
    expected_refs = sum(len(a["project_ids"]) for a in plan["assets"])

    by_scope: "OrderedDict[int, List[PlannedAsset]]" = OrderedDict()
    for a in plan["assets"]:
        by_scope.setdefault(a["scope_id"], []).append(a)

    found: Dict[Tuple[int, str, str], int] = {}
    ref_pairs: Set[Tuple[int, int]] = set()
    if by_scope:
        async with read_scope() as session:
            for scope_id, group in by_scope.items():
                keys = sorted(
                    {(a["asset_type"], a["name"].strip().lower()) for a in group}
                )
                for chunk in _chunks(keys, _KEY_CHUNK):
                    rows = (
                        await session.execute(
                            select(
                                Assets.id, Assets.asset_type, func.lower(Assets.name)
                            )
                            .where(Assets.scope_id == scope_id)
                            .where(Assets.deleted_at.is_(None))
                            .where(
                                tuple_(Assets.asset_type, func.lower(Assets.name)).in_(
                                    chunk
                                )
                            )
                        )
                    ).all()
                    for asset_id, asset_type, lowered in rows:
                        found[(scope_id, asset_type, lowered)] = int(asset_id)
            asset_ids = sorted(set(found.values()))
            for chunk in _chunks(asset_ids, _ID_CHUNK):
                rows = (
                    await session.execute(
                        select(
                            AssetProjectRefs.asset_id, AssetProjectRefs.project_id
                        ).where(AssetProjectRefs.asset_id.in_(chunk))
                    )
                ).all()
                ref_pairs.update((int(x), int(y)) for x, y in rows)

    present_assets = present_refs = 0
    missing_assets: List[Dict[str, Any]] = []
    missing_refs: List[Dict[str, Any]] = []
    for a in plan["assets"]:
        asset_id = found.get(_key(a["scope_id"], a["asset_type"], a["name"]))
        if asset_id is None:
            # Its refs cannot exist either — report them so the two numbers in
            # the failure message stay consistent with each other.
            missing_assets.append(
                {
                    "scope_id": str(a["scope_id"]),
                    "asset_type": a["asset_type"],
                    "name": a["name"],
                }
            )
            missing_refs.extend(
                {"name": a["name"], "project_id": str(pid), "asset_id": None}
                for pid in a["project_ids"]
            )
            continue
        present_assets += 1
        for pid in a["project_ids"]:
            if (asset_id, int(pid)) in ref_pairs:
                present_refs += 1
            else:
                missing_refs.append(
                    {
                        "name": a["name"],
                        "project_id": str(pid),
                        "asset_id": str(asset_id),
                    }
                )

    # BIGINT ids are stringified above: this dict lands in task_tracking.metadata
    # (jsonb) and is read by JS, where a Snowflake id over 2^53 loses precision.
    return {
        "assets_expected": expected_assets,
        "assets_present": present_assets,
        "assets_missing": missing_assets[:_MISSING_CAP],
        "assets_missing_count": len(missing_assets),
        "assets_missing_truncated": len(missing_assets) > _MISSING_CAP,
        "project_refs_expected": expected_refs,
        "project_refs_present": present_refs,
        "project_refs_missing": missing_refs[:_MISSING_CAP],
        "project_refs_missing_count": len(missing_refs),
        "project_refs_missing_truncated": len(missing_refs) > _MISSING_CAP,
    }


async def _apply(plan: MigrationPlan, run_user_id: str) -> Dict[str, int]:
    created = existing = refs = 0
    for a in plan["assets"]:
        async with write_scope() as session:
            # lower(name), not ILIKE: the name is data, and ``%``/``_`` inside
            # it would be LIKE wildcards — an asset called "old_zhang" must not
            # claim "oldXzhang". This is also the expression the unique index
            # uq_assets_scope_type_name is built on.
            found = (
                await session.execute(
                    select(Assets)
                    .where(Assets.scope_id == a["scope_id"])
                    .where(Assets.asset_type == a["asset_type"])
                    .where(func.lower(Assets.name) == a["name"].strip().lower())
                    .where(Assets.deleted_at.is_(None))
                    .limit(1)
                )
            ).scalar_one_or_none()
            if found is None:
                obj = Assets(
                    scope_id=a["scope_id"],
                    asset_type=a["asset_type"],
                    name=a["name"],
                    role_tag=a["role_tag"],
                    description=a["description"],
                    tags=a["tags"],
                    source="migrated",
                    attrs={
                        "legacy_ids": [list(x) for x in a["legacy"]],
                        "merged_from": (
                            [list(x) for x in a["legacy"]]
                            if len(a["legacy"]) > 1
                            else []
                        ),
                        "legacy_cover_url": a["cover_url"],
                    },
                    created_by=run_user_id,
                )
                session.add(obj)
                await session.flush()
                asset_id = int(obj.id)
                created += 1
                if a["asset_type"] == "character":
                    session.add(
                        AssetLoadouts(
                            asset_id=asset_id, name="Default", is_default=True
                        )
                    )
            else:
                asset_id = int(found.id)
                existing += 1
            have = {
                int(p)
                for (p,) in (
                    await session.execute(
                        select(AssetProjectRefs.project_id).where(
                            AssetProjectRefs.asset_id == asset_id
                        )
                    )
                ).all()
            }
            for pid in a["project_ids"]:
                if pid not in have:
                    session.add(
                        AssetProjectRefs(
                            asset_id=asset_id, project_id=pid, linked_by=run_user_id
                        )
                    )
                    refs += 1
    return {"created": created, "existing": existing, "project_refs_added": refs}


@DBOS.workflow()
async def backfill_assets_from_project_entities(
    dry_run: bool = True, run_user_id: Optional[str] = None
) -> Dict[str, Any]:
    """Plan (and in P3, execute) the legacy project library → assets migration."""
    from app.services.infra.unified_task_manager import get_task_manager

    manager = get_task_manager()
    task_id = DBOS.workflow_id
    owner = run_user_id or SYSTEM_RUN_USER_ID

    try:
        await manager.create(
            user_id=owner,
            task_type="backfill",
            title="Backfill: project library → team assets",
            subtitle=f"dry_run={dry_run}",
            dbos_workflow_id=task_id,
            metadata={
                "backfill": "assets_from_project_entities",
                "dry_run": dry_run,
            },
        )
    except Exception as e:
        logger.warning(
            f"[backfill-assets] create task_tracking failed (non-fatal): {e}"
        )
    try:
        await manager.start(task_id, user_id=owner, task_type="backfill")
    except Exception as e:
        logger.warning(f"[backfill-assets] start {task_id}: {e}")

    out: Dict[str, Any] = {"dry_run": dry_run}
    try:
        chars, ents, project_team, personal_project_ids = await _load_inputs()
        plan = plan_migration(chars, ents, project_team, personal_project_ids)
        logger.info(
            "[backfill-assets] plan counts={} dry_run={}", plan["counts"], dry_run
        )
        out["counts"] = plan["counts"]
        out["merges"] = plan["merges"][:_MERGES_CAP]
        if not dry_run:
            out["applied"] = await _apply(plan, owner)
            # Reconciliation (spec §4) — against DB counts, not our own tally.
            # The report is stored BEFORE the check raises, so a failed run's
            # metadata names the keys that are missing.
            report = await _reconcile(plan)
            out["reconciled"] = report
            reconcile_counts(
                report["assets_expected"],
                report["assets_present"],
                report["project_refs_expected"],
                report["project_refs_present"],
                missing_assets=report["assets_missing"],
                missing_refs=report["project_refs_missing"],
            )
    except Exception:
        # Persist whatever was computed before the crash, then raise —
        # 路线 C rule 4: the trigger writes phase=failed, we never do.
        try:
            await manager.patch_metadata(task_id, out)
        except Exception as e:
            logger.warning(f"[backfill-assets] failed-run metadata patch: {e}")
        raise

    counts = out["counts"]
    # I5: the skip buckets belong in the line a human reads. On a workspace of
    # mostly personal projects the headline alone said "0 assets from 42 rows,
    # 0 merges" — indistinguishable from "nothing to migrate" when the truth is
    # "42 rows are waiting on a P3 decision".
    skipped = (
        f", skipped {counts['skipped_personal_project']} personal"
        f" / {counts['skipped_unknown_project']} unknown"
    )
    subtitle = (
        f"dry-run: {counts['assets']} assets from "
        f"{counts['characters']}+{counts['entities']} rows, "
        f"{counts['merges']} merges{skipped}"
        if dry_run
        else (
            f"{out['applied']['created']} created, {out['applied']['existing']} existing, "
            f"{out['applied']['project_refs_added']} project refs{skipped}"
        )
    )
    try:
        await manager.complete(task_id, subtitle=subtitle[:200], metadata_patch=out)
    except Exception as e:
        logger.warning(f"[backfill-assets] complete {task_id}: {e}")
    return out
