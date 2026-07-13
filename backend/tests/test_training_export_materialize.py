"""Training-set export includes sb:// rows via materialize().

Task 2.4c: the export used to probe ``Path(DOWNLOAD_PATH)/file_path`` with
``is_file()`` — always False for an sb:// row, so object-store originals were
silently dropped from the zip. Now every file routes through materialize();
a failed materialization is still skipped, but logged with the file_path
shape (object-store vs filesystem) so storage-down is distinguishable from
a genuinely missing file.
"""

from __future__ import annotations

import io
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.resources_ai_router import export_training_set
from app.schemas.resources import TrainingSetExportRequest

pytestmark = pytest.mark.asyncio

_SB_OK = "sb://library/t42/ab/cd/aaaa1111.png"
_SB_BROKEN = "sb://library/t42/ef/00/bbbb2222.png"


def _resource(rid: str, file_path: str, prompt: str = "") -> dict:
    return {
        "id": rid,
        "file_type": "image",
        "file_path": file_path,
        "filename": f"img-{rid}.png",
        "gen_prompt": prompt,
        "gen_prompt_zh": "",
    }


async def _collect_zip(response) -> zipfile.ZipFile:
    chunks = [chunk async for chunk in response.body_iterator]
    return zipfile.ZipFile(io.BytesIO(b"".join(chunks)))


async def test_export_includes_sb_row_and_skips_failed_materialize(tmp_path):
    """sb row with a working materialize lands in the zip (with its caption
    sidecar); an sb row whose materialize raises is skipped with a
    shape-tagged warning instead of failing the whole export."""
    local = tmp_path / "materialized.png"
    local.write_bytes(b"png-bytes")

    rows = {
        "1": _resource("1", _SB_OK, prompt="a good prompt"),
        "2": _resource("2", _SB_BROKEN),
    }

    @asynccontextmanager
    async def fake_materialize(file_path: str):
        if file_path == _SB_BROKEN:
            raise RuntimeError("storage-api unreachable")
        yield local

    repo = MagicMock()
    repo.get_resource_by_id = AsyncMock(side_effect=lambda rid: rows.get(rid))
    warn = MagicMock()

    with (
        patch(
            "app.api.resources_ai_router.ResourcesRepository",
            return_value=repo,
        ),
        patch(
            "app.api.media_permissions.check_media_access",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "app.services.library.media_storage.materialize",
            new=fake_materialize,
        ),
        patch("app.api.resources_ai_router.logger.warning", warn),
    ):
        response = await export_training_set(
            TrainingSetExportRequest(resource_ids=["1", "2"], lang="en"),
            SimpleNamespace(user_id="u1"),
        )

    zf = await _collect_zip(response)
    names = set(zf.namelist())
    assert "img-1.png" in names, "sb row must be included via materialize"
    assert "img-1.txt" in names and zf.read("img-1.txt") == b"a good prompt"
    assert not any(n.startswith("img-2") for n in names), "broken row skipped"
    # The skip log tags the file_path shape so storage-down (object-store)
    # is distinguishable from a plain missing filesystem file.
    warn.assert_called_once()
    assert "object-store" in warn.call_args[0][0]
    assert _SB_BROKEN in warn.call_args[0][0]


async def test_export_fs_row_missing_file_logs_filesystem_shape(tmp_path, monkeypatch):
    """Legacy fs row whose file is gone: still skipped (as today) and the
    log line names the filesystem shape."""
    from app.core.config import settings as app_settings

    monkeypatch.setattr(app_settings, "DOWNLOAD_PATH", str(tmp_path))

    fs_path = "teams/9/uploads/3/v1/gone.png"
    rows = {"3": _resource("3", fs_path)}

    repo = MagicMock()
    repo.get_resource_by_id = AsyncMock(side_effect=lambda rid: rows.get(rid))
    warn = MagicMock()

    with (
        patch(
            "app.api.resources_ai_router.ResourcesRepository",
            return_value=repo,
        ),
        patch(
            "app.api.media_permissions.check_media_access",
            new=AsyncMock(return_value=True),
        ),
        patch("app.api.resources_ai_router.logger.warning", warn),
    ):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            await export_training_set(
                TrainingSetExportRequest(resource_ids=["3"], lang="en"),
                SimpleNamespace(user_id="u1"),
            )

    assert exc.value.status_code == 404  # zero exportable rows
    warn.assert_called_once()
    assert "file missing on disk" in warn.call_args[0][0]
    assert fs_path in warn.call_args[0][0]


async def test_export_fs_row_present_still_included(tmp_path, monkeypatch):
    """Legacy fs row with the file on disk: byte-identical behavior — the
    real materialize yields the DOWNLOAD_PATH-joined path and the file lands
    in the zip."""
    from app.core.config import settings as app_settings

    monkeypatch.setattr(app_settings, "DOWNLOAD_PATH", str(tmp_path))

    fs_path = "teams/9/uploads/4/v1/here.png"
    abs_path = Path(tmp_path) / fs_path
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_bytes(b"legacy-bytes")

    rows = {"4": _resource("4", fs_path, prompt="fs prompt")}
    repo = MagicMock()
    repo.get_resource_by_id = AsyncMock(side_effect=lambda rid: rows.get(rid))

    with (
        patch(
            "app.api.resources_ai_router.ResourcesRepository",
            return_value=repo,
        ),
        patch(
            "app.api.media_permissions.check_media_access",
            new=AsyncMock(return_value=True),
        ),
    ):
        response = await export_training_set(
            TrainingSetExportRequest(resource_ids=["4"], lang="en"),
            SimpleNamespace(user_id="u1"),
        )

    zf = await _collect_zip(response)
    assert zf.read("img-4.png") == b"legacy-bytes"
    assert zf.read("img-4.txt") == b"fs prompt"
