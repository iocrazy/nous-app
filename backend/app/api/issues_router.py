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

from dbos import DBOS, SetWorkflowID
from fastapi import APIRouter, HTTPException, Query, status
from loguru import logger

from app.core.deps import AuthDep
from app.repositories.issue_repository import issue_repository
from app.schemas.issue import (
    DispatchBlockedReason,
    DispatchPreview,
    Issue,
    IssueCreate,
    IssueListResponse,
    IssueStatusTransition,
    IssueUpdate,
)
from app.workflows.issue_lifecycle import execute_issue

router = APIRouter(prefix="/issues", tags=["Issues"])


def _dispatch_execute_issue(issue_id: int, wf_id: str) -> None:
    """Dispatch the execute_issue DBOS workflow under a pinned workflow_id.

    Client-aware (gateway→DBOSClient prep, currently DORMANT): when the gateway
    has constructed a DBOSClient, enqueue through it into the `dbos_dispatch`
    queue so the worker process claims+runs the workflow. Otherwise (client is
    None — today's reality) fall back to the in-process
    `SetWorkflowID + DBOS.start_workflow` path. Zero behavior change while the
    client stays None.
    """
    from app.services.infra.dbos_orchestrator import (
        _resolve_pinned_app_version,
        get_dbos_client,
    )

    client = get_dbos_client()
    if client is not None:
        from dbos import EnqueueOptions

        opts: dict = {
            "workflow_name": "execute_issue",
            "queue_name": "dbos_dispatch",
            "workflow_id": wf_id,
        }
        pinned = _resolve_pinned_app_version()
        if pinned:
            opts["app_version"] = pinned
        client.enqueue(EnqueueOptions(**opts), issue_id)
        return

    with SetWorkflowID(wf_id):
        DBOS.start_workflow(execute_issue, issue_id)


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
    """List issues visible to the authenticated user.

    D6.1 team folding: own OR assignee OR member of the issue's team
    (team_members subquery in the repo). The team_id param is a filter on
    top of that visibility, never an authorization input.
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
    await _assert_visibility(row, auth)
    return Issue.model_validate(_normalise_uuid_strs(row))


@router.get("/{issue_id}", response_model=Issue)
async def get_issue(issue_id: int, auth: AuthDep) -> Issue:
    row = await issue_repository.get_by_id(issue_id)
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"id={issue_id} not found",
        )
    await _assert_visibility(row, auth)
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
    await _assert_visibility(existing, auth)

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
    await _assert_visibility(existing, auth)
    try:
        row = await issue_repository.transition_status(issue_id, body.status.value)
    except Exception as e:
        logger.warning(f"[issues] transition {issue_id} failed: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    return Issue.model_validate(_normalise_uuid_strs(row))


@router.get("/{issue_id}/dispatch-preview", response_model=DispatchPreview)
async def dispatch_preview(issue_id: int, auth: AuthDep) -> DispatchPreview:
    """Predict what POST /{id}/dispatch would start — without starting it.

    Read-only. Mirrors dispatch_issue's own guards so the confirm dialog can
    say "X will start working" (or why nothing would) from the server's rule
    rather than a client-side copy that drifts.
    """
    from app.services.infra import dbos_orchestrator

    existing = await issue_repository.get_by_id(issue_id)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"id={issue_id} not found"
        )
    await _assert_visibility(existing, auth)

    agent_raw = existing.get("assignee_agent_id")
    agent_id = str(agent_raw) if agent_raw else None
    issue_status = existing.get("status")

    if not dbos_orchestrator.is_enabled():
        return DispatchPreview(
            will_start=False,
            agent_id=agent_id,
            blocked_reason=DispatchBlockedReason.DBOS_DISABLED,
        )
    if issue_status in ("done", "cancelled"):
        return DispatchPreview(
            will_start=False,
            agent_id=agent_id,
            blocked_reason=DispatchBlockedReason.TERMINAL_STATUS,
        )
    if not agent_id:
        return DispatchPreview(
            will_start=False, blocked_reason=DispatchBlockedReason.NO_ASSIGNEE
        )
    # A live run holds the CAS lock; re-dispatching would be rejected downstream.
    if existing.get("dbos_workflow_id") and issue_status == "in_progress":
        return DispatchPreview(
            will_start=False,
            agent_id=agent_id,
            blocked_reason=DispatchBlockedReason.ALREADY_RUNNING,
        )
    return DispatchPreview(will_start=True, agent_id=agent_id)


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
    await _assert_visibility(existing, auth)

    if not dbos_orchestrator.is_enabled():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="DBOS not enabled — issue dispatch unavailable",
        )

    import uuid as _uuid

    # Unique per dispatch so an issue can be re-dispatched after a prior run
    # finished or errored — a fixed `issue-{id}` id would dedup in DBOS → the
    # re-dispatch becomes a silent no-op. The atomic_checkout CAS lock
    # (execution_locked_at) still prevents concurrent double-runs.
    workflow_id = f"issue-{issue_id}-{_uuid.uuid4().hex[:12]}"
    try:
        _dispatch_execute_issue(issue_id, workflow_id)
    except Exception as e:
        # Duplicate workflow_id is a soft success — DBOS already has it.
        if (
            "already exists" not in repr(e).lower()
            and "duplicate" not in repr(e).lower()
        ):
            logger.warning(f"[issues] dispatch {issue_id} failed: {e}")
            raise HTTPException(status_code=500, detail=f"DBOS dispatch failed: {e}")

    # Persist workflow_id so the UI can find it without re-deriving.
    # dbos_workflow_id is service_role-only (mig-170 allowlist trigger); the
    # repository writes via the app-role engine and the trigger rejects it —
    # use the SET LOCAL ROLE service_role helper instead (same pattern as
    # issue_lifecycle.py execution-field writes).
    from app.db import engine as db_engine

    await db_engine.execute_as_service_role(
        "UPDATE public.issues SET dbos_workflow_id = :wf WHERE id = :iid",
        {"wf": workflow_id, "iid": issue_id},
    )
    row = await issue_repository.get_by_id(issue_id)
    return Issue.model_validate(_normalise_uuid_strs(row))


@router.delete("/{issue_id}", status_code=status.HTTP_204_NO_CONTENT)
async def soft_delete_issue(issue_id: int, auth: AuthDep) -> None:
    """User-facing delete — sets hidden_at, keeps row for audit / undo."""
    existing = await issue_repository.get_by_id(issue_id)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"id={issue_id} not found"
        )
    await _assert_visibility(existing, auth)
    await issue_repository.soft_delete(issue_id)


# ── helpers ────────────────────────────────────────────────────────


async def _assert_visibility(row: dict, auth) -> None:
    """App-layer visibility check. RLS does the same at the DB layer for
    non-service-role callers, but since the repo uses service_role we
    re-check here.

    D6.1 (用户立约: team 是铁边界): visible = own OR assignee OR member of
    the issue's team. Membership is checked server-side against
    team_members — never inferred from client input. 404 (not 403) so we
    don't leak existence across teams.
    """
    user_id = str(auth.user_id)
    if (
        row.get("created_by_user_id") == user_id
        or row.get("assignee_user_id") == user_id
    ):
        return
    team_id = row.get("team_id")
    if team_id is not None and await issue_repository.is_team_member(
        user_id, int(team_id)
    ):
        return
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
