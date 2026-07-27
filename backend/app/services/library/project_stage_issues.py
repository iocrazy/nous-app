"""Project SOP stage / workflow-node → auto todo (issue) mirror.

M2 PR-G/G1.5/fix-round retired the legacy SOP stage cursor
(``current_stage_id``) end to end, including its two consumers that used to
live here: ``advance_project_stage`` (wrapped
``ProjectStagesRepository.set_current_stage``, deleted in G1) and
``sync_stage_issues`` (the ``set_current_stage`` post-commit callback that
opened/closed the legacy stage-mirror issue). Both are gone — their only
callers were the deleted PUT endpoint and the deleted repo method
respectively; keeping them around only let 20+ tests stay false-green via
fake repos that still implemented ``set_current_stage``.

What remains is genuinely live via the WORKFLOW node chain (M1), not the
legacy SOP path:

  * ``ensure_node_issues`` — called from project creation
    (``instantiation.py``) and node advance/retreat (``advance_service.py``)
    to idempotently open a mirror issue for each active-group node;
  * ``ensure_stage_issue`` — the idempotent single-issue creator
    ``ensure_node_issues`` calls per node (inherits owner as assignee +
    ``planned_due`` as ``due_date``; never dispatches an agent run);
  * ``build_stage_origin_id`` / ``parse_stage_origin_id`` — the
    ``origin_id`` shape shared by node-mirror issues and the status-回流 hook
    that writes a node's state back from its mirror issue.

Discipline (project立约):
  * Automation NEVER assigns the issue — no silent指派/计费. ``created_by_user_id``
    is the user who advanced the stage (satisfies the creator-required CHECK and
    gives that user visibility), but ``assignee_*`` is left null unless the node
    itself carries an owner.
  * Every mirror write here is best-effort: any failure is swallowed with a
    ``logger.warning`` so it can never block the node advance itself (the
    primary operation).
  * team_id boundary translation: a *personal* project has ``team_id IS NULL``
    (the projects convention), but a NULL-team issue is invisible to the
    team-scoped Todolist. ``_resolve_issue_team_id`` translates a NULL project
    team into the owner's personal-team snowflake for the ISSUE only — the
    project row's NULL is left as-is. Legacy issues written before this
    hardening are repaired by the ``project_stage_issue_team_ids`` backfill.
"""

from __future__ import annotations

from typing import Any, Optional

from loguru import logger

# origin_kind stamped on stage-mirror issues (must match the widened
# issues_origin_kind_check — migration 367 + models/reviews.py).
ORIGIN_KIND = "project_stage"


def build_stage_origin_id(project_id: Any, stage_id: Any) -> str:
    """Origin id a stage-mirror issue is stamped with.

    ``project_stage:{project_id}:{stage_id}`` — the frontend ``parseOriginId``
    splits on the FIRST colon (kind = ``project_stage``, id =
    ``{project_id}:{stage_id}``), so ids must stay strings end-to-end (Snowflake
    bigints, never Number()-coerced). ``stage_id`` is a workflow node id for the
    M1 node mirror, or a legacy SOP stage id for the pre-existing stage mirror —
    both share this three-segment shape.
    """
    return f"{ORIGIN_KIND}:{project_id}:{stage_id}"


def parse_stage_origin_id(origin_id: str) -> tuple[Optional[str], str]:
    """Split a project-stage ``origin_id`` into (project_id, node_or_stage_id).

    Two shapes coexist and must not be confused:
      * NEW three-segment ``project_stage:{project_id}:{node_id}`` → both parts
        recovered → ``(project_id, node_id)``.
      * OLD two-segment ``project_stage:{stage_id}`` (存量镜像 issue, never
        migrated) → no project scope encoded → ``(None, stage_id)``.

    The status-回流 hook only writes a node back when ``project_id`` is present
    (the new shape); the old two-segment origin is skipped.
    """
    raw = origin_id
    prefix = f"{ORIGIN_KIND}:"
    if raw.startswith(prefix):
        raw = raw[len(prefix) :]
    if ":" in raw:
        project_id, node_id = raw.split(":", 1)
        return project_id or None, node_id
    return None, raw


