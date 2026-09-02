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


# ── P4: canvas_asset_refs is the SECOND mirror, maintained independently ────


class FakeAssetRefsRepo:
    def __init__(self) -> None:
        self.calls: List[tuple[str, List[Dict[str, Any]]]] = []

    async def replace_for_canvas(self, canvas_id: str, refs) -> None:
        self.calls.append((canvas_id, refs))


_ASSET_A = "727145299382534145"
_LOADOUT = "727145299382534200"


def _asset_node(node_id: str, asset_id: str, loadout_id=None) -> Dict[str, Any]:
    return {
        "id": node_id,
        "type": "asset",
        "data": {"asset_id": asset_id, "loadout_id": loadout_id},
    }


@pytest.mark.asyncio
async def test_save_replaces_asset_refs_too():
    asset_refs = FakeAssetRefsRepo()
    svc = CanvasService(
        repository=FakeRepo(),
        refs_repository=FakeRefsRepo(),
        asset_refs_repository=asset_refs,
    )
    await svc.update_with_lock(
        "5001",
        CanvasUpdate(
            base_updated_at=FROZEN,
            nodes_json=[
                _asset_node("asset-1", _ASSET_A, _LOADOUT),
                {"id": "out-1", "type": "output", "data": {"resource_id": "222"}},
            ],
        ),
    )
    assert asset_refs.calls == [
        (
            "5001",
            [
                {
                    "asset_id": int(_ASSET_A),
                    "node_id": "asset-1",
                    "loadout_id": int(_LOADOUT),
                }
            ],
        )
    ]


@pytest.mark.asyncio
async def test_save_without_nodes_json_touches_neither_mirror():
    asset_refs = FakeAssetRefsRepo()
    refs = FakeRefsRepo()
    svc = CanvasService(
        repository=FakeRepo(),
        refs_repository=refs,
        asset_refs_repository=asset_refs,
    )
    await svc.update_with_lock(
        "5001", CanvasUpdate(base_updated_at=FROZEN, name="renamed")
    )
    assert refs.calls == [] and asset_refs.calls == []


@pytest.mark.asyncio
async def test_asset_refs_failure_does_not_break_save_or_skip_resource_refs():
    """Each mirror owns its own try/except.

    Sharing one would make the SECOND write silently conditional on the first
    succeeding — the "one result's reporting nested inside another's branch"
    shape CLAUDE.md's defensive-patterns section forbids. Both directions are
    asserted below, and neither may cost the user their save.
    """

    class Boom:
        async def replace_for_canvas(self, *a, **k):
            raise RuntimeError("db down")

    refs = FakeRefsRepo()
    svc = CanvasService(
        repository=FakeRepo(),
        refs_repository=refs,
        asset_refs_repository=Boom(),
    )
    result = await svc.update_with_lock(
        "5001",
        CanvasUpdate(base_updated_at=FROZEN, nodes_json=[_asset_node("a", _ASSET_A)]),
    )
    assert result["id"] == "5001"  # the save still returned
    assert len(refs.calls) == 1  # …and the OTHER mirror still ran


@pytest.mark.asyncio
async def test_resource_refs_failure_does_not_skip_asset_refs():
    """The reverse direction — the asset mirror must not be collateral damage
    when the resource mirror is the one that fails."""

    class Boom:
        async def replace_for_canvas(self, *a, **k):
            raise RuntimeError("db down")

    asset_refs = FakeAssetRefsRepo()
    svc = CanvasService(
        repository=FakeRepo(),
        refs_repository=Boom(),
        asset_refs_repository=asset_refs,
    )
    result = await svc.update_with_lock(
        "5001",
        CanvasUpdate(base_updated_at=FROZEN, nodes_json=[_asset_node("a", _ASSET_A)]),
    )
    assert result["id"] == "5001"
    assert len(asset_refs.calls) == 1


@pytest.mark.asyncio
async def test_unreadable_asset_node_is_logged_not_silently_dropped():
    """A node whose asset_id is not a snowflake yields no row. The only thing
    that can say so is this log line — without it the ref is simply absent,
    which is indistinguishable from a canvas that never had the node."""
    from loguru import logger as loguru_logger

    seen: List[str] = []
    handler_id = loguru_logger.add(lambda m: seen.append(str(m)), level="WARNING")
    try:
        asset_refs = FakeAssetRefsRepo()
        svc = CanvasService(
            repository=FakeRepo(),
            refs_repository=FakeRefsRepo(),
            asset_refs_repository=asset_refs,
        )
        await svc.update_with_lock(
            "5001",
            CanvasUpdate(
                base_updated_at=FROZEN,
                nodes_json=[_asset_node("bad", "not-a-snowflake")],
            ),
        )
    finally:
        loguru_logger.remove(handler_id)

    assert asset_refs.calls == [("5001", [])]
    assert any("1 asset node(s) skipped" in line for line in seen), (
        "the skipped count must be reported; a dropped ref that nothing logs "
        f"is a silent no-op. lines={seen}"
    )
