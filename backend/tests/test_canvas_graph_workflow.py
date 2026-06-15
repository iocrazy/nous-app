"""Tests for canvas_graph DBOS workflow — Phase 6d M2 + M3.

Coverage:
    Static 路线 C compliance (M2):
        - workflow never returns {"status":"failed"} dict
        - except path contains `raise`
        - no direct phase column PATCH

    Behavioral — run order + failure handling (M2):
        - 3-node chain runs nodes in the given topo order
        - empty node_order succeeds immediately
        - mid-chain failure RAISES (not returns dict) when continue_on_failure=False
        - stops immediately on first failure (continue_on_failure=False)
        - continue_on_failure=True: remaining nodes run despite mid-chain failure
        - continue_on_failure=True with all-success: returns success

    Per-node subtask rows (M3):
        - create_node_subtask_step called once per node
        - mark_node_complete_step called on success
        - mark_node_failed_step called on failure (and only that subtask)
        - failed node subtask does NOT call mark_node_complete_step

    id-match regression (M1):
        - manager.create dbos_workflow_id matches start_workflow_routed workflow_id
"""

from __future__ import annotations

import inspect
from typing import Any
from unittest.mock import AsyncMock, call

import pytest

from app.workflows import canvas_graph as canvas_graph_module
from app.workflows.canvas_graph import _run_graph

# ============================================================
# Helpers
# ============================================================


class _FakeNodeStep:
    """Async callable replacing run_canvas_node_step / run_gen_canvas_node_step.

    ``raise_on`` maps node_id → exception message; absent ids succeed.
    ``calls`` records (node_id, node_type) tuples in invocation order.
    ``records`` captures the full kwargs (node_type/node_data/body) so tests
    can assert the REAL node data was threaded through.
    """

    def __init__(self, raise_on: dict[str, str] | None = None) -> None:
        self.raise_on = raise_on or {}
        self.calls: list[tuple[str, str]] = []
        self.records: list[dict[str, Any]] = []

    async def __call__(
        self,
        canvas_id: str,
        node_id: str,
        node_type: str,
        node_data: dict,
        body: str,
        agent_id: Any,
        project_id: Any,
    ) -> dict:
        self.calls.append((node_id, node_type))
        self.records.append(
            {
                "node_id": node_id,
                "node_type": node_type,
                "node_data": node_data,
                "body": body,
            }
        )
        if node_id in self.raise_on:
            raise RuntimeError(self.raise_on[node_id])
        return {"ok": True, "text": f"ok-{node_id}", "error": None, "result": None}


class _DefaultCanvas:
    """id -> node lookup that synthesizes a default 'llm' node for ANY id.

    Used by legacy behavioral tests that don't care about a node's real
    type/data — they only exercise run order / failure handling. ``.get``
    therefore never returns None, so every id in node_order resolves.
    """

    def get(self, node_id: str) -> dict:
        return {"id": node_id, "type": "llm", "data": {}}


def _fake_canvas(
    node_order: list[str], types: dict[str, str] | None = None
) -> dict[str, dict]:
    """Build an explicit id -> node lookup from a node_order.

    Unlike _DefaultCanvas, a missing id resolves to None (real dict.get),
    which is what the node-not-found tests rely on.
    """
    types = types or {}
    return {
        nid: {
            "id": nid,
            "type": types.get(nid, "llm"),
            "data": {"prompt": f"prompt-{nid}"},
        }
        for nid in node_order
    }