async def ensure_stage_issue(
    issues, project_id: int, new_stage: dict[str, Any], user_id: str
) -> None:
    """Create the new stage/node's issue unless one already exists (idempotent).

    For a workflow NODE (M1), the mirror issue inherits the node's owner as its
    assignee (user XOR agent) and the node's ``planned_due`` as ``due_date`` (a
    real ``date`` object — never an ISO string, the asyncpg DATE-bind footgun).
    An AGENT owner is only ASSIGNED here, never dispatched — the run-confirm
    gate is untouched, so no silent指派/计费. Legacy SOP stage dicts carry none
    of these keys, so the pre-existing stage mirror is unaffected.
    """
    origin_id = build_stage_origin_id(project_id, new_stage["id"])

    # Idempotency: any existing issue (terminal or not) for this exact origin
    # means the advance was already mirrored — do not create a duplicate.
    if await issues.list_by_origin(ORIGIN_KIND, origin_id):
        return

    project = await _load_project(project_id)
    project_name = (project or {}).get("name") or "Project"
    stage_name = new_stage.get("name") or "Stage"

    payload: dict[str, Any] = {
        "title": f"{project_name} — {stage_name}",
        "status": "todo",
        "origin_kind": ORIGIN_KIND,
        "origin_id": origin_id,
        "project_id": int(project_id),
        "created_by_user_id": str(user_id),
    }

    # Node owner → issue assignee (XOR). This is an ASSIGNMENT only; the agent
    # run-confirm gate stays in place, so no workflow is dispatched from here.
    owner_user_id = new_stage.get("owner_user_id")
    owner_agent_id = new_stage.get("owner_agent_id")
    if owner_user_id is not None:
        payload["assignee_user_id"] = str(owner_user_id)
    elif owner_agent_id is not None:
        payload["assignee_agent_id"] = str(owner_agent_id)

    # planned_due → issues.due_date. Kept as a real date object end-to-end; the
    # atomic-create jsonb boundary ISO-formats it for the bind.
    planned_due = new_stage.get("planned_due")
    if planned_due is not None:
        if isinstance(planned_due, str):
            raise TypeError(
                "planned_due must be a datetime.date, got an ISO string "
                f"({planned_due!r})"
            )
        payload["due_date"] = planned_due

    team_id = await _resolve_issue_team_id(project)
    if team_id is not None:
        payload["team_id"] = int(team_id)

    await issues.atomic_create(payload)


async def ensure_node_issues(
    project_id: int, nodes: list[dict[str, Any]], user_id: str
) -> None:
    """Idempotently open a mirror issue for each non-skipped node in a group.

    Used at project creation (first active group) and on advance (next group).
    Best-effort per node: one node's failure never blocks the others, and the
    whole helper never raises — the arrival mirror is enrichment, not the
    primary op.
    """
    from app.repositories.issue_repository import get_issue_repository

    issues = get_issue_repository()
    for node in nodes:
        if node.get("skipped"):
            continue
        try:
            await ensure_stage_issue(issues, int(project_id), node, user_id)
        except Exception as exc:  # noqa: BLE001 — best-effort per-node mirror
            logger.warning(
                f"[project_stage_issues] node mirror failed for project "
                f"{project_id} node {node.get('id')}: {exc!r}"
            )


async def _resolve_issue_team_id(project: Optional[dict[str, Any]]) -> Optional[int]:
    """Team id (snowflake) to stamp on a project-stage mirror issue.

    Boundary translation between two conventions: a ``projects`` row encodes a
    *personal* project as ``team_id IS NULL`` (see projects_repository.
    get_user_projects), but the ``issues`` / Todolist subsystem scopes personal
    work by the owner's personal-team snowflake — a NULL-team issue is filtered
    out of every team-scoped Todolist query and is effectively invisible. So when
    the project carries no team, resolve the OWNER's personal team and stamp the
    ISSUE with it (the project stays NULL — that convention is untouched).

    Returns ``None`` only when the project has no team AND the owner has no
    personal team; the issue is then created team-less (its creator-required
    CHECK is still satisfied), same as before this hardening.
    """
    if project is None:
        return None
    team_id = project.get("team_id")
    if team_id is not None:
        return int(team_id)

    owner_id = project.get("owner_id")
    if not owner_id:
        return None
    try:
        from app.repositories.team_repository import get_team_repository

        personal = await get_team_repository().get_personal_team_id(str(owner_id))
        return int(personal) if personal else None
    except Exception as exc:  # noqa: BLE001 — degrade to team-less, never block mirror
        logger.warning(
            f"[project_stage_issues] personal-team resolve failed for project "
            f"owner {owner_id}: {exc!r}"
        )
        return None


async def _load_project(project_id: int) -> Optional[dict[str, Any]]:
    """Best-effort project fetch for the issue's title / team_id."""
    from app.repositories.projects_repository import get_projects_repository

    return await get_projects_repository().get_project_by_id(project_id)
