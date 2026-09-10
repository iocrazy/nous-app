"""Content relay pipelines (W2b) — sequential fan-OUT of agent sub-issues.

A pipeline is a FIXED ordered relay of agent steps (Topic → Script → Storyboard
→ …). Running one against a parent issue creates the step-1 child assigned to
that step's agent; when a step's child reaches ``done``, the next step's child is
auto-created and dispatched; every child hangs off the same parent. This is the
fan-OUT half — ``subissue_barrier.py`` already does the fan-IN roll-up when every
child goes terminal, and the two compose (a pipeline whose children all finish
also trips the barrier, which is fine — they serve different purposes).

Two observation seams (the SAME two the barrier rides, wired right next to it):
  * ``IssueRepository.transition_status`` post-callback — the /transition
    endpoint and the project-stage close both flow through it.
  * ``issue_lifecycle.execute_issue`` workflow BODY — the agent workflow lands
    terminal via a raw SQL setter that bypasses the repo, so the workflow body
    fires the hook after routing. It must be the body, never a @DBOS.step:
    dispatching a workflow inside a @DBOS.step raises a bare AssertionError
    (bug_retry_failed_downloads_two_layer). ``start_pipeline_run`` is only ever
    called from the REST endpoint, which is also a safe (non-step) dispatch site.

IDEMPOTENCY (race-safe advance) — chosen mechanism: COMPARE-AND-SWAP on the
run's ``current_step``. Both seams may observe the same child's non-terminal→
terminal edge nearly simultaneously. Advancing does an atomic single-row UPDATE
``… SET current_step = N+1 WHERE id = run AND status='running' AND
current_step = N`` (pipeline_repository.advance_run_step). Exactly ONE racing
observer gets a row back (the CAS winner) and creates the next child; the loser
gets 0 rows and no-ops. Completion and halt are the same CAS keyed on status.
As a second belt, the next child is stamped
``origin_id = 'pipeline:{run_id}:{step_order}'`` and creation is skipped when an
issue already carries that origin — so even a replay after the CAS still can't
double-create. No advisory lock and no unique index on the child are needed; the
run row IS the state machine.

Testability: all I/O is funnelled through a ``RelayGateway`` so the decision
logic unit-tests with a fake (mirrors subissue_barrier.BarrierGateway).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

# A pipeline child is "finished" on the same terminal set as the barrier.
TERMINAL_STATUSES = frozenset({"done", "cancelled"})

ORIGIN_KIND = "pipeline"

# prev_output excerpt cap handed to the next step's template.
PREV_OUTPUT_CAP = 2000


# ── errors (mapped to HTTP by the router) ─────────────────────────────────


class PipelineRelayError(Exception):
    """Base for relay errors; ``status_code`` drives the REST response."""

    status_code = 400

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class PipelineNotFound(PipelineRelayError):
    status_code = 404


class PipelineConflict(PipelineRelayError):
    status_code = 409


class PipelineValidationError(PipelineRelayError):
    status_code = 400


# ── pure helpers ──────────────────────────────────────────────────────────


def is_terminal(status: Optional[str]) -> bool:
    return status in TERMINAL_STATUSES


def build_origin_id(run_id: Any, step_order: int) -> str:
    return f"{ORIGIN_KIND}:{run_id}:{int(step_order)}"


def parse_origin_id(origin_id: Optional[str]) -> Optional[Tuple[int, int]]:
    """``pipeline:{run_id}:{step_order}`` → (run_id, step_order), or None if it
    is not a well-formed pipeline origin. Strict — a malformed id yields None
    rather than a partial parse."""
    if not origin_id or not origin_id.startswith(ORIGIN_KIND + ":"):
        return None
    parts = origin_id.split(":")
    if len(parts) != 3:
        return None
    try:
        return int(parts[1]), int(parts[2])
    except (TypeError, ValueError):
        return None


def _truncate(text: str, cap: int) -> str:
    text = (text or "").strip()
    if len(text) <= cap:
        return text
    return text[: max(0, cap - 1)].rstrip() + "…"


def render_template(template: str, variables: Dict[str, Any]) -> str:
    """Substitute the known ``{name}`` placeholders. Explicit replacement (not
    str.format) so a template that happens to contain other braces — a JSON
    example, a code block — is left intact instead of raising KeyError."""
    out = template or ""
    for key, value in variables.items():
        out = out.replace("{" + key + "}", "" if value is None else str(value))
    return out


def _template_vars(
    *,
    parent: Dict[str, Any],
    step_order: int,
    pipeline_name: str,
    prev_output: Optional[str],
) -> Dict[str, Any]:
    return {
        "parent_title": parent.get("title") or "",
        "parent_description": parent.get("description") or "",
        "step_order": step_order,
        "pipeline_name": pipeline_name or "",
        "prev_output": _truncate(prev_output or "", PREV_OUTPUT_CAP),
    }


# ── gateway (all external I/O behind one seam) ────────────────────────────


class RelayGateway:
    """Real-binding gateway; unit tests pass a fake with the same surface."""

    async def get_issue(self, issue_id: int) -> Optional[Dict[str, Any]]:
        from app.repositories.issue_repository import issue_repository

        return await issue_repository.get_by_id(int(issue_id))

    async def get_pipeline(self, pipeline_id: int) -> Optional[Dict[str, Any]]:
        from app.repositories.pipeline_repository import pipeline_repository

        return await pipeline_repository.get_pipeline(int(pipeline_id))

    async def get_run(self, run_id: int) -> Optional[Dict[str, Any]]:
        from app.repositories.pipeline_repository import pipeline_repository

        return await pipeline_repository.get_run(int(run_id))

    async def get_active_run_for_parent(
        self, parent_issue_id: int
    ) -> Optional[Dict[str, Any]]:
        from app.repositories.pipeline_repository import pipeline_repository

        return await pipeline_repository.get_active_run_for_parent(int(parent_issue_id))

    async def create_run(
        self, *, pipeline_id: int, parent_issue_id: int, started_by_user_id: str
    ) -> Dict[str, Any]:
        from app.repositories.pipeline_repository import pipeline_repository

        return await pipeline_repository.create_run(
            pipeline_id=int(pipeline_id),
            parent_issue_id=int(parent_issue_id),
            started_by_user_id=started_by_user_id,
        )

    async def advance_run_step(
        self, run_id: int, *, from_step: int, to_step: int
    ) -> Optional[Dict[str, Any]]:
        from app.repositories.pipeline_repository import pipeline_repository

        return await pipeline_repository.advance_run_step(
            int(run_id), from_step=int(from_step), to_step=int(to_step)
        )

    async def complete_run(
        self, run_id: int, *, from_step: int
    ) -> Optional[Dict[str, Any]]:
        from app.repositories.pipeline_repository import pipeline_repository

        return await pipeline_repository.complete_run(
            int(run_id), from_step=int(from_step)
        )

    async def halt_run(self, run_id: int, *, reason: str) -> Optional[Dict[str, Any]]:
        from app.repositories.pipeline_repository import pipeline_repository

        return await pipeline_repository.halt_run(int(run_id), reason=reason)

    async def list_by_origin(
        self, origin_kind: str, origin_id: str
    ) -> List[Dict[str, Any]]:
        from app.repositories.issue_repository import issue_repository

        return await issue_repository.list_by_origin(origin_kind, origin_id)

    async def create_issue(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        from app.repositories.issue_repository import issue_repository

        return await issue_repository.atomic_create(payload)

    async def dispatch_issue(self, issue_id: int) -> Optional[str]:
        """Kick off execute_issue for a freshly-created step child and persist
        its workflow id (mirrors issues_router.dispatch_issue). Best-effort:
        when DBOS is disabled the child is left in ``todo`` and logged."""
        from app.services.infra import dbos_orchestrator

        if not dbos_orchestrator.is_enabled():
            logger.warning(
                f"[pipeline_relay] DBOS disabled — step child {issue_id} left in "
                f"todo (not dispatched)"
            )
            return None

        import uuid as _uuid

        from sqlalchemy import text, update

        from app.api.issues_router import _dispatch_execute_issue
        from app.db.session import write_scope
        from app.models import Issues

        wf_id = f"issue-{int(issue_id)}-{_uuid.uuid4().hex[:12]}"
        await _dispatch_execute_issue(int(issue_id), wf_id)
        async with write_scope() as session:
            await session.execute(text("SET LOCAL ROLE service_role"))
            await session.execute(
                update(Issues)
                .where(Issues.id == int(issue_id))
                .values(dbos_workflow_id=wf_id)
            )
        return wf_id

    async def last_substantive_message(self, issue_row: Dict[str, Any]) -> str:
        """Reuse the barrier's dual-path last-message extractor verbatim (session
        conversation, else legacy issue_messages)."""
        from app.services.issues.subissue_barrier import BarrierGateway

        return await BarrierGateway().last_substantive_message(issue_row)

    async def agent_name(self, agent_id: Optional[str]) -> str:
        """Best-effort human name for a step's agent (falls back to the id)."""
        if not agent_id:
            return "agent"
        try:
            import uuid as _uuid

            from app.repositories.agent_repository import get_agent_repository

            row = await get_agent_repository().get_by_id(_uuid.UUID(str(agent_id)))
            if row:
                return row.get("name") or row.get("slug") or str(agent_id)
        except Exception as exc:  # noqa: BLE001 — name is decoration only
            logger.debug(f"[pipeline_relay] agent_name lookup failed: {exc!r}")
        return str(agent_id)

    async def post_parent_message(
        self, parent_row: Dict[str, Any], body: str, key: str
    ) -> None:
        """Post a system handoff / completion line onto the PARENT's timeline.
        Dual path mirrors the barrier: agent parents read their session, humans
        read issue_messages."""
        session_id = parent_row.get("ai_session_id")
        owner_id = parent_row.get("created_by_user_id") or parent_row.get(
            "assignee_user_id"
        )
        if session_id:
            from app.repositories.conversation_repository import (
                get_conversation_repository,
            )

            await get_conversation_repository().send_message(
                conversation_id=int(session_id),
                sender_id=str(owner_id) if owner_id else None,
                sender_type="user",
                type="text",
                body={
                    "text": body,
                    "meta": {"pipeline_key": key, "pipeline_relay": True},
                },
                parent_id=None,
            )
            return

        from sqlalchemy import insert

        from app.db.session import write_scope
        from app.models import IssueMessages

        async with write_scope() as session:
            await session.execute(
                insert(IssueMessages).values(
                    {
                        "issue_id": int(parent_row["id"]),
                        "kind": "comment",
                        "author_user_id": str(owner_id) if owner_id else None,
                        "body": body,
                        "meta": {"pipeline_key": key, "pipeline_relay": True},
                    }
                )
            )


