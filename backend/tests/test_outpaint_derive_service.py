"""Tests for the outpaint derive service (Phase 3 Day 13 + 6f AI path)."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import AsyncMock, patch

import pytest
from PIL import Image

from app.services.canvas.image_outpaint import Padding
from app.services.canvas.nous_center_runner import NousCenterNotConfigured
from app.services.canvas.outpaint_derive_service import (
    OutpaintDeriveError,
    derive_outpaint_resource,
)


def _png_bytes(w: int = 100, h: int = 50) -> bytes:
    img = Image.new("RGB", (w, h), (10, 200, 50))
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


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
        row["id"] = f"777700000000000{len(self.created)}"
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


def _source_repo(root: Path) -> FakeRepo:
    rel = "teams/scope-1/uploads/src/v1/orig.png"
    src = root / rel
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(_png_bytes())
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


@pytest.mark.asyncio
async def test_happy_path_persists_extended_resource(
    tmp_download_root: Path,
) -> None:
    repo = _source_repo(tmp_download_root)
    result = await derive_outpaint_resource(
        source_resource_id="111",
        user_id="user-A",
        padding=Padding(left=0.5, top=0, right=0.5, bottom=0),
        prompt="extend the meadow",
        repo=repo,
    )
    assert result.resource["filename"] == "outpaint-orig.png"
    assert result.resource["mime_type"] == "image/png"
    target = tmp_download_root / result.resource["file_path"]
    decoded = Image.open(target)
    assert decoded.size == (200, 50)
    assert len(repo.versions) == 1
    assert repo.items[0]["scope_id"] == "scope-1"


@pytest.mark.asyncio
async def test_zero_padding_raises_400_before_write(
    tmp_download_root: Path,
) -> None:
    repo = _source_repo(tmp_download_root)
    with pytest.raises(OutpaintDeriveError) as exc:
        await derive_outpaint_resource(
            source_resource_id="111",
            user_id="u",
            padding=Padding(0, 0, 0, 0),
            repo=repo,
        )
    assert exc.value.status_code == 400
    assert repo.created == []


@pytest.mark.asyncio
async def test_missing_source_raises_404() -> None:
    repo = FakeRepo(source=None, item=None)
    with pytest.raises(OutpaintDeriveError) as exc:
        await derive_outpaint_resource(
            source_resource_id="missing",
            user_id="u",
            padding=Padding(0.5, 0, 0, 0),
            repo=repo,
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_filename_override(tmp_download_root: Path) -> None:
    repo = _source_repo(tmp_download_root)
    result = await derive_outpaint_resource(
        source_resource_id="111",
        user_id="u",
        padding=Padding(0, 0.5, 0, 0),
        filename_override="wide.png",
        repo=repo,
    )
    assert result.resource["filename"] == "wide.png"


# ---------------------------------------------------------------------------
# 6f — AI generative path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ai_mode_calls_nous_and_persists_bytes(
    tmp_download_root: Path,
) -> None:
    """AI mode with a configured nous slug → run_outpaint_via_nous is called
    and its bytes (not the deterministic blur fill) are persisted."""
    repo = _source_repo(tmp_download_root)
    # Distinct size so we can tell AI bytes from deterministic fill (200x50).
    nous_bytes = _png_bytes(300, 150)

    with patch(
        "app.services.canvas.outpaint_derive_service.run_outpaint_via_nous",
        new_callable=AsyncMock,
        return_value=nous_bytes,
    ) as mock_nous:
        result = await derive_outpaint_resource(
            source_resource_id="111",
            user_id="user-A",
            padding=Padding(left=0.5, top=0, right=0.5, bottom=0),
            prompt="extend the meadow scenery",
            mode="ai",
            repo=repo,
        )

    mock_nous.assert_called_once()
    target = tmp_download_root / result.resource["file_path"]
    decoded = Image.open(target)
    # Must be the nous bytes (300x150), not the deterministic fill (200x50).
    assert decoded.size == (300, 150)
    assert len(repo.versions) == 1
    assert repo.items[0]["scope_id"] == "scope-1"


@pytest.mark.asyncio
async def test_ai_mode_falls_back_when_nous_not_configured(
    tmp_download_root: Path,
) -> None:
    """AI mode + NousCenterNotConfigured → silent deterministic fallback, never raises."""
    repo = _source_repo(tmp_download_root)

    with patch(
        "app.services.canvas.outpaint_derive_service.run_outpaint_via_nous",
        new_callable=AsyncMock,
        side_effect=NousCenterNotConfigured("NOUS_CENTER_OUTPAINT_SLUG not configured"),
    ):
        result = await derive_outpaint_resource(
            source_resource_id="111",
            user_id="user-A",
            padding=Padding(left=0.5, top=0, right=0.5, bottom=0),
            prompt="extend the meadow",
            mode="ai",
            repo=repo,
        )

    # Deterministic blur fill: 100px wide + 50 % each side → 200px.
    target = tmp_download_root / result.resource["file_path"]
    decoded = Image.open(target)
    assert decoded.size == (200, 50)
    assert len(repo.versions) == 1  # resource still persisted


@pytest.mark.asyncio
async def test_deterministic_mode_never_calls_nous(
    tmp_download_root: Path,
) -> None:
    """Explicit deterministic mode → run_outpaint_via_nous is not invoked,
    even when a prompt is present."""
    repo = _source_repo(tmp_download_root)

    with patch(
        "app.services.canvas.outpaint_derive_service.run_outpaint_via_nous",
        new_callable=AsyncMock,
    ) as mock_nous:
        result = await derive_outpaint_resource(
            source_resource_id="111",
            user_id="user-A",
            padding=Padding(left=0.5, top=0, right=0.5, bottom=0),
            prompt="extend the meadow",
            mode="deterministic",
            repo=repo,
        )

    mock_nous.assert_not_called()
    target = tmp_download_root / result.resource["file_path"]
    decoded = Image.open(target)
    assert decoded.size == (200, 50)
