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
    """Carries the REAL return contract: ``replace_for_canvas`` answers an
    ``int`` — how many refs had a loadout dropped as not belonging to their
    asset.

    It used to return ``None``. That is falsy, so ``if disowned:`` behaved
    identically and every wiring test stayed green — which is exactly how the
    warning branch below went untested: a fake that never produces a non-zero
    count cannot reach the only line that reports the drop.
    """

    def __init__(self, disowned: int = 0) -> None:
        self.calls: List[tuple[str, List[Dict[str, Any]]]] = []
        self.disowned = disowned

    async def replace_for_canvas(self, canvas_id: str, refs) -> int:
        self.calls.append((canvas_id, refs))
        return self.disowned


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


@pytest.mark.asyncio
async def test_a_refused_loadout_is_logged_not_silently_dropped():
    """A node pairing an asset with ANOTHER asset's loadout keeps the ref and
    drops the costume half. Only this log line says so.

    The ref is still stored (the asset really is on the canvas), so nothing in
    the mirror, the response, or the canvas itself records that the loadout was
    refused — the row simply has ``loadout_id NULL``, which is also what a node
    that never named a loadout looks like. Deleting the warning changes no
    assertion anywhere else; that is why it needs one of its own.
    """
    from loguru import logger as loguru_logger

    seen: List[str] = []
    handler_id = loguru_logger.add(lambda m: seen.append(str(m)), level="WARNING")
    try:
        asset_refs = FakeAssetRefsRepo(disowned=2)
        svc = CanvasService(
            repository=FakeRepo(),
            refs_repository=FakeRefsRepo(),
            asset_refs_repository=asset_refs,
        )
        await svc.update_with_lock(
            "5001",
            CanvasUpdate(
                base_updated_at=FROZEN,
                nodes_json=[_asset_node("a", _ASSET_A, _LOADOUT)],
            ),
        )
    finally:
        loguru_logger.remove(handler_id)

    assert len(asset_refs.calls) == 1
    assert any("2 asset ref(s) stored without" in line for line in seen), (
        "the refused-loadout count must be reported; the ref survives with a "
        f"NULL loadout and nothing else can tell. lines={seen}"
    )


@pytest.mark.asyncio
async def test_a_clean_save_does_not_warn_about_loadouts():
    """Negative control. A warning that fires on every save is a warning
    nobody reads, and it would make the pin above pass for the wrong reason."""
    from loguru import logger as loguru_logger

    seen: List[str] = []
    handler_id = loguru_logger.add(lambda m: seen.append(str(m)), level="WARNING")
    try:
        svc = CanvasService(
            repository=FakeRepo(),
            refs_repository=FakeRefsRepo(),
            asset_refs_repository=FakeAssetRefsRepo(disowned=0),
        )
        await svc.update_with_lock(
            "5001",
            CanvasUpdate(
                base_updated_at=FROZEN,
                nodes_json=[_asset_node("a", _ASSET_A, _LOADOUT)],
            ),
        )
    finally:
        loguru_logger.remove(handler_id)

    assert not any("stored without" in line for line in seen), seen


class FakeGenRepo:
    def __init__(
        self,
        promoted: Dict[int, int] | None = None,
        boom: bool = False,
        in_scope: int | None = None,
    ):
        self.promoted = promoted or {}
        self.boom = boom
        # The scope the archived resources are actually filed in. A lookup for
        # any OTHER scope comes back empty, like the scoped SQL does.
        self.in_scope = in_scope
        self.calls: List[List[int]] = []
        self.scopes: List[int] = []

    async def promoted_resource_ids(self, gen_ids, *, scope_id: int) -> Dict[int, int]:
        ids = [int(g) for g in gen_ids]
        self.calls.append(ids)
        self.scopes.append(scope_id)
        if self.boom:
            raise RuntimeError("db down")
        if self.in_scope is not None and scope_id != self.in_scope:
            return {}
        return {g: r for g, r in self.promoted.items() if g in ids}


def _archived_update() -> CanvasUpdate:
    return CanvasUpdate(
        base_updated_at=FROZEN,
        nodes_json=[
            {
                "id": "out-1",
                "type": "output",
                "data": {
                    "kind": "image",
                    "preview_url": "/api/v1/generated-media/5/cover",
                    "images": [{"url": "/api/v1/generated-media/6/cover"}],
                },
            },
            {
                "id": "out-2",
                "type": "output",
                "data": {"kind": "image", "resource_id": "222"},
            },
        ],
    )


