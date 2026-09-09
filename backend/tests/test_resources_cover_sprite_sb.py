"""POST /cover + /preview-sprite behavior for sb:// rows (Task 2.4c).

POST /cover used to derive the write dir from the ORIGINAL's parent — for an
sb:// row that's a literal ``sb:/...`` directory on disk, and content-
addressed dedup means two same-content resources share a fanout dir, so
their covers would overwrite each other. sb rows now write to
``derived/covers/{resource_id}/`` (legacy fs rows keep writing next to the
source, byte-identical).

/preview-sprite 404'd for sb rows: their sprite lives in
``derived/thumbnails/{resource_id}/preview_sprite.jpg`` (where
thumbnail_service writes it for sb sources) — that location is probed first,
with the legacy next-to-source probe unchanged for fs rows.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.responses import FileResponse, StreamingResponse

from app.api.resources_crud_router import (
    serve_preview_sprite,
    serve_resource_cover,
    upload_resource_cover,
)

pytestmark = pytest.mark.asyncio

_RID = "9000000000000000001"
SB_PATH = "sb://library/t42/ab/cd/abcdef1234.mp4"
FS_PATH = "teams/9/uploads/RID/v1/video.mp4"


class FakeUploadFile:
    def __init__(self, filename: str, content: bytes, content_type: str):
        self.filename = filename
        self.content_type = content_type
        self._content = content

    async def read(self) -> bytes:
        return self._content


def _repo(resource: dict) -> MagicMock:
    repo = MagicMock()
    repo.get_resource_by_id = AsyncMock(return_value=resource)

    async def _update(rid, data):
        resource.update(data)
        return dict(resource)

    repo.update_resource = AsyncMock(side_effect=_update)
    return repo


def _patches(repo):
    return (
        patch("app.api.resources_crud_router.ResourcesRepository", return_value=repo),
        patch(
            "app.api.media_permissions.check_media_access",
            new=AsyncMock(return_value=True),
        ),
    )


async def test_cover_upload_sb_row_writes_derived_dir_and_serves_back(
    tmp_path, monkeypatch
):
    from app.core.config import settings as app_settings

    monkeypatch.setattr(app_settings, "DOWNLOAD_PATH", str(tmp_path))
    resource = {"id": _RID, "file_path": SB_PATH, "mime_type": "video/mp4"}
    repo = _repo(resource)
    p1, p2 = _patches(repo)

    with p1, p2:
        out = await upload_resource_cover(
            _RID,
            SimpleNamespace(user_id="u1"),
            None,
            file=FakeUploadFile("my-cover.png", b"cover-bytes", "image/png"),
        )

    assert out["success"] is True
    expected_rel = f"derived/covers/{_RID}/cover.png"
    assert resource["cover_image_path"] == expected_rel
    on_disk = Path(tmp_path) / expected_rel
    assert on_disk.read_bytes() == b"cover-bytes"
    # No literal "sb:" directory was ever created on disk.
    assert not (Path(tmp_path) / "sb:").exists()

    # Read side: GET /cover serves exactly the stored cover_image_path.
    with p1:
        resp = await serve_resource_cover(_RID, MagicMock())
    assert isinstance(resp, FileResponse)
    assert resp.path == str(on_disk)


async def test_serve_cover_thumbnail_path_sb_row_goes_through_serve_stored_file(
    monkeypatch,
):
    """fix round 1, Finding 1: derived module migrates thumbnail_path/
    cover_image_path to sb://library/derived/{rid}/... — GET /cover must
    route an sb:// value through serve_stored_file, not silently fail a raw
    ``Path(DOWNLOAD_PATH) / "sb://..."`` existence check (which never
    exists()), fall into the lazy-thumbnail placeholder path, and regenerate
    + overwrite the column back to a local path — quietly undoing the
    migration and orphaning the S3 copy."""
    from fastapi.responses import Response

    sb_thumb = "sb://library/derived/9000000000000000001/thumbnail.webp"
    resource = {
        "id": _RID,
        "file_path": None,
        "thumbnail_path": sb_thumb,
        "cover_image_path": None,
        "mime_type": "video/mp4",
    }
    repo = _repo(resource)
    p1, p2 = _patches(repo)

    sentinel = Response(status_code=200, media_type="image/webp")
    fake_serve_stored_file = AsyncMock(return_value=sentinel)
    with (
        p1,
        p2,
        patch(
            "app.services.library.media_serving.serve_stored_file",
            fake_serve_stored_file,
        ),
    ):
        resp = await serve_resource_cover(_RID, MagicMock())

    assert resp is sentinel
    fake_serve_stored_file.assert_awaited_once()
    assert fake_serve_stored_file.call_args.args[0] == sb_thumb


async def test_cover_upload_legacy_fs_row_keeps_next_to_source(tmp_path, monkeypatch):
    """Legacy fs row: byte-identical behavior — cover still lands next to
    the original."""
    from app.core.config import settings as app_settings

    monkeypatch.setattr(app_settings, "DOWNLOAD_PATH", str(tmp_path))
    resource = {"id": _RID, "file_path": FS_PATH, "mime_type": "video/mp4"}
    repo = _repo(resource)
    p1, p2 = _patches(repo)

    with p1, p2:
        await upload_resource_cover(
            _RID,
            SimpleNamespace(user_id="u1"),
            None,
            file=FakeUploadFile("c.jpg", b"legacy-cover", "image/jpeg"),
        )

    expected_rel = "teams/9/uploads/RID/v1/cover.jpg"
    assert resource["cover_image_path"] == expected_rel
    assert (Path(tmp_path) / expected_rel).read_bytes() == b"legacy-cover"


class FakeDerivedStore:
    """Stand-in for ``media_storage.library_store()`` in the sprite endpoint's
    object-store derived probe — ``exists``/``get_stream`` only, no real
    network. Keeps these tests hermetic (a real ``ObjectStore.exists()``
    swallows any failure and returns False, but would still attempt a live
    HEAD to whatever SUPABASE_URL is configured; mocking pins the test to
    OUR dispatch logic, not the SDK/network — house style, see
    test_storage_migration.py's FakeStore)."""

    bucket = "library"

    def __init__(self, *, existing_keys: set[str] | None = None):
        self._existing = existing_keys or set()

    async def exists(self, key: str) -> bool:
        return key in self._existing

    def get_stream(self, key: str):
        async def _gen():
            yield b"object-store-sprite-bytes"

        return _gen()


async def test_preview_sprite_sb_row_served_from_derived_dir(tmp_path, monkeypatch):
    from app.core.config import settings as app_settings
    from app.services.library import media_storage

    monkeypatch.setattr(app_settings, "DOWNLOAD_PATH", str(tmp_path))
    monkeypatch.setattr(media_storage, "library_store", lambda: FakeDerivedStore())
    sprite = Path(tmp_path) / "derived" / "thumbnails" / _RID / "preview_sprite.jpg"
    sprite.parent.mkdir(parents=True, exist_ok=True)
    sprite.write_bytes(b"sprite-bytes")

    repo = _repo({"id": _RID, "file_path": SB_PATH})
    p1, _ = _patches(repo)
    with p1:
        resp = await serve_preview_sprite(_RID)

    assert isinstance(resp, FileResponse)
    assert resp.path == str(sprite)


async def test_preview_sprite_legacy_row_probe_unchanged(tmp_path, monkeypatch):
    from app.core.config import settings as app_settings
    from app.services.library import media_storage

    monkeypatch.setattr(app_settings, "DOWNLOAD_PATH", str(tmp_path))
    monkeypatch.setattr(media_storage, "library_store", lambda: FakeDerivedStore())
    legacy_sprite = Path(tmp_path) / "teams/9/uploads/RID/v1/preview_sprite.jpg"
    legacy_sprite.parent.mkdir(parents=True, exist_ok=True)
    legacy_sprite.write_bytes(b"legacy-sprite")

    repo = _repo({"id": _RID, "file_path": FS_PATH})
    p1, _ = _patches(repo)
    with p1:
        resp = await serve_preview_sprite(_RID)

    assert isinstance(resp, FileResponse)
    assert resp.path == str(legacy_sprite)


async def test_preview_sprite_sb_row_404_when_no_derived_sprite(tmp_path, monkeypatch):
    from fastapi import HTTPException

    from app.core.config import settings as app_settings
    from app.services.library import media_storage

    monkeypatch.setattr(app_settings, "DOWNLOAD_PATH", str(tmp_path))
    monkeypatch.setattr(media_storage, "library_store", lambda: FakeDerivedStore())
    repo = _repo({"id": _RID, "file_path": SB_PATH})
    p1, _ = _patches(repo)
    with p1:
        with pytest.raises(HTTPException) as exc:
            await serve_preview_sprite(_RID)
    assert exc.value.status_code == 404


async def test_preview_sprite_sb_row_served_from_object_store(tmp_path, monkeypatch):
    """After the derived module (PR-4) migrates + deletes the local file,
    the sprite lives only at sb://library/derived/{rid}/preview_sprite.jpg —
    the endpoint must still serve it (not 404 just because the local dir is
    gone)."""
    from app.core.config import settings as app_settings
    from app.services.library import media_storage

    monkeypatch.setattr(app_settings, "DOWNLOAD_PATH", str(tmp_path))
    key = f"derived/{_RID}/preview_sprite.jpg"
    monkeypatch.setattr(
        media_storage, "library_store", lambda: FakeDerivedStore(existing_keys={key})
    )

    repo = _repo({"id": _RID, "file_path": SB_PATH})
    p1, _ = _patches(repo)
    with p1:
        resp = await serve_preview_sprite(_RID)

    assert isinstance(resp, StreamingResponse)


# ── the cover itself goes to the object store (2026-09-10) ───────────────────
# DOWNLOAD_PATH is a transit dir on the server's local NVMe since 2026-09-07,
# not a place to keep a user's cover: writing ``derived/covers/{rid}/cover.png``
# there and persisting that relative path made the cover a local-only artefact
# (unshared between backend/worker, wiped on the next deploy). With unified
# storage ON the bytes are staged in the transit dir, content-addressed into
# the library bucket, the staging file is discarded, and ``cover_image_path``
# is the sb:// value. The flag-OFF tests above keep the legacy filesystem
# behavior byte-identical.


class FakeCoverStore:
    bucket = "library"

    def __init__(self):
        self.puts: list = []

    async def exists(self, key):
        return False

    async def put_file(self, key, path, mime):
        self.puts.append((key, path, mime))


async def test_cover_upload_goes_to_object_store_when_unified_storage_on(
    tmp_path, monkeypatch
):
    from app.core.config import settings as app_settings
    from app.services.library import media_storage, resources_service, storage_flag

    monkeypatch.setattr(app_settings, "DOWNLOAD_PATH", str(tmp_path))

    async def _on():
        return True

    async def _team(_uid):
        return "42"

    store = FakeCoverStore()
    monkeypatch.setattr(storage_flag, "unified_storage_enabled", _on)
    monkeypatch.setattr(resources_service, "_resolve_personal_team_id", _team)
    monkeypatch.setattr(media_storage, "library_store", lambda: store)

    resource = {"id": _RID, "file_path": SB_PATH, "mime_type": "video/mp4"}
    repo = _repo(resource)
    p1, p2 = _patches(repo)
    with p1, p2:
        out = await upload_resource_cover(
            _RID,
            SimpleNamespace(user_id="u1"),
            None,
            file=FakeUploadFile("my-cover.png", b"cover-bytes", "image/png"),
        )
    assert out["success"] is True
    assert resource["cover_image_path"].startswith("sb://library/t42/")
    assert len(store.puts) == 1 and store.puts[0][2] == "image/png"
    # The staging file was discarded: nothing durable left in the transit dir.
    assert [p for p in Path(tmp_path).rglob("*") if p.is_file()] == []
