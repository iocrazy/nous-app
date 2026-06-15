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

M4a additions (data piping + result persistence):
  - DATA PIPING: _load_canvas_connections loads connections_json once and
    builds an incoming-wires map (target_id → wires).  Before each runnable
    node runs, _build_effective_data folds upstream outputs into its data
    (connected input overrides stored widget — ComfyUI semantics, mirroring
    dataPiping.ts faithfully).
  - PASSIVE / TRANSFORM NODES: passive source nodes (prompt / text / image /
    video / output / note / preview / group) are recorded so downstream nodes
    can pipe from them; they are NOT dispatched to run_canvas_node_step.
    text_join computes its effective data and records it so downstream llm /
    image_gen / video_gen nodes can read the joined text.
  - RESULT PERSISTENCE: after each runnable node succeeds, its run_result is
    written back to canvas nodes_json via persist_node_result_step (best-effort,
    incremental — Phase 6a realtime then delivers the update to open tabs).

Testability: the DBOS workflow function is a thin wrapper that injects
DBOS.workflow_id into ``_run_graph``, which carries all the logic.
Tests call ``_run_graph`` directly with mocked step functions.

NOTE: all steps are async (no run_async bridge) because CanvasRunService
is fully async-native (no sync third-party library involved). This keeps
the file off the §2.4b run_async ratchet allowlist (test_no_new_run_async_bridges).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from dbos import DBOS
from loguru import logger

# ---------------------------------------------------------------------------
# Node-type category sets — mirrors classicDispatch.ts
# ---------------------------------------------------------------------------

# Literal / sink / annotation node types: they hold or display data, they do
# NOT dispatch to a provider.  In the cascade they are pass-through (recorded
# so downstream nodes can pipe from source types; sinks record too but emit
# nothing useful).
_PASSIVE_NODE_TYPES = frozenset(
    {
        "image",
        "prompt",
        "text",
        "video",
        "output",
        "note",
        "preview",
        "group",
    }
)

# W3 transform node types: client-side data transformations that produce an
# output without a backend call.  text_join concatenates two text inputs.
_TRANSFORM_NODE_TYPES = frozenset({"text_join"})

# Node types that bill per-generation credits — NOT retried by DBOS to
# avoid charging users twice for the same generation.
_GEN_NODE_TYPES = frozenset({"image_gen", "video_gen"})

# Key fallback order used to resolve a node's run "body" from its data.
_NODE_BODY_KEYS = ("body", "prompt", "text", "content")


# ---------------------------------------------------------------------------
# Data piping helpers — faithful Python mirrors of dataPiping.ts
# ---------------------------------------------------------------------------


def _as_piped_string(value: Any) -> Optional[str]:
    """Return value iff it is a str, else None.

    Mirrors ``asPipedString`` in dataPiping.ts: output values that are not
    strings do not pipe.
    """
    return value if isinstance(value, str) else None


