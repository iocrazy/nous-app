"""Behavioral tests for Phase 6d M4a additions: data piping + result persistence.

Coverage
--------
Passive nodes:
  - Passive source node (prompt / text / image / ...) is NOT dispatched to a runner.
  - mark_node_complete_step is called for passive nodes.
  - persist_node_result_step is NOT called for passive nodes.

Data piping (mirrors dataPiping.ts / cascade.ts):
  - prompt → llm (prompt-out → prompt-in): piped value overrides stored widget.
  - text → llm (text-out → text-in): maps to 'prompt' key.
  - image → llm (image-out → image-in): maps to 'reference_image_url'.
  - Disconnected node uses its own stored data.
  - text_join transform: effective data folded from upstream, output piped downstream.
  - llm → llm chaining: second llm receives first llm's run_result.text as prompt.

Result persistence:
  - persist_node_result_step called once per successful runnable node.
  - llm run_result normalised from text field → {"text": <text>}.
  - image_gen run_result normalised from result dict → {"image_url": <url>}.
  - persist called with run_status="succeeded".
  - persist NOT called when the node step raises.

Gen routing with piped data:
  - prompt → image_gen: gen step receives piped prompt (not stored widget).
  - image_gen → video_gen: video_gen receives image_url as source_image_url.
"""

from __future__ import annotations

import types
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.workflows import canvas_graph as canvas_graph_module
from app.workflows.canvas_graph import _run_graph

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_WF_ID = "wf-m4a-001"
_CANVAS = "canvas-m4a"
_USER = "user-m4a"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeRunStep:
    """Async runner whose results are configurable per node_id.

    Absent node_ids return a generic ok=True / text="ok-{node_id}" response,
    which normalises to run_result={"text": "ok-{node_id}"} in _run_graph.
    """

    def __init__(self, results: dict[str, dict] | None = None) -> None:
        self._results = results or {}
        self.calls: list[dict[str, Any]] = []

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
        self.calls.append(
            {
                "node_id": node_id,
                "node_type": node_type,
                "node_data": dict(node_data),
                "body": body,
            }
        )
        return self._results.get(
            node_id,
            {"ok": True, "text": f"ok-{node_id}", "error": None, "result": None},
        )


def _node(node_id: str, node_type: str, **data_kwargs: Any) -> dict:
    """Compact node dict constructor for test setup."""
    return {"id": node_id, "type": node_type, "data": dict(data_kwargs)}


def _patch_all(
    monkeypatch,
    *,
    nodes_by_id: dict,
    incoming_wires_map: dict | None = None,
    run_results: dict | None = None,
) -> types.SimpleNamespace:
    """Patch all canvas_graph module attributes used by _run_graph.

    Both plain_runner and gen_runner share the same run_results dict — each
    node goes through exactly one of the two runners, so there is no conflict.

    Returns a SimpleNamespace with all mock objects for per-test assertions.
    """
    mark_proc = AsyncMock()
    create_sub = AsyncMock()
    mark_complete = AsyncMock()
    mark_failed = AsyncMock()
    plain_runner = _FakeRunStep(results=run_results)
    gen_runner = _FakeRunStep(results=run_results)
    persist_step = AsyncMock()
    load_nodes = AsyncMock(return_value=nodes_by_id)
    load_conns = AsyncMock(return_value=incoming_wires_map or {})

    monkeypatch.setattr(canvas_graph_module, "mark_graph_processing_step", mark_proc)
    monkeypatch.setattr(canvas_graph_module, "create_node_subtask_step", create_sub)
    monkeypatch.setattr(canvas_graph_module, "mark_node_complete_step", mark_complete)
    monkeypatch.setattr(canvas_graph_module, "mark_node_failed_step", mark_failed)
    monkeypatch.setattr(canvas_graph_module, "run_canvas_node_step", plain_runner)
    monkeypatch.setattr(canvas_graph_module, "run_gen_canvas_node_step", gen_runner)
    monkeypatch.setattr(canvas_graph_module, "persist_node_result_step", persist_step)
    monkeypatch.setattr(canvas_graph_module, "_load_canvas_nodes", load_nodes)
    monkeypatch.setattr(canvas_graph_module, "_load_canvas_connections", load_conns)

    return types.SimpleNamespace(
        mark_proc=mark_proc,
        create_sub=create_sub,
        mark_complete=mark_complete,
        mark_failed=mark_failed,
        plain_runner=plain_runner,
        gen_runner=gen_runner,
        persist_step=persist_step,
        load_nodes=load_nodes,
        load_conns=load_conns,
    )


