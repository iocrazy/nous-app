"""Workflow advance/retreat predicate + executor (M1 PR-B B3).

``compute_advance_preview`` (pure read) and ``execute_advance`` share ONE
predicate: the executor computes the preview first and refuses to mutate unless
``will_advance`` is True (the #1400 discipline — the server never trusts a
client-side copy of the rules, and preview/execute can never drift).

Forward advance leaves the current group (its review-required mirror issues must
be done, its deliverable-required nodes must have a filed file), closes the
current group's mirror issues (open sub-issues stay open, flagged in the
preview), moves the cursor to the next non-skipped group, and idempotently opens
that group's mirror issues. Retreat moves the cursor back one group and reopens
that group's mirror issues (transition → in_progress). Agent owners are never
dispatched — arrival only assigns.

Dependencies are resolved through module-level seams (``resolve_effective_role``
and the ``get_*_repository`` getters) so the unit tests can fake every branch
without a database, mirroring ``project_stage_issues``' test pattern.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from loguru import logger

from app.core.workflow_roles import WRITE_ROLES, resolve_effective_role
from app.schemas.workflow import (
    BLOCK_DELIVERABLE_MISSING,
    BLOCK_DEPS_PENDING,
    BLOCK_FORM_INCOMPLETE,
    BLOCK_NO_NEXT,
    BLOCK_NOT_MANAGER_OR_EDITOR,
    BLOCK_REVIEW_PENDING,
    AdvanceNodeRef,
    AdvancePreview,
)
from app.services.library.project_stage_issues import (
    ORIGIN_KIND,
    build_stage_origin_id,
    ensure_node_issues,
)
from app.services.workflow.node_folders import ensure_node_folders
from app.services.workflow.stage_notifications import notify_stage_event

# Issue statuses that count as closed for a stage-mirror issue.
_TERMINAL = frozenset({"done", "cancelled"})


def _build_groups(nodes: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    """Ordered list of non-skipped node groups.

    Nodes are grouped by ``parallel_group`` (equal non-null values → one group,
    ordered by first appearance); a null group is its own singleton. Skipped
    nodes are dropped — they are not advance stops.
    """
    ordered = sorted(nodes, key=lambda n: n.get("sort_order", 0))
    groups: List[List[Dict[str, Any]]] = []
    by_pg: Dict[Any, List[Dict[str, Any]]] = {}
    for n in ordered:
        if n.get("skipped"):
            continue
        pg = n.get("parallel_group")
        if pg is None:
            groups.append([n])
        elif pg in by_pg:
            by_pg[pg].append(n)
        else:
            g = [n]
            by_pg[pg] = g
            groups.append(g)
    return groups


def _active_index(
    groups: List[List[Dict[str, Any]]], current_node_id: Optional[Any]
) -> int:
    """Index of the group holding the cursor node, or 0 (first group) when the
    cursor is unset, or -1 when there are no groups."""
    if not groups:
        return -1
    if current_node_id is None:
        return 0
    cid = str(current_node_id)
    for i, g in enumerate(groups):
        if any(str(n["id"]) == cid for n in g):
            return i
    return 0


def _node_ref(node: Dict[str, Any]) -> AdvanceNodeRef:
    return AdvanceNodeRef(
        node_id=str(node["id"]),
        name=node.get("name") or "",
        assignee_user_id=(
            str(node["owner_user_id"]) if node.get("owner_user_id") else None
        ),
        assignee_agent_id=(
            str(node["owner_agent_id"]) if node.get("owner_agent_id") else None
        ),
        due_date=node.get("planned_due"),
    )


async def _mirror_issues(project_id: str, node_id: str) -> List[Dict[str, Any]]:
    """The mirror issues for one node's origin (best-effort empty on error)."""
    from app.repositories.issue_repository import get_issue_repository

    origin_id = build_stage_origin_id(project_id, node_id)
    try:
        return await get_issue_repository().list_by_origin(ORIGIN_KIND, origin_id)
    except Exception as exc:  # noqa: BLE001 — a missing mirror reads as "none"
        logger.warning(
            f"[advance] mirror lookup failed for project {project_id} node "
            f"{node_id}: {exc!r}"
        )
        return []


