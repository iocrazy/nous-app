"""Tests for the crop-derive service (Phase 3 Day 5)."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List

import pytest
from PIL import Image

from app.services.canvas.crop_derive_service import (
    CropDeriveError,
    derive_crop_resource,
)
from app.services.canvas.image_crop import CropRegion


def _png_bytes(w: int = 200, h: int = 100) -> bytes:
    img = Image.new("RGB", (w, h), (10, 200, 50))
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class FakeRepo:
    """In-memory stand-in for ``ResourcesRepository`` — records every
    call so the test can assert ordering + payload shape."""

    def __init__(
        self,
        *,
        source: Dict[str, Any] | None = None,
        item: Dict[str, Any] | None = None,
    ):
        self.source = source
        self.item = item
        self.created_resource: Dict[str, Any] | None = None
        self.updated_resource: Dict[str, Any] | None = None
        self.versions: List[Dict[str, Any]] = []
        self.items: List[Dict[str, Any]] = []

    async def get_resource_by_id(self, resource_id: str):
        if self.source and str(self.source.get("id")) == resource_id:
            return self.source
        return None

    async def get_first_resource_item(self, resource_id: str):
        return self.item

    async def create_resource(self, data: Dict[str, Any]):
        # Mimic the DB by minting a snowflake-shaped id.
        row = dict(data)
        row["id"] = "9999000000000001"
        self.created_resource = row
        return row

    async def update_resource(self, resource_id: str, data: Dict[str, Any]):
        assert self.created_resource is not None
        self.updated_resource = {**self.created_resource, **data}
        return self.updated_resource

    async def create_version(self, data: Dict[str, Any]):
        self.versions.append(dict(data))
        return data

    async def create_resource_item(self, data: Dict[str, Any]):
        self.items.append(dict(data))
        return data


@pytest.fixture
def tmp_download_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Re-point ``settings.DOWNLOAD_PATH`` at a tmp dir so the service
    writes there. Yield the root so the test can stage source bytes."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "DOWNLOAD_PATH", str(tmp_path))
    return tmp_path


def _stage_source_image(root: Path, rel_path: str) -> bytes:
    """Write a source PNG to ``root / rel_path`` and return its bytes."""
    src = root / rel_path
    src.parent.mkdir(parents=True, exist_ok=True)
    data = _png_bytes(200, 100)
    src.write_bytes(data)
    return data


# ============================================================
# Happy path
# ============================================================


@pytest.mark.asyncio
async def test_happy_path_persists_new_resource(tmp_download_root: Path) -> None:
    rel = "teams/scope-1/uploads/src-id/v1/orig.png"
    _stage_source_image(tmp_download_root, rel)
    repo = FakeRepo(
        source={
            "id": "111",
            "file_path": rel,
            "file_type": "image",
            "mime_type": "image/png",
            "filename": "orig.png",
        },
        item={"scope_id": "scope-1", "folder_id": None, "library_id": None},
    )

    result = await derive_crop_resource(
        source_resource_id="111",
        user_id="user-A",
        region=CropRegion(0.0, 0.0, 0.5, 1.0),
        repo=repo,
    )

    # 1. The returned resource carries the file_path of the persisted
    # cropped bytes.
    assert result.resource["file_path"].startswith("teams/scope-1/derived/")
    assert result.resource["file_path"].endswith("/v1/crop-orig.png")

    # 2. The file actually exists on disk and decodes to half the width.
    target = tmp_download_root / result.resource["file_path"]
    assert target.exists()
    decoded = Image.open(target)
    assert decoded.size == (100, 100)

    # 3. The new resource row has source_type='derived' and inherits
    # mime_type from the source.
    created = repo.created_resource
    assert created is not None
    assert created["source_type"] == "derived"
    assert created["mime_type"] == "image/png"
    assert created["file_type"] == "image"

    # 4. Version + resource_item rows landed under the new resource id
    # in the same scope as the source.
    assert len(repo.versions) == 1
    assert repo.versions[0]["version_number"] == 1
    assert repo.versions[0]["resource_id"] == str(created["id"])
    assert len(repo.items) == 1
    assert repo.items[0]["scope_id"] == "scope-1"


@pytest.mark.asyncio
async def test_filename_override_replaces_default(tmp_download_root: Path) -> None:
    rel = "teams/s2/uploads/src/v1/photo.png"
    _stage_source_image(tmp_download_root, rel)
    repo = FakeRepo(
        source={
            "id": "222",
            "file_path": rel,
            "file_type": "image",
            "mime_type": "image/png",
            "filename": "photo.png",
        },
        item={"scope_id": "s2", "folder_id": "folder-7", "library_id": None},
    )
    result = await derive_crop_resource(
        source_resource_id="222",
        user_id="user-B",
        region=CropRegion(0.0, 0.0, 1.0, 1.0),
        filename_override="hero.png",
        repo=repo,
    )
    assert result.resource["file_path"].endswith("/v1/hero.png")
    # folder_id propagates through the new resource_item.
    assert repo.items[0]["folder_id"] == "folder-7"


# ============================================================
# Error paths
# ============================================================


@pytest.mark.asyncio
async def test_missing_source_raises_404() -> None:
    repo = FakeRepo(source=None, item=None)
    with pytest.raises(CropDeriveError) as exc:
        await derive_crop_resource(
            source_resource_id="missing",
            user_id="u",
            region=CropRegion(0.0, 0.0, 1.0, 1.0),
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
    with pytest.raises(CropDeriveError) as exc:
        await derive_crop_resource(
            source_resource_id="10",
            user_id="u",
            region=CropRegion(0.0, 0.0, 1.0, 1.0),
            repo=repo,
        )
    assert exc.value.status_code == 400
    assert "image" in exc.value.detail


@pytest.mark.asyncio
async def test_missing_scope_item_raises_400(tmp_download_root: Path) -> None:
    rel = "teams/s/uploads/src/v1/a.png"
    _stage_source_image(tmp_download_root, rel)
    repo = FakeRepo(
        source={
            "id": "1",
            "file_path": rel,
            "file_type": "image",
            "mime_type": "image/png",
            "filename": "a.png",
        },
        item=None,
    )
    with pytest.raises(CropDeriveError) as exc:
        await derive_crop_resource(
            source_resource_id="1",
            user_id="u",
            region=CropRegion(0.0, 0.0, 1.0, 1.0),
            repo=repo,
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_source_file_missing_on_disk_raises_404(
    tmp_download_root: Path,
) -> None:
    # `file_path` points at a file we did NOT stage.
    repo = FakeRepo(
        source={
            "id": "1",
            "file_path": "teams/s/uploads/src/v1/nope.png",
            "file_type": "image",
            "mime_type": "image/png",
            "filename": "nope.png",
        },
        item={"scope_id": "s", "folder_id": None, "library_id": None},
    )
    with pytest.raises(CropDeriveError) as exc:
        await derive_crop_resource(
            source_resource_id="1",
            user_id="u",
            region=CropRegion(0.0, 0.0, 1.0, 1.0),
            repo=repo,
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_traversal_in_file_path_is_blocked(
    tmp_download_root: Path,
) -> None:
    # A malformed file_path tries to escape the download root.
    repo = FakeRepo(
        source={
            "id": "1",
            "file_path": "../../../etc/passwd",
            "file_type": "image",
            "mime_type": "image/png",
            "filename": "x.png",
        },
        item={"scope_id": "s", "folder_id": None, "library_id": None},
    )
    with pytest.raises(CropDeriveError) as exc:
        await derive_crop_resource(
            source_resource_id="1",
            user_id="u",
            region=CropRegion(0.0, 0.0, 1.0, 1.0),
            repo=repo,
        )
    # Either path-traversal rejection or missing-file, both are safe.
    assert exc.value.status_code in (400, 404)
