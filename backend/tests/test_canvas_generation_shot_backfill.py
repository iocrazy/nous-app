"""canvas_generation shot-backfill step (shot-nodes-on-canvas spec
2026-08-11, Task 2).

When a canvas generation targets a ``shot``-type node whose ``data.shot_id``
is set, completion must ALSO write the result onto the shot row: this is
lane (c) of the shot three-write-lanes split (facts §7) — the generation
workflow lane, never the agent ledger lane (``script_shot_ops``).

The step reads node type/``data.shot_id`` from ``canvases.nodes_json``
server-side (never trusts a client-supplied node payload at completion
time), guards cross-project backfill, and reuses
``ScriptShotRepository.update_status`` — the SAME write lane
``script_shot_generate.py``'s ``mark_shot_done`` already uses, which
internally fires ``fire_surface_sync_for_shot`` on ``status='done'``. No new
call to ``fire_surface_sync_for_shot`` is needed in the step itself; these
tests instead assert on ``update_status`` (and, for full contract coverage,
that it flows through to the shot's status/image_url).
"""

from __future__ import annotations

from typing import Any, Dict, Optional
from unittest.mock import AsyncMock

import pytest

from app.repositories.canvas_repository import CanvasRepository
from app.repositories.script_shot_repository import ScriptShotRepository
from app.workflows.canvas_generation import backfill_shot_from_generation_step

pytestmark = pytest.mark.unit

CANVAS_ID = 4001
PROJECT_ID = 5001
OTHER_PROJECT_ID = 5002
SHOT_ID = "9007199254740997"


def _canvas_row(nodes: list, project_id: int = PROJECT_ID) -> Dict[str, Any]:
    return {
        "id": str(CANVAS_ID),
        "project_id": project_id,
        "nodes_json": nodes,
    }


def _shot_node(node_id: str = "n1", shot_id: Optional[str] = SHOT_ID) -> Dict[str, Any]:
    return {"id": node_id, "type": "shot", "data": {"shot_id": shot_id}}


@pytest.mark.asyncio
async def test_generation_backfills_bound_shot(monkeypatch):
    async def _fake_get_by_id_canvas(self, canvas_id):
        assert str(canvas_id) == str(CANVAS_ID)
        return _canvas_row([_shot_node()])

    async def _fake_get_project_id(self, shot_id):
        assert shot_id == SHOT_ID
        return PROJECT_ID

    calls: Dict[str, Any] = {}

    async def _fake_update_status(self, shot_id, status, **kwargs):
        calls["shot_id"] = shot_id
        calls["status"] = status
        calls.update(kwargs)
        return {"id": shot_id, "status": status, **kwargs}

    monkeypatch.setattr(CanvasRepository, "get_by_id", _fake_get_by_id_canvas)
    monkeypatch.setattr(ScriptShotRepository, "get_project_id", _fake_get_project_id)
    monkeypatch.setattr(ScriptShotRepository, "update_status", _fake_update_status)

    await backfill_shot_from_generation_step(
        canvas_id=CANVAS_ID, node_id="n1", result_url="https://cdn/x.png"
    )

    assert calls["shot_id"] == SHOT_ID
    assert calls["status"] == "done"
    assert calls["image_url"] == "https://cdn/x.png"


@pytest.mark.asyncio
async def test_generation_skips_non_shot_node(monkeypatch):
    async def _fake_get_by_id_canvas(self, canvas_id):
        return _canvas_row([{"id": "n1", "type": "media", "data": {}}])

    update_status = AsyncMock()
    monkeypatch.setattr(CanvasRepository, "get_by_id", _fake_get_by_id_canvas)
    monkeypatch.setattr(ScriptShotRepository, "update_status", update_status)

    await backfill_shot_from_generation_step(
        canvas_id=CANVAS_ID, node_id="n1", result_url="https://cdn/x.png"
    )

    update_status.assert_not_called()


@pytest.mark.asyncio
async def test_generation_skips_unbound_shot(monkeypatch):
    """``data.shot_id`` is None → not a bound shot node, no repo write."""

    async def _fake_get_by_id_canvas(self, canvas_id):
        return _canvas_row([_shot_node(shot_id=None)])

    update_status = AsyncMock()
    monkeypatch.setattr(CanvasRepository, "get_by_id", _fake_get_by_id_canvas)
    monkeypatch.setattr(ScriptShotRepository, "update_status", update_status)

    await backfill_shot_from_generation_step(
        canvas_id=CANVAS_ID, node_id="n1", result_url="https://cdn/x.png"
    )

    update_status.assert_not_called()


