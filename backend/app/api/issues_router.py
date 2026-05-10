"""Issues REST API — top-level user-visible "thing" entity (PR-D6).

Endpoints (under /api/v1/issues):
    POST   /                 — create
    GET    /                 — list (paged, status/project/team filters)
    GET    /{id}             — get by snowflake id
    GET    /by-identifier/{ident} — get by 'MH-N'
    PATCH  /{id}             — partial update (title/description/etc)
    POST   /{id}/transition  — status transition (lifecycle ts side-effects)
    POST   /{id}/dispatch    — start execute_issue DBOS workflow + persist wf_id
    DELETE /{id}             — soft-delete (sets hidden_at)

The dispatch endpoint kicks off `execute_issue` via DBOS and writes
the workflow_id back onto issues.dbos_workflow_id so the frontend can
subscribe via /api/v1/workflows/{workflow_id}/events (D4 SSE).
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query, status
from loguru import logger

from app.core.deps import AuthDep
from app.repositories.issue_repository import issue_repository
from app.schemas.issue import (
    Issue,
    IssueCreate,
    IssueListResponse,
    IssueStatusTransition,
    IssueUpdate,
)

router = APIRouter(prefix="/issues", tags=["Issues"])


def _normalise_uuid_strs(row: dict) -> dict:
    """PostgREST returns UUIDs as strings — pydantic Issue model parses
    them. Pass-through helper to keep the router code symmetric."""
    return row


@router.post("/", response_model=Issue, status_code=status.HTTP_201_CREATED)
async def create_issue(payload: IssueCreate, auth: AuthDep) -> Issue:
    """Create a new issue. created_by_user_id is set from auth context;
    callers may NOT spoof it via the payload."""
    body = payload.model_dump(exclude_none=True)
    body["created_by_user_id"] = str(auth.user_id)
    # Coerce enums to their .value for JSON serialization
    for field in ("status", "priority", "origin_kind"):
        if field in body and hasattr(body[field], "value"):
            body[field] = body[field].value
    try:
        row = await issue_repository.atomic_create(body)
    except Exception as e:
        logger.warning(f"[issues] create failed: {e}")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return Issue.model_validate(_normalise_uuid_strs(row))


@router.get("/", response_model=IssueListResponse)
async def list_issues(
    auth: AuthDep,
    status_filter: Optional[str] = Query(None, alias="status"),
    project_id: Optional[int] = Query(None),
    team_id: Optional[int] = Query(None),
    include_hidden: bool = Query(False),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> IssueListResponse:
    """List issues visible to the authenticated user (own OR assignee).

    Team / project visibility folding is intentionally NOT implemented
    here — it requires a JOIN against team_members which the repo will
    add in PR-D6.1. For now: own + assignee scope only.
    """
    items, total = await issue_repository.list_for_user(
        user_id=str(auth.user_id),
        status=status_filter,
        project_id=project_id,
        team_id=team_id,
        include_hidden=include_hidden,
        limit=limit,
        offset=offset,
    )
    return IssueListResponse(
        items=[Issue.model_validate(_normalise_uuid_strs(r)) for r in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/by-identifier/{identifier}", response_model=Issue)
async def get_by_identifier(identifier: str, auth: AuthDep) -> Issue:
    """Lookup by human identifier 'MH-N'. Useful for deep links."""
    row = await issue_repository.get_by_identifier(identifier)
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"identifier={identifier} not found",
        )
    _assert_visibility(row, auth)
    return Issue.model_validate(_normalise_uuid_strs(row))


@router.get("/{issue_id}", response_model=Issue)
async def get_issue(issue_id: int, auth: AuthDep) -> Issue:
    row = await issue_repository.get_by_id(issue_id)
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"id={issue_id} not found",
        )
    _assert_visibility(row, auth)
    return Issue.model_validate(_normalise_uuid_strs(row))


@router.patch("/{issue_id}", response_model=Issue)
async def update_issue(issue_id: int, payload: IssueUpdate, auth: AuthDep) -> Issue:
    """Partial update. Status changes go through /transition instead so
    the lifecycle timestamps (started_at / completed_at / cancelled_at)
    fire correctly."""
    existing = await issue_repository.get_by_id(issue_id)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"id={issue_id} not found"
        )
    _assert_visibility(existing, auth)

    patch = payload.model_dump(exclude_none=True)
    for field in ("priority",):
        if field in patch and hasattr(patch[field], "value"):
            patch[field] = patch[field].value
    if "hidden_at" in patch and patch["hidden_at"] is not None:
        patch["hidden_at"] = patch["hidden_at"].isoformat()

    if not patch:
        return Issue.model_validate(_normalise_uuid_strs(existing))

    try:
        row = await issue_repository.update(issue_id, patch)
    except Exception as e:
        logger.warning(f"[issues] update {issue_id} failed: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    return Issue.model_validate(_normalise_uuid_strs(row))


@router.post("/{issue_id}/transition", response_model=Issue)
async def transition_status(
    issue_id: int, body: IssueStatusTransition, auth: AuthDep
) -> Issue:
    """Status transition with lifecycle timestamp side-effects."""
    existing = await issue_repository.get_by_id(issue_id)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"id={issue_id} not found"
        )
    _assert_visibility(existing, auth)
    try:
        row = await issue_repository.transition_status(issue_id, body.status.value)
    except Exception as e:
        logger.warning(f"[issues] transition {issue_id} failed: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    return Issue.model_validate(_normalise_uuid_strs(row))


@router.post("/{issue_id}/dispatch", response_model=Issue)
async def dispatch_issue(issue_id: int, auth: AuthDep) -> Issue:
    """Kick off the execute_issue DBOS workflow. Persists the workflow_id
    onto issues.dbos_workflow_id so the frontend can subscribe to
    /api/v1/workflows/{workflow_id}/events for live status."""
    from app.services.infra import dbos_orchestrator

    existing = await issue_repository.get_by_id(issue_id)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"id={issue_id} not found"
        )
    _assert_visibility(existing, auth)

    if not dbos_orchestrator.is_enabled():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="DBOS not enabled — issue dispatch unavailable",
        )

    from dbos import DBOS, SetWorkflowID

    from app.workflows.issue_lifecycle import execute_issue

    workflow_id = f"issue-{issue_id}"
    try:
        with SetWorkflowID(workflow_id):
            DBOS.start_workflow(execute_issue, issue_id)
    except Exception as e:
        # Duplicate workflow_id is a soft success — DBOS already has it.
        if (
            "already exists" not in repr(e).lower()
            and "duplicate" not in repr(e).lower()
        ):
            logger.warning(f"[issues] dispatch {issue_id} failed: {e}")
            raise HTTPException(status_code=500, detail=f"DBOS dispatch failed: {e}")

    # Persist workflow_id so the UI can find it without re-deriving.
    row = await issue_repository.update(issue_id, {"dbos_workflow_id": workflow_id})
    return Issue.model_validate(_normalise_uuid_strs(row))


@router.delete("/{issue_id}", status_code=status.HTTP_204_NO_CONTENT)
async def soft_delete_issue(issue_id: int, auth: AuthDep) -> None:
    """User-facing delete — sets hidden_at, keeps row for audit / undo."""
    existing = await issue_repository.get_by_id(issue_id)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"id={issue_id} not found"
        )
    _assert_visibility(existing, auth)
    await issue_repository.soft_delete(issue_id)


# ── helpers ────────────────────────────────────────────────────────


def _assert_visibility(row: dict, auth) -> None:
    """App-layer visibility check (own OR assignee). RLS does the same
    at the DB layer for non-service-role callers, but since the repo
    uses service_role we re-check here."""
    user_id = str(auth.user_id)
    if (
        row.get("created_by_user_id") == user_id
        or row.get("assignee_user_id") == user_id
    ):
        return
    # Team/project visibility folded in by repo in D6.1; until then
    # restrict to own/assignee. 404 (not 403) so we don't leak existence.
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
