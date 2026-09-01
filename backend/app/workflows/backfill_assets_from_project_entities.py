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

THE FIVE STEPS, in the order the run performs them (spec §4):
1/2. legacy rows → assets + ``asset_project_refs`` + Default loadout (``_apply``)
1/2-tail. ``portrait_url`` / ``cover_url`` → ``cover_file_id`` + ``unsorted``
     slot, id-bearing URLs only (``_resolve_covers``)
4.   ``"{name} · {Kind}"`` entity canvases → ``canvases.asset_id``
     (``_link_entity_canvases``)
5.   ``generated_media.params.entity_kind/entity_id`` → ``source_asset_id``,
     plus the two inbox-state transitions (``_map_generated_media``)
Step 5 runs last because it judges ``asset_files``, which step 1/2-tail writes
into. Steps 3 (merge) and 6 (chat uploads, ``backfill_generated_inbox``) are
elsewhere; step 7 is a no-op placeholder in the spec.

``dry_run=True`` performs every read and every decision of all five steps and
writes nothing, so the preview an operator approves reports the same numbers
the live run will produce. Two places make that true rather than
approximately true: a canvas whose asset the plan is ABOUT to create counts as
linkable, and the cover step hands its resolved resource ids to step 5 so the
``in_assets`` count includes the attachments this run would make.

PARTIAL APPLY IS THE NORMAL FAILURE SHAPE, and the operator has to know it:
``_apply`` commits **per asset** (one ``write_scope()`` per planned row), and
the steps after it commit per row too. A run that raises half-way has
therefore already written everything up to the point it died — it is not
all-or-nothing and there is no undo. **Re-running the same plan IS the
recovery**: every write is idempotent (the adoption lookup, the
``(asset_id, project_id)`` PK, the ``asset_id IS NULL`` / ``review_state``
guards), so a second run finishes the work and adds nothing twice.
``reconcile_counts``'s failure message repeats this, because that message is
what an admin actually reads when a run goes red.

PERSONAL PROJECTS (``projects.team_id IS NULL``), the mapping P3 ruled:
their rows migrate to the **OWNER's personal team** — the same scope
``assets_router._project_scope_id`` resolves for ``GET /projects/{id}/assets``,
which is the read the workspace pages now perform. Anything else and the
migration would write where the UI does not look.

Merge semantics are SYMMETRIC with team projects: the mapped scope goes into
the same ``(scope_id, asset_type, lower(name))`` grouping as every other row,
so one person's same-name character across two of their personal projects
becomes ONE asset referenced by both — exactly what happens to two team
projects in one team. Nothing downstream special-cases it; step 4's canvas
reverse-parse gets the mapping for free because it reads the same
``project_team`` map.

The residue is one bucket: ``skipped_unmappable_personal`` counts rows whose
project has no team AND whose owner has no ``teams`` row with
``kind='personal'``. There is no scope to write them to (``assets.scope_id``
is a ``teams`` FK), so they are counted, never guessed at. The bucket was
called ``skipped_personal_project`` before P3 and meant *every* personal
project; it is renamed rather than reused so a pre-P3 run's stored metadata
cannot be read as if it meant the same thing.

Task Center: pass the dispatching admin's ``run_user_id`` (a real auth.users
row) so the run shows up; the all-zero system id never creates a row.