# ── start ─────────────────────────────────────────────────────────────────


async def start_pipeline_run(
    pipeline_id: int,
    parent_issue_id: int,
    user_id: str,
    *,
    gateway: Optional[RelayGateway] = None,
) -> Dict[str, Any]:
    """Validate + create a run and dispatch its step-1 child.

    Raises PipelineNotFound / PipelineConflict / PipelineValidationError; the
    router maps ``.status_code`` to the HTTP response. Team membership is checked
    in the router (like _assert_visibility); here we enforce that the pipeline
    and the parent issue live in the SAME team (cross-team → 404, never 403)."""
    gw = gateway or RelayGateway()

    pipeline = await gw.get_pipeline(pipeline_id)
    if not pipeline:
        raise PipelineNotFound("pipeline not found")
    if not pipeline.get("enabled"):
        raise PipelineValidationError("pipeline is disabled")

    steps = sorted(pipeline.get("steps") or [], key=lambda s: int(s["step_order"]))
    if not steps:
        raise PipelineValidationError("pipeline has no steps")

    parent = await gw.get_issue(parent_issue_id)
    if not parent:
        raise PipelineNotFound("parent issue not found")

    # Hard team boundary: the pipeline and the parent issue must share a team.
    # 404 (not 403) so cross-team existence never leaks.
    if str(parent.get("team_id")) != str(pipeline.get("team_id")):
        raise PipelineNotFound("parent issue not found")

    if await gw.get_active_run_for_parent(parent_issue_id):
        raise PipelineConflict("a pipeline run is already active on this issue")

    try:
        run = await gw.create_run(
            pipeline_id=int(pipeline_id),
            parent_issue_id=int(parent_issue_id),
            started_by_user_id=str(user_id),
        )
    except Exception as exc:  # noqa: BLE001
        # The partial-unique index (one running run per parent) is the real
        # guard — a race that slipped past the check above lands here.
        if "unique" in repr(exc).lower() or "duplicate" in repr(exc).lower():
            raise PipelineConflict("a pipeline run is already active on this issue")
        raise

    await _create_and_dispatch_step_child(
        gw,
        run=run,
        pipeline=pipeline,
        step=steps[0],
        parent=parent,
        prev_output=None,
    )
    # Re-read so current_step / status reflect any concurrent advance.
    return await gw.get_run(int(run["id"])) or run