async def _first_issue_identifier(project_id: str, node_id: str) -> Optional[str]:
    """The human identifier (e.g. ``MH-42``) of a node's mirror issue, or
    ``None`` (best-effort — ``_mirror_issues`` already degrades to [] on
    error, so this never raises)."""
    for issue in await _mirror_issues(project_id, node_id):
        identifier = issue.get("identifier")
        if identifier:
            return str(identifier)
    return None


async def _review_satisfied(project_id: str, node: Dict[str, Any]) -> bool:
    """A review-required node passes only when a mirror issue reached ``done``."""
    issues = await _mirror_issues(project_id, str(node["id"]))
    return any(i.get("status") == "done" for i in issues)


async def _deliverable_present(
    project_id: str, node: Dict[str, Any], episode_id: Optional[str] = None
) -> bool:
    """Whether the node's stage deliverable has been filed.

    Primary path (M2-W1): the node carries an explicit ``folder_id`` (its stage
    folder, materialized lazily on arrival, mig 383) — a non-trashed file in
    that folder satisfies the deliverable. Both this path and the fallback's
    name-matched-folder path go through ``list_folder_files`` (shared with the
    Stage Board endpoint's ``files`` section, F1) so "is something filed?" and
    "what's filed?" can never disagree.

    Fallback (a node whose folder creation lost the race, or a legacy row): a
    file counts when it lives in a project folder whose name matches the node
    name; absent such a folder, ANY non-trashed project file counts. Server-
    side, never trusts the client.

    ``episode_id`` (B2 T1 double-path shim, reserved): the primary path keys off
    the node's own ``folder_id`` so it is already episode-correct once the
    stage folders carry an episode prefix (that folder-naming fix is B2 T2, in
    ``node_folders.py`` — deliberately NOT touched here). It is threaded through
    now so the episode-scoped fallback can use it without another signature
    churn; today it does not change behaviour. Removed / promoted by B6.
    """
    from app.repositories.project_stage_nodes_repository import (
        get_project_stage_nodes_repository,
    )
    from app.repositories.projects_repository import get_projects_repository

    nodes_repo = get_project_stage_nodes_repository()
    folder_id = node.get("folder_id")
    if folder_id:
        try:
            folder_files = await nodes_repo.list_folder_files(str(folder_id))
        except Exception as exc:  # noqa: BLE001 — treat an unreadable store as empty
            logger.warning(
                f"[advance] deliverable file scan failed for project {project_id} "
                f"node {node.get('id')}: {exc!r}"
            )
            return False
        return bool(folder_files)

    repo = get_projects_repository()
    try:
        files = await repo.get_project_files(str(project_id))
        folders = await repo.get_folders(str(project_id))
    except Exception as exc:  # noqa: BLE001 — treat an unreadable store as empty
        logger.warning(
            f"[advance] deliverable file scan failed for project {project_id} "
            f"node {node.get('id')}: {exc!r}"
        )
        return False

    node_name = (node.get("name") or "").strip().lower()
    match = next(
        (
            f
            for f in folders
            if (f.get("name") or "").strip().lower() == node_name and node_name
        ),
        None,
    )
    if match is not None:
        try:
            matched_files = await nodes_repo.list_folder_files(str(match["id"]))
        except Exception as exc:  # noqa: BLE001 — treat an unreadable store as empty
            logger.warning(
                f"[advance] deliverable file scan failed for project {project_id} "
                f"node {node.get('id')}: {exc!r}"
            )
            return False
        return bool(matched_files)
    return len(files) > 0