No ``limit`` parameter, unlike the other backfills: this one plans a *global*
grouping, so scanning a prefix of the rows would emit a plan whose merge groups
are wrong (two rows that merge could straddle the cutoff). ``workflow_kwargs``
in the admin router therefore only passes ``limit`` to workflows that take one.
"""

from __future__ import annotations

import re
from collections import OrderedDict
from typing import (
    AbstractSet,
    Any,
    Dict,
    Iterable,
    List,
    Optional,
    Sequence,
    Set,
    Tuple,
    TypedDict,
)

from dbos import DBOS
from loguru import logger
from sqlalchemy import String, cast, func, select, tuple_
from sqlalchemy import update as sa_update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.scope import system_request_scope
from app.db.session import read_scope, write_scope
from app.models import (
    AssetFiles,
    AssetLoadouts,
    AssetProjectRefs,
    Assets,
    Canvases,
    GeneratedMedia,
    ProjectCharacters,
    ProjectLibEntities,
    Projects,
    ResourceItems,
    Resources,
    Teams,
)
from app.services.assets.slots import UNSORTED

# Shared with the P1 inbox backfill ON PURPOSE — these two are the definition
# of "this file is in the asset library", and two copies of it are exactly the
# drift that would let the two backfills disagree about the same row:
#   * ``_resources_with_asset_files_stmt`` carries the join to ``assets`` that
#     keeps a soft-deleted asset's leftover ``asset_files`` rows from counting.
#   * ``_NOT_MARKABLE`` is the pair of states that must never be overwritten
#     ('in_assets' is already there; 'deleted' is a user's decision).
from app.workflows.backfill_generated_inbox import (
    _NOT_MARKABLE as _GENMEDIA_NOT_MARKABLE,
)
from app.workflows.backfill_generated_inbox import (
    _resources_with_asset_files_stmt,
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

# ── spec §4 step 1/2 tail: legacy cover URL → resources row ────────────────
#
# The only URL shapes we resolve are the two THIS APP builds with a resource id
# in the path. Everything else in ``portrait_url`` / ``cover_url`` is a free
# text column a user could have pasted anything into.
#
#   ``/api/v1/resources/{id}/cover|file|...``  — frontend/services/resourceService.ts
#   ``/media/{id}`` and ``/media/{id}/cover``  — frontend/utils/mediaUrl.ts
#
# Both may carry a query string (``?token=…&v=…``) and an absolute origin.
#
# ``/media/{id}`` is served for EITHER a resources id or a parsed_media id
# (app/main.py::serve_media_by_id), which sounds ambiguous and is not:
# ``generate_snowflake_id()`` (mig 050) draws from ONE global sequence, so an
# id is unique across tables. A candidate that resolves in ``resources`` is
# therefore a resource; one that does not is simply unresolved.
#
# Deliberately NOT matched: ``/media/{file_path}`` (the legacy by-path
# fallback), ``sb://`` object-store paths, and bare file names. Matching those
# would mean fuzzy-matching a path tail against ``resources.file_path`` —
# a guess that can attach the WRONG file to a character's cover slot, which is
# strictly worse than leaving ``attrs.legacy_cover_url`` for a human to look at.
_RESOURCE_URL_ID_RE = re.compile(r"/api/v\d+/resources/(\d+)(?:/|\?|#|$)")
_MEDIA_URL_ID_RE = re.compile(r"/media/(\d+)(?:/cover)?(?:\?|#|$)")


def resource_id_from_media_url(url: Optional[str]) -> Optional[int]:
    """Pure: the resource id an id-bearing media URL names, or None.

    None means "this task declines to guess", not "no such file" — the caller
    counts it as unresolved and the legacy URL stays in ``attrs``.
    """
    if not url or not isinstance(url, str):
        return None
    for pattern in (_RESOURCE_URL_ID_RE, _MEDIA_URL_ID_RE):
        m = pattern.search(url)
        if m:
            return int(m.group(1))
    return None


# ── spec §4 step 4: entity canvas name → the asset it belongs to ───────────
#
# VERIFIED against the code that CREATED these canvases, not against the spec:
#   frontend/components/workspace/CharacterLibrary.tsx:55
#       const canvasNameFor = (name: string) => `${name} · Character`;
#   frontend/components/workspace/EntityLibrary.tsx:120
#       const wanted = `${row.name} · ${meta.canvasSuffix}`;   // Location | Prop
# The separator is U+00B7 MIDDLE DOT with a space on each side (hexdumped, not
# eyeballed — U+00B7 and U+2027/U+30FB look alike in a diff).
_CANVAS_NAME_SEP = " · "
_CANVAS_SUFFIX_TO_KIND = {
    "Character": "character",
    "Location": "location",
    "Prop": "prop",
}
ENTITY_CANVAS_KINDS = tuple(_CANVAS_SUFFIX_TO_KIND.values())


def parse_entity_canvas_name(name: Optional[str], kind: str) -> Optional[str]:
    """Pure: the entity name inside ``"{name} · {Kind}"``, or None.

    ``rpartition`` on the LAST separator, so an entity whose own name contains
    " · " still parses (the suffix is the tail, not the second field).

    The parsed suffix must AGREE with the canvas's own ``kind`` column. The two
    are set together at creation, so a disagreement means a human renamed the
    canvas — and a rename is exactly the case where guessing is wrong. An
    unparseable or disagreeing name is left alone and counted, never linked.
    """
    if not name:
        return None
    head, sep, tail = name.rpartition(_CANVAS_NAME_SEP)
    if not sep:
        return None
    if _CANVAS_SUFFIX_TO_KIND.get(tail.strip()) != kind:
        return None
    return head.strip() or None


# ── spec §4 step 5: generated_media.params → the legacy row it came from ───
#
# ``params.entity_kind`` / ``params.entity_id`` are stamped by the canvas
# dispatcher (frontend/features/canvas-core/smart/entityRef.ts): a character
# card binds ``project_characters`` via ``character_id``, a location/prop card
# binds ``project_lib_entities`` via ``entity_id``. Both land in JSONB as
# STRINGS (``String(raw)``), so the id is parsed, never compared as text.
_ENTITY_KIND_TO_LEGACY_TABLE = {
    "character": "project_characters",
    "location": "project_lib_entities",
    "prop": "project_lib_entities",
}


def legacy_ref_for_entity(
    entity_kind: Optional[str], entity_id: Optional[str]
) -> Optional[LegacyRef]:
    """Pure: ``(legacy_table, legacy_id)`` for a stamped params pair, or None.

    None for an unknown kind or a non-numeric id — both are "we cannot say
    which legacy row this was", which the caller counts rather than guesses at.
    """
    table = _ENTITY_KIND_TO_LEGACY_TABLE.get(entity_kind or "")
    if table is None or entity_id is None:
        return None
    try:
        return (table, int(str(entity_id).strip()))
    except (TypeError, ValueError):
        return None


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
    unmappable_personal_project_ids: AbstractSet[int] = frozenset(),
) -> MigrationPlan:
    """Pure: group legacy rows by (scope, type, lower(name)); merge duplicates.

    Merge policy: first row's display name, longest description, union of tags
    (first-wins per key), first non-empty role_tag, first non-null cover, all
    project ids, all legacy ids.

    ``project_team`` is a project → SCOPE map, not a project → ``team_id``
    column read: ``_load_inputs`` has already resolved a personal project
    (``team_id IS NULL``) to its OWNER's personal team, so a personal project's
    rows fold through this function exactly like a team project's — same
    grouping key, same merge, no branch. That symmetry is the P3 ruling, and
    keeping it out of this function is what makes it true rather than
    approximately true.

    Two skip buckets, deliberately distinct:
      * ``skipped_unmappable_personal`` — the project has no team AND its owner
        has no personal team, so there is no ``teams`` row to scope the asset
        to. Counted, never guessed at.
      * ``skipped_unknown_project`` — the project id resolves to nothing at all.
    """
    groups: "OrderedDict[Tuple[int, str, str], PlannedAsset]" = OrderedDict()
    skipped_unknown = 0
    skipped_unmappable = 0

    def _fold(
        row: Dict[str, Any],
        asset_type: str,
        role_tag: str,
        cover: Optional[str],
        table: str,
    ) -> None:
        nonlocal skipped_unknown, skipped_unmappable
        pid = int(row["project_id"])
        if pid in unmappable_personal_project_ids:
            skipped_unmappable += 1
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
            "skipped_unmappable_personal": skipped_unmappable,
        },
    }


async def _load_inputs() -> Tuple[List[dict], List[dict], Dict[int, int], Set[int]]:
    """The legacy rows + a project → ASSET SCOPE map + the unmappable residue.

    A team project's scope is its ``team_id``. A personal project's
    (``team_id IS NULL``) is its OWNER's personal team — the same answer
    ``assets_router._project_scope_id`` gives ``GET /projects/{id}/assets``,
    which is the read the workspace pages perform, so the migration writes
    where the UI looks.

    That resolution is MIRRORED from ``resources_service._resolve_personal_team_id``
    rather than called: the helper answers one user per round-trip and this is
    a global scan, so a workspace with N personal-project owners would cost N
    sequential queries inside a read that is otherwise two statements. The
    mirror is safe to keep in step because the answer is pinned by a UNIQUE
    index — ``uq_teams_owner_personal`` (``owner_id`` WHERE
    ``kind = 'personal'``) — so "the owner's personal team" is one row, not a
    ``LIMIT 1`` over an ordered set that the two call sites could order
    differently.

    Owners with no personal team row land in the returned set: there is no
    ``teams`` id to scope their assets to (``assets.scope_id`` is a FK), and
    inventing one would put a user's characters in a library nothing reads.
    """

    def _rows(objs) -> List[dict]:
        return [{c.name: getattr(o, c.name) for c in o.__table__.columns} for o in objs]

    async with read_scope() as session:
        chars = _rows(
            (await session.execute(select(ProjectCharacters))).scalars().all()
        )
        ents = _rows(
            (await session.execute(select(ProjectLibEntities))).scalars().all()
        )
        projs = (
            await session.execute(
                select(Projects.id, Projects.team_id, Projects.owner_id)
            )
        ).all()
        owner_ids = sorted(
            {str(owner) for (_p, team, owner) in projs if team is None and owner}
        )
        personal_team_by_owner: Dict[str, int] = {}
        for chunk in _chunks(owner_ids, _ID_CHUNK):
            rows = (
                await session.execute(
                    select(cast(Teams.owner_id, String), Teams.id)
                    .where(Teams.kind == "personal")
                    .where(cast(Teams.owner_id, String).in_(chunk))
                )
            ).all()
            for owner, team_id in rows:
                personal_team_by_owner[str(owner)] = int(team_id)

    project_team: Dict[int, int] = {}
    unmappable_personal_project_ids: Set[int] = set()
    for pid, team_id, owner_id in projs:
        if team_id is not None:
            project_team[int(pid)] = int(team_id)
            continue
        mapped = personal_team_by_owner.get(str(owner_id)) if owner_id else None
        if mapped is None:
            unmappable_personal_project_ids.add(int(pid))
        else:
            project_team[int(pid)] = int(mapped)
    return chars, ents, project_team, unmappable_personal_project_ids


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
        f"project_refs expected={expected_refs} actual={actual_refs}"
        + detail
        + "; this run is PARTIALLY applied (writes commit per row, there is no"
        " rollback) — re-running the same backfill is the recovery: every write"
        " is idempotent and adds nothing twice"
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


class ApplyResult(TypedDict):
    """What ``_apply`` produced, plus the two indexes the later steps need.

    ``asset_id_by_key`` keys on ``(scope_id, asset_type, lower(name))`` — the
    same key ``_reconcile`` and the unique index use — and feeds the cover
    step. ``asset_id_by_ref`` keys on ``(legacy_table, legacy_id)`` and feeds
    the generated_media mapping.

    Both are built from the PLAN's own ``legacy`` lists, for created and
    ADOPTED assets alike. Re-deriving them from ``attrs.legacy_ids`` instead
    would be wrong in the one case that matters: adoption never writes
    ``attrs`` (a hand-made asset the run claimed keeps its own attrs), so a
    re-run — or any run with adoptions — would silently map nothing for those
    rows. The plan already knows which legacy rows belong to which key; that
    is the authority.
    """

    counts: Dict[str, int]
    asset_id_by_key: Dict[Tuple[int, str, str], int]
    asset_id_by_ref: Dict[LegacyRef, int]


async def _apply(plan: MigrationPlan, run_user_id: str) -> ApplyResult:
    created = existing = refs = 0
    asset_id_by_key: Dict[Tuple[int, str, str], int] = {}
    asset_id_by_ref: Dict[LegacyRef, int] = {}
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
            asset_id_by_key[_key(a["scope_id"], a["asset_type"], a["name"])] = asset_id
            for ref in a["legacy"]:
                asset_id_by_ref[(str(ref[0]), int(ref[1]))] = asset_id
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
    return {
        "counts": {
            "created": created,
            "existing": existing,
            "project_refs_added": refs,
        },
        "asset_id_by_key": asset_id_by_key,
        "asset_id_by_ref": asset_id_by_ref,
    }


# ── the four idempotency guards, as statements you can assert on ───────────
#
# Every write below repeats, in its WHERE clause, the predicate that SELECTED
# the row. In a single-threaded run that is redundant — the scan already
# excluded anything already done, so removing a guard changes no observable
# behaviour and no data-driven test can see it. The guard is there for the
# case a data-driven test CANNOT stage: two runs overlapping, or a user acting
# between this run's read and its write. That makes them exactly the kind of
# invariant this repo pins on the compiled statement instead
# (``asset_relations_repository._owned_probe_stmt`` is the same stance), so
# they live here as pure builders with
# ``tests/workflows/test_backfill_assets_write_guards.py`` asserting the
# predicate is present.


def cover_set_stmt(asset_id: int, resource_id: int):
    """Set a cover only while there ISN'T one. A cover the user picked after
    the first run outranks the legacy URL."""
    return (
        sa_update(Assets)
        .where(Assets.id == int(asset_id))
        .where(Assets.cover_file_id.is_(None))
        .where(Assets.deleted_at.is_(None))
        .values(cover_file_id=int(resource_id), updated_at=func.now())
    )


def cover_attach_stmt(asset_id: int, resource_id: int, attached_by: Optional[str]):
    """Attach to ``unsorted``, ON CONFLICT DO NOTHING.

    DO NOTHING rather than ``attach()``'s DO UPDATE: that repo method is the
    interactive path and refreshes ``note``/``loadout_id`` on purpose. A
    backfill re-running must not touch a slot someone has since organised.
    """
    stmt = pg_insert(AssetFiles).values(
        asset_id=int(asset_id),
        resource_id=int(resource_id),
        slot=UNSORTED,
        attached_by=attached_by,
    )
    return stmt.on_conflict_do_nothing(
        index_elements=[AssetFiles.asset_id, AssetFiles.resource_id, AssetFiles.slot]
    )


def canvas_link_stmt(canvas_id: int, asset_id: int):
    """Link an entity canvas only while it is UNLINKED — never re-point a link
    a human set by hand."""
    return (
        sa_update(Canvases)
        .where(Canvases.id == int(canvas_id))
        .where(Canvases.asset_id.is_(None))
        .values(asset_id=int(asset_id))
    )


def genmedia_map_stmt(gen_id: int, asset_id: int):
    """Stamp ``source_asset_id`` only while it is unset."""
    return (
        sa_update(GeneratedMedia)
        .where(GeneratedMedia.id == int(gen_id))
        .where(GeneratedMedia.source_asset_id.is_(None))
        .values(source_asset_id=int(asset_id))
    )


def genmedia_saved_stmt(gen_id: int):
    """unreviewed → saved. The source state is in the predicate, so this can
    never overwrite a state someone else moved the row to."""
    return (
        sa_update(GeneratedMedia)
        .where(GeneratedMedia.id == int(gen_id))
        .where(GeneratedMedia.review_state == "unreviewed")
        .values(review_state="saved")
    )


def genmedia_in_assets_stmt(gen_id: int):
    """→ in_assets, except from the two states that must never be overwritten
    (already there; or dismissed by the user)."""
    return (
        sa_update(GeneratedMedia)
        .where(GeneratedMedia.id == int(gen_id))
        .where(GeneratedMedia.review_state.not_in(_GENMEDIA_NOT_MARKABLE))
        .values(review_state="in_assets")
    )


# ── spec §4 steps 1/2-tail, 4, 5 ───────────────────────────────────────────
#
# All three run in BOTH modes. ``dry_run=True`` performs every read and every
# decision and writes nothing, so the numbers an operator reviews are the
# numbers the live run will produce — not a guess, and not silence. Each step
# reports its own orthogonal buckets: "resolved" and the several distinct
# reasons for "not resolved" are separate facts, and folding them into one
# number is how a run that skipped everything reads like a run with nothing
# to do.


async def _resolve_covers(
    plan: MigrationPlan,
    *,
    dry_run: bool,
    asset_id_by_key: Dict[Tuple[int, str, str], int],
    run_user_id: str,
) -> Tuple[Dict[str, int], Set[int]]:
    """Legacy ``portrait_url`` / ``cover_url`` → ``cover_file_id`` + ``unsorted``.

    Returns (counts, resolved resource ids). The id set is handed to the
    generated_media step so its dry-run ``in_assets`` count includes the files
    THIS run would attach — otherwise the preview would under-report by
    exactly the covers it is about to create.

    Resolution is id-only (see ``resource_id_from_media_url``) and then
    SCOPE-CHECKED: the resource must sit in the asset's own team scope
    (``resource_items.scope_id``). A cross-scope hit is dropped as unresolved,
    never attached — pulling another team's file onto a character sheet
    because a URL happened to be pasted there is a leak, not a migration.

    The ``Resources`` read is wrapped in ``system_request_scope``: ``Resources``
    is ``UserScoped`` and production runs with ``SCOPE_ENFORCE_RESOURCES=True``,
    so an unscoped SELECT would raise ``UnscopedQueryError`` and take the whole
    run down. A global reconciliation is system work by definition.

    Both writes are non-clobbering, which is what makes a re-run a no-op:
    ``cover_file_id`` is only set when it is still NULL (a cover a user picked
    later outranks the legacy URL), and the ``asset_files`` insert is
    ON CONFLICT DO NOTHING (re-attaching must not reshuffle or re-note a slot
    someone has since organised).
    """
    with_url = 0
    candidates: List[Tuple[Tuple[int, str, str], int, int]] = []  # key, scope, rid
    for a in plan["assets"]:
        url = a.get("cover_url")
        if not url:
            continue
        with_url += 1
        rid = resource_id_from_media_url(url)
        if rid is not None:
            candidates.append(
                (
                    _key(a["scope_id"], a["asset_type"], a["name"]),
                    int(a["scope_id"]),
                    rid,
                )
            )

    scopes_by_rid: Dict[int, Set[int]] = {}
    if candidates:
        rids = sorted({rid for (_k, _s, rid) in candidates})
        # One joined read for the whole plan. The join to ``resource_items`` is
        # what answers "which scope is this file in"; ``resources`` proves the
        # row exists at all (``asset_files.resource_id`` and
        # ``assets.cover_file_id`` are both FKs to it).
        async with system_request_scope(
            reason="backfill-assets-migration: resolve legacy cover urls"
        ):
            async with read_scope() as session:
                for chunk in _chunks(rids, _ID_CHUNK):
                    rows = (
                        await session.execute(
                            select(Resources.id, ResourceItems.scope_id)
                            .join(
                                ResourceItems,
                                ResourceItems.resource_id == Resources.id,
                            )
                            .where(Resources.id.in_(chunk))
                        )
                    ).all()
                    for rid, scope_id in rows:
                        scopes_by_rid.setdefault(int(rid), set()).add(int(scope_id))

    resolved: List[Tuple[Tuple[int, str, str], int]] = [
        (key, rid)
        for (key, scope_id, rid) in candidates
        if scope_id in scopes_by_rid.get(rid, ())
    ]
    resolved_ids = {rid for (_k, rid) in resolved}

    attached = 0
    if not dry_run:
        for key, rid in resolved:
            asset_id = asset_id_by_key.get(key)
            if asset_id is None:
                # Cannot happen after ``_apply`` (every planned key is live by
                # then). Skipping rather than raising keeps a surprise here
                # from destroying an otherwise complete run; the count below
                # is what makes the surprise visible.
                continue
            async with write_scope() as session:
                await session.execute(cover_set_stmt(asset_id, rid))
                await session.execute(cover_attach_stmt(asset_id, rid, run_user_id))
            attached += 1

    return (
        {
            "covers_with_url": with_url,
            "covers_resolved": len(resolved),
            # with_url − resolved, stated rather than left to be derived: this
            # is the number that says "N legacy covers are still only a URL in
            # attrs.legacy_cover_url".
            "covers_unresolved": with_url - len(resolved),
            "covers_attached": attached,
        },
        resolved_ids,
    )


async def _link_entity_canvases(
    plan: MigrationPlan,
    project_team: Dict[int, int],
    *,
    dry_run: bool,
) -> Dict[str, int]:
    """Spec §4 step 4: entity canvases → ``canvases.asset_id``.

    Scans live canvases of an entity kind that are not linked yet, parses the
    ``"{name} · {Kind}"`` title (see ``parse_entity_canvas_name``), and links
    to the same-scope same-type asset matched on ``lower(name)`` — the same
    match ``_apply`` and the unique index use.

    A canvas's scope comes from ``project_team`` (``project_id`` is NOT NULL),
    which is the SAME map ``plan_migration`` folds on — so a personal project's
    entity canvases link to the assets this run just created in its owner's
    personal team, with no branch here. Getting the P3 personal mapping for
    free is the reason this step takes the map rather than re-reading
    ``projects.team_id``: two derivations of "which scope is this project's"
    is how a canvas ends up pointing at nothing.

    ``canvases_no_scope`` is therefore now the residue only — a canvas whose
    project is unknown, or is a personal project whose owner has no personal
    team (the same rows ``skipped_unmappable_personal`` counts).

    In dry-run the planned assets do not exist yet, so a key that the plan is
    about to create counts as resolvable. In a live run the same lookup finds
    it in the database, so the two modes report the same number for the same
    data — which is the whole point of a preview.

    ``asset_id IS NULL`` in both the scan and the UPDATE makes the step
    idempotent and non-clobbering: a re-run does not even see the canvases it
    linked, and never overwrites a link someone set by hand.
    """
    counts = {
        "canvases_scanned": 0,
        "canvases_linked": 0,
        "skipped_unparsed_canvas": 0,
        "canvases_no_asset": 0,
        "canvases_no_scope": 0,
    }
    async with read_scope() as session:
        rows = (
            await session.execute(
                select(Canvases.id, Canvases.project_id, Canvases.kind, Canvases.name)
                .where(Canvases.kind.in_(ENTITY_CANVAS_KINDS))
                .where(Canvases.asset_id.is_(None))
                .where(Canvases.deleted_at.is_(None))
                .order_by(Canvases.id)
            )
        ).all()
    counts["canvases_scanned"] = len(rows)

    parsed: List[Tuple[int, Tuple[int, str, str]]] = []  # canvas id, asset key
    for canvas_id, project_id, kind, name in rows:
        scope_id = project_team.get(int(project_id))
        if scope_id is None:
            counts["canvases_no_scope"] += 1
            continue
        entity_name = parse_entity_canvas_name(name, kind)
        if entity_name is None:
            counts["skipped_unparsed_canvas"] += 1
            continue
        parsed.append((int(canvas_id), _key(scope_id, kind, entity_name)))

    live: Dict[Tuple[int, str, str], int] = {}
    if parsed:
        by_scope: "OrderedDict[int, Set[Tuple[str, str]]]" = OrderedDict()
        for _cid, (scope_id, asset_type, lowered) in parsed:
            by_scope.setdefault(scope_id, set()).add((asset_type, lowered))
        async with read_scope() as session:
            for scope_id, keys in by_scope.items():
                for chunk in _chunks(sorted(keys), _KEY_CHUNK):
                    found = (
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
                    for asset_id, asset_type, lowered in found:
                        live[(scope_id, asset_type, lowered)] = int(asset_id)

    planned_keys = {
        _key(a["scope_id"], a["asset_type"], a["name"]) for a in plan["assets"]
    }
    for canvas_id, key in parsed:
        asset_id = live.get(key)
        if asset_id is None:
            if dry_run and key in planned_keys:
                counts["canvases_linked"] += 1
                continue
            counts["canvases_no_asset"] += 1
            continue
        if not dry_run:
            async with write_scope() as session:
                await session.execute(canvas_link_stmt(canvas_id, asset_id))
        counts["canvases_linked"] += 1
    return counts


async def _map_generated_media(
    plan: MigrationPlan,
    *,
    dry_run: bool,
    asset_id_by_ref: Dict[LegacyRef, int],
    extra_asset_file_resource_ids: Iterable[int] = (),
) -> Dict[str, int]:
    """Spec §4 step 5 — three independent facts about ``generated_media``.

    1. ``params.entity_kind`` + ``params.entity_id`` → ``source_asset_id``.
    2. ``promoted_resource_id IS NOT NULL`` and still ``unreviewed`` → ``saved``.
    3. promoted resource attached to a LIVE asset → ``in_assets``.

    (2) runs before (3) on purpose: a row that is both promoted and attached
    should end at ``in_assets``, the stronger statement, and ``saved`` is not
    in ``_GENMEDIA_NOT_MARKABLE`` so (3) still claims it.

    This step runs LAST in the run because (3) reads ``asset_files`` — the
    cover step writes into that table, and a row promoted from a resource this
    run just attached belongs in ``in_assets`` on the same run, not the next
    one. ``extra_asset_file_resource_ids`` carries the same fact into dry-run,
    where the attachment has not happened yet.

    (1) is scope-checked: a generation is mapped only to an asset in its own
    ``scope_id``. A mismatch is a data anomaly, not a mapping — counted, never
    written. All three writes carry the predicate that selected the row, so a
    re-run changes nothing (``source_asset_id IS NULL``, ``review_state`` in
    the state the transition starts from).
    """
    counts = {
        "genmedia_mapped": 0,
        "genmedia_unmatched": 0,
        "genmedia_scope_mismatch": 0,
        "genmedia_saved": 0,
        "genmedia_in_assets": 0,
    }
    scope_by_ref: Dict[LegacyRef, int] = {}
    for a in plan["assets"]:
        for ref in a["legacy"]:
            scope_by_ref[(str(ref[0]), int(ref[1]))] = int(a["scope_id"])

    async with read_scope() as session:
        map_rows = (
            await session.execute(
                select(
                    GeneratedMedia.id,
                    GeneratedMedia.scope_id,
                    GeneratedMedia.params["entity_kind"].astext,
                    GeneratedMedia.params["entity_id"].astext,
                )
                .where(GeneratedMedia.source_asset_id.is_(None))
                .where(GeneratedMedia.params["entity_kind"].astext.is_not(None))
                .where(GeneratedMedia.params["entity_id"].astext.is_not(None))
                .order_by(GeneratedMedia.id)
            )
        ).all()
        save_ids = [
            int(r)
            for (r,) in (
                await session.execute(
                    select(GeneratedMedia.id)
                    .where(GeneratedMedia.promoted_resource_id.is_not(None))
                    .where(GeneratedMedia.review_state == "unreviewed")
                    .order_by(GeneratedMedia.id)
                )
            ).all()
        ]
        # The candidate set is decided in Python rather than with an
        # ``IN (subquery)`` so dry-run can add the resources the cover step is
        # about to attach — a preview that omits its own effects is not a
        # preview. The attached-resource set itself still comes from the
        # shared statement, so "attached to a live asset" cannot drift.
        attached_ids = {
            int(x)
            for x in (
                (await session.execute(_resources_with_asset_files_stmt()))
                .scalars()
                .all()
            )
        }
        mark_rows = (
            await session.execute(
                select(GeneratedMedia.id, GeneratedMedia.promoted_resource_id)
                .where(GeneratedMedia.promoted_resource_id.is_not(None))
                .where(GeneratedMedia.review_state.not_in(_GENMEDIA_NOT_MARKABLE))
                .order_by(GeneratedMedia.id)
            )
        ).all()

    attached_ids |= {int(x) for x in extra_asset_file_resource_ids}

    for gen_id, gen_scope, entity_kind, entity_id in map_rows:
        ref = legacy_ref_for_entity(entity_kind, entity_id)
        if ref is None or ref not in scope_by_ref:
            counts["genmedia_unmatched"] += 1
            continue
        if int(gen_scope) != scope_by_ref[ref]:
            counts["genmedia_scope_mismatch"] += 1
            continue
        asset_id = asset_id_by_ref.get(ref)
        if asset_id is None:
            if dry_run:
                # The asset does not exist yet; the plan says it will, and the
                # ref/scope checks above already passed.
                counts["genmedia_mapped"] += 1
                continue
            counts["genmedia_unmatched"] += 1
            continue
        async with write_scope() as session:
            await session.execute(genmedia_map_stmt(int(gen_id), asset_id))
        counts["genmedia_mapped"] += 1

    for gen_id in save_ids:
        if not dry_run:
            async with write_scope() as session:
                await session.execute(genmedia_saved_stmt(gen_id))
        counts["genmedia_saved"] += 1

    for gen_id, promoted_resource_id in mark_rows:
        if int(promoted_resource_id) not in attached_ids:
            continue
        if not dry_run:
            async with write_scope() as session:
                await session.execute(genmedia_in_assets_stmt(int(gen_id)))
        counts["genmedia_in_assets"] += 1

    return counts


async def _run_extra_steps(
    plan: MigrationPlan,
    project_team: Dict[int, int],
    *,
    dry_run: bool,
    run_user_id: str,
    asset_id_by_key: Optional[Dict[Tuple[int, str, str], int]] = None,
    asset_id_by_ref: Optional[Dict[LegacyRef, int]] = None,
) -> Dict[str, Dict[str, int]]:
    """Steps 1/2-tail → 4 → 5, in the order their inputs become true.

    Covers first (it writes ``asset_files``), canvases next (independent), and
    generated_media last so its ``in_assets`` judgement sees what the cover
    step attached. In dry-run the maps are empty and every step falls back to
    the plan; nothing is written.
    """
    covers, cover_resource_ids = await _resolve_covers(
        plan,
        dry_run=dry_run,
        asset_id_by_key=asset_id_by_key or {},
        run_user_id=run_user_id,
    )
    canvases = await _link_entity_canvases(plan, project_team, dry_run=dry_run)
    genmedia = await _map_generated_media(
        plan,
        dry_run=dry_run,
        asset_id_by_ref=asset_id_by_ref or {},
        extra_asset_file_resource_ids=cover_resource_ids if dry_run else (),
    )
    return {"covers": covers, "canvases": canvases, "generated_media": genmedia}


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
        chars, ents, project_team, unmappable = await _load_inputs()
        plan = plan_migration(chars, ents, project_team, unmappable)
        logger.info(
            "[backfill-assets] plan counts={} dry_run={}", plan["counts"], dry_run
        )
        out["counts"] = plan["counts"]
        out["merges"] = plan["merges"][:_MERGES_CAP]
        if dry_run:
            # Steps 4/5 and the cover resolution are read-only here, but they
            # are NOT skipped: a dry-run whose report stops at "N assets" tells
            # an operator nothing about the three steps that touch canvases,
            # generations and cover slots.
            out.update(
                await _run_extra_steps(
                    plan, project_team, dry_run=True, run_user_id=owner
                )
            )
        else:
            applied = await _apply(plan, owner)
            out["applied"] = applied["counts"]
            out.update(
                await _run_extra_steps(
                    plan,
                    project_team,
                    dry_run=False,
                    run_user_id=owner,
                    asset_id_by_key=applied["asset_id_by_key"],
                    asset_id_by_ref=applied["asset_id_by_ref"],
                )
            )
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
    # I5: the skip buckets belong in the line a human reads. A headline of
    # "0 assets from 42 rows, 0 merges" is indistinguishable from "nothing to
    # migrate" when the truth is "42 rows had nowhere to go". Since P3 maps
    # personal projects onto their owner's personal team, a non-zero
    # `unmappable` here is the narrow residue — projects whose owner has no
    # personal team row at all — and it is the number an operator must chase.
    skipped = (
        f", skipped {counts['skipped_unmappable_personal']} unmappable"
        f" / {counts['skipped_unknown_project']} unknown"
    )
    # Steps 1/2-tail, 4 and 5 get their own clause for the same reason the skip
    # buckets do: a run that mapped nothing and a run with nothing to map are
    # different facts, and only the line a human reads can tell them apart.
    cov, cnv, gen = out["covers"], out["canvases"], out["generated_media"]
    extras = (
        f", covers {cov['covers_resolved']}/{cov['covers_with_url']}"
        f", canvases {cnv['canvases_linked']}/{cnv['canvases_scanned']}"
        f", genmedia {gen['genmedia_mapped']} mapped"
        f" / {gen['genmedia_saved']} saved / {gen['genmedia_in_assets']} in_assets"
    )
    subtitle = (
        f"dry-run: {counts['assets']} assets from "
        f"{counts['characters']}+{counts['entities']} rows, "
        f"{counts['merges']} merges{skipped}{extras}"
        if dry_run
        else (
            f"{out['applied']['created']} created, {out['applied']['existing']} existing, "
            f"{out['applied']['project_refs_added']} project refs{skipped}{extras}"
        )
    )
    try:
        await manager.complete(task_id, subtitle=subtitle[:200], metadata_patch=out)
    except Exception as e:
        logger.warning(f"[backfill-assets] complete {task_id}: {e}")
    return out
