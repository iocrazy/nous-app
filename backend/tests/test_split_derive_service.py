"""Tests for the grid-split derive service (mirrors crop/grid derive)."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List

import pytest
from PIL import Image

from app.services.canvas.split_derive_service import (
    SplitDeriveError,
    derive_split_resource,
)


def _png_bytes(w: int = 200, h: int = 100) -> bytes:
    img = Image.new("RGB", (w, h), (10, 200, 50))
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class FakeRepo:
    """In-memory stand-in for ``ResourcesRepository`` supporting the
    multiple create_resource calls a split derive performs."""

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
async def test_2x2_split_persists_four_frames_in_source_scope(
    tmp_download_root: Path,
) -> None:
    rel = "teams/scope-1/uploads/src/v1/orig.png"
    _stage_source_image(tmp_download_root, rel, 200, 100)
    repo = _source_repo(rel)

    result = await derive_split_resource(
        source_resource_id="111",
        user_id="user-A",
        rows=2,
        cols=2,
        repo=repo,
    )

    # N = rows*cols frames, row-major, carrying row/col/index.
    assert len(result.frames) == 4
    assert [(f["row"], f["col"], f["index"]) for f in result.frames] == [
        (0, 0, 0),
        (0, 1, 1),
        (1, 0, 2),
        (1, 1, 3),
    ]

    # persist_derived_image was invoked N times: N resource rows, N
    # versions, N scope links — ALL in the source's scope_id so the
    # frames land in Project Assets (工程资产).
    assert len(repo.created) == 4
    assert len(repo.versions) == 4
    assert len(repo.items) == 4
    assert {i["scope_id"] for i in repo.items} == {"scope-1"}
    assert all(r["source_type"] == "derived" for r in repo.created)

    # Frames are real resources: each blob is on disk at the right size.
    for frame in result.frames:
        target = tmp_download_root / frame["file_path"]
        assert target.exists()
        assert Image.open(target).size == (100, 50)

    # Filenames are unique per frame.
    names = [f["filename"] for f in result.frames]
    assert names[0] == "split-0x0-orig.png"
    assert names[3] == "split-1x1-orig.png"
    assert len(set(names)) == 4


@pytest.mark.asyncio
async def test_folder_and_library_propagate(tmp_download_root: Path) -> None:
    rel = "teams/scope-1/uploads/src/v1/orig.png"
    _stage_source_image(tmp_download_root, rel)
    repo = FakeRepo(
        source={
            "id": "111",
            "file_path": rel,
            "file_type": "image",
            "mime_type": "image/png",
            "filename": "orig.png",
        },
        item={"scope_id": "scope-1", "folder_id": "folder-7", "library_id": "lib-3"},
    )

    result = await derive_split_resource(
        source_resource_id="111",
        user_id="u",
        rows=1,
        cols=3,
        repo=repo,
    )

    assert len(result.frames) == 3
    assert {i["folder_id"] for i in repo.items} == {"folder-7"}
    assert {i["library_id"] for i in repo.items} == {"lib-3"}


# ============================================================
# Error paths
# ============================================================


@pytest.mark.asyncio
async def test_missing_source_raises_404() -> None:
    repo = FakeRepo(source=None, item=None)
    with pytest.raises(SplitDeriveError) as exc:
        await derive_split_resource(
            source_resource_id="missing",
            user_id="u",
            rows=2,
            cols=2,
            repo=repo,
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_out_of_range_grid_raises_400_before_any_write(
    tmp_download_root: Path,
) -> None:
    rel = "teams/scope-1/uploads/src/v1/orig.png"
    _stage_source_image(tmp_download_root, rel)
    repo = _source_repo(rel)
    with pytest.raises(SplitDeriveError) as exc:
        await derive_split_resource(
            source_resource_id="111",
            user_id="u",
            rows=99,
            cols=1,
            repo=repo,
        )
    assert exc.value.status_code == 400
    # No partial writes when the grid is rejected.
    assert repo.created == []


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
    with pytest.raises(SplitDeriveError) as exc:
        await derive_split_resource(
            source_resource_id="10",
            user_id="u",
            rows=2,
            cols=2,
            repo=repo,
        )
    assert exc.value.status_code == 400