@pytest.fixture
def canvas_scope(monkeypatch):
    """Stub the canvas → project → team resolution (a DB read otherwise).

    Returns the mutable holder so a test can make the resolution FAIL by
    setting ``["scope"] = None``.
    """
    from app.services.canvas import canvas_service as cs

    holder: Dict[str, Optional[int]] = {"scope": 77}

    async def fake_scope(canvas_id: str) -> Optional[int]:
        return holder["scope"]

    monkeypatch.setattr(cs, "_canvas_scope_id", fake_scope)
    return holder


@pytest.mark.asyncio
async def test_save_counts_archived_generations_shown_by_output_nodes(canvas_scope):
    refs_repo = FakeRefsRepo()
    gen_repo = FakeGenRepo({5: 333})
    svc = CanvasService(
        repository=FakeRepo(),
        refs_repository=refs_repo,
        generated_media_repository=gen_repo,
    )
    await svc.update_with_lock("5001", _archived_update())
    assert gen_repo.calls == [[5, 6]]
    # The lookup is asked about THIS canvas's scope, not an unscoped one.
    assert gen_repo.scopes == [77]
    _, refs = refs_repo.calls[0]
    assert {(r["node_id"], r["resource_id"], r["role"]) for r in refs} == {
        ("out-1", "333", "output"),
        ("out-2", "222", "output"),
    }


@pytest.mark.asyncio
async def test_an_archived_output_outside_the_canvas_scope_is_not_mirrored(
    canvas_scope,
):
    """A canvas member can put ANY generation id in nodes_json.

    If the archived resource behind it is filed in someone else's scope, it is
    not this canvas's to reference — the mirror keeps only the legacy refs and
    the foreign resource id never reaches canvas_resource_refs.
    """
    refs_repo = FakeRefsRepo()
    gen_repo = FakeGenRepo({5: 333}, in_scope=9999)  # filed in another tenant
    svc = CanvasService(
        repository=FakeRepo(),
        refs_repository=refs_repo,
        generated_media_repository=gen_repo,
    )
    await svc.update_with_lock("5001", _archived_update())
    assert gen_repo.scopes == [77]
    _, refs = refs_repo.calls[0]
    assert [(r["node_id"], r["resource_id"]) for r in refs] == [("out-2", "222")]


@pytest.mark.asyncio
async def test_an_unresolved_scope_degrades_to_the_legacy_refs(canvas_scope):
    """Not resolvable is not "allow everything": without a scope there is
    nothing to check the archived resource against, so the lookup is skipped
    and the save carries on with the legacy refs (and says so)."""
    from loguru import logger as loguru_logger

    canvas_scope["scope"] = None
    seen: List[str] = []
    handler_id = loguru_logger.add(lambda m: seen.append(str(m)), level="ERROR")
    refs_repo = FakeRefsRepo()
    gen_repo = FakeGenRepo({5: 333})
    try:
        svc = CanvasService(
            repository=FakeRepo(),
            refs_repository=refs_repo,
            generated_media_repository=gen_repo,
        )
        await svc.update_with_lock("5001", _archived_update())
    finally:
        loguru_logger.remove(handler_id)

    assert gen_repo.calls == []
    _, refs = refs_repo.calls[0]
    assert [(r["node_id"], r["resource_id"]) for r in refs] == [("out-2", "222")]
    assert any("scope" in line for line in seen), seen


@pytest.mark.asyncio
async def test_save_without_generation_urls_skips_the_lookup():
    refs_repo = FakeRefsRepo()
    gen_repo = FakeGenRepo({5: 333})
    svc = CanvasService(
        repository=FakeRepo(),
        refs_repository=refs_repo,
        generated_media_repository=gen_repo,
    )
    upd = CanvasUpdate(
        base_updated_at=FROZEN,
        nodes_json=[{"id": "out-2", "type": "output", "data": {"resource_id": "222"}}],
    )
    await svc.update_with_lock("5001", upd)
    assert gen_repo.calls == []
    assert [r["resource_id"] for r in refs_repo.calls[0][1]] == ["222"]


@pytest.mark.asyncio
async def test_archived_lookup_failure_still_writes_the_legacy_refs(canvas_scope):
    refs_repo = FakeRefsRepo()
    svc = CanvasService(
        repository=FakeRepo(),
        refs_repository=refs_repo,
        generated_media_repository=FakeGenRepo(boom=True),
    )
    await svc.update_with_lock("5001", _archived_update())
    _, refs = refs_repo.calls[0]
    assert [(r["node_id"], r["resource_id"]) for r in refs] == [("out-2", "222")]