# ---------------------------------------------------------------------------
# Passive node handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestPassiveNodes:
    """Passive nodes must be recorded for downstream piping but never dispatched."""

    async def test_prompt_node_not_dispatched(self, monkeypatch):
        """A prompt node must not invoke run_canvas_node_step or run_gen_canvas_node_step."""
        m = _patch_all(
            monkeypatch,
            nodes_by_id={"pn": _node("pn", "prompt", prompt="hello")},
        )
        await _run_graph(
            workflow_id=_WF_ID,
            canvas_id=_CANVAS,
            node_order=["pn"],
            user_id=_USER,
            continue_on_failure=False,
        )
        assert m.plain_runner.calls == [], "prompt node must not call plain runner"
        assert m.gen_runner.calls == [], "prompt node must not call gen runner"

    async def test_text_node_not_dispatched(self, monkeypatch):
        """A text node must not invoke any runner."""
        m = _patch_all(
            monkeypatch,
            nodes_by_id={"tn": _node("tn", "text", text="body")},
        )
        await _run_graph(
            workflow_id=_WF_ID,
            canvas_id=_CANVAS,
            node_order=["tn"],
            user_id=_USER,
            continue_on_failure=False,
        )
        assert m.plain_runner.calls == []
        assert m.gen_runner.calls == []

    async def test_passive_node_calls_mark_complete(self, monkeypatch):
        """mark_node_complete_step is called once for a passive node."""
        m = _patch_all(
            monkeypatch,
            nodes_by_id={"pn": _node("pn", "prompt", prompt="hello")},
        )
        await _run_graph(
            workflow_id=_WF_ID,
            canvas_id=_CANVAS,
            node_order=["pn"],
            user_id=_USER,
            continue_on_failure=False,
        )
        assert m.mark_complete.call_count == 1
        m.mark_failed.assert_not_called()

    async def test_passive_node_does_not_persist(self, monkeypatch):
        """persist_node_result_step must NOT be called for passive nodes (no run_result)."""
        m = _patch_all(
            monkeypatch,
            nodes_by_id={"pn": _node("pn", "text", text="hello")},
        )
        await _run_graph(
            workflow_id=_WF_ID,
            canvas_id=_CANVAS,
            node_order=["pn"],
            user_id=_USER,
            continue_on_failure=False,
        )
        m.persist_step.assert_not_called()


