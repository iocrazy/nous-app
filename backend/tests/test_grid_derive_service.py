"""Tests for the grid-derive service (Phase 3 Day 7)."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List

import pytest
from PIL import Image

from app.services.canvas.grid_derive_service import (
    GridDeriveError,
    derive_grid_resources,
)


def _png_bytes(w: int = 200, h: int = 100) -> bytes:
    img = Image.new("RGB", (w, h), (10, 200, 50))
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class FakeRepo:
    """In-memory stand-in for ``ResourcesRepository`` supporting the
    multiple create_resource calls a grid derive performs."""

    def __init__(
        self,
        *,
        source: Dict[str, Any] | None = None,
        item: Dict[str, Any] | None = None,
    ):
        self.source = source
        self.item = item
        self.created: List[Dict[str, Any]] = []
        self.updated: Dict[str, Dict[str, Any]] = {}
        self.versions: List[Dict[str, Any]] = []
        self.items: List[Dict[str, Any]] = []

    async def get_resource_by_id(self, resource_id: str):
        if self.source and str(self.source.get("id")) == resource_id:
            return self.source
        return None

    async def get_first_resource_item(self, resource_id: str):
        return self.item

    async def create_resource(self, data: Dict[str, Any]):
        row = dict(data)
        row["id"] = f"99990000000000{len(self.created):02d}"
        self.created.append(row)
        return row

    async def update_resource(self, resource_id: str, data: Dict[str, Any]):
        base = next(r for r in self.created if r["id"] == resource_id)
        merged = {**base, **data}
        self.updated[resource_id] = merged
        return merged

    async def create_version(self, data: Dict[str, Any]):
        self.versions.append(dict(data))
        return data

    async def create_resource_item(self, data: Dict[str, Any]):
        self.items.append(dict(data))
        return data


@pytest.fixture
def tmp_download_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from app.core.config import settings

    monkeypatch.setattr(settings, "DOWNLOAD_PATH", str(tmp_path))
    return tmp_path


def _stage_source_image(root: Path, rel_path: str, w: int = 200, h: int = 100) -> None:
    src = root / rel_path
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(_png_bytes(w, h))


def _source_repo(rel: str) -> FakeRepo:
    return FakeRepo(
        source={
            "id": "111",
            "file_path": rel,
            "file_type": "image",
            "mime_type": "image/png",
            "filename": "orig.png",
        },
        item={"scope_id": "scope-1", "folder_id": None, "library_id": None},
    )


# ============================================================
# Happy path
# ============================================================


@pytest.mark.asyncio
async def test_quadrant_split_persists_four_resources(
    tmp_download_root: Path,
) -> None:
    rel = "teams/scope-1/uploads/src/v1/orig.png"
    _stage_source_image(tmp_download_root, rel, 200, 100)
    repo = _source_repo(rel)

    result = await derive_grid_resources(
        source_resource_id="111",
        user_id="user-A",
        xs=[0.5],
        ys=[0.5],
        repo=repo,
    )

    assert result.rows == 2
    assert result.cols == 2
    assert len(result.tiles) == 4
    # Row-major ordering with 1-based naming.
    assert [(t.row, t.col) for t in result.tiles] == [
        (0, 0),
        (0, 1),
        (1, 0),
        (1, 1),
    ]
    assert result.tiles[0].resource["filename"] == "grid-r1c1-orig.png"
    assert result.tiles[3].resource["filename"] == "grid-r2c2-orig.png"

    # Every tile landed on disk with the right pixel size (100x50 each).
    for tile in result.tiles:
        target = tmp_download_root / tile.resource["file_path"]
        assert target.exists()
        assert Image.open(target).size == (100, 50)

    # Version + scope-link rows per tile, all in the source scope.
    assert len(repo.versions) == 4
    assert len(repo.items) == 4
    assert {i["scope_id"] for i in repo.items} == {"scope-1"}
    assert all(r["source_type"] == "derived" for r in repo.created)


@pytest.mark.asyncio
async def test_filename_prefix_override(tmp_download_root: Path) -> None:
    rel = "teams/scope-1/uploads/src/v1/orig.png"
    _stage_source_image(tmp_download_root, rel)
    repo = _source_repo(rel)

    result = await derive_grid_resources(
        source_resource_id="111",
        user_id="user-A",
        xs=[0.5],
        ys=[],
        filename_prefix="shot",
        repo=repo,
    )
    assert result.rows == 1
    assert result.cols == 2
    assert result.tiles[0].resource["filename"] == "shot-r1c1-orig.png"
    assert result.tiles[1].resource["filename"] == "shot-r1c2-orig.png"


# ============================================================
# Error paths
# ============================================================


@pytest.mark.asyncio
async def test_invalid_lines_raise_400_before_any_write(
    tmp_download_root: Path,
) -> None:
    rel = "teams/scope-1/uploads/src/v1/orig.png"
    _stage_source_image(tmp_download_root, rel)
    repo = _source_repo(rel)

    with pytest.raises(GridDeriveError) as exc:
        await derive_grid_resources(
            source_resource_id="111",
            user_id="user-A",
            xs=[0.7, 0.3],  # unsorted
            ys=[],
            repo=repo,
        )
    assert exc.value.status_code == 400
    assert repo.created == []


@pytest.mark.asyncio
async def test_no_lines_raise_400(tmp_download_root: Path) -> None:
    rel = "teams/scope-1/uploads/src/v1/orig.png"
    _stage_source_image(tmp_download_root, rel)
    repo = _source_repo(rel)

    with pytest.raises(GridDeriveError) as exc:
        await derive_grid_resources(
            source_resource_id="111",
            user_id="user-A",
            xs=[],
            ys=[],
            repo=repo,
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_missing_source_raises_404() -> None:
    repo = FakeRepo(source=None, item=None)
    with pytest.raises(GridDeriveError) as exc:
        await derive_grid_resources(
            source_resource_id="missing",
            user_id="u",
            xs=[0.5],
            ys=[],
            repo=repo,
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_non_image_source_raises_400() -> None:
    repo = FakeRepo(
        source={
            "id": "10",
            "file_path": "x.mp4",
            "file_type": "video",
            "mime_type": "video/mp4",
            "filename": "x.mp4",
        },
        item={"scope_id": "s", "folder_id": None, "library_id": None},
    )
    with pytest.raises(GridDeriveError) as exc:
        await derive_grid_resources(
            source_resource_id="10",
            user_id="u",
            xs=[0.5],
            ys=[],
            repo=repo,
        )
    assert exc.value.status_code == 400