def _patch_steps(
    monkeypatch,
    *,
    node_step: _FakeNodeStep | None = None,
    gen_step: _FakeNodeStep | None = None,
    canvas: Any | None = None,
):
    """Patch all step functions + canvas loader on canvas_graph_module.

    Returns (mark_proc, create_sub, mark_complete, mark_failed, node_runner).

    ``canvas`` is the id -> node lookup returned by the patched
    ``_load_canvas_nodes``; defaults to ``_DefaultCanvas()`` so legacy tests
    that don't set up a canvas still resolve every node as a plain 'llm'.
    When ``gen_step`` is omitted, the same runner backs both the plain and
    the gen step (sufficient for tests that don't distinguish routing).
    """
    mark_proc = AsyncMock()
    create_sub = AsyncMock()
    mark_complete = AsyncMock()
    mark_failed = AsyncMock()
    runner = node_step or _FakeNodeStep()
    gen_runner = gen_step or runner

    load_canvas = AsyncMock(
        return_value=canvas if canvas is not None else _DefaultCanvas()
    )
    # M4a: patch new helpers so existing tests don't hit a live DB or DBOS.
    # _load_canvas_connections returns {} → no piping in legacy tests (node data
    # flows through unchanged, so all node_data / body assertions still hold).
    # persist_node_result_step is a DBOS step; without patching, its decorator
    # would raise because DBOS is not initialised in the unit-test process.
    load_conns = AsyncMock(return_value={})
    persist_step = AsyncMock()

    monkeypatch.setattr(canvas_graph_module, "mark_graph_processing_step", mark_proc)
    monkeypatch.setattr(canvas_graph_module, "create_node_subtask_step", create_sub)
    monkeypatch.setattr(canvas_graph_module, "mark_node_complete_step", mark_complete)
    monkeypatch.setattr(canvas_graph_module, "mark_node_failed_step", mark_failed)
    monkeypatch.setattr(canvas_graph_module, "run_canvas_node_step", runner)
    monkeypatch.setattr(canvas_graph_module, "run_gen_canvas_node_step", gen_runner)
    monkeypatch.setattr(canvas_graph_module, "_load_canvas_nodes", load_canvas)
    monkeypatch.setattr(canvas_graph_module, "_load_canvas_connections", load_conns)
    monkeypatch.setattr(canvas_graph_module, "persist_node_result_step", persist_step)

    return mark_proc, create_sub, mark_complete, mark_failed, runner


_WF_ID = "wf-test-graph-001"
_CANVAS = "canvas-abc123"
_USER = "user-xyz"


# ============================================================
# Static 路线 C compliance checks (M2)
# ============================================================


class TestRouteCStaticCompliance:
    """Source-level 路线 C guards — mirror test_download_workflow_raise_on_error.py."""

    def test_canvas_graph_workflow_no_return_failed_dict(self):
        """canvas_graph_workflow must NEVER return {"status":"failed"} — 路线 C 第 4 条.

        Returning a failed dict makes DBOS record the workflow as SUCCESS;
        the mirror_dbos_lifecycle_to_tracking trigger then stamps
        task_tracking.phase=completed even though nodes actually failed.
        """
        source = inspect.getsource(canvas_graph_module.canvas_graph_workflow)
        forbidden = ['"status": "failed"', "'status': 'failed'"]
        for pat in forbidden:
            assert pat not in source, (
                f"canvas_graph_workflow must not short-circuit-return a failed "
                f"dict ({pat!r}). Use `raise` — CLAUDE.md 路线 C 第 4 条."
            )

    def test_run_graph_reraises_on_failure(self):
        """_run_graph must re-raise so DBOS surfaces the workflow as FAILED."""
        source = inspect.getsource(canvas_graph_module._run_graph)
        assert "raise" in source, (
            "_run_graph must contain `raise` in its failure path so DBOS "
            "marks the workflow FAILED and the trigger writes phase=failed."
        )

    def test_no_direct_phase_column_patch_in_steps(self):
        """workflow/step code must go through manager API, never raw-patch phase."""
        source = inspect.getsource(canvas_graph_module)
        # Detect accidental direct SQL phase patches (e.g. .update({"phase": ...}))
        assert '{"phase":' not in source, (
            "canvas_graph.py must not directly patch the phase column. "
            "Use manager.complete()/fail()/start() — 路线 C 第 2 条."
        )
        assert '"phase":' not in source.replace(
            '"phase": TaskPhase', "__placeholder__"
        ).replace(
            '"phase": task_phase', "__placeholder__"
        ), "Suspicious direct phase assignment detected — use manager API."

    def test_gen_node_step_retries_allowed_false(self):
        """run_gen_canvas_node_step must have retries_allowed=False to avoid
        double-charging credits on image_gen / video_gen re-runs."""
        source = inspect.getsource(canvas_graph_module.run_gen_canvas_node_step)
        assert "retries_allowed=False" in source, (
            "run_gen_canvas_node_step must set retries_allowed=False so DBOS "
            "does not retry a credit-billing generation step."
        )