# ---------------------------------------------------------------------------
# Data piping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestDataPiping:
    """Connected input OVERRIDES stored widget value (ComfyUI semantics)."""

    async def test_prompt_to_llm_pipes_prompt_text(self, monkeypatch):
        """2-node chain: prompt→llm.  llm receives piped prompt, not stored widget."""
        nodes = {
            "src": _node("src", "prompt", prompt="piped hello"),
            "dst": _node("dst", "llm", prompt="STORED_WIDGET"),
        }
        wires = {
            "dst": [
                {
                    "sourceId": "src",
                    "sourceHandle": "prompt-out",
                    "targetHandle": "prompt-in",
                }
            ]
        }
        m = _patch_all(monkeypatch, nodes_by_id=nodes, incoming_wires_map=wires)

        await _run_graph(
            workflow_id=_WF_ID,
            canvas_id=_CANVAS,
            node_order=["src", "dst"],
            user_id=_USER,
            continue_on_failure=False,
        )

        assert len(m.plain_runner.calls) == 1
        dst_call = m.plain_runner.calls[0]
        assert dst_call["node_id"] == "dst"
        assert dst_call["node_data"]["prompt"] == "piped hello", (
            f"llm must receive piped prompt 'piped hello', not stored widget. "
            f"Got: {dst_call['node_data']}"
        )

    async def test_disconnected_node_uses_stored_data(self, monkeypatch):
        """A node with no incoming wires runs with its own stored data unchanged."""
        nodes = {"llm1": _node("llm1", "llm", prompt="my own prompt")}
        m = _patch_all(monkeypatch, nodes_by_id=nodes, incoming_wires_map={})

        await _run_graph(
            workflow_id=_WF_ID,
            canvas_id=_CANVAS,
            node_order=["llm1"],
            user_id=_USER,
            continue_on_failure=False,
        )

        assert len(m.plain_runner.calls) == 1
        call_rec = m.plain_runner.calls[0]
        assert call_rec["node_data"]["prompt"] == "my own prompt"

    async def test_text_node_pipes_via_text_in_handle(self, monkeypatch):
        """text source → llm text-in: text-in maps to 'prompt' key (dataPiping.ts rule)."""
        nodes = {
            "txt": _node("txt", "text", text="wired text"),
            "llm1": _node("llm1", "llm", prompt="STORED"),
        }
        wires = {
            "llm1": [
                {
                    "sourceId": "txt",
                    "sourceHandle": "text-out",
                    "targetHandle": "text-in",
                }
            ]
        }
        m = _patch_all(monkeypatch, nodes_by_id=nodes, incoming_wires_map=wires)

        await _run_graph(
            workflow_id=_WF_ID,
            canvas_id=_CANVAS,
            node_order=["txt", "llm1"],
            user_id=_USER,
            continue_on_failure=False,
        )

        llm_calls = [c for c in m.plain_runner.calls if c["node_id"] == "llm1"]
        assert len(llm_calls) == 1
        assert llm_calls[0]["node_data"]["prompt"] == "wired text", (
            f"text-in handle must pipe text node's text to llm prompt. "
            f"Got: {llm_calls[0]['node_data']}"
        )

    async def test_image_node_pipes_reference_image_url(self, monkeypatch):
        """image source → llm image-in: maps to 'reference_image_url'."""
        nodes = {
            "img": _node("img", "image", image_url="https://example.com/img.png"),
            "llm1": _node("llm1", "llm", prompt="describe this"),
        }
        wires = {
            "llm1": [
                {
                    "sourceId": "img",
                    "sourceHandle": "image-out",
                    "targetHandle": "image-in",
                }
            ]
        }
        m = _patch_all(monkeypatch, nodes_by_id=nodes, incoming_wires_map=wires)

        await _run_graph(
            workflow_id=_WF_ID,
            canvas_id=_CANVAS,
            node_order=["img", "llm1"],
            user_id=_USER,
            continue_on_failure=False,
        )

        llm_calls = [c for c in m.plain_runner.calls if c["node_id"] == "llm1"]
        assert len(llm_calls) == 1
        data = llm_calls[0]["node_data"]
        assert data["reference_image_url"] == "https://example.com/img.png"
        # unpiped key is not overridden
        assert data["prompt"] == "describe this"

    async def test_llm_output_pipes_to_downstream_llm(self, monkeypatch):
        """llm → llm chaining: second llm receives first llm's run_result text as prompt."""
        nodes = {
            "llm1": _node("llm1", "llm", prompt="Describe a dog"),
            "llm2": _node("llm2", "llm", prompt="STORED"),
        }
        wires = {
            "llm2": [
                {
                    "sourceId": "llm1",
                    "sourceHandle": "text-out",
                    "targetHandle": "prompt-in",
                }
            ]
        }
        m = _patch_all(
            monkeypatch,
            nodes_by_id=nodes,
            incoming_wires_map=wires,
            run_results={
                "llm1": {
                    "ok": True,
                    "text": "A golden retriever with fluffy fur",
                    "error": None,
                    "result": None,
                },
                "llm2": {
                    "ok": True,
                    "text": "response2",
                    "error": None,
                    "result": None,
                },
            },
        )

        await _run_graph(
            workflow_id=_WF_ID,
            canvas_id=_CANVAS,
            node_order=["llm1", "llm2"],
            user_id=_USER,
            continue_on_failure=False,
        )

        llm2_calls = [c for c in m.plain_runner.calls if c["node_id"] == "llm2"]
        assert len(llm2_calls) == 1
        assert (
            llm2_calls[0]["node_data"]["prompt"] == "A golden retriever with fluffy fur"
        ), "Second llm must receive first llm's run_result text, not stored widget."