def _node_output_value(
    node_type: Optional[str],
    output_handle_id: Optional[str],
    data: Dict[str, Any],
    run_result: Optional[Dict[str, Any]],
) -> Optional[str]:
    """What an upstream node emits on a given output handle.

    Faithful Python mirror of ``nodeOutputValue`` in dataPiping.ts:

    Passive source types read from ``data``:
      - prompt    ``prompt-out``  → data.prompt
      - text      ``text-out``    → data.text
      - image     ``image-out``   → data.image_url or data.imageUrl
      - video     ``video-out``   → data.video_url
      - text_join ``text-out``    → join(data.text_a, sep, data.text_b)
        (cascade records EFFECTIVE data for text_join so piped values from
        upstream are already folded in by the time this is called)

    Runnable types read from ``run_result``:
      - image_gen ``image-out`` → run_result.image_url
      - llm       ``text-out``  → run_result.text
      - comfy     ``image-out`` → run_result.image_url
      - comfy     ``text-out``  → run_result.text
      - video_gen ``video-out`` → run_result.video_url
    """
    if not node_type or not output_handle_id:
        return None
    rr: Dict[str, Any] = run_result or {}

    if node_type == "prompt":
        return (
            _as_piped_string(data.get("prompt"))
            if output_handle_id == "prompt-out"
            else None
        )
    if node_type == "text":
        return (
            _as_piped_string(data.get("text"))
            if output_handle_id == "text-out"
            else None
        )
    if node_type == "image":
        if output_handle_id == "image-out":
            return _as_piped_string(data.get("image_url") or data.get("imageUrl"))
        return None
    if node_type == "video":
        return (
            _as_piped_string(data.get("video_url"))
            if output_handle_id == "video-out"
            else None
        )
    if node_type == "text_join":
        if output_handle_id != "text-out":
            return None
        sep = data.get("separator")
        sep = sep if isinstance(sep, str) else " "
        parts: List[str] = [
            p
            for p in [data.get("text_a"), data.get("text_b")]
            if isinstance(p, str) and p
        ]
        return sep.join(parts) if parts else None
    if node_type == "image_gen":
        return (
            _as_piped_string(rr.get("image_url"))
            if output_handle_id == "image-out"
            else None
        )
    if node_type == "llm":
        return (
            _as_piped_string(rr.get("text")) if output_handle_id == "text-out" else None
        )
    if node_type == "comfy":
        if output_handle_id == "image-out":
            return _as_piped_string(rr.get("image_url"))
        if output_handle_id == "text-out":
            return _as_piped_string(rr.get("text"))
        return None
    if node_type == "video_gen":
        return (
            _as_piped_string(rr.get("video_url"))
            if output_handle_id == "video-out"
            else None
        )
    return None


def _input_param_key(
    node_type: Optional[str],
    input_handle_id: Optional[str],
) -> Optional[str]:
    """Which data key on the downstream node a piped value fills.

    Faithful Python mirror of ``inputParamKey`` in dataPiping.ts.
    Returns None when the (type, handle) pair carries no run param.

    Key mapping:
      - image_gen: prompt-in  → prompt,             image-in → reference_image_url
      - video_gen: image-in   → source_image_url,   prompt-in → prompt
      - llm:       prompt-in  → prompt, text-in → prompt, image-in → reference_image_url
      - comfy:     prompt-in  → prompt,             image-in → reference_image_url
      - text_join: text-a-in → text_a,              text-b-in → text_b
    """
    if not node_type or not input_handle_id:
        return None

    if node_type == "image_gen":
        if input_handle_id == "prompt-in":
            return "prompt"
        if input_handle_id == "image-in":
            return "reference_image_url"
        return None
    if node_type == "video_gen":
        if input_handle_id == "image-in":
            return "source_image_url"
        if input_handle_id == "prompt-in":
            return "prompt"
        return None
    if node_type == "llm":
        if input_handle_id in ("prompt-in", "text-in"):
            return "prompt"
        if input_handle_id == "image-in":
            return "reference_image_url"
        return None
    if node_type == "comfy":
        if input_handle_id == "prompt-in":
            return "prompt"
        if input_handle_id == "image-in":
            return "reference_image_url"
        return None
    if node_type == "text_join":
        if input_handle_id == "text-a-in":
            return "text_a"
        if input_handle_id == "text-b-in":
            return "text_b"
        return None
    return None