# ============================================================
# Behavioral: run order + empty graph (M2)
# ============================================================


@pytest.mark.asyncio
class TestRunOrder:
    async def test_three_node_chain_runs_in_order(self, monkeypatch):
        """3-node graph: nodes run in the supplied topo order."""
        _, _, _, _, runner = _patch_steps(monkeypatch)

        result = await _run_graph(
            workflow_id=_WF_ID,
            canvas_id=_CANVAS,
            node_order=["n1", "n2", "n3"],
            user_id=_USER,
            continue_on_failure=False,
        )

        assert result["status"] == "success"
        assert result["nodes_run"] == 3
        assert [nid for nid, _ in runner.calls] == ["n1", "n2", "n3"]

    async def test_empty_node_order_succeeds(self, monkeypatch):
        """Empty node list: workflow exits immediately with success."""
        _patch_steps(monkeypatch)

        result = await _run_graph(
            workflow_id=_WF_ID,
            canvas_id=_CANVAS,
            node_order=[],
            user_id=_USER,
            continue_on_failure=False,
        )

        assert result["status"] == "success"
        assert result["nodes_run"] == 0
        assert result["failed_nodes"] == []

    async def test_mark_processing_called_once(self, monkeypatch):
        """mark_graph_processing_step called exactly once with the parent wf_id."""
        mark_proc, _, _, _, _ = _patch_steps(monkeypatch)

        await _run_graph(
            workflow_id=_WF_ID,
            canvas_id=_CANVAS,
            node_order=["n1"],
            user_id=_USER,
            continue_on_failure=False,
        )

        mark_proc.assert_called_once_with(_WF_ID)


# ============================================================
# Behavioral: failure → raises (M2)
# ============================================================


@pytest.mark.asyncio
class TestFailureRaises:
    async def test_mid_chain_failure_raises(self, monkeypatch):
        """路线 C 第 4 条: failure must RAISE, not return a dict.

        When node n2 of 3 fails and continue_on_failure=False, _run_graph
        must raise so DBOS surfaces the workflow as FAILED (which causes
        mirror_dbos_lifecycle_to_tracking to write phase=failed to
        task_tracking).
        """
        _patch_steps(monkeypatch, node_step=_FakeNodeStep(raise_on={"n2": "boom"}))

        with pytest.raises(RuntimeError):
            await _run_graph(
                workflow_id=_WF_ID,
                canvas_id=_CANVAS,
                node_order=["n1", "n2", "n3"],
                user_id=_USER,
                continue_on_failure=False,
            )

    async def test_stops_at_first_failure(self, monkeypatch):
        """With continue_on_failure=False, no nodes run after the failed one."""
        _, _, _, _, runner = _patch_steps(
            monkeypatch, node_step=_FakeNodeStep(raise_on={"n2": "boom"})
        )

        with pytest.raises(RuntimeError):
            await _run_graph(
                workflow_id=_WF_ID,
                canvas_id=_CANVAS,
                node_order=["n1", "n2", "n3"],
                user_id=_USER,
                continue_on_failure=False,
            )

        ran = [nid for nid, _ in runner.calls]
        # n1 succeeded, n2 raised, n3 must NOT have run
        assert ran == ["n1", "n2"], (
            f"Expected only n1+n2 to run but got: {ran}. "
            "n3 must not run after a failure with continue_on_failure=False."
        )

    async def test_failure_result_is_raise_not_dict(self, monkeypatch):
        """Ensure the except path propagates the original exception type (not
        RuntimeError wrapping it in a {"status":"failed"} dict)."""

        class _Sentinel(Exception):
            pass

        class _FailStep(_FakeNodeStep):
            async def __call__(self, *args, **kwargs):
                raise _Sentinel("sentinel error")

        _patch_steps(monkeypatch, node_step=_FailStep())

        with pytest.raises(_Sentinel):
            await _run_graph(
                workflow_id=_WF_ID,
                canvas_id=_CANVAS,
                node_order=["n1"],
                user_id=_USER,
                continue_on_failure=False,
            )


