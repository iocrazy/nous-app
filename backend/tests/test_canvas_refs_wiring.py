# backend/tests/test_canvas_refs_wiring.py
"""CanvasService maintains canvas_resource_refs on save."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pytest

from app.schemas.canvas import CanvasUpdate
from app.services.canvas.canvas_service import CanvasService

FROZEN = datetime(2026, 6, 13, 12, 0, 0, tzinfo=timezone.utc)


class FakeRepo:
    def __init__(self) -> None:
        self.row = {
            "id": "5001",
            "project_id": "9000",
            "name": "C",
            "kind": "smart",
            "viewport_json": {"x": 0, "y": 0, "zoom": 1},
            "nodes_json": [],
            "connections_json": [],
            "node_ops_json": [],
            "connection_ops_json": [],
            "base_updated_at": FROZEN.isoformat(),
        }

    async def get_by_id(self, canvas_id: str) -> Optional[Dict[str, Any]]:
        return dict(self.row)

    async def update_with_lock(self, canvas_id, expected_base_updated_at, fields):
        self.row.update(fields)
        self.row["base_updated_at"] = "2026-06-13T12:01:00+00:00"
        return dict(self.row)


class FakeRefsRepo:
    def __init__(self) -> None:
        self.calls: List[tuple[str, List[Dict[str, str]]]] = []

    async def replace_for_canvas(
        self, canvas_id: str, refs: List[Dict[str, str]]
    ) -> None:
        self.calls.append((canvas_id, refs))


@pytest.mark.asyncio
async def test_save_extracts_and_replaces_refs():
    refs_repo = FakeRefsRepo()
    svc = CanvasService(repository=FakeRepo(), refs_repository=refs_repo)
    upd = CanvasUpdate(
        base_updated_at=FROZEN,
        nodes_json=[
            {
                "id": "shot-1",
                "type": "shot",
                "data": {"reference_resource_ids": ["111"]},
            },
            {
                "id": "out-1",
                "type": "output",
                "data": {"kind": "image", "resource_id": "222"},
            },
        ],
    )
    await svc.update_with_lock("5001", upd)
    assert len(refs_repo.calls) == 1
    canvas_id, refs = refs_repo.calls[0]
    assert canvas_id == "5001"
    assert {(r["resource_id"], r["role"]) for r in refs} == {
        ("111", "reference"),
        ("222", "output"),
    }


@pytest.mark.asyncio
async def test_save_without_nodes_json_does_not_touch_refs():
    refs_repo = FakeRefsRepo()
    svc = CanvasService(repository=FakeRepo(), refs_repository=refs_repo)
    await svc.update_with_lock(
        "5001", CanvasUpdate(base_updated_at=FROZEN, name="renamed")
    )
    assert refs_repo.calls == []  # nodes_json absent → no ref recompute


@pytest.mark.asyncio
async def test_refs_failure_does_not_break_save(monkeypatch):
    class Boom:
        async def replace_for_canvas(self, *a, **k):
            raise RuntimeError("db down")

    svc = CanvasService(repository=FakeRepo(), refs_repository=Boom())
    upd = CanvasUpdate(base_updated_at=FROZEN, nodes_json=[])
    result = await svc.update_with_lock("5001", upd)  # must not raise
    assert result["id"] == "5001"