def _form_incomplete(node: Dict[str, Any]) -> List[str]:
    """Missing REQUIRED form-field LABELS for one node (mig 390, M3 PR-I).

    Required-fill rules by field type (spec §2):
      - text/textarea/select/date: the value must be a non-empty string
        (missing key counts as empty)
      - number: the KEY must be present in ``form_data`` — ``0`` counts as
        filled, so this is a presence check, not a truthiness check
      - checkbox: the value must be exactly ``True`` — ``False`` (or a
        missing key) does not satisfy a required checkbox

    A field with ``required`` falsy is never checked. A node with no
    ``form_schema`` (or an empty one — e.g. every pre-mig-390 node) always
    returns ``[]``, so this is zero-impact for the existing workflow.
    """
    schema = node.get("form_schema") or []
    data = node.get("form_data") or {}
    missing: List[str] = []
    for field in schema:
        if not field.get("required"):
            continue
        key = field.get("key")
        label = field.get("label") or key or ""
        ftype = field.get("type")
        if ftype == "number":
            filled = key in data
        elif ftype == "checkbox":
            filled = data.get(key) is True
        else:  # text, textarea, select, date
            value = data.get(key)
            filled = isinstance(value, str) and bool(value.strip())
        if not filled:
            missing.append(label)
    return missing


def _unmet_dependency_names(
    target_group: List[Dict[str, Any]],
    node_by_id: Dict[str, Dict[str, Any]],
    exempt_ids: Optional[set] = None,
    external_by_id: Optional[Dict[str, Dict[str, Any]]] = None,
) -> List[str]:
    """Names of unmet-dependency nodes for ``target_group`` (mig 391, M3 PR-J).

    A dependency is satisfied when the depended-on node's ``status`` is
    ``done`` OR it is ``skipped`` (skipped counts as satisfied per spec §3),
    OR its id is a member of ``exempt_ids`` (see below). A ``depends_on`` id
    absent from BOTH ``node_by_id`` AND ``external_by_id`` (the depended-on
    node was deleted — FK CASCADE already dropped the edge row, but defend
    anyway) is treated as already resolved, never as unmet.

    ``external_by_id`` (T2 cross-episode supplement): a stripped
    ``{id: {status, skipped, name, ...}}`` map of dependency TARGETS that fall
    OUTSIDE this episode's ``node_by_id`` — i.e. cross-episode edges pointing
    at an earlier episode's node. It is resolved by the call site with a
    precise id-only point lookup (``get_node_statuses_by_ids``) and consulted
    ONLY here, for the done/skipped judgment — it never enters ``node_by_id`` /
    groups / cursor math (T2 三重护栏②). When empty (every project with no
    cross-episode edge), lookup order collapses to ``node_by_id`` alone and
    behaviour is byte-for-byte identical to the mig-391 original (三重护栏③).
    Local (``node_by_id``) is consulted first so a same-id in both never lets
    the stripped external row shadow the full local one.

    ``exempt_ids`` (M3 final review, two exemptions folded into one set by
    the call site so this predicate only has to check membership):
      - CO-ARRIVAL: a dependency whose target is itself a member of
        ``target_group`` (parallel siblings arriving together — e.g. C
        depends_on B, both in the group that would become next). Without
        this, B can never be "done" before the group arrives (they arrive
        together) and the group can never arrive until B is done — a
        permanent deadlock. Same-group deps are "start together", not
        "finish before".
      - CLOSING CURRENT GROUP: a dependency whose target is a member of the
        CURRENT group that this very advance is closing. The most natural
        template config is "next group depends on current group", but during
        preview the current group's nodes are still in_progress (their
        mirror issues close during execute, not before) — without this
        exemption that obvious config would DEPS_PENDING forever. Ruling:
        gates 1-3 already own the current group's completion bar, so a dep
        edge naming the group this advance is already declaring done is
        redundant, not a real blocker.
    Callers build ``exempt_ids`` from ``next_group`` (co-arrival half) union
    the closing current group (closing half); this function itself has no
    opinion on which ids belong there.

    Every node in ``target_group`` is by construction non-skipped
    (``_build_groups`` drops skipped nodes before grouping), so no
    skipped-target-node exemption is needed here — the group itself already
    excludes those.

    Returned names are deduped and ordered by the unmet node's
    ``sort_order`` (stable, matches the Stage Board's node ordering) —
    never dict/set iteration order.
    """
    exempt = exempt_ids or set()
    external = external_by_id or {}
    unmet_by_id: Dict[str, Dict[str, Any]] = {}
    for node in target_group:
        for dep_id in node.get("depends_on") or []:
            dep_id_str = str(dep_id)
            if dep_id_str in exempt:
                continue
            # Local (this episode) first, then the cross-episode supplement.
            dep_node = node_by_id.get(dep_id_str)
            if dep_node is None:
                dep_node = external.get(dep_id_str)
            if dep_node is None:
                continue
            if dep_node.get("status") == "done" or dep_node.get("skipped"):
                continue
            # Key by the dep id (external rows carry no ``id`` field), so a
            # cross-episode target dedupes/orders cleanly alongside local ones.
            unmet_by_id[dep_id_str] = dep_node

    ordered = sorted(unmet_by_id.values(), key=lambda n: n.get("sort_order", 0))
    names: List[str] = []
    seen: set = set()
    for n in ordered:
        name = n.get("name") or ""
        if name in seen:
            continue
        seen.add(name)
        names.append(name)
    return names


