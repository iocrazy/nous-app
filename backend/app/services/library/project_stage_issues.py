"""Project SOP stage → auto todo (issue) sync.

When a project's SOP stage advances (the manual PUT /current_stage entry point),
this module keeps a mirror issue in lock-step with the stage:

  * a new stage → an idempotent ``status='todo'`` issue is created, back-linked
    to the stage via ``origin_kind='project_stage'`` +
    ``origin_id='project_stage:{project_id}:{stage_id}'``;
  * the previous stage's still-open issue is transitioned to ``done``.

Discipline (project立约):
  * Automation NEVER assigns the issue — no silent指派/计费. ``created_by_user_id``
    is the user who advanced the stage (satisfies the creator-required CHECK and
    gives that user visibility), but ``assignee_*`` is left null.
  * The hook is best-effort: any failure is swallowed with a ``logger.warning``
    so it can never block the stage advance itself (the primary operation).
  * Same-stage no-ops do nothing — ``set_current_stage`` returns ``None`` and the
    hook is skipped.
  * Only forward, future advances are mirrored — existing stages are not
    retroactively back-filled.
"""

from __future__ import annotations

from typing import Any, Optional

from loguru import logger

# origin_kind stamped on stage-mirror issues (must match the widened
# issues_origin_kind_check — migration 367 + models/reviews.py).
ORIGIN_KIND = "project_stage"

# Issue statuses that count as closed — a stage-mirror issue in one of these is
# not re-closed, and a new-stage issue is only created if none exists.
_TERMINAL_ISSUE_STATUSES = frozenset({"done", "cancelled"})


def build_stage_origin_id(project_id: Any, stage_id: Any) -> str:
    """Origin id a stage-mirror issue is stamped with.

    ``project_stage:{project_id}:{stage_id}`` — the frontend ``parseOriginId``
    splits on the FIRST colon (kind = ``project_stage``, id =
    ``{project_id}:{stage_id}``), so ids must stay strings end-to-end (Snowflake
    bigints, never Number()-coerced).
    """
    return f"{ORIGIN_KIND}:{project_id}:{stage_id}"


async def advance_project_stage(
    project_id: int, stage_id: Any, user_id: str
) -> Optional[dict[str, Any]]:
    """Advance a project's SOP stage and mirror the change into issues.

    Thin orchestration over ``ProjectStagesRepository.set_current_stage`` (the
    stage machine stays the single source of truth for the transition): reads the
    current stage first to know which issue to close, performs the advance, then
    runs the best-effort issue sync. Returns the new stage row, or ``None`` for a
    same-stage no-op (in which case NO issue action is taken).

    Raises ``ValueError`` straight through from ``set_current_stage`` for an
    invalid project/stage — the router maps that to a 422 exactly as before.
    """
    from app.repositories.project_stages_repository import (
        get_project_stages_repository,
    )

    stages_repo = get_project_stages_repository()

    # Capture the outgoing stage BEFORE advancing so we know which issue to close.
    prev = await stages_repo.get_current(int(project_id))
    old_stage_id = prev.get("id") if prev else None

    new_stage = await stages_repo.set_current_stage(int(project_id), stage_id, user_id)
    if new_stage is None:
        # Same-stage no-op — nothing advanced, nothing to mirror.
        return None

    try:
        await _sync_stage_issues(int(project_id), old_stage_id, new_stage, user_id)
    except Exception as exc:  # noqa: BLE001 — the advance is the primary op
        logger.warning(
            f"[project_stage_issues] issue sync failed for project "
            f"{project_id} → stage {new_stage.get('id')}: {exc!r}"
        )

    return new_stage


async def _sync_stage_issues(
    project_id: int,
    old_stage_id: Any,
    new_stage: dict[str, Any],
    user_id: str,
) -> None:
    """Close the previous stage's issue and open the new stage's issue.

    The two halves are independent: a failure closing the old issue must not
    prevent opening the new one, and vice versa (each is wrapped on its own).
    """
    from app.repositories.issue_repository import get_issue_repository

    issues = get_issue_repository()
    new_stage_id = str(new_stage["id"])

    # 1) Close the previous stage's open issue (if any). Skip when the old and
    #    new stage ids coincide (can't happen for a real advance, but defensive).
    if old_stage_id is not None and str(old_stage_id) != new_stage_id:
        try:
            await _close_stage_issue(issues, project_id, str(old_stage_id))
        except Exception as exc:  # noqa: BLE001 — still try to open the new one
            logger.warning(
                f"[project_stage_issues] closing old-stage issue failed for "
                f"project {project_id} stage {old_stage_id}: {exc!r}"
            )

    # 2) Idempotently open the new stage's issue.
    try:
        await _ensure_stage_issue(issues, project_id, new_stage, user_id)
    except Exception as exc:  # noqa: BLE001 — best-effort mirror
        logger.warning(
            f"[project_stage_issues] opening new-stage issue failed for "
            f"project {project_id} stage {new_stage_id}: {exc!r}"
        )


async def _close_stage_issue(issues, project_id: int, stage_id: str) -> None:
    """Transition every non-terminal issue for a stage's origin to ``done``."""
    origin_id = build_stage_origin_id(project_id, stage_id)
    existing = await issues.list_by_origin(ORIGIN_KIND, origin_id)
    for issue in existing:
        if issue.get("status") in _TERMINAL_ISSUE_STATUSES:
            continue
        await issues.transition_status(int(issue["id"]), "done")


async def _ensure_stage_issue(
    issues, project_id: int, new_stage: dict[str, Any], user_id: str
) -> None:
    """Create the new stage's issue unless one already exists (idempotent)."""
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
        # Automation never assigns — assignee_* stays null (no silent指派/计费).
        "created_by_user_id": str(user_id),
    }
    team_id = (project or {}).get("team_id")
    if team_id is not None:
        payload["team_id"] = int(team_id)

    await issues.atomic_create(payload)


async def _load_project(project_id: int) -> Optional[dict[str, Any]]:
    """Best-effort project fetch for the issue's title / team_id."""
    from app.repositories.projects_repository import get_projects_repository

    return await get_projects_repository().get_project_by_id(project_id)
