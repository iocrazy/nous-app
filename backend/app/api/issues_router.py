"""Issues REST API — top-level user-visible "thing" entity (PR-D6).

Endpoints (under /api/v1/issues):
    POST   /                 — create
    GET    /                 — list (paged, status/project/team filters)
    GET    /{id}             — get by snowflake id
    GET    /by-identifier/{ident} — get by 'MH-N'
    PATCH  /{id}             — partial update (title/description/etc)
    POST   /{id}/transition  — status transition (lifecycle ts side-effects)
    POST   /{id}/dispatch    — start execute_issue DBOS workflow + persist wf_id
    GET    /paused           — the caller's paused issues (phase 2a §4)
    POST   /{id}/pause       — target-level pause (paused_at + pause the root run)
    POST   /{id}/resume      — clear paused_at; re-dispatch when there is work
    DELETE /{id}             — soft-delete (sets hidden_at)

The dispatch endpoint kicks off `execute_issue` via DBOS and writes
the workflow_id back onto issues.dbos_workflow_id so the frontend can
subscribe via /api/v1/workflows/{workflow_id}/events (D4 SSE).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Optional

from dbos import DBOS, SetWorkflowID
from fastapi import APIRouter, Depends, HTTPException, Query, status
from loguru import logger

from app.core.deps import AuthDep
from app.repositories.issue_repository import issue_repository
from app.repositories.run_deliverables_repository import (
    get_run_deliverables_repository,
)
from app.schemas.issue import (
    DispatchBlockedReason,
    DispatchPreview,
    Issue,
    IssueCreate,
    IssueListResponse,
    IssuePauseResponse,
    IssueResumeResponse,
    IssueStatusTransition,
    IssueUpdate,
    NeedsInputItem,
    NeedsInputListResponse,
    PausedIssueItem,
    PausedListResponse,
)
from app.schemas.outputs import IssueOutputsResponse
from app.services.deliverables.lineage_view import group_by_object
from app.services.issues.issue_visibility import is_issue_visible
from app.services.modules.gate import require_module
from app.workflows.issue_lifecycle import execute_issue

router = APIRouter(
    prefix="/issues",
    tags=["Issues"],
    dependencies=[Depends(require_module("todolist"))],
)


async def _dispatch_execute_issue(
    issue_id: int, wf_id: str, *, auto: bool = False
) -> None:
    """Dispatch the execute_issue DBOS workflow under a pinned workflow_id.

    Phase 2b-2 §4.1: this is also where the dispatch WINDOW opens. Every path
    that starts an ``execute_issue`` comes through here — ``start_execute_issue``
    (the two endpoints + fork) plus four direct callers (autopilot node_start,
    pipeline relay, routine schedule, stranded recovery) — so the
    ``execution_state.dispatching`` marker is written HERE rather than at each
    call site. That is what makes the guard cover the unattended dispatches,
    which are the ones most likely to collide with a human's fork. The write is
    async, which is why this helper is; it is best-effort (see
    ``issue_dispatch.mark_dispatching``) so a marker failure degrades to an
    unguarded dispatch, never to a refused one.

    Client-aware (gateway→DBOSClient prep, currently DORMANT): when the gateway
    has constructed a DBOSClient, enqueue through it into the `dbos_dispatch`
    queue so the worker process claims+runs the workflow. Otherwise (client is
    None — today's reality) fall back to the in-process
    `SetWorkflowID + DBOS.start_workflow` path. Zero behavior change while the
    client stays None.

    ``auto`` (M4 Autopilot, task O2): forwarded as ``execute_issue``'s own
    ``auto`` kwarg — see that workflow's docstring. Manual dispatch (this
    endpoint, ``dispatch_issue`` below) always leaves it at the default
    False; ``node_start._dispatch_node`` is the only caller that passes True.
    """
    from app.services.infra.dbos_orchestrator import (
        _resolve_pinned_app_version,
        get_dbos_client,
    )
    from app.services.issues import issue_dispatch

    # Before the enqueue: a marker stamped afterwards would leave open exactly
    # the window it exists to close — DBOS can have the workflow running
    # before the next statement here executes.
    await issue_dispatch.mark_dispatching(issue_id, wf_id)
    try:
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
            client.enqueue(EnqueueOptions(**opts), issue_id, auto)
            return

        with SetWorkflowID(wf_id):
            DBOS.start_workflow(execute_issue, issue_id, auto)
    except Exception as exc:
        # A duplicate means DBOS already holds the workflow and its own
        # atomic_checkout will remove the key; only a real failure means
        # nobody is coming, so only that clears the window here.
        if not issue_dispatch.looks_like_duplicate_dispatch(exc):
            await issue_dispatch.clear_dispatching(issue_id)
        raise


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


@router.get("/needs-input", response_model=NeedsInputListResponse)
async def list_needs_input(auth: AuthDep) -> NeedsInputListResponse:
    """Issues waiting on a human answer (Spec-4 needs_input first-class) —
    feeds the Task Center 'needs your answer' section (Task 3).

    Route-ordering trap: this MUST be declared before GET /{issue_id} below.
    FastAPI matches routes in declaration order, and /{issue_id}'s int
    converter would 422 trying to parse the literal path segment
    "needs-input" as an int if that route came first.
    """
    rows = await issue_repository.list_needs_input(str(auth.user_id))
    return NeedsInputListResponse(items=[_needs_input_item(r) for r in rows])


def _needs_input_item(r: dict) -> NeedsInputItem:
    """One row → item. Phase 2a: the typed question (if the issue was parked
    with one) rides along from ``execution_state.awaiting_input`` so the
    Task Center can render buttons; ``question`` stays the legacy prose.
    ``assignee_agent_id`` is str()'d so a raw UUID from any caller cannot
    leak through as a non-JSON type."""
    state = r.get("execution_state") or {}
    marker = state.get("awaiting_input") or {}
    if not isinstance(marker, dict):
        marker = {}
    options = marker.get("options") if isinstance(marker.get("options"), list) else []
    return NeedsInputItem(
        issue_id=str(r["id"]),
        title=r["title"],
        question=state.get("outcome_reason"),
        project_id=(str(r["project_id"]) if r.get("project_id") is not None else None),
        team_id=str(r["team_id"]) if r.get("team_id") is not None else None,
        asked_at=r["updated_at"],
        assignee_agent_id=(
            str(r["assignee_agent_id"])
            if r.get("assignee_agent_id") is not None
            else None
        ),
        identifier=r.get("identifier"),
        question_id=marker.get("question_id"),
        kind=marker.get("kind"),
        options=[o for o in options if isinstance(o, dict)],
        allow_free_text=bool(marker.get("allow_free_text", True)),
    )


# Page size of GET /paused; the response flags overflow instead of hiding it.
PAUSED_PAGE = 50


@router.get("/paused", response_model=PausedListResponse)
async def list_paused(auth: AuthDep) -> PausedListResponse:
    """Issues a person paused (phase 2a §2/§4) — the Task Center Paused
    section and the attention strip. Same route-ordering trap as
    /needs-input: declared before GET /{issue_id} or the int converter 422s
    the literal segment."""
    rows = await issue_repository.list_paused(str(auth.user_id), limit=PAUSED_PAGE + 1)
    has_more = len(rows) > PAUSED_PAGE
    return PausedListResponse(
        has_more=has_more,
        items=[
            PausedIssueItem(
                issue_id=str(r["id"]),
                identifier=r.get("identifier"),
                title=r["title"],
                paused_at=r["paused_at"],
                team_id=str(r["team_id"]) if r.get("team_id") is not None else None,
                project_id=(
                    str(r["project_id"]) if r.get("project_id") is not None else None
                ),
                assignee_agent_id=(
                    str(r["assignee_agent_id"])
                    if r.get("assignee_agent_id") is not None
                    else None
                ),
            )
            for r in rows[:PAUSED_PAGE]
        ],
    )


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
    # harness p4 §1-⑤: ``clear_budget`` is the only way to write NULL through
    # an exclude_none payload; it wins over a budget_cents sent alongside.
    if patch.pop("clear_budget", None):
        patch["budget_cents"] = None

    if not patch:
        return Issue.model_validate(_normalise_uuid_strs(existing))

    try:
        row = await issue_repository.update(issue_id, patch)
    except Exception as e:
        logger.warning(f"[issues] update {issue_id} failed: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    return Issue.model_validate(_normalise_uuid_strs(row))


async def _assert_stage_owner_or_manager(issue: dict, auth) -> None:
    """Owner-review guard (spec §7 — 项目 workflow M1 PR-B): a ``project_stage``
    mirror issue moving in_review→done may only be closed by the node's own
    owner, or by the project's manager as an override (agent-owner nodes have
    no human owner to match, so they always require the manager path).

    Flow Rules (mig 386, M2 PR-D): a node's ``completion_policy`` gates this
    further. ``'owner'`` (default) keeps the semantics above exactly. On
    ``'any_editor'`` any effective role of manager OR editor may complete the
    review — not just the node's own owner or a manager override.

    Applies ONLY to the NEW three-segment origin_id shape
    (``project_stage:{project_id}:{node_id}``) — the OLD two-segment SOP-stage
    mirror (no project scope encoded, 存量镜像 issue) is never locked, matching
    ``_fire_stage_node_sync``'s own old-format skip. A node id that fails to
    resolve to a live ``project_stage_nodes`` row degrades the same way (fail
    open — there is no owner to enforce against).
    """
    from app.repositories.project_stage_nodes_repository import (
        get_project_stage_nodes_repository,
    )
    from app.services.library.project_stage_issues import parse_stage_origin_id

    origin_id = issue.get("origin_id")
    if not origin_id:
        return
    project_id, node_id = parse_stage_origin_id(str(origin_id))
    if project_id is None:
        return  # old two-segment origin — not locked

    node = await get_project_stage_nodes_repository().get_node(node_id, project_id)
    if node is None:
        return  # can't resolve an owner — fail open

    user_id = str(auth.user_id)
    owner_user_id = node.get("owner_user_id")
    if owner_user_id is not None and str(owner_user_id) == user_id:
        return

    from app.core.workflow_roles import EDITOR, MANAGER, resolve_effective_role

    role = await resolve_effective_role(user_id, project_id=project_id)
    if role == MANAGER:
        return
    if node.get("completion_policy") == "any_editor" and role == EDITOR:
        return

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Only the stage owner or a project manager may complete this review",
    )


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
    # Owner-review guard: only a project_stage issue's node owner (or the
    # project manager, as an override) may push it in_review→done. Every other
    # issue — different origin_kind, different edge, or no project — is
    # completely unaffected.
    if (
        body.status.value == "done"
        and existing.get("status") == "in_review"
        and existing.get("origin_kind") == "project_stage"
        and existing.get("project_id") is not None
    ):
        await _assert_stage_owner_or_manager(existing, auth)
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

    await _start_execute_issue(issue_id)
    row = await issue_repository.get_by_id(issue_id)
    return Issue.model_validate(_normalise_uuid_strs(row))


async def _start_execute_issue(issue_id: int) -> str:
    """Shared by ``/dispatch`` and ``/resume`` (and, via the service, the
    fork endpoint): ``issue_dispatch.start_execute_issue`` with the DBOS
    refusal mapped to the endpoint-shaped HTTPException (500)."""
    from app.services.issues.issue_dispatch import DispatchFailed, start_execute_issue

    try:
        return await start_execute_issue(issue_id)
    except DispatchFailed as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


async def _persist_workflow_id(issue_id: int, workflow_id: str) -> None:
    """Persist workflow_id so the UI can find it without re-deriving.
    dbos_workflow_id is service_role-only (mig-170 allowlist trigger); the
    repository writes via the app-role engine and the trigger rejects it —
    use the SET LOCAL ROLE service_role helper instead (same pattern as
    issue_lifecycle.py execution-field writes)."""
    from sqlalchemy import text, update

    from app.db.session import write_scope
    from app.models import Issues

    async with write_scope() as session:
        await session.execute(text("SET LOCAL ROLE service_role"))
        await session.execute(
            update(Issues)
            .where(Issues.id == issue_id)
            .values(dbos_workflow_id=workflow_id)
        )


def _conversation_key(row: dict) -> Optional[int]:
    """The issue's session conversation, the live key for "is a run on this
    issue" (``agent_runs.issue_id`` is backfilled after the turn). Read-only:
    an issue without a session has never run — do not create one here."""
    sid = row.get("ai_session_id")
    return int(sid) if sid is not None else None


async def _load_visible_issue(issue_id: int, auth) -> dict:
    existing = await issue_repository.get_by_id(issue_id)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"id={issue_id} not found"
        )
    await _assert_visibility(existing, auth)
    return existing


@router.post("/{issue_id}/pause", response_model=IssuePauseResponse)
async def pause_issue(issue_id: int, auth: AuthDep) -> IssuePauseResponse:
    """Target-level pause (phase 2a §2). Two writes, in this order:

    1. ``issues.paused_at`` — the truth. The rollup phase reads it first, the
       dispatch loop refuses to start a turn while it is set, comments queue
       on the inbox, the sweeper leaves those queued items alone.
    2. ``agent_runs.pause_requested`` on the ROOT run in flight, if any —
       ``PauseHook`` stops it at the next step boundary (``turn_end{reason:
       paused}``). Order matters: the run observes the flag AFTER the issue
       is already marked, so the workflow's paused return finds ``paused_at``.

    Authorised at the target (issue visibility), not run ownership."""
    from datetime import datetime, timezone

    from app.repositories.agent_runs_repository import get_agent_runs_repository

    existing = await _load_visible_issue(issue_id, auth)
    if existing.get("paused_at"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "already_paused", "message": "issue is already paused"},
        )
    runs = get_agent_runs_repository()
    run_id = await _running_root_run(runs, issue_id, _conversation_key(existing))
    now = datetime.now(timezone.utc)
    updated = await issue_repository.set_paused_at(issue_id, now)
    if run_id is not None:
        requested = await runs.request_pause(run_id)
        if not requested:
            # The run ended between the two reads; paused_at still holds and
            # the next dispatch refuses to start — say so rather than claim a
            # run was stopped.
            logger.info(
                f"[issues] pause {issue_id}: run {run_id} no longer running "
                "when the flag was raised"
            )
            run_id = None
    return IssuePauseResponse(
        issue_id=str(issue_id),
        paused_at=(updated or {}).get("paused_at") or now,
        run_id=str(run_id) if run_id is not None else None,
    )


async def _last_run_ended_paused(
    runs, *, issue_id: int, conversation_id: Optional[int]
) -> Optional[int]:
    """The id of the newest ROOT run when it ended on a pause, else None
    (``metadata_json.view.ended.reason`` is the folded turn_end reason)."""
    rows = await runs.list_for_issue(
        issue_id=issue_id, conversation_id=conversation_id, limit=1
    )
    if not rows:
        return None
    latest = rows[0]
    ended = (((latest.get("metadata_json") or {}).get("view") or {}).get("ended")) or {}
    return int(latest["id"]) if ended.get("reason") == "paused" else None


@router.post("/{issue_id}/resume", response_model=IssueResumeResponse)
async def resume_issue(issue_id: int, auth: AuthDep) -> IssueResumeResponse:
    """Resume a paused issue — and the "run the queued comments" button for
    an idle one. Decision table (``paused`` = ``paused_at`` set, ``pending`` =
    unclaimed inbox items on the issue, ``last_paused`` = newest root run
    ended on a pause), first match wins:

    - neither paused nor pending                → 409 ``not_paused``
    - a root run is live, paused                → withdraw its pause request;
                                                  it keeps going (``withdrawn``).
                                                  If the withdrawal finds the
                                                  run already ended, fall
                                                  through as "not running".
    - a root run is live, not paused            → nothing to dispatch: the run
                                                  claims the queue at its next
                                                  step boundary (``running``)
    - execution lock held, no live run          → a workflow is parked on a
                                                  question (``parked``) or is
                                                  between turns (``running``);
                                                  a second dispatch would only
                                                  lose on ``atomic_checkout``
    - pending or last_paused                    → write
                                                  ``execution_state.resumed_from_run_id``,
                                                  clear the flag, dispatch
                                                  (``dispatched``)
    - otherwise (e.g. paused while idle)        → clear the flag (``cleared``)

    The flag is cleared right before a re-dispatch (the new workflow reads
    it) and restored when the dispatch fails, so a failed resume leaves the
    issue visibly paused instead of silently idle."""
    from app.repositories.agent_run_inbox_repository import (
        get_agent_run_inbox_repository,
    )
    from app.repositories.agent_runs_repository import get_agent_runs_repository
    from app.services.infra import dbos_orchestrator
    from app.services.issues.execution_state import merge_execution_state
    from app.services.issues.issue_dispatch import is_dispatching

    existing = await _load_visible_issue(issue_id, auth)
    paused = bool(existing.get("paused_at"))
    pending = await get_agent_run_inbox_repository().pending_count(
        target_kind="issue", target_id=issue_id
    )
    if not paused and pending == 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "not_paused", "message": "issue is not paused"},
        )
    runs = get_agent_runs_repository()
    conversation_id = _conversation_key(existing)
    running = await _running_root_run(runs, issue_id, conversation_id)

    if running is not None:
        if not paused:
            return IssueResumeResponse(
                issue_id=str(issue_id),
                dispatched=False,
                reason="running",
                run_id=str(running),
            )
        if await runs.clear_pause_request(running):
            await issue_repository.set_paused_at(issue_id, None)
            return IssueResumeResponse(
                issue_id=str(issue_id),
                dispatched=False,
                reason="withdrawn",
                run_id=str(running),
            )
        # The run ended between the two reads (PauseHook fired, or it finished):
        # "keeps going" would be a lie. Re-read and decide as if nothing ran.
        logger.info(
            f"[issues] resume {issue_id}: run {running} ended before the pause "
            "could be withdrawn"
        )
        existing = await _load_visible_issue(issue_id, auth)

    # Phase 2b-2 §4.1: same window the fork guard closes. The lock below is
    # written by the workflow's atomic_checkout, so between an enqueue and that
    # step a resume sees an idle issue and dispatches a second workflow — which
    # atomic_checkout then silently skips.
    if is_dispatching(existing):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "issue_busy", "message": "a dispatch is in flight"},
        )

    if existing.get("execution_locked_at"):
        marker = (existing.get("execution_state") or {}).get("awaiting_input") or {}
        parked = bool(marker) and not marker.get("answered_at")
        if paused:
            await issue_repository.set_paused_at(issue_id, None)
        return IssueResumeResponse(
            issue_id=str(issue_id),
            dispatched=False,
            reason="parked" if parked else "running",
            workflow_id=(
                str(existing["dbos_workflow_id"])
                if existing.get("dbos_workflow_id")
                else None
            ),
        )

    last_paused = await _last_run_ended_paused(
        runs, issue_id=issue_id, conversation_id=conversation_id
    )
    if pending == 0 and last_paused is None:
        await issue_repository.set_paused_at(issue_id, None)
        return IssueResumeResponse(
            issue_id=str(issue_id), dispatched=False, reason="cleared"
        )

    if not dbos_orchestrator.is_enabled():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="DBOS not enabled — issue resume unavailable",
        )
    # Marker first (a resumed dispatch is distinguishable from a fresh one,
    # and from which run it continues); then the flag — BEFORE the dispatch,
    # because the new workflow's first loop iteration reads ``paused_at`` and
    # would park itself on a stale flag; a failed dispatch restores it so the
    # issue stays visibly paused instead of silently idle.
    await merge_execution_state(
        issue_id,
        {"resumed_from_run_id": str(last_paused) if last_paused else None},
    )
    if paused:
        await issue_repository.set_paused_at(issue_id, None)
    try:
        workflow_id = await _start_execute_issue(issue_id)
    except Exception:
        if paused:
            await issue_repository.set_paused_at(issue_id, existing["paused_at"])
        raise
    return IssueResumeResponse(
        issue_id=str(issue_id),
        dispatched=True,
        reason="dispatched",
        workflow_id=workflow_id,
    )


async def _running_root_run(runs, issue_id: int, conversation_id: Optional[int]):
    """The live ROOT run on the issue, or None. A failed read is NOT "nothing
    running" (CLAUDE.md: an empty answer is not a negative result) — it is a
    503 the caller can retry, never a silent pause/resume of the wrong thing."""
    try:
        return await runs.running_root_run_id(
            issue_id=issue_id, conversation_id=conversation_id
        )
    except Exception as exc:  # noqa: BLE001 — typed at the boundary
        logger.error(f"[issues] run-state read failed for issue {issue_id}: {exc}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "run_state_unavailable",
                "message": "could not read the issue's run state; retry",
            },
        )


@router.get("/{issue_id}/pipeline-runs")
async def list_issue_pipeline_runs(issue_id: int, auth: AuthDep):
    """List content-relay runs (W2b) whose parent is this issue, newest first,
    decorated with pipeline name / total steps / current agent for the UI strip.
    Visibility follows the same team boundary as the issue itself."""
    from app.api.pipelines_router import _enrich_run
    from app.repositories.pipeline_repository import pipeline_repository
    from app.schemas.pipeline import PipelineRun, PipelineRunListResponse

    existing = await issue_repository.get_by_id(issue_id)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"id={issue_id} not found"
        )
    await _assert_visibility(existing, auth)
    runs = await pipeline_repository.list_runs_for_parent(issue_id)
    items = [PipelineRun.model_validate(await _enrich_run(r)) for r in runs]
    return PipelineRunListResponse(items=items)


@router.get("/{issue_id}/outputs", response_model=IssueOutputsResponse)
async def list_issue_outputs(issue_id: int, auth: AuthDep) -> IssueOutputsResponse:
    """Everything this issue produced, grouped by ``(kind, ref_id)``.

    The panel's unit is the OBJECT: three revisions of one shot are one entry
    with three versions, not three entries — otherwise "what did this issue
    produce" reads as more work than actually happened.

    An issue with no outputs is an empty list, NOT a 404: the issue exists and
    is visible, it simply produced nothing yet. (The per-object endpoint is the
    opposite — see ``outputs_router`` — because there "no rows" means the
    object was never registered, which is a different fact.)

    Ownership goes through the run: ``run_deliverables`` has no ``issue_id``
    column, and ``agent_runs.issue_id`` is the single truth (spec §3).
    """
    existing = await issue_repository.get_by_id(issue_id)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"id={issue_id} not found"
        )
    await _assert_visibility(existing, auth)
    rows = await get_run_deliverables_repository().list_for_issue(issue_id)
    return IssueOutputsResponse(items=group_by_object(rows, issue_id=str(issue_id)))


#: How long a stopped schedule keeps showing on its issue. Long enough to
#: answer "why didn't my wake-up fire?", short enough that a long-lived issue
#: does not accumulate every wake-up it ever armed.
_RETIRED_SCHEDULE_WINDOW = timedelta(days=7)
_ISSUE_SCHEDULES_LIMIT = 50


@router.get("/{issue_id}/schedules")
async def list_issue_schedules(issue_id: int, auth: AuthDep) -> dict:
    """Everything timed on this issue: the one-shot wake-ups pointing at it,
    plus the routine that created it.

    Read-only — cancelling goes through ``DELETE /api/v1/schedules/{id}``,
    which is owner-scoped. Visibility here is the ISSUE's (a teammate who can
    read the issue sees what will wake it), which is why the query is not
    additionally filtered by schedule owner.

    ``payload.last_issue_id`` is the only field an agent_routine payload has
    that names an issue — it is the evidence that THIS routine produced THIS
    issue (see ``_fire_agent_routine``'s stash).

    Bounded on purpose: live rows plus recently stopped ones, newest fire
    first, capped. Every wake-up a long-lived issue ever armed would otherwise
    stay in this list for ever and the panel reading it would grow without
    limit."""
    import datetime as _dt

    from sqlalchemy import or_, select

    from app.db.session import read_scope
    from app.models import UserSchedules

    existing = await issue_repository.get_by_id(issue_id)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"id={issue_id} not found"
        )
    await _assert_visibility(existing, auth)

    # jsonb ->> yields TEXT, so the id has to be bound as a string; a bigint
    # bind silently matches nothing.
    sid = str(issue_id)
    recent = _dt.datetime.now(_dt.timezone.utc) - _RETIRED_SCHEDULE_WINDOW
    stmt = (
        select(*UserSchedules.__table__.columns)
        .where(
            or_(
                (UserSchedules.task_type == "issue_wakeup")
                & (UserSchedules.payload["issue_id"].astext == sid),
                (UserSchedules.task_type == "agent_routine")
                & (UserSchedules.payload["last_issue_id"].astext == sid),
            )
        )
        .where(
            or_(
                UserSchedules.enabled.is_(True),
                UserSchedules.paused_at > recent,
            )
        )
        .order_by(UserSchedules.enabled.desc(), UserSchedules.next_fire_at)
        .limit(_ISSUE_SCHEDULES_LIMIT)
    )
    async with read_scope() as session:
        rows = (await session.execute(stmt)).mappings().all()

    items = []
    for row in rows:
        payload = row["payload"] or {}
        items.append(
            {
                "id": str(row["id"]),
                "task_type": row["task_type"],
                "fire_at": (
                    row["next_fire_at"].isoformat() if row["next_fire_at"] else None
                ),
                "cron_expr": row["cron_expr"],
                # A wake-up carries `text`; a routine carries the prompt it
                # will run. Both answer "what will happen when this fires".
                "text": (payload.get("text") or payload.get("prompt_md") or "")[:500],
                "created_by": payload.get("created_by") or "user",
                "enabled": bool(row["enabled"]),
                # WHY it stopped. fired_once / issue_terminal / stale /
                # dispatch_failed are four very different states that all
                # render as `enabled: false` without this.
                "pause_reason": row["pause_reason"],
            }
        )
    return {"items": items}


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
    """Shared rule (services/issues/issue_visibility): own / assignee / team
    member, else 404 so existence never leaks across teams."""
    if not await is_issue_visible(row, str(auth.user_id)):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
