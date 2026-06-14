"""canvas_graph DBOS workflow — Phase 6d full-graph orchestration.

Runs a topo-sorted sequence of canvas nodes as a single DBOS workflow.
Each node executes as an async @DBOS.step that delegates to the existing
CanvasRunService; per-node status is tracked in task_tracking subtask
rows via the manager API.

路线 C guardrails (CLAUDE.md 任务系统架构纪律):
  1. trigger owns phase/status/progress/started_at/completed_at/error_msg
     for the parent workflow row — business code only calls manager API.
  2. Per-node state goes into task_tracking subtask rows + metadata.
  3. Failures RAISE — never return {"status": "failed"} (DBOS would treat
     that as SUCCESS and the trigger would mirror phase=completed).
  4. Gen steps (image_gen / video_gen) have retries_allowed=False to avoid
     double-charging credits on a DBOS retry.

Testability: the DBOS workflow function is a thin wrapper that injects
DBOS.workflow_id into ``_run_graph``, which carries all the logic.
Tests call ``_run_graph`` directly with mocked step functions.

NOTE: all steps are async (no run_async bridge) because CanvasRunService
is fully async-native (no sync third-party library involved). This keeps
the file off the §2.4b run_async ratchet allowlist (test_no_new_run_async_bridges).
"""

from __future__ import annotations

from typing import Any, Optional

from dbos import DBOS
from loguru import logger

# Node types that bill per-generation credits — NOT retried by DBOS to
# avoid charging users twice for the same generation.
_GEN_NODE_TYPES = frozenset({"image_gen", "video_gen"})


# ---------------------------------------------------------------------------
# Manager-lifecycle steps (async — called with await from workflow / _run_graph)
# ---------------------------------------------------------------------------


@DBOS.step()
async def mark_graph_processing_step(workflow_id: str) -> None:
    """Best-effort: mark parent task_tracking row as processing.

    Without this, the mirror_dbos_lifecycle_to_tracking trigger only updates
    status/phase on terminal transitions, so the row stays phase=queued
    while the workflow is actively running (cosmetically wrong in TaskCenter).
    Hiccups here are non-fatal — workflow continues regardless.
    """
    from app.services.infra.unified_task_manager import get_task_manager

    try:
        await get_task_manager().start(workflow_id, task_type="canvas_graph_run")
    except Exception as exc:
        logger.warning(f"[canvas_graph.mark_processing] {workflow_id}: {exc!r}")


@DBOS.step()
async def create_node_subtask_step(
    *,
    parent_wf_id: str,
    node_wf_id: str,
    node_id: str,
    user_id: str,
    canvas_id: str,
    position: int,
) -> None:
    """Create a task_tracking row for one node (best-effort).

    M3: per-node status is tracked via subtask rows. The subtask is not a
    real DBOS workflow, so its phase is managed via manager API (not trigger).
    node_wf_id is synthetic: ``{parent_wf_id}-node-{position}``.
    """
    from app.services.infra.unified_task_manager import get_task_manager

    try:
        await get_task_manager().create(
            user_id=user_id,
            task_type="canvas_node_run",
            title=f"Node {node_id[:20]}",
            dbos_workflow_id=node_wf_id,
            metadata={
                "canvas_id": canvas_id,
                "node_id": node_id,
                "position": position,
                "parent_wf_id": parent_wf_id,
            },
        )
    except Exception as exc:
        logger.warning(f"[canvas_graph.create_node_subtask] {node_wf_id}: {exc!r}")


@DBOS.step()
async def mark_node_complete_step(node_wf_id: str) -> None:
    """Mark a per-node subtask row as completed (best-effort)."""
    from app.services.infra.unified_task_manager import get_task_manager

    try:
        await get_task_manager().complete(node_wf_id)
    except Exception as exc:
        logger.warning(f"[canvas_graph.mark_node_complete] {node_wf_id}: {exc!r}")


@DBOS.step()
async def mark_node_failed_step(node_wf_id: str, error_msg: str) -> None:
    """Mark a per-node subtask row as failed (best-effort)."""
    from app.services.infra.unified_task_manager import get_task_manager

    try:
        await get_task_manager().fail(node_wf_id, error=error_msg)
    except Exception as exc:
        logger.warning(f"[canvas_graph.mark_node_failed] {node_wf_id}: {exc!r}")


# ---------------------------------------------------------------------------
# Node-runner steps (async — awaited from _run_graph)
# ---------------------------------------------------------------------------


@DBOS.step(retries_allowed=True, max_attempts=3)
async def run_canvas_node_step(
    canvas_id: str,
    node_id: str,
    node_type: str,
    node_data: dict,
    body: str,
    agent_id: Optional[str],
    project_id: Optional[str],
) -> dict[str, Any]:
    """Run one non-gen canvas node (retryable, max 3 attempts).

    Delegates to CanvasRunService.run_classic_node — the same service used
    by the synchronous ``POST /canvases/runs/classic-node`` route. Raises
    when the service returns ok=False so DBOS surfaces this as a step error
    and can retry.
    """
    from app.services.canvas.canvas_run_service import CanvasRunService

    svc = CanvasRunService()
    result = await svc.run_classic_node(
        node_type=node_type,
        node={"data": node_data},
        body=body,
        agent_id=agent_id,
        node_id=node_id,
        project_id=project_id,
    )
    if not result.ok:
        raise RuntimeError(f"node {node_id!r} failed: {result.error}")
    return result.model_dump()