async def _open_subissue_warnings(
    project_id: str, group: List[Dict[str, Any]]
) -> List[str]:
    """Warn about open sub-issues that will remain open when a group closes."""
    from app.repositories.issue_repository import get_issue_repository

    repo = get_issue_repository()
    warnings: List[str] = []
    for node in group:
        for issue in await _mirror_issues(project_id, str(node["id"])):
            try:
                children = await repo.list_children(int(issue["id"]))
            except Exception:  # noqa: BLE001 — advisory only
                children = []
            open_children = [c for c in children if c.get("status") not in _TERMINAL]
            if open_children:
                warnings.append(
                    f"{node.get('name') or 'Node'} has {len(open_children)} "
                    "open sub-issue(s) that will remain open."
                )
    return warnings


# ── B2 T1 double-path shim ──────────────────────────────────────────────────
#
# advance_service was born project-scoped (one cursor per project). B2 lowers
# the workflow cursor to per-episode (``episodes.current_node_id``, mig 402).
# To land T1 without touching its callers (the /advance* endpoints are B2 T3,
# the autopilot engine is B2 T4), every public entry gained an optional
# ``episode_id`` and forks here:
#
#   episode_id is None  → the byte-for-byte LEGACY project path — reads
#       ``list_nodes`` + ``projects.current_node_id``, writes the legacy
#       ``ProjectStageNodesRepository.set_current_node_id`` cursor. Every
#       not-yet-migrated caller (and the whole existing test suite) stays on
#       this path with identical behaviour.
#   episode_id given    → the NEW per-episode path — reads
#       ``list_nodes_by_episode`` (single-episode node set, so group-building
#       and active-index math can never fold a sibling episode's template-
#       cloned twins in — B2 陷阱①) + ``episodes.current_node_id``, and writes
#       the cursor through ``EpisodeRepository.set_current_node_id``.
#
# The ``None`` branch is transitional. Once T3/T4 make every caller pass an
# episode_id, B6 deletes it and ``episode_id`` becomes required.