@pytest.mark.asyncio
async def test_generation_skips_stale_shot_row(monkeypatch):
    """shot_id set but the shot row no longer exists → warning + skip, the
    generation itself still succeeds (no exception)."""

    async def _fake_get_by_id_canvas(self, canvas_id):
        return _canvas_row([_shot_node()])

    async def _fake_get_project_id(self, shot_id):
        return None  # chain missing → treated as not found / not verifiable

    update_status = AsyncMock()
    monkeypatch.setattr(CanvasRepository, "get_by_id", _fake_get_by_id_canvas)
    monkeypatch.setattr(ScriptShotRepository, "get_project_id", _fake_get_project_id)
    monkeypatch.setattr(ScriptShotRepository, "update_status", update_status)

    await backfill_shot_from_generation_step(
        canvas_id=CANVAS_ID, node_id="n1", result_url="https://cdn/x.png"
    )

    update_status.assert_not_called()


@pytest.mark.asyncio
async def test_generation_skips_cross_project_shot(monkeypatch):
    """shot belongs to a DIFFERENT project than the canvas → reject the
    backfill (warning + skip), never trust the node's shot_id blindly."""

    async def _fake_get_by_id_canvas(self, canvas_id):
        return _canvas_row([_shot_node()], project_id=PROJECT_ID)

    async def _fake_get_project_id(self, shot_id):
        return OTHER_PROJECT_ID

    update_status = AsyncMock()
    monkeypatch.setattr(CanvasRepository, "get_by_id", _fake_get_by_id_canvas)
    monkeypatch.setattr(ScriptShotRepository, "get_project_id", _fake_get_project_id)
    monkeypatch.setattr(ScriptShotRepository, "update_status", update_status)

    await backfill_shot_from_generation_step(
        canvas_id=CANVAS_ID, node_id="n1", result_url="https://cdn/x.png"
    )

    update_status.assert_not_called()


@pytest.mark.asyncio
async def test_generation_skips_when_node_id_missing_on_canvas(monkeypatch):
    async def _fake_get_by_id_canvas(self, canvas_id):
        return _canvas_row([_shot_node(node_id="other-node")])

    update_status = AsyncMock()
    monkeypatch.setattr(CanvasRepository, "get_by_id", _fake_get_by_id_canvas)
    monkeypatch.setattr(ScriptShotRepository, "update_status", update_status)

    await backfill_shot_from_generation_step(
        canvas_id=CANVAS_ID, node_id="n1", result_url="https://cdn/x.png"
    )

    update_status.assert_not_called()


@pytest.mark.asyncio
async def test_generation_noop_when_canvas_or_node_id_absent(monkeypatch):
    """No node was targeted (floating generation) — nothing to look up."""
    get_by_id = AsyncMock()
    monkeypatch.setattr(CanvasRepository, "get_by_id", get_by_id)

    await backfill_shot_from_generation_step(
        canvas_id=None, node_id=None, result_url="https://cdn/x.png"
    )
    await backfill_shot_from_generation_step(
        canvas_id=CANVAS_ID, node_id=None, result_url="https://cdn/x.png"
    )

    get_by_id.assert_not_called()


@pytest.mark.asyncio
async def test_generation_noop_when_canvas_missing(monkeypatch):
    async def _fake_get_by_id_canvas(self, canvas_id):
        return None

    update_status = AsyncMock()
    monkeypatch.setattr(CanvasRepository, "get_by_id", _fake_get_by_id_canvas)
    monkeypatch.setattr(ScriptShotRepository, "update_status", update_status)

    await backfill_shot_from_generation_step(
        canvas_id=CANVAS_ID, node_id="n1", result_url="https://cdn/x.png"
    )

    update_status.assert_not_called()


@pytest.mark.asyncio
async def test_backfill_write_failure_raises(monkeypatch):
    """House rule: a DB write failure in the backfill lane must raise (DBOS
    retries the step) — never swallowed into a silent skip. Registration of
    ``generated_media`` already succeeded upstream and is idempotent, so a
    step retry is safe."""

    async def _fake_get_by_id_canvas(self, canvas_id):
        return _canvas_row([_shot_node()])

    async def _fake_get_project_id(self, shot_id):
        return PROJECT_ID

    async def _raising_update_status(self, shot_id, status, **kwargs):
        raise RuntimeError("db write failed")

    monkeypatch.setattr(CanvasRepository, "get_by_id", _fake_get_by_id_canvas)
    monkeypatch.setattr(ScriptShotRepository, "get_project_id", _fake_get_project_id)
    monkeypatch.setattr(ScriptShotRepository, "update_status", _raising_update_status)

    with pytest.raises(RuntimeError):
        await backfill_shot_from_generation_step(
            canvas_id=CANVAS_ID, node_id="n1", result_url="https://cdn/x.png"
        )