async def _create_and_dispatch_step_child(
    gw: RelayGateway,
    *,
    run: Dict[str, Any],
    pipeline: Dict[str, Any],
    step: Dict[str, Any],
    parent: Dict[str, Any],
    prev_output: Optional[str],
) -> Optional[Dict[str, Any]]:
    """Create the child issue for ``step`` and dispatch it. Idempotent: if an
    issue already carries this step's pipeline origin, return it without a
    second create (belt to the CAS advance)."""
    run_id = run["id"]
    step_order = int(step["step_order"])
    origin_id = build_origin_id(run_id, step_order)

    existing = await gw.list_by_origin(ORIGIN_KIND, origin_id)
    if existing:
        return existing[0]

    variables = _template_vars(
        parent=parent,
        step_order=step_order,
        pipeline_name=pipeline.get("name") or "",
        prev_output=prev_output,
    )
    title = render_template(step["title_template"], variables).strip()[:500] or (
        f"{pipeline.get('name') or 'Pipeline'} — step {step_order}"
    )
    description = render_template(step["prompt_template"], variables)

    payload: Dict[str, Any] = {
        "title": title,
        "description": description,
        "status": "todo",
        "assignee_agent_id": str(step["agent_id"]),
        "parent_id": int(parent["id"]),
        "origin_kind": ORIGIN_KIND,
        "origin_id": origin_id,
        # origin_fingerprint carries the same key for traceability; the run's
        # current_step CAS is the actual idempotency gate.
        "origin_fingerprint": origin_id,
        "created_by_user_id": run.get("started_by_user_id"),
    }
    if parent.get("team_id") is not None:
        payload["team_id"] = int(parent["team_id"])
    if parent.get("project_id") is not None:
        payload["project_id"] = int(parent["project_id"])

    child = await gw.create_issue(payload)
    await gw.dispatch_issue(int(child["id"]))
    return child