# ---------------------------------------------------------------------------
# text_join transform
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestTextJoinTransform:
    """text_join is a transform node: effective data folded in, output piped downstream."""

    async def test_text_join_not_dispatched(self, monkeypatch):
        """text_join must not call run_canvas_node_step or run_gen_canvas_node_step."""
        nodes = {"j": _node("j", "text_join", separator=" ", text_a="a", text_b="b")}
        m = _patch_all(monkeypatch, nodes_by_id=nodes)

        await _run_graph(
            workflow_id=_WF_ID,
            canvas_id=_CANVAS,
            node_order=["j"],
            user_id=_USER,
            continue_on_failure=False,
        )

        assert m.plain_runner.calls == []
        assert m.gen_runner.calls == []
        assert m.mark_complete.call_count == 1
        m.persist_step.assert_not_called()

    async def test_text_join_pipes_joined_text_to_llm(self, monkeypatch):
        """Two text sources → text_join → llm: llm receives joined text as prompt."""
        nodes = {
            "ta": _node("ta", "text", text="Hello"),
            "tb": _node("tb", "text", text="World"),
            "j": _node("j", "text_join", separator="-"),
            "llm1": _node("llm1", "llm", prompt="STORED"),
        }
        wires = {
            "j": [
                {
                    "sourceId": "ta",
                    "sourceHandle": "text-out",
                    "targetHandle": "text-a-in",
                },
                {
                    "sourceId": "tb",
                    "sourceHandle": "text-out",
                    "targetHandle": "text-b-in",
                },
            ],
            "llm1": [
                {
                    "sourceId": "j",
                    "sourceHandle": "text-out",
                    "targetHandle": "prompt-in",
                }
            ],
        }
        m = _patch_all(monkeypatch, nodes_by_id=nodes, incoming_wires_map=wires)

        await _run_graph(
            workflow_id=_WF_ID,
            canvas_id=_CANVAS,
            node_order=["ta", "tb", "j", "llm1"],
            user_id=_USER,
            continue_on_failure=False,
        )

        # text_join must not dispatch to runner
        assert all(
            c["node_id"] != "j" for c in m.plain_runner.calls
        ), "text_join must not run via plain runner"

        # llm1 must receive the joined text ("Hello" + "-" + "World")
        llm_calls = [c for c in m.plain_runner.calls if c["node_id"] == "llm1"]
        assert len(llm_calls) == 1
        assert (
            llm_calls[0]["node_data"]["prompt"] == "Hello-World"
        ), f"llm must receive joined text. Got: {llm_calls[0]['node_data']}"


# ---------------------------------------------------------------------------
# Result persistence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestResultPersistence:
    """persist_node_result_step is called after each successful runnable node."""

    async def test_persist_called_once_for_llm(self, monkeypatch):
        """persist_node_result_step called exactly once after a successful llm node."""
        nodes = {"llm1": _node("llm1", "llm", prompt="hello")}
        run_results = {
            "llm1": {
                "ok": True,
                "text": "This is the generated text",
                "error": None,
                "result": None,
            }
        }
        m = _patch_all(monkeypatch, nodes_by_id=nodes, run_results=run_results)

        await _run_graph(
            workflow_id=_WF_ID,
            canvas_id=_CANVAS,
            node_order=["llm1"],
            user_id=_USER,
            continue_on_failure=False,
        )

        m.persist_step.assert_called_once()
        kw = m.persist_step.call_args.kwargs
        assert kw["canvas_id"] == _CANVAS
        assert kw["node_id"] == "llm1"
        assert kw["run_status"] == "succeeded"
        assert kw["run_result"] == {"text": "This is the generated text"}

    async def test_llm_run_result_normalised_from_text_field(self, monkeypatch):
        """run_result for an llm node is {"text": <text>} (no result dict on llm)."""
        nodes = {"llm1": _node("llm1", "llm", prompt="hello")}
        run_results = {
            "llm1": {
                "ok": True,
                "text": "the output",
                "error": None,
                "result": None,
            }
        }
        m = _patch_all(monkeypatch, nodes_by_id=nodes, run_results=run_results)

        await _run_graph(
            workflow_id=_WF_ID,
            canvas_id=_CANVAS,
            node_order=["llm1"],
            user_id=_USER,
            continue_on_failure=False,
        )

        run_result = m.persist_step.call_args.kwargs["run_result"]
        assert run_result == {"text": "the output"}

    async def test_image_gen_run_result_normalised_from_result_dict(self, monkeypatch):
        """run_result for image_gen is the result dict (e.g. {"image_url": ...})."""
        nodes = {"img1": _node("img1", "image_gen", prompt="a cat")}
        run_results = {
            "img1": {
                "ok": True,
                "text": None,
                "error": None,
                "result": {"image_url": "https://cdn.example.com/cat.png"},
            }
        }
        m = _patch_all(monkeypatch, nodes_by_id=nodes, run_results=run_results)

        await _run_graph(
            workflow_id=_WF_ID,
            canvas_id=_CANVAS,
            node_order=["img1"],
            user_id=_USER,
            continue_on_failure=False,
        )

        run_result = m.persist_step.call_args.kwargs["run_result"]
        assert run_result == {"image_url": "https://cdn.example.com/cat.png"}

    async def test_persist_not_called_when_node_raises(self, monkeypatch):
        """persist_node_result_step must NOT be called when the node step raises."""
        nodes = {"llm1": _node("llm1", "llm", prompt="hello")}
        m = _patch_all(monkeypatch, nodes_by_id=nodes)

        class _FailStep(_FakeRunStep):
            async def __call__(self, *a, **kw):
                raise RuntimeError("model error")

        monkeypatch.setattr(canvas_graph_module, "run_canvas_node_step", _FailStep())

        with pytest.raises(RuntimeError):
            await _run_graph(
                workflow_id=_WF_ID,
                canvas_id=_CANVAS,
                node_order=["llm1"],
                user_id=_USER,
                continue_on_failure=False,
            )

        m.persist_step.assert_not_called()

    async def test_persist_called_once_per_node_in_three_node_graph(self, monkeypatch):
        """3 runnable nodes all succeed: persist called exactly 3 times."""
        nodes = {
            "l1": _node("l1", "llm", prompt="a"),
            "l2": _node("l2", "llm", prompt="b"),
            "l3": _node("l3", "llm", prompt="c"),
        }
        m = _patch_all(monkeypatch, nodes_by_id=nodes)

        await _run_graph(
            workflow_id=_WF_ID,
            canvas_id=_CANVAS,
            node_order=["l1", "l2", "l3"],
            user_id=_USER,
            continue_on_failure=False,
        )

        assert m.persist_step.call_count == 3