async def _load_scoped_nodes_and_cursor(
    project_id: str, episode_id: Optional[str]
) -> tuple[List[Dict[str, Any]], Optional[Any]]:
    """Return ``(nodes, current_node_id)`` for the requested scope.

    See the module-level shim note. ``episode_id is None`` reproduces the
    legacy project-level read exactly; a given ``episode_id`` reads only that
    episode's nodes and that episode's own cursor.
    """
    from app.repositories.project_stage_nodes_repository import (
        get_project_stage_nodes_repository,
    )

    nodes_repo = get_project_stage_nodes_repository()

    if episode_id is None:
        from app.repositories.projects_repository import get_projects_repository

        nodes = await nodes_repo.list_nodes(str(project_id))
        project = await get_projects_repository().get_project_by_id(
            int(str(project_id))
        )
        return nodes, (project or {}).get("current_node_id")

    from app.repositories.episode_repository import get_episode_repository

    nodes = await nodes_repo.list_nodes_by_episode(str(project_id), str(episode_id))
    episode = await get_episode_repository().get_by_id(str(episode_id))
    return nodes, (episode or {}).get("current_node_id")


async def _write_cursor(
    project_id: str, episode_id: Optional[str], node_id: str
) -> None:
    """Move the active-group cursor for the requested scope.

    See the module-level shim note. ``episode_id is None`` writes the legacy
    ``projects.current_node_id`` (via ``ProjectStageNodesRepository``); a given
    ``episode_id`` writes ``episodes.current_node_id`` (via
    ``EpisodeRepository``, which never touches the project column).
    """
    if episode_id is None:
        from app.repositories.project_stage_nodes_repository import (
            get_project_stage_nodes_repository,
        )

        await get_project_stage_nodes_repository().set_current_node_id(
            str(project_id), str(node_id)
        )
        return

    from app.repositories.episode_repository import get_episode_repository

    await get_episode_repository().set_current_node_id(str(episode_id), str(node_id))


async def compute_advance_preview(
    project_id: str,
    user_id: str,
    direction: str,
    episode_id: Optional[str] = None,
) -> AdvancePreview:
    """Pure-read ruling on a forward/back move. Never mutates.

    ``episode_id`` selects the scope (see the module-level double-path shim
    note). Role resolution stays PROJECT-level regardless — permissions are a
    project-wide concern and do not descend to the episode (B2 ruling).
    """
    direction = "back" if direction == "back" else "forward"

    role = await resolve_effective_role(str(user_id), project_id=str(project_id))
    if role not in WRITE_ROLES:
        return AdvancePreview(
            direction=direction,
            will_advance=False,
            blocked_reason=BLOCK_NOT_MANAGER_OR_EDITOR,
        )

    nodes, current_node_id = await _load_scoped_nodes_and_cursor(project_id, episode_id)
    groups = _build_groups(nodes)
    idx = _active_index(groups, current_node_id)

    if idx < 0:
        return AdvancePreview(
            direction=direction,
            will_advance=False,
            blocked_reason=BLOCK_NO_NEXT,
        )

    if direction == "back":
        return await _preview_back(project_id, groups, idx, episode_id)
    node_by_id = {str(n["id"]): n for n in nodes}
    return await _preview_forward(project_id, groups, idx, node_by_id, episode_id)