# ── advance hook (fired from the two seams) ───────────────────────────────


async def on_pipeline_child_terminal(
    child_id: int,
    prev_status: Optional[str],
    new_status: str,
    *,
    gateway: Optional[RelayGateway] = None,
) -> Dict[str, Any]:
    """Evaluate a pipeline advance for one child's status transition.
    Best-effort: always returns a result dict, never raises (a raise here would
    abort the child's own status flow — same contract as the barrier)."""
    gw = gateway or RelayGateway()
    try:
        return await _evaluate_advance(child_id, prev_status, new_status, gw)
    except Exception as exc:  # noqa: BLE001 — the child transition is primary
        logger.warning(
            f"[pipeline_relay] advance failed for child {child_id} "
            f"({prev_status}→{new_status}): {exc!r}"
        )
        return {"fired": False, "reason": "error", "error": repr(exc)}


async def _evaluate_advance(
    child_id: int,
    prev_status: Optional[str],
    new_status: str,
    gw: RelayGateway,
) -> Dict[str, Any]:
    # Gate 1: real non-terminal → terminal edge only.
    if not is_terminal(new_status):
        return {"fired": False, "reason": "new_not_terminal"}
    if is_terminal(prev_status):
        return {"fired": False, "reason": "prev_already_terminal"}

    # Gate 2: the child must be a pipeline-step issue.
    child = await gw.get_issue(child_id)
    if not child:
        return {"fired": False, "reason": "child_missing"}
    if child.get("origin_kind") != ORIGIN_KIND:
        return {"fired": False, "reason": "not_pipeline_child"}
    parsed = parse_origin_id(child.get("origin_id"))
    if not parsed:
        return {"fired": False, "reason": "bad_origin_id"}
    run_id, step_order = parsed

    # Gate 3: the run must still be running, and this child must be its current
    # step (a stale edge from an already-advanced step is ignored).
    run = await gw.get_run(run_id)
    if not run:
        return {"fired": False, "reason": "run_missing"}
    if run.get("status") != "running":
        return {"fired": False, "reason": "run_not_running"}
    if int(run.get("current_step")) != step_order:
        return {"fired": False, "reason": "stale_step_edge"}

    pipeline = await gw.get_pipeline(int(run["pipeline_id"]))
    if not pipeline:
        return {"fired": False, "reason": "pipeline_missing"}
    steps = sorted(pipeline.get("steps") or [], key=lambda s: int(s["step_order"]))
    total = len(steps)
    parent = await gw.get_issue(int(run["parent_issue_id"]))
    if not parent:
        return {"fired": False, "reason": "parent_missing"}

    agent_name = await gw.agent_name(child.get("assignee_agent_id"))

    # Cancelled child → halt the whole relay (no further steps).
    if new_status == "cancelled":
        halted = await gw.halt_run(
            run_id, reason=f"Step {step_order} ({agent_name}) was cancelled"
        )
        if not halted:
            return {"fired": False, "reason": "already_terminal"}
        await gw.post_parent_message(
            parent,
            f"Pipeline halted — step {step_order} ({agent_name}) was cancelled.",
            key=build_origin_id(run_id, step_order),
        )
        return {"fired": True, "reason": "halted", "run_id": str(run_id)}

    # Done child → advance or complete.
    if step_order < total:
        # W3c budget breaker: before spinning up the next (paid) pipeline step,
        # gate on the parent's team budget. Over budget → halt the run with a
        # visible reason so it's stopped, not silently stuck. Pipeline children
        # propagate the parent's team_id, so no owner→team resolution is needed.
        from app.services.ai_usage import is_team_over_budget

        if await is_team_over_budget(parent.get("team_id")):
            halted = await gw.halt_run(
                run_id, reason="Budget exceeded — monthly AI budget reached"
            )
            if not halted:
                return {"fired": False, "reason": "already_terminal"}
            await gw.post_parent_message(
                parent,
                (
                    f"Pipeline halted at step {step_order + 1} — the team's "
                    "monthly AI budget has been reached."
                ),
                key=build_origin_id(run_id, step_order),
            )
            return {"fired": True, "reason": "halted_budget", "run_id": str(run_id)}

        next_step = steps[step_order]  # steps is 0-indexed; step_order is 1-based
        advanced = await gw.advance_run_step(
            run_id, from_step=step_order, to_step=step_order + 1
        )
        if not advanced:
            # Another observer already advanced this edge (CAS loser).
            return {"fired": False, "reason": "already_advanced"}

        prev_output = await gw.last_substantive_message(child)
        await _create_and_dispatch_step_child(
            gw,
            run=run,
            pipeline=pipeline,
            step=next_step,
            parent=parent,
            prev_output=prev_output,
        )
        next_agent = await gw.agent_name(next_step.get("agent_id"))
        await gw.post_parent_message(
            parent,
            (
                f"Step {step_order} ({agent_name}) done → handing off to "
                f"step {step_order + 1} ({next_agent})."
            ),
            key=build_origin_id(run_id, step_order + 1),
        )
        return {
            "fired": True,
            "reason": "advanced",
            "run_id": str(run_id),
            "next_step": step_order + 1,
        }

    # Last step done → complete the run.
    completed = await gw.complete_run(run_id, from_step=step_order)
    if not completed:
        return {"fired": False, "reason": "already_terminal"}
    await gw.post_parent_message(
        parent,
        (
            f"Pipeline '{pipeline.get('name') or 'pipeline'}' complete — "
            f"all {total} steps finished."
        ),
        key=build_origin_id(run_id, step_order),
    )
    return {"fired": True, "reason": "completed", "run_id": str(run_id)}


__all__ = [
    "RelayGateway",
    "PipelineRelayError",
    "PipelineNotFound",
    "PipelineConflict",
    "PipelineValidationError",
    "start_pipeline_run",
    "on_pipeline_child_terminal",
    "build_origin_id",
    "parse_origin_id",
    "render_template",
    "is_terminal",
    "TERMINAL_STATUSES",
    "ORIGIN_KIND",
    "PREV_OUTPUT_CAP",
]