# ---------------------------------------------------------------------------
# Gen node routing with piped effective data
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestGenNodePipingAndRouting:
    """image_gen / video_gen route to gen step and receive effective (piped) data."""

    async def test_prompt_pipes_to_image_gen(self, monkeypatch):
        """prompt → image_gen: gen step receives piped prompt, not stored widget."""
        nodes = {
            "src": _node("src", "prompt", prompt="a majestic mountain"),
            "img": _node("img", "image_gen", prompt="STORED"),
        }
        wires = {
            "img": [
                {
                    "sourceId": "src",
                    "sourceHandle": "prompt-out",
                    "targetHandle": "prompt-in",
                }
            ]
        }
        m = _patch_all(
            monkeypatch,
            nodes_by_id=nodes,
            incoming_wires_map=wires,
            run_results={
                "img": {
                    "ok": True,
                    "text": None,
                    "error": None,
                    "result": {"image_url": "https://example.com/mountain.png"},
                }
            },
        )

        await _run_graph(
            workflow_id=_WF_ID,
            canvas_id=_CANVAS,
            node_order=["src", "img"],
            user_id=_USER,
            continue_on_failure=False,
        )

        # image_gen must NOT use the plain runner
        assert m.plain_runner.calls == [], "image_gen must not use plain runner"

        assert len(m.gen_runner.calls) == 1
        gen_call = m.gen_runner.calls[0]
        assert gen_call["node_id"] == "img"
        assert gen_call["node_type"] == "image_gen"
        # Piped value overrides stored widget
        assert (
            gen_call["node_data"]["prompt"] == "a majestic mountain"
        ), f"image_gen must receive piped prompt. Got: {gen_call['node_data']}"

    async def test_image_gen_output_pipes_to_video_gen(self, monkeypatch):
        """image_gen run_result.image_url → video_gen source_image_url (via image-out → image-in)."""
        nodes = {
            "img": _node("img", "image_gen", prompt="a cat"),
            "vid": _node(
                "vid",
                "video_gen",
                prompt="animate it",
                source_image_url="STORED_URL",
            ),
        }
        wires = {
            "vid": [
                {
                    "sourceId": "img",
                    "sourceHandle": "image-out",
                    "targetHandle": "image-in",
                }
            ]
        }
        m = _patch_all(
            monkeypatch,
            nodes_by_id=nodes,
            incoming_wires_map=wires,
            run_results={
                "img": {
                    "ok": True,
                    "text": None,
                    "error": None,
                    "result": {"image_url": "https://example.com/cat.png"},
                },
                "vid": {
                    "ok": True,
                    "text": None,
                    "error": None,
                    "result": {"video_url": "https://example.com/cat.mp4"},
                },
            },
        )

        await _run_graph(
            workflow_id=_WF_ID,
            canvas_id=_CANVAS,
            node_order=["img", "vid"],
            user_id=_USER,
            continue_on_failure=False,
        )

        vid_calls = [c for c in m.gen_runner.calls if c["node_id"] == "vid"]
        assert len(vid_calls) == 1
        vid_data = vid_calls[0]["node_data"]
        assert vid_data["source_image_url"] == "https://example.com/cat.png", (
            f"video_gen must receive image_gen's image_url via piping. "
            f"Got: {vid_data}"
        )
        # Unpiped field unchanged
        assert vid_data["prompt"] == "animate it"
