"""A repository write that matched no row answers a typed 404.

``update_resource`` / ``update_folder`` / ``update_version`` /
``update_resource_item`` return ``{}`` when the row vanished between the
route's existence check and the write, or the scope hides it. That used to be
``200 {"data": {}}``; with the declared row models it would have been a 500.
It is now ``404`` with ``details.code == "not_found_or_out_of_scope"`` (see
``app/api/row_guard.py``). The body is the production ``ErrorResponse``
shell, so the code is read from ``details``.
"""

from __future__ import annotations

import pytest

from app.api.row_guard import NOT_FOUND_OR_OUT_OF_SCOPE
from tests.api import resources_wire_rows
from tests.api.resources_wire_rows import LRC, Fake, folder_row, resource_row
from tests.api.test_resources_crud_wire import RID, _use_repo, _use_service
from tests.api.test_resources_folders_versions_wire import (
    FID,
    _smart_folder,
    _use_folders,
    _use_versions,
)

client = resources_wire_rows.client
resources_http_overrides = resources_wire_rows.resources_http_overrides


def _assert_typed_404(resp) -> None:
    assert resp.status_code == 404, resp.text
    assert resp.json()["details"]["code"] == NOT_FOUND_OR_OUT_OF_SCOPE


@pytest.mark.asyncio
async def test_patch_resource(monkeypatch, client) -> None:
    _use_repo(monkeypatch, get_resource_by_id=resource_row(), update_resource={})
    resp = await client.patch(f"/api/v1/resources/{RID}", json={"notes": "x"})
    _assert_typed_404(resp)


@pytest.mark.asyncio
async def test_upload_cover(monkeypatch, tmp_path, client) -> None:
    import app.services.library.transit_upload as transit
    from app.core.config import settings

    monkeypatch.setattr(settings, "DOWNLOAD_PATH", str(tmp_path))

    async def _transit(*, user_id, local_path, relative_path, mime):
        return relative_path

    monkeypatch.setattr(transit, "upload_transit_file", _transit)
    _use_repo(
        monkeypatch,
        get_resource_by_id=resource_row(file_path="teams/42/uploads/1/v1/a.png"),
        update_resource={},
    )
    resp = await client.post(
        f"/api/v1/resources/{RID}/cover",
        files={"file": ("c.png", b"png-bytes", "image/png")},
    )
    _assert_typed_404(resp)


@pytest.mark.asyncio
async def test_upload_lyrics(monkeypatch, client) -> None:
    _use_repo(monkeypatch, get_resource_by_id=resource_row(), update_resource={})
    resp = await client.post(
        f"/api/v1/resources/{RID}/lyrics",
        files={"file": ("song.lrc", LRC.encode(), "text/plain")},
    )
    _assert_typed_404(resp)


@pytest.mark.asyncio
async def test_chorus(monkeypatch, client) -> None:
    audio = resource_row(source_type="upload", mime_type="audio/mpeg")
    _use_repo(monkeypatch, get_resource_by_id=audio, update_resource={})
    resp = await client.put(
        f"/api/v1/resources/{RID}/chorus", json={"chorus_start_ms": 42000}
    )
    _assert_typed_404(resp)


@pytest.mark.asyncio
async def test_move(monkeypatch, client) -> None:
    _use_service(monkeypatch, move_resource={})
    resp = await client.post(
        f"/api/v1/resources/{RID}/move", json={"scope_id": "42", "folder_id": "9"}
    )
    _assert_typed_404(resp)


@pytest.mark.asyncio
async def test_update_smart_folder(monkeypatch, client) -> None:
    _use_folders(monkeypatch, get_folder_by_id=_smart_folder(), update_folder={})
    resp = await client.patch(
        f"/api/v1/resources/smart-folders/{FID}", json={"name": "Renamed"}
    )
    _assert_typed_404(resp)


@pytest.mark.asyncio
async def test_update_folder(monkeypatch, client) -> None:
    _use_folders(
        monkeypatch, get_folder_by_id=folder_row(is_system=False), update_folder={}
    )
    resp = await client.patch(
        f"/api/v1/resources/folders/{FID}", json={"name": "Renamed"}
    )
    _assert_typed_404(resp)


@pytest.mark.asyncio
async def test_overwrite_version(monkeypatch, client) -> None:
    _use_versions(monkeypatch, Fake(overwrite_version_content={}))
    resp = await client.put(
        f"/api/v1/resources/{RID}/versions/9/content",
        files={"file": ("a.txt", b"text", "text/plain")},
    )
    _assert_typed_404(resp)
