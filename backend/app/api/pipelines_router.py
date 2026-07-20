"""Content relay pipelines REST API (W2b).

Endpoints (under /api/v1/pipelines):
    GET    /?team_id=            — list a team's pipelines (member-validated)
    POST   /                     — create a pipeline with an ordered step set
    GET    /{id}                 — get a pipeline + steps
    PATCH  /{id}                 — update (steps, when given, replace atomically)
    DELETE /{id}                 — delete (cascades steps + runs)
    POST   /{id}/run             — start a relay run against a parent issue
    POST   /runs/{run_id}/cancel — cancel a running relay run (no child cascade)

Team is a HARD boundary: every route validates membership server-side against
team_members and returns 404 (never 403) on a cross-team access so existence
never leaks. The runs-for-parent read lives on the issues path
(GET /api/v1/issues/{issue_id}/pipeline-runs) in issues_router.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status
from loguru import logger

from app.core.deps import AuthDep
from app.repositories.issue_repository import issue_repository
from app.repositories.pipeline_repository import pipeline_repository
from app.schemas.pipeline import (
    Pipeline,
    PipelineCreate,
    PipelineRun,
    PipelineRunCreate,
    PipelineUpdate,
)
from app.services.issues.pipeline_relay import (
    PipelineRelayError,
    RelayGateway,
    build_origin_id,
    start_pipeline_run,
)

router = APIRouter(prefix="/pipelines", tags=["Pipelines"])


async def _assert_team_member(user_id: str, team_id: int) -> None:
    """404 (not 403) when the user is not a member of ``team_id``."""
    if not await issue_repository.is_team_member(user_id, int(team_id)):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")


async def _load_pipeline_or_404(pipeline_id: int, auth) -> dict:
    pipeline = await pipeline_repository.get_pipeline(pipeline_id)
    if not pipeline:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
    await _assert_team_member(str(auth.user_id), int(pipeline["team_id"]))
    return pipeline


@router.get("/", response_model=list[Pipeline])
async def list_pipelines(auth: AuthDep, team_id: int = Query(...)) -> list[Pipeline]:
    await _assert_team_member(str(auth.user_id), team_id)
    rows = await pipeline_repository.list_pipelines(team_id)
    return [Pipeline.model_validate(r) for r in rows]


@router.post("/", response_model=Pipeline, status_code=status.HTTP_201_CREATED)
async def create_pipeline(payload: PipelineCreate, auth: AuthDep) -> Pipeline:
    await _assert_team_member(str(auth.user_id), payload.team_id)
    try:
        row = await pipeline_repository.create_pipeline(
            team_id=payload.team_id,
            name=payload.name,
            description=payload.description,
            enabled=payload.enabled,
            created_by_user_id=str(auth.user_id),
            steps=[s.model_dump() for s in payload.steps],
        )
    except Exception as exc:  # noqa: BLE001 — surface a clean 400
        logger.warning(f"[pipelines] create failed: {exc}")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return Pipeline.model_validate(row)


@router.get("/{pipeline_id}", response_model=Pipeline)
async def get_pipeline(pipeline_id: int, auth: AuthDep) -> Pipeline:
    pipeline = await _load_pipeline_or_404(pipeline_id, auth)
    return Pipeline.model_validate(pipeline)


@router.patch("/{pipeline_id}", response_model=Pipeline)
async def update_pipeline(
    pipeline_id: int, payload: PipelineUpdate, auth: AuthDep
) -> Pipeline:
    await _load_pipeline_or_404(pipeline_id, auth)
    steps = (
        [s.model_dump() for s in payload.steps] if payload.steps is not None else None
    )
    try:
        row = await pipeline_repository.update_pipeline(
            pipeline_id,
            name=payload.name,
            description=payload.description,
            enabled=payload.enabled,
            steps=steps,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[pipelines] update {pipeline_id} failed: {exc}")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
    return Pipeline.model_validate(row)


@router.delete("/{pipeline_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_pipeline(pipeline_id: int, auth: AuthDep) -> None:
    await _load_pipeline_or_404(pipeline_id, auth)
    await pipeline_repository.delete_pipeline(pipeline_id)


@router.post("/{pipeline_id}/run", response_model=PipelineRun)
async def run_pipeline(
    pipeline_id: int, payload: PipelineRunCreate, auth: AuthDep
) -> PipelineRun:
    """Start a relay run of ``pipeline_id`` against ``parent_issue_id``. Creates
    and dispatches the step-1 child. Membership on the pipeline's team is checked
    here; same-team + active-run guards live in start_pipeline_run."""
    await _load_pipeline_or_404(pipeline_id, auth)
    try:
        run = await start_pipeline_run(
            pipeline_id, payload.parent_issue_id, str(auth.user_id)
        )
    except PipelineRelayError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    return PipelineRun.model_validate(await _enrich_run(run))


@router.post("/runs/{run_id}/cancel", response_model=PipelineRun)
async def cancel_pipeline_run(run_id: int, auth: AuthDep) -> PipelineRun:
    """Cancel a RUNNING content-relay run.

    Team is a HARD boundary: a run that does not exist, or that lives in a team
    the caller is not a member of, returns 404 (never 403) so existence never
    leaks — the same guard shape the rest of this router uses. Only a run still
    in ``running`` can be cancelled; a run that already reached a terminal state
    (completed / halted / cancelled) returns 409 and the caller should re-read
    the true state. On success the run row flips to ``cancelled`` via the repo
    compare-and-swap and one system line is posted onto the parent issue's
    timeline.

    Boundary (matches multica MUL-4113 — no status cascade): cancelling stops
    the run's state machine ONLY. Already-dispatched step sub-issues and any
    in-flight agent work are left exactly as they are; because the relay's
    advance is a CAS gated on ``status = 'running'`` (see pipeline_relay), a
    cancelled run simply can never fire the next step — no child is touched.
    """
    run = await pipeline_repository.get_run(run_id)
    if not run:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
    # Resolve the owning team via the pipeline and enforce membership (404 on a
    # cross-team run so its existence is not leaked).
    pipeline = await pipeline_repository.get_pipeline(int(run["pipeline_id"]))
    if not pipeline:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
    await _assert_team_member(str(auth.user_id), int(pipeline["team_id"]))

    if run["status"] != "running":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"run is not running (status={run['status']})",
        )

    cancelled = await pipeline_repository.cancel_run(run_id)
    if not cancelled:
        # Lost the race — the run went terminal between the read and the CAS.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="run is no longer running",
        )

    # Re-read for the fully string-coerced row (the CAS RETURNING row is raw).
    fresh = await pipeline_repository.get_run(run_id) or run
    await _post_run_cancelled_message(fresh)
    return PipelineRun.model_validate(await _enrich_run(fresh))


async def _post_run_cancelled_message(run: dict) -> None:
    """Post one system line onto the parent issue's timeline noting the run was
    cancelled by the user. Best-effort — a timeline write must never fail the
    cancel itself (the run is already terminal)."""
    try:
        parent = await issue_repository.get_by_id(int(run["parent_issue_id"]))
        if not parent:
            return
        step = int(run.get("current_step") or 0)
        await RelayGateway().post_parent_message(
            parent,
            (
                f"Pipeline run cancelled — stopped at step {step}. "
                "Already-dispatched sub-issues were left running."
            ),
            key=build_origin_id(run["id"], step),
        )
    except Exception as exc:  # noqa: BLE001 — timeline is decoration only
        logger.warning(f"[pipelines] cancel timeline post failed: {exc!r}")


async def _enrich_run(run: dict) -> dict:
    """Join the pipeline name / total steps / current step's agent onto a run
    row for the UI strip. Best-effort — missing pieces stay None."""
    enriched = dict(run)
    try:
        pipeline = await pipeline_repository.get_pipeline(int(run["pipeline_id"]))
        if pipeline:
            steps = sorted(
                pipeline.get("steps") or [], key=lambda s: int(s["step_order"])
            )
            enriched["pipeline_name"] = pipeline.get("name")
            enriched["total_steps"] = len(steps)
            cur = int(run.get("current_step") or 0)
            match = next((s for s in steps if int(s["step_order"]) == cur), None)
            enriched["current_agent_id"] = match["agent_id"] if match else None
    except Exception as exc:  # noqa: BLE001 — decoration only
        logger.debug(f"[pipelines] enrich run failed: {exc!r}")
    return enriched