# ============================================================
# Behavioral: continue_on_failure (M2)
# ============================================================


@pytest.mark.asyncio
class TestContinueOnFailure:
    async def test_remaining_nodes_run_after_mid_chain_failure(self, monkeypatch):
        """continue_on_failure=True: n3 runs even though n2 failed."""
        _, _, _, _, runner = _patch_steps(
            monkeypatch, node_step=_FakeNodeStep(raise_on={"n2": "boom"})
        )

        with pytest.raises(RuntimeError):
            await _run_graph(
                workflow_id=_WF_ID,
                canvas_id=_CANVAS,
                node_order=["n1", "n2", "n3"],
                user_id=_USER,
                continue_on_failure=True,
            )

        ran = [nid for nid, _ in runner.calls]
        assert ran == ["n1", "n2", "n3"], (
            f"With continue_on_failure=True all 3 nodes should attempt to run. "
            f"Got: {ran}"
        )

    async def test_continue_on_failure_raises_at_end_when_nodes_failed(
        self, monkeypatch
    ):
        """continue_on_failure=True still RAISES at the end when any node failed.

        This keeps the parent task_tracking row as phase=failed (mirror trigger)
        even though we ran all nodes — partial success is a failure, not success.
        """
        _patch_steps(monkeypatch, node_step=_FakeNodeStep(raise_on={"n2": "boom"}))

        with pytest.raises(RuntimeError) as exc_info:
            await _run_graph(
                workflow_id=_WF_ID,
                canvas_id=_CANVAS,
                node_order=["n1", "n2", "n3"],
                user_id=_USER,
                continue_on_failure=True,
            )

        # Error message must reference the failed node(s)
        assert "n2" in str(exc_info.value)

    async def test_continue_on_failure_all_succeed_returns_success(self, monkeypatch):
        """continue_on_failure=True with zero failures: returns success dict."""
        _patch_steps(monkeypatch)

        result = await _run_graph(
            workflow_id=_WF_ID,
            canvas_id=_CANVAS,
            node_order=["n1", "n2", "n3"],
            user_id=_USER,
            continue_on_failure=True,
        )

        assert result["status"] == "success"
        assert result["failed_nodes"] == []


# ============================================================
# Per-node subtask rows (M3)
# ============================================================