@DBOS.step(retries_allowed=False)
async def run_gen_canvas_node_step(
    canvas_id: str,
    node_id: str,
    node_type: str,
    node_data: dict,
    body: str,
    agent_id: Optional[str],
    project_id: Optional[str],
) -> dict[str, Any]:
    """Run one gen canvas node (image_gen / video_gen) — NOT retried.

    Credit-billing generation steps must not be retried by DBOS to avoid
    charging the user twice for the same image/video. If the step fails,
    the exception propagates to the workflow which decides whether to
    continue or raise (based on continue_on_failure).
    """
    from app.services.canvas.canvas_run_service import CanvasRunService

    svc = CanvasRunService()
    result = await svc.run_classic_node(
        node_type=node_type,
        node={"data": node_data},
        body=body,
        agent_id=agent_id,
        node_id=node_id,
        project_id=project_id,
    )
    if not result.ok:
        raise RuntimeError(f"gen node {node_id!r} failed: {result.error}")
    return result.model_dump()


# ---------------------------------------------------------------------------
# Core orchestration logic (extracted for testability)
# ---------------------------------------------------------------------------


async def _run_graph(
    workflow_id: str,
    canvas_id: str,
    node_order: list[str],
    user_id: str,
    continue_on_failure: bool,
) -> dict[str, Any]:
    """Orchestrate a full-graph canvas run.

    ``workflow_id`` is injected by the DBOS workflow wrapper so this
    function is testable without a live DBOS context (tests patch the
    step functions and call this directly).

    路线 C 第 4 条: failures RAISE — never return {"status": "failed"}.
    Returning a dict would make DBOS record the workflow as SUCCESS;
    the mirror_dbos_lifecycle_to_tracking trigger would then stamp
    task_tracking.phase=completed even though nodes failed.
    """
    # Mark parent task_tracking row processing immediately so TaskCenter
    # shows the run as active rather than stuck at queued.
    await mark_graph_processing_step(workflow_id)

    failed_nodes: list[str] = []

    for i, node_id in enumerate(node_order):
        # Synthetic node_wf_id — not a real DBOS workflow; managed via
        # manager API directly (create_node_subtask_step / mark_node_*).
        node_wf_id = f"{workflow_id}-node-{i}"

        # M3: create subtask row before running the node (best-effort).
        await create_node_subtask_step(
            parent_wf_id=workflow_id,
            node_wf_id=node_wf_id,
            node_id=node_id,
            user_id=user_id,
            canvas_id=canvas_id,
            position=i,
        )

        # v1: all nodes default to "llm" type — the route doesn't carry
        # per-node type yet. CanvasRunService.run_classic_node resolves
        # the actual provider internally. Gen node types (image_gen /
        # video_gen) will use run_gen_canvas_node_step in v2 when the
        # request carries a nodes dict with type info.
        node_type = "llm"
        node_data: dict = {}
        body = ""
        agent_id: Optional[str] = None
        project_id: Optional[str] = None

        try:
            if node_type in _GEN_NODE_TYPES:
                await run_gen_canvas_node_step(
                    canvas_id=canvas_id,
                    node_id=node_id,
                    node_type=node_type,
                    node_data=node_data,
                    body=body,
                    agent_id=agent_id,
                    project_id=project_id,
                )
            else:
                await run_canvas_node_step(
                    canvas_id=canvas_id,
                    node_id=node_id,
                    node_type=node_type,
                    node_data=node_data,
                    body=body,
                    agent_id=agent_id,
                    project_id=project_id,
                )

            # M3: mark this node's subtask complete (best-effort).
            await mark_node_complete_step(node_wf_id)

        except Exception as exc:
            logger.warning(
                f"[canvas_graph] node {node_id!r} (position={i}) failed: {exc!r}"
            )
            # M3: mark this node's subtask failed (best-effort).
            await mark_node_failed_step(node_wf_id, str(exc))

            if continue_on_failure:
                # Collect the failure; remaining nodes still run.
                failed_nodes.append(node_id)
                continue

            # Re-raise immediately (continue_on_failure=False):
            # DBOS marks the workflow FAILED and the
            # mirror_dbos_lifecycle_to_tracking trigger writes
            # phase=failed + error_msg to task_tracking.
            # NEVER return {"status": "failed"} — 路线 C 第 4 条.
            raise

    if failed_nodes:
        # continue_on_failure=True but some nodes failed: raise so the
        # parent task_tracking row reflects partial failure (not completed).
        raise RuntimeError(
            f"canvas graph run partial failure: {len(failed_nodes)} node(s) "
            f"failed: {failed_nodes}"
        )

    return {
        "status": "success",
        "canvas_id": canvas_id,
        "nodes_run": len(node_order),
        "failed_nodes": failed_nodes,
    }


# ---------------------------------------------------------------------------
# DBOS workflow entry-point
# ---------------------------------------------------------------------------


@DBOS.workflow()
async def canvas_graph_workflow(
    canvas_id: str,
    node_order: list[str],
    user_id: str,
    *,
    continue_on_failure: bool = False,
) -> dict[str, Any]:
    """Full-graph canvas run — DBOS workflow entry-point.

    Enqueued by ``POST /canvases/{id}/graph-runs`` via
    ``start_workflow_routed('canvas_graph_run', ...)``.

    Thin wrapper: injects DBOS.workflow_id into ``_run_graph`` which
    carries all orchestration logic and is testable without DBOS.
    """
    return await _run_graph(
        workflow_id=DBOS.workflow_id,
        canvas_id=canvas_id,
        node_order=node_order,
        user_id=user_id,
        continue_on_failure=continue_on_failure,
    )
