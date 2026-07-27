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


async def _deliverable_present(project_id: str, node: Dict[str, Any]) -> bool:
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
    target_group: List[Dict[str, Any]], node_by_id: Dict[str, Dict[str, Any]]
) -> List[str]:
    """Names of unmet-dependency nodes for ``target_group`` (mig 391, M3 PR-J).

    A dependency is satisfied when the depended-on node's ``status`` is
    ``done`` OR it is ``skipped`` (skipped counts as satisfied per spec §3).
    A ``depends_on`` id absent from ``node_by_id`` (the depended-on node was
    deleted — FK CASCADE already dropped the edge row, but defend anyway)
    is treated as already resolved, never as unmet.

    Every node in ``target_group`` is by construction non-skipped
    (``_build_groups`` drops skipped nodes before grouping), so no
    skipped-target-node exemption is needed here — the group itself already
    excludes those.

    Returned names are deduped and ordered by the unmet node's
    ``sort_order`` (stable, matches the Stage Board's node ordering) —
    never dict/set iteration order.
    """
    unmet_by_id: Dict[str, Dict[str, Any]] = {}
    for node in target_group:
        for dep_id in node.get("depends_on") or []:
            dep_node = node_by_id.get(str(dep_id))
            if dep_node is None:
                continue
            if dep_node.get("status") == "done" or dep_node.get("skipped"):
                continue
            unmet_by_id[str(dep_node["id"])] = dep_node

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


async def compute_advance_preview(
    project_id: str, user_id: str, direction: str
) -> AdvancePreview:
    """Pure-read ruling on a forward/back move. Never mutates."""
    from app.repositories.project_stage_nodes_repository import (
        get_project_stage_nodes_repository,
    )
    from app.repositories.projects_repository import get_projects_repository

    direction = "back" if direction == "back" else "forward"

    role = await resolve_effective_role(str(user_id), project_id=str(project_id))
    if role not in WRITE_ROLES:
        return AdvancePreview(
            direction=direction,
            will_advance=False,
            blocked_reason=BLOCK_NOT_MANAGER_OR_EDITOR,
        )

    nodes_repo = get_project_stage_nodes_repository()
    nodes = await nodes_repo.list_nodes(str(project_id))
    groups = _build_groups(nodes)

    project = await get_projects_repository().get_project_by_id(int(str(project_id)))
    current_node_id = (project or {}).get("current_node_id")
    idx = _active_index(groups, current_node_id)

    if idx < 0:
        return AdvancePreview(
            direction=direction,
            will_advance=False,
            blocked_reason=BLOCK_NO_NEXT,
        )

    if direction == "back":
        return await _preview_back(project_id, groups, idx)
    node_by_id = {str(n["id"]): n for n in nodes}
    return await _preview_forward(project_id, groups, idx, node_by_id)


async def _preview_forward(
    project_id: str,
    groups: List[List[Dict[str, Any]]],
    idx: int,
    node_by_id: Dict[str, Dict[str, Any]],
) -> AdvancePreview:
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
        and not await _deliverable_present(project_id, n)
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
    waiting_on = _unmet_dependency_names(next_group, node_by_id)
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
    project_id: str, groups: List[List[Dict[str, Any]]], idx: int
) -> AdvancePreview:
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
    project_id: str, user_id: str, direction: str
) -> AdvancePreview:
    """Compute the preview, then (only if it clears) perform the move.

    Returns the SAME preview object — blocked when ``will_advance`` is False (no
    mutation happened), else the executed plan. Router maps a blocked preview to
    a 409.
    """
    from app.repositories.issue_repository import get_issue_repository
    from app.repositories.project_stage_nodes_repository import (
        get_project_stage_nodes_repository,
    )
    from app.repositories.projects_repository import get_projects_repository
    from app.workflows.stage_hook import enqueue_stage_hook_dispatch

    preview = await compute_advance_preview(project_id, user_id, direction)
    if not preview.will_advance:
        return preview

    nodes_repo = get_project_stage_nodes_repository()
    issues_repo = get_issue_repository()
    nodes = await nodes_repo.list_nodes(str(project_id))
    groups = _build_groups(nodes)
    project = await get_projects_repository().get_project_by_id(int(str(project_id)))
    idx = _active_index(groups, (project or {}).get("current_node_id"))

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
        await nodes_repo.set_current_node_id(str(project_id), str(next_group[0]["id"]))
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
    else:
        prev_group = groups[idx - 1]
        await nodes_repo.set_current_node_id(str(project_id), str(prev_group[0]["id"]))
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

    return preview