@pytest.mark.asyncio
class TestPerNodeSubtasks:
    async def test_create_subtask_called_per_node(self, monkeypatch):
        """create_node_subtask_step invoked once per node in node_order (M3)."""
        _, create_sub, _, _, _ = _patch_steps(monkeypatch)

        await _run_graph(
            workflow_id=_WF_ID,
            canvas_id=_CANVAS,
            node_order=["n1", "n2", "n3"],
            user_id=_USER,
            continue_on_failure=False,
        )

        assert create_sub.call_count == 3

    async def test_subtask_node_wf_ids_are_unique(self, monkeypatch):
        """Each node subtask row gets a distinct node_wf_id derived from parent."""
        _, create_sub, _, _, _ = _patch_steps(monkeypatch)

        await _run_graph(
            workflow_id=_WF_ID,
            canvas_id=_CANVAS,
            node_order=["n1", "n2"],
            user_id=_USER,
            continue_on_failure=False,
        )

        node_wf_ids = [c.kwargs.get("node_wf_id") for c in create_sub.call_args_list]
        assert (
            len(set(node_wf_ids)) == 2
        ), f"Expected 2 distinct node_wf_ids but got: {node_wf_ids}"

    async def test_mark_complete_called_on_success(self, monkeypatch):
        """mark_node_complete_step called for each successful node (M3)."""
        _, _, mark_complete, mark_failed, _ = _patch_steps(monkeypatch)

        await _run_graph(
            workflow_id=_WF_ID,
            canvas_id=_CANVAS,
            node_order=["n1", "n2"],
            user_id=_USER,
            continue_on_failure=False,
        )

        assert mark_complete.call_count == 2
        assert mark_failed.call_count == 0

    async def test_only_failed_subtask_marked_failed(self, monkeypatch):
        """M3: only the failing node's subtask is marked failed; others are complete."""
        _, _, mark_complete, mark_failed, _ = _patch_steps(
            monkeypatch, node_step=_FakeNodeStep(raise_on={"n2": "boom"})
        )

        with pytest.raises(RuntimeError):
            await _run_graph(
                workflow_id=_WF_ID,
                canvas_id=_CANVAS,
                node_order=["n1", "n2", "n3"],
                user_id=_USER,
                continue_on_failure=False,
            )

        # n1 completed before n2 failed; n2 failed; n3 never ran
        assert mark_complete.call_count == 1  # only n1
        assert mark_failed.call_count == 1  # only n2

    async def test_subtask_parent_wf_id_in_create_call(self, monkeypatch):
        """create_node_subtask_step receives parent_wf_id equal to the workflow id."""
        _, create_sub, _, _, _ = _patch_steps(monkeypatch)

        await _run_graph(
            workflow_id=_WF_ID,
            canvas_id=_CANVAS,
            node_order=["n1"],
            user_id=_USER,
            continue_on_failure=False,
        )

        assert create_sub.call_args.kwargs.get("parent_wf_id") == _WF_ID


# ============================================================
# Real node-data resolution + gen/non-gen routing (Phase 6d fix)
# ============================================================