async def _preview_forward(
    project_id: str,
    groups: List[List[Dict[str, Any]]],
    idx: int,
    node_by_id: Dict[str, Dict[str, Any]],
    episode_id: Optional[str] = None,
) -> AdvancePreview:
    # ``episode_id`` is transparently threaded to ``_deliverable_present`` (B2
    # T1 shim). ``groups``/``node_by_id`` are already episode-scoped by the
    # caller when episode_id is given, so Gate 4's "no next group" here means
    # "this episode is finished" — the cross-episode "whole series done" roll-
    # up is B5, not this function; each episode simply BLOCK_NO_NEXTs on its own
    # last group.
    active = groups[idx]

    # Gate 1: review-required nodes' mirror issues must be done.
    pending_review = [
        n
        for n in active
        if n.get("review_required") and not await _review_satisfied(project_id, n)
    ]
    if pending_review:
        return AdvancePreview(
            direction="forward",
            will_advance=False,
            blocked_reason=BLOCK_REVIEW_PENDING,
            closing=[_node_ref(n) for n in active],
        )

    # Gate 2: deliverable-required nodes must have a filed file.
    missing_deliverable = [
        n
        for n in active
        if n.get("deliverable_required")
        and not await _deliverable_present(project_id, n, episode_id)
    ]
    if missing_deliverable:
        return AdvancePreview(
            direction="forward",
            will_advance=False,
            blocked_reason=BLOCK_DELIVERABLE_MISSING,
            closing=[_node_ref(n) for n in active],
        )

    # Gate 3: required form fields must be filled (mig 390, M3 PR-I) — parallel
    # to (never confused with) gate 2's deliverable-file check.
    missing_fields: List[str] = []
    for n in active:
        missing_fields.extend(_form_incomplete(n))
    if missing_fields:
        return AdvancePreview(
            direction="forward",
            will_advance=False,
            blocked_reason=BLOCK_FORM_INCOMPLETE,
            closing=[_node_ref(n) for n in active],
            missing_fields=missing_fields,
        )

    # Gate 4: a next group must exist.
    if idx + 1 >= len(groups):
        return AdvancePreview(
            direction="forward",
            will_advance=False,
            blocked_reason=BLOCK_NO_NEXT,
            closing=[_node_ref(n) for n in active],
        )

    next_group = groups[idx + 1]

    # Gate 5: dependency gate (mig 391, M3 PR-J). Gates 1-3 above all gate the
    # CURRENT group's own completion (review/deliverable/form); this one is
    # different in kind — it gates the TARGET group's readiness to START, so
    # it can only be evaluated once Gate 4 has resolved which group that is.
    # Placed here (after NO_NEXT, before the success path) rather than as
    # Gate 1 because "can the target group start" is meaningless until a
    # target group is known to exist. Every non-skipped node in ``next_group``
    # must have every ``depends_on`` node done or skipped; back (retreat)
    # never runs this check (spec §3).
    #
    # ``exempt_ids`` (M3 final review #1 + #2): next_group's own ids (a dep on
    # a parallel sibling arriving in the SAME group is co-arrival, not a real
    # ordering — see ``_unmet_dependency_names`` docstring) union ``active``'s
    # ids (the current group this advance is closing — its nodes are still
    # in_progress at preview time, so a dep naming it would otherwise
    # DEPS_PENDING forever on the most natural "next depends on current"
    # config). ``waiting_on`` therefore only ever lists genuinely-earlier,
    # unrelated, unfinished nodes.
    exempt_ids = {str(n["id"]) for n in next_group} | {str(n["id"]) for n in active}

    # Cross-episode dependency supplement (T2). A next_group node's depends_on
    # may name a target that lives OUTSIDE this episode's node_by_id — a
    # cross-episode edge whose target node belongs to an earlier episode. Those
    # ids (typically 0-2, only ever non-empty when a real cross-episode edge
    # exists) are collected and resolved with a PRECISE id-only point lookup —
    # never an episode/project batch pull (三重护栏①), and the stripped result
    # is passed to the predicate for the done/skipped judgment ONLY, never into
    # groups / node_by_id / cursor math (三重护栏②). A project with no
    # cross-episode edge yields an empty set → no lookup → byte-for-byte
    # identical to the mig-391 behaviour (三重护栏③).
    external_ids = list(
        dict.fromkeys(
            str(dep_id)
            for n in next_group
            for dep_id in (n.get("depends_on") or [])
            if str(dep_id) not in node_by_id and str(dep_id) not in exempt_ids
        )
    )
    external_by_id: Dict[str, Dict[str, Any]] = {}
    if external_ids:
        from app.repositories.project_stage_nodes_repository import (
            get_project_stage_nodes_repository,
        )

        external_by_id = await get_project_stage_nodes_repository().get_node_statuses_by_ids(  # noqa: E501
            str(project_id), external_ids
        )

    waiting_on = _unmet_dependency_names(
        next_group, node_by_id, exempt_ids, external_by_id
    )
    if waiting_on:
        return AdvancePreview(
            direction="forward",
            will_advance=False,
            blocked_reason=BLOCK_DEPS_PENDING,
            closing=[_node_ref(n) for n in active],
            waiting_on=waiting_on,
        )

    warnings = await _open_subissue_warnings(project_id, active)
    if any(n.get("owner_agent_id") for n in next_group):
        warnings.append("No agent will start automatically.")

    return AdvancePreview(
        direction="forward",
        will_advance=True,
        closing=[_node_ref(n) for n in active],
        creating=[_node_ref(n) for n in next_group],
        warnings=warnings,
    )


