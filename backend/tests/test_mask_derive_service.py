"""Tests for the mask-cutout derive service (Phase 3 Day 10)."""

from __future__ import annotations

import base64
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List

import pytest
from PIL import Image

from app.services.canvas.mask_derive_service import (
    MaskDeriveError,
    derive_mask_cutout,
)


def _png_bytes(w: int = 100, h: int = 50) -> bytes:
    img = Image.new("RGB", (w, h), (10, 200, 50))
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _mask_b64(w: int = 100, h: int = 50) -> str:
    """White left half, black right half."""
    img = Image.new("L", (w, h), 0)
    for y in range(h):
        for x in range(w // 2):
            img.putpixel((x, y), 255)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


class FakeRepo:
    def __init__(
        self,
        *,
        source: Dict[str, Any] | None = None,
        item: Dict[str, Any] | None = None,
    ):
        self.source = source
        self.item = item
        self.created: List[Dict[str, Any]] = []
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
        row["id"] = f"888800000000000{len(self.created)}"
        self.created.append(row)
        return row

    async def update_resource(self, resource_id: str, data: Dict[str, Any]):
        base = next(r for r in self.created if r["id"] == resource_id)
        return {**base, **data}

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


def _source_repo(root: Path, *, filename: str = "orig.png") -> FakeRepo:
    rel = f"teams/scope-1/uploads/src/v1/{filename}"
    src = root / rel
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(_png_bytes())
    return FakeRepo(
        source={
            "id": "111",
            "file_path": rel,
            "file_type": "image",
            "mime_type": "image/png",
            "filename": filename,
        },
        item={"scope_id": "scope-1", "folder_id": None, "library_id": None},
    )


@pytest.mark.asyncio
async def test_happy_path_persists_rgba_png_cutout(tmp_download_root: Path) -> None:
    repo = _source_repo(tmp_download_root)
    result = await derive_mask_cutout(
        source_resource_id="111",
        user_id="user-A",
        mask_png_base64=_mask_b64(),
        repo=repo,
    )
    assert result.resource["filename"] == "cutout-orig.png"
    assert result.resource["mime_type"] == "image/png"
    target = tmp_download_root / result.resource["file_path"]
    decoded = Image.open(target)
    assert decoded.mode == "RGBA"
    assert decoded.getpixel((25, 25))[3] == 255
    assert decoded.getpixel((75, 25))[3] == 0
    assert len(repo.versions) == 1
    assert repo.items[0]["scope_id"] == "scope-1"


@pytest.mark.asyncio
async def test_jpg_source_filename_extension_becomes_png(
    tmp_download_root: Path,
) -> None:
    repo = _source_repo(tmp_download_root, filename="photo.jpg")
    result = await derive_mask_cutout(
        source_resource_id="111",
        user_id="u",
        mask_png_base64=_mask_b64(),
        repo=repo,
    )
    assert result.resource["filename"] == "cutout-photo.png"


@pytest.mark.asyncio
async def test_invalid_base64_raises_400(tmp_download_root: Path) -> None:
    repo = _source_repo(tmp_download_root)
    with pytest.raises(MaskDeriveError) as exc:
        await derive_mask_cutout(
            source_resource_id="111",
            user_id="u",
            mask_png_base64="!!!not-base64!!!",
            repo=repo,
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_empty_mask_raises_400_before_any_write(
    tmp_download_root: Path,
) -> None:
    # All-black mask decodes fine but keeps zero pixels.
    img = Image.new("L", (100, 50), 0)
    buf = BytesIO()
    img.save(buf, format="PNG")
    repo = _source_repo(tmp_download_root)
    with pytest.raises(MaskDeriveError) as exc:
        await derive_mask_cutout(
            source_resource_id="111",
            user_id="u",
            mask_png_base64=base64.b64encode(buf.getvalue()).decode("ascii"),
            repo=repo,
        )
    assert exc.value.status_code == 400
    assert repo.created == []


@pytest.mark.asyncio
async def test_missing_source_raises_404() -> None:
    repo = FakeRepo(source=None, item=None)
    with pytest.raises(MaskDeriveError) as exc:
        await derive_mask_cutout(
            source_resource_id="missing",
            user_id="u",
            mask_png_base64=_mask_b64(),
            repo=repo,
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_oversized_mask_raises_413(tmp_download_root: Path) -> None:
    repo = _source_repo(tmp_download_root)
    big = base64.b64encode(b"x" * (8 * 1024 * 1024 + 1)).decode("ascii")
    with pytest.raises(MaskDeriveError) as exc:
        await derive_mask_cutout(
            source_resource_id="111",
            user_id="u",
            mask_png_base64=big,
            repo=repo,
        )
    assert exc.value.status_code == 413