def _build_effective_data(
    node_type: Optional[str],
    own_data: Dict[str, Any],
    incoming_wires: List[Dict[str, Any]],
    recorded_outputs: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """Build the effective data for a node about to run.

    Faithful Python mirror of ``buildEffectiveData`` in dataPiping.ts:
    a shallow copy of ``own_data`` with each resolved incoming wire's value
    written onto the mapped param key.  A connected input OVERRIDES the
    node's stored widget value (ComfyUI semantics).  When two wires target
    the same input the last one in ``incoming_wires`` order wins (deterministic
    by edge order, same as the TS implementation).

    ``own_data`` is never mutated — a new dict is returned.
    """
    effective: Dict[str, Any] = dict(own_data)
    for wire in incoming_wires:
        src = recorded_outputs.get(wire.get("sourceId") or "")
        if src is None:
            continue
        value = _node_output_value(
            src.get("nodeType"),
            wire.get("sourceHandle"),
            src.get("data") or {},
            src.get("runResult"),
        )
        if value is None:
            continue
        key = _input_param_key(node_type, wire.get("targetHandle"))
        if not key:
            continue
        effective[key] = value  # piped value overrides own widget value
    return effective


def _build_incoming_wires(
    connections: List[Any],
) -> Dict[str, List[Dict[str, Any]]]:
    """Index connections by target node id.

    Returns target_id → list of incoming wire dicts with keys:
      sourceId (str), sourceHandle (str | None), targetHandle (str | None).
    Preserves edge order so multi-wire-into-one-input is deterministic (last
    wins), matching cascade.ts ``buildIncomingWires``.
    """
    incoming: Dict[str, List[Dict[str, Any]]] = {}
    for edge in connections:
        if not isinstance(edge, dict):
            continue
        target = edge.get("target")
        source = edge.get("source")
        if not isinstance(target, str) or not isinstance(source, str):
            continue
        sh = edge.get("sourceHandle")
        th = edge.get("targetHandle")
        wire: Dict[str, Any] = {
            "sourceId": source,
            "sourceHandle": sh if isinstance(sh, str) else None,
            "targetHandle": th if isinstance(th, str) else None,
        }
        incoming.setdefault(target, []).append(wire)
    return incoming


# ---------------------------------------------------------------------------
# Canvas loader helpers
# ---------------------------------------------------------------------------


def _node_body(data: Dict[str, Any]) -> str:
    """Resolve a node's run body from its data (first present key).

    Local fallback resolver mirroring ``_first_str`` in canvas_run_service —
    extracts the first non-empty string under _NODE_BODY_KEYS, defaulting to
    "" when none are present.  Applied to EFFECTIVE data so a piped prompt
    reaches the llm / comfy body seam.
    """
    for key in _NODE_BODY_KEYS:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


async def _load_canvas_nodes(canvas_id: str) -> Dict[str, Dict[str, Any]]:
    """Load the canvas once and return an id -> node lookup.

    Deliberately a plain async function (NOT a @DBOS.step): it is a pure read
    used to drive the orchestration loop, so keeping it off the DBOS step
    ledger avoids recording a redundant step result, and keeps it trivially
    monkeypatchable in tests. Raises if the canvas row is missing — a canvas
    that cannot be found is a real error, not a silent empty run.
    """
    from app.services.canvas.canvas_service import CanvasService

    row = await CanvasService().get(canvas_id)
    if row is None:
        raise RuntimeError(f"canvas {canvas_id!r} not found")

    nodes_by_id: Dict[str, Dict[str, Any]] = {}
    for node in row.get("nodes_json") or []:
        node_id = node.get("id") if isinstance(node, dict) else None
        if isinstance(node_id, str):
            nodes_by_id[node_id] = node
    return nodes_by_id


async def _load_canvas_connections(
    canvas_id: str,
) -> Dict[str, List[Dict[str, Any]]]:
    """Load connections_json and return the incoming-wires map.

    Returns target_id → list-of-wires (see _build_incoming_wires).
    Deliberately a plain async function (NOT a @DBOS.step) — same reasoning
    as _load_canvas_nodes.

    Returns an empty dict on any error so the run degrades gracefully
    (piping disabled; nodes run with only their own stored data).
    """
    from app.services.canvas.canvas_service import CanvasService

    try:
        row = await CanvasService().get(canvas_id)
        if row is None:
            return {}
        connections = row.get("connections_json")
        if not isinstance(connections, list):
            return {}
        return _build_incoming_wires(connections)
    except Exception as exc:
        logger.warning(
            f"[canvas_graph] _load_canvas_connections {canvas_id!r}: {exc!r}"
        )
        return {}


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
# Result persistence step (M4a)
# ---------------------------------------------------------------------------


@DBOS.step()
async def persist_node_result_step(
    canvas_id: str,
    node_id: str,
    run_result: Dict[str, Any],
    run_status: str,
) -> None:
    """Persist a node's run_result back into the canvas nodes_json (best-effort).

    M4a: writes {run_result, run_status} into node.data for the given node via
    CanvasRepository.patch_node_run_results (unconditional read-modify-write,
    no optimistic lock).  Non-fatal: if persistence fails the workflow continues
    — node results are in-memory for piping purposes regardless.

    The frontend reads node.data.run_result and node.data.run_status to
    display inline thumbnails / text previews.  Phase 6a realtime delivers the
    persisted row to open canvas tabs via applyRemoteUpdate.
    """
    from app.repositories.canvas_repository import CanvasRepository

    try:
        repo = CanvasRepository()
        await repo.patch_node_run_results(
            canvas_id,
            {node_id: {"run_result": run_result, "run_status": run_status}},
        )
    except Exception as exc:
        logger.warning(
            f"[canvas_graph.persist_node_result] {canvas_id}/{node_id}: {exc!r}"
        )


# ---------------------------------------------------------------------------
# Node-runner steps (async — awaited from _run_graph)
# ---------------------------------------------------------------------------


@DBOS.step(retries_allowed=True, max_attempts=3)
async def run_canvas_node_step(
    canvas_id: str,
    node_id: str,
    node_type: str,
    node_data: Dict[str, Any],
    body: str,
    agent_id: Optional[str],
    project_id: Optional[str],
) -> Dict[str, Any]:
    """Run one non-gen canvas node (retryable, max 3 attempts).

    Delegates to CanvasRunService.run_classic_node — the same service used
    by the synchronous ``POST /canvases/runs/classic-node`` route. Raises
    when the service returns ok=False so DBOS surfaces this as a step error
    and can retry.

    M4a: ``node_data`` is the EFFECTIVE data (after piping from upstreams),
    not the raw persisted data.  The run service reads prompt / reference_image_url /
    source_image_url etc. from this dict, so upstream outputs flow through
    transparently.
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
    node_data: Dict[str, Any],
    body: str,
    agent_id: Optional[str],
    project_id: Optional[str],
) -> Dict[str, Any]:
    """Run one gen canvas node (image_gen / video_gen) — NOT retried.

    Credit-billing generation steps must not be retried by DBOS to avoid
    charging the user twice for the same image/video. If the step fails,
    the exception propagates to the workflow which decides whether to
    continue or raise (based on continue_on_failure).

    M4a: ``node_data`` is the EFFECTIVE data (after piping from upstreams).
    For video_gen this means source_image_url may arrive from an upstream
    image_gen node rather than the stored widget value.
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
    node_order: List[str],
    user_id: str,
    continue_on_failure: bool,
) -> Dict[str, Any]:
    """Orchestrate a full-graph canvas run with data piping + result persistence.

    ``workflow_id`` is injected by the DBOS workflow wrapper so this
    function is testable without a live DBOS context (tests patch the
    step functions and call this directly).

    路线 C 第 4 条: failures RAISE — never return {"status": "failed"}.
    Returning a dict would make DBOS record the workflow as SUCCESS;
    the mirror_dbos_lifecycle_to_tracking trigger would then stamp
    task_tracking.phase=completed even though nodes failed.

    M4a DATA PIPING contract (mirrors cascade.ts / dataPiping.ts):
      Passive nodes (prompt / text / image / video / output / note / preview /
      group) are recorded in ``recorded_outputs`` with their own stored data
      and no run_result.  Transform nodes (text_join) compute effective data
      (folding in piped inputs) and record that.  Runnable nodes (llm / comfy /
      image_gen / video_gen) get effective data built from upstream recorded
      outputs via _build_effective_data, then run with that effective data.

    M4a RESULT PERSISTENCE contract:
      After each runnable node succeeds, its run_result is persisted back to
      the canvas via persist_node_result_step (incremental — one write per
      node, so Phase 6a realtime delivers progress to open tabs).
      Passive / transform nodes do NOT trigger a persist call (they have no
      run_result to write).
    """
    # Mark parent task_tracking row processing immediately so TaskCenter
    # shows the run as active rather than stuck at queued.
    await mark_graph_processing_step(workflow_id)

    # Load nodes AND connections once.  nodes_by_id drives the run loop;
    # incoming_wires_map drives data piping.
    nodes_by_id = await _load_canvas_nodes(canvas_id)
    incoming_wires_map = await _load_canvas_connections(canvas_id)

    # recorded_outputs: node_id → {nodeType, data, runResult}
    # Populated in topo order so each node can pipe from its upstreams.
    recorded_outputs: Dict[str, Dict[str, Any]] = {}

    failed_nodes: List[str] = []

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

        # Resolve REAL node data from the persisted canvas. A node id in
        # node_order that is absent from the canvas is a real error — treat
        # it the same as a node run failure (mark subtask failed, then
        # continue or raise per continue_on_failure).
        node = nodes_by_id.get(node_id)
        if node is None:
            exc_msg = f"node {node_id!r} not found in canvas {canvas_id!r}"
            logger.warning(f"[canvas_graph] {exc_msg} (position={i})")
            await mark_node_failed_step(node_wf_id, exc_msg)
            if continue_on_failure:
                failed_nodes.append(node_id)
                continue
            raise RuntimeError(exc_msg)

        node_type: Optional[str] = node.get("type")
        node_data: Dict[str, Any] = node.get("data") or {}

        # M4a: passive source nodes — record stored data for downstream piping,
        # skip execution.  Sinks (output / note / preview / group) also record
        # but _node_output_value returns None for them (they emit nothing).
        if node_type in _PASSIVE_NODE_TYPES:
            recorded_outputs[node_id] = {
                "nodeType": node_type,
                "data": node_data,
                "runResult": None,
            }
            await mark_node_complete_step(node_wf_id)
            continue

        # M4a: transform nodes (text_join) — compute effective data by folding
        # in any wired upstream values, then record it.  Downstream nodes call
        # _node_output_value("text_join", "text-out", effective_data, None) to
        # get the concatenated text — effective_data must contain text_a/text_b.
        if node_type in _TRANSFORM_NODE_TYPES:
            incoming_wires = incoming_wires_map.get(node_id, [])
            effective_data = _build_effective_data(
                node_type, node_data, incoming_wires, recorded_outputs
            )
            recorded_outputs[node_id] = {
                "nodeType": node_type,
                "data": effective_data,
                "runResult": None,
            }
            await mark_node_complete_step(node_wf_id)
            continue

        # Runnable node (llm / comfy / image_gen / video_gen):
        # 1. Build effective data by folding upstream outputs.
        # 2. Run the node step with effective data.
        # 3. Persist run_result + record for downstream piping.
        incoming_wires = incoming_wires_map.get(node_id, [])
        effective_data = _build_effective_data(
            node_type, node_data, incoming_wires, recorded_outputs
        )
        body = _node_body(effective_data)
        agent_id: Optional[str] = None
        project_id: Optional[str] = None

        try:
            if node_type in _GEN_NODE_TYPES:
                raw_result = await run_gen_canvas_node_step(
                    canvas_id=canvas_id,
                    node_id=node_id,
                    node_type=node_type,
                    node_data=effective_data,
                    body=body,
                    agent_id=agent_id,
                    project_id=project_id,
                )
            else:
                raw_result = await run_canvas_node_step(
                    canvas_id=canvas_id,
                    node_id=node_id,
                    node_type=node_type,
                    node_data=effective_data,
                    body=body,
                    agent_id=agent_id,
                    project_id=project_id,
                )

            # M4a: extract run_result from the step return value.
            # run_canvas_node_step returns result.model_dump() which is the
            # CanvasPromptRunResult dict: {ok, text, error, result}.
            # Mirror what cascade.ts does (lines 364-371):
            #   use result.result if present (image_gen → image_url,
            #   video_gen → video_url), else fold text into {"text": text}
            #   so llm output is pipeable downstream.
            raw: Dict[str, Any] = raw_result if isinstance(raw_result, dict) else {}
            step_result: Optional[Dict[str, Any]] = raw.get("result")
            step_text: str = raw.get("text") or ""
            run_result: Dict[str, Any] = step_result or (
                {"text": step_text} if step_text else {}
            )

            # Record for downstream piping.
            recorded_outputs[node_id] = {
                "nodeType": node_type,
                "data": effective_data,
                "runResult": run_result,
            }

            # M4a: persist run_result incrementally (best-effort).
            # The frontend reads node.data.run_result and node.data.run_status;
            # Phase 6a realtime delivers the updated row to open canvas tabs.
            await persist_node_result_step(
                canvas_id=canvas_id,
                node_id=node_id,
                run_result=run_result,
                run_status="succeeded",
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
    node_order: List[str],
    user_id: str,
    *,
    continue_on_failure: bool = False,
) -> Dict[str, Any]:
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