async def _preview_back(
    project_id: str,
    groups: List[List[Dict[str, Any]]],
    idx: int,
    episode_id: Optional[str] = None,
) -> AdvancePreview:
    # ``episode_id`` accepted for signature symmetry with ``_preview_forward``
    # (B2 T1 shim); retreat previews are computed purely from the already-
    # episode-scoped ``groups`` the caller passed, so nothing downstream needs
    # it today.
    del episode_id
    if idx <= 0:
        return AdvancePreview(
            direction="back",
            will_advance=False,
            blocked_reason=BLOCK_NO_NEXT,
        )
    prev_group = groups[idx - 1]
    return AdvancePreview(
        direction="back",
        will_advance=True,
        creating=[_node_ref(n) for n in prev_group],
        warnings=["Reopening the previous stage will set it back to in progress."],
    )


async def execute_advance(
    project_id: str,
    user_id: str,
    direction: str,
    episode_id: Optional[str] = None,
) -> AdvancePreview:
    """Compute the preview, then (only if it clears) perform the move.

    Returns the SAME preview object — blocked when ``will_advance`` is False (no
    mutation happened), else the executed plan. Router maps a blocked preview to
    a 409.

    ``episode_id`` selects the scope (see the module-level double-path shim
    note). When given, the node set, active-index cursor read, and the cursor
    WRITE are all episode-scoped; the six other side-effects (mirror-issue
    close/reopen, ``ensure_node_issues`` / ``ensure_node_folders``,
    notifications, stage-hook dispatch, autopilot tick) run against the same
    episode-scoped node groups but stay keyed by (project_id, node) — node ids
    are globally unique, so those helpers need no episode awareness. The
    autopilot tick stays PROJECT-level (its per-project re-entrancy guard must
    not descend to the episode — that is B2 T4's concern).
    """
    from app.repositories.issue_repository import get_issue_repository
    from app.repositories.projects_repository import get_projects_repository
    from app.workflows.stage_hook import enqueue_stage_hook_dispatch

    preview = await compute_advance_preview(project_id, user_id, direction, episode_id)
    if not preview.will_advance:
        return preview

    issues_repo = get_issue_repository()
    nodes, current_node_id = await _load_scoped_nodes_and_cursor(project_id, episode_id)
    groups = _build_groups(nodes)
    idx = _active_index(groups, current_node_id)

    # The project row is still read PROJECT-level — notifications carry the
    # project's name / team, which do not descend to the episode.
    project = await get_projects_repository().get_project_by_id(int(str(project_id)))
    project_name = (project or {}).get("name") or "Project"
    team_id = (project or {}).get("team_id")

    if preview.direction == "forward":
        # Close the current group's mirror issues (open sub-issues stay open —
        # already flagged in the preview). Status回流 hook flips those nodes to
        # done.
        closing_group = groups[idx]
        for node in closing_group:
            for issue in await _mirror_issues(project_id, str(node["id"])):
                if issue.get("status") not in _TERMINAL:
                    await issues_repo.transition_status(int(issue["id"]), "done")
        next_group = groups[idx + 1]
        await _write_cursor(project_id, episode_id, str(next_group[0]["id"]))
        await ensure_node_issues(int(str(project_id)), next_group, str(user_id))
        await ensure_node_folders(str(project_id), next_group, str(user_id))

        # Best-effort stage notifications (E2): the closing group gets
        # "completion", the newly-arrived group gets "arrival". Never blocks
        # the advance — notify_stage_event() itself never raises.
        for node in closing_group:
            await notify_stage_event(
                event="completion",
                project_id=str(project_id),
                project_name=project_name,
                node=node,
                issue_identifier=await _first_issue_identifier(
                    project_id, str(node["id"])
                ),
                team_id=team_id,
                actor_user_id=str(user_id),
            )
        for node in next_group:
            await notify_stage_event(
                event="arrival",
                project_id=str(project_id),
                project_name=project_name,
                node=node,
                issue_identifier=await _first_issue_identifier(
                    project_id, str(node["id"])
                ),
                team_id=team_id,
                actor_user_id=str(user_id),
            )

        # Best-effort stage-hook prepare (M3 PR-H2): agent-owned nodes in the
        # newly-arrived group with events.prepare_agent_run get a "run ready"
        # notification queued behind the confirm gate — never a dispatch.
        # enqueue_stage_hook_dispatch() never raises (per-node try/except), so
        # this can never affect the advance's return value or mutations.
        await enqueue_stage_hook_dispatch(str(project_id), next_group)

        # M4 Autopilot (task O2, spec §2 trigger 2): the newly-arrived group
        # may itself contain auto_start nodes, or unblock a further cascade —
        # best-effort tail enqueue, never affects the advance's return value.
        await _enqueue_autopilot_tick_best_effort(str(project_id))
    else:
        prev_group = groups[idx - 1]
        await _write_cursor(project_id, episode_id, str(prev_group[0]["id"]))
        # Ensure the previous group's mirror issues exist, then reopen them
        # (transition → in_progress fires the status回流 hook → node in_progress).
        await ensure_node_issues(int(str(project_id)), prev_group, str(user_id))
        await ensure_node_folders(str(project_id), prev_group, str(user_id))
        for node in prev_group:
            for issue in await _mirror_issues(project_id, str(node["id"])):
                if issue.get("status") != "in_progress":
                    await issues_repo.transition_status(int(issue["id"]), "in_progress")

        # Best-effort reopen notification (E2) — same recipients as arrival.
        for node in prev_group:
            await notify_stage_event(
                event="reopen",
                project_id=str(project_id),
                project_name=project_name,
                node=node,
                issue_identifier=await _first_issue_identifier(
                    project_id, str(node["id"])
                ),
                team_id=team_id,
                actor_user_id=str(user_id),
            )

        # M4 Autopilot (task O2, spec §2 trigger 2): reopening the previous
        # group is an arrival too — best-effort tail enqueue.
        await _enqueue_autopilot_tick_best_effort(str(project_id))

    return preview


async def _enqueue_autopilot_tick_best_effort(project_id: str) -> None:
    """Best-effort tail enqueue of ``autopilot_tick`` after an arrival.

    Re-entrancy guard (see ``autopilot.cascade_in_progress``'s docstring):
    when THIS ``execute_advance`` call is itself one of the autopilot
    engine's own cascade-loop steps, skip the enqueue — the tick that
    started the cascade is already looping through every subsequent step
    in-process, so a nested enqueue here would only be a redundant, wasteful
    extra tick, never a missed one. Never raises — an enqueue failure must
    never affect the advance that just completed.
    """
    try:
        from app.workflows.autopilot import cascade_in_progress, enqueue_autopilot_tick

        if cascade_in_progress():
            return
        await enqueue_autopilot_tick(project_id)
    except Exception as exc:  # noqa: BLE001 — never blocks the advance
        logger.warning(
            f"[advance] autopilot tick enqueue failed for project {project_id}: {exc!r}"
        )