@pytest.mark.asyncio
class TestNodeDataResolution:
    async def test_image_gen_node_routes_through_gen_step(self, monkeypatch):
        """A node typed image_gen runs via run_gen_canvas_node_step (NOT the
        plain step), and receives the REAL node_type + node data."""
        plain = _FakeNodeStep()
        gen = _FakeNodeStep()
        canvas = _fake_canvas(["n1", "n2"], types={"n2": "image_gen"})

        _patch_steps(monkeypatch, node_step=plain, gen_step=gen, canvas=canvas)

        result = await _run_graph(
            workflow_id=_WF_ID,
            canvas_id=_CANVAS,
            node_order=["n1", "n2"],
            user_id=_USER,
            continue_on_failure=False,
        )

        assert result["status"] == "success"

        # n2 went through the gen step with its real type + data.
        gen_ids = [nid for nid, _ in gen.calls]
        assert gen_ids == ["n2"], f"expected gen step to run n2 only, got {gen_ids}"
        gen_rec = gen.records[0]
        assert gen_rec["node_type"] == "image_gen"
        assert gen_rec["node_data"] == {"prompt": "prompt-n2"}
        assert gen_rec["body"] == "prompt-n2"

        # The plain step ran n1 but never n2.
        plain_ids = [nid for nid, _ in plain.calls]
        assert plain_ids == ["n1"], f"plain step should run n1 only, got {plain_ids}"

    async def test_llm_node_routes_through_plain_step(self, monkeypatch):
        """An llm node routes through run_canvas_node_step, never the gen step."""
        plain = _FakeNodeStep()
        gen = _FakeNodeStep()
        canvas = _fake_canvas(["n1"], types={"n1": "llm"})

        _patch_steps(monkeypatch, node_step=plain, gen_step=gen, canvas=canvas)

        await _run_graph(
            workflow_id=_WF_ID,
            canvas_id=_CANVAS,
            node_order=["n1"],
            user_id=_USER,
            continue_on_failure=False,
        )

        assert [nid for nid, _ in plain.calls] == ["n1"]
        assert gen.calls == [], "llm node must NOT route through the gen step"
        assert plain.records[0]["node_type"] == "llm"

    async def test_node_id_absent_from_canvas_raises(self, monkeypatch):
        """A node id in node_order but missing from the loaded canvas is a real
        error: raises with continue_on_failure=False, and still raises at the
        end with continue_on_failure=True (after attempting the other nodes)."""
        # Canvas only contains n1 + n3; "missing" is absent.
        canvas = _fake_canvas(["n1", "n3"])

        # continue_on_failure=False → raises immediately at the missing node.
        _, _, _, mark_failed, runner = _patch_steps(monkeypatch, canvas=canvas)
        with pytest.raises(RuntimeError):
            await _run_graph(
                workflow_id=_WF_ID,
                canvas_id=_CANVAS,
                node_order=["n1", "missing", "n3"],
                user_id=_USER,
                continue_on_failure=False,
            )
        ran = [nid for nid, _ in runner.calls]
        assert ran == ["n1"], f"n3 must not run after missing node, got {ran}"
        assert mark_failed.call_count == 1  # the missing node's subtask

        # continue_on_failure=True → attempts n1 + n3, raises at the end.
        _, _, _, mark_failed2, runner2 = _patch_steps(monkeypatch, canvas=canvas)
        with pytest.raises(RuntimeError) as exc_info:
            await _run_graph(
                workflow_id=_WF_ID,
                canvas_id=_CANVAS,
                node_order=["n1", "missing", "n3"],
                user_id=_USER,
                continue_on_failure=True,
            )
        ran2 = [nid for nid, _ in runner2.calls]
        assert ran2 == ["n1", "n3"], f"both real nodes should run, got {ran2}"
        assert "missing" in str(exc_info.value)
        assert mark_failed2.call_count == 1


# ============================================================
# id-match regression (M1 — manager.create wf_id == start_workflow_routed wf_id)
# ============================================================


class TestIdMatchRegression:
    """The router enqueue path must pass the SAME wf_id to manager.create() and
    start_workflow_routed() — deviation causes a "task_tracking row with no DBOS
    workflow" ghost (task stays queued forever) or the reverse (DBOS workflow
    with no tracking row visible to the UI).

    Reference: CLAUDE.md 任务系统架构纪律 第 6 条 +
               reference_dbos_dispatch_endpoint_wf_id.md.
    """

    def test_router_source_uses_same_wf_id_variable(self):
        """Static check: the route handler uses one wf_id variable in both
        manager.create(dbos_workflow_id=wf_id) and start_workflow_routed(workflow_id=wf_id).

        We analyse the source of canvases_router to verify the pattern without
        running a live DB/DBOS.

        Note: ``from app.api import canvases_router`` would give the APIRouter
        *object* (because __init__.py does
        ``from app.api.canvases_router import router as canvases_router``).
        Use importlib to load the actual module so inspect.getsource works.
        """
        import importlib

        cr_module = importlib.import_module("app.api.canvases_router")
        source = inspect.getsource(cr_module)

        # The route must exist
        assert (
            "graph-runs" in source or "graph_runs" in source
        ), "POST /canvases/{id}/graph-runs route not found in canvases_router.py"

        # Both manager.create and start_workflow_routed must reference wf_id
        assert "dbos_workflow_id=wf_id" in source, (
            "manager.create() call must pass dbos_workflow_id=wf_id so the "
            "task_tracking row is linked to the DBOS workflow."
        )
        assert "workflow_id=wf_id" in source, (
            "start_workflow_routed() must pass workflow_id=wf_id (the same "
            "variable as manager.create dbos_workflow_id) — id-match rule."
        )
