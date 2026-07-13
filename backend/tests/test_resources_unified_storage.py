"""Task 2.1 — dual-track ``upload_resource`` write.

Exercises ResourcesService.upload_resource against a stubbed repo (in-memory
dict) so the test pins ONLY the storage-selection logic — flag on routes
bytes through store_local_file() into the "sb://library/..." shape, flag off
(and any storage failure) preserves the legacy filesystem move. Mirrors the
FakeStore style of test_media_storage_unified.py.
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

import app.services.library.resources_service as rs
from app.services.library.media_storage import StoredObject


class FakeUploadFile:
    """Minimal UploadFile stand-in: one-shot .read(), like the real thing
    streamed by stream_upload_to_disk (chunk, then empty to signal EOF)."""

    def __init__(self, filename: str, content: bytes, content_type: str = "text/plain"):
        self.filename = filename
        self.content_type = content_type
        self._content = content
        self._sent = False

    async def read(self, n: int = -1) -> bytes:
        if self._sent:
            return b""
        self._sent = True
        return self._content


class FakeResourcesRepo:
    """In-memory stand-in for ResourcesRepository — only the methods
    upload_resource actually calls."""

    def __init__(self):
        self.resources: dict[int, dict] = {}
        self.versions: list[dict] = []
        self.items: list[dict] = []
        self._next_id = 100

    async def create_resource(self, data: dict) -> dict:
        rid = self._next_id
        self._next_id += 1
        row = {"id": rid, **data}
        self.resources[rid] = row
        return dict(row)

    async def update_resource(self, resource_id, data: dict) -> dict:
        row = self.resources[int(resource_id)]
        row.update(data)
        return dict(row)

    async def create_version(self, data: dict) -> dict:
        self.versions.append(dict(data))
        return dict(data)

    async def create_resource_item(self, data: dict) -> dict:
        self.items.append(dict(data))
        return dict(data)


@pytest.fixture
def service(monkeypatch, tmp_path):
    monkeypatch.setattr(rs.settings, "DOWNLOAD_PATH", str(tmp_path))
    svc = rs.ResourcesService()
    svc.repo = FakeResourcesRepo()
    return svc


@pytest.mark.asyncio
async def test_flag_on_writes_object_store_path(service, monkeypatch):
    monkeypatch.setattr(rs.settings, "FEATURE_UNIFIED_STORAGE", True)

    captured = {}

    async def fake_store_local_file(
        *, scope_id, source_path, mime, filename=None, sha256=None, store=None
    ):
        # Called before the finally-block unlink — the tmp file must still
        # be present for the (real) implementation to stream from.
        assert Path(source_path).exists()
        captured["scope_id"] = scope_id
        captured["source_path"] = source_path
        captured["sha256"] = sha256
        return StoredObject(
            file_path=f"sb://library/t{scope_id}/ab/cd/{sha256}.bin",
            size_bytes=Path(source_path).stat().st_size,
            sha256=sha256,
        )

    monkeypatch.setattr(rs, "store_local_file", fake_store_local_file)

    file = FakeUploadFile("clip.mp4", b"hello unified storage", "video/mp4")
    resource = await service.upload_resource(
        user_id="u1", file=file, scope_id="42", folder_id=None, library_id=None
    )

    assert resource["file_path"].startswith("sb://library/t42/")
    assert service.repo.versions[0]["file_path"] == resource["file_path"]
    # scope_id reached store_local_file as an int
    assert captured["scope_id"] == 42
    # sha256 kwarg reused stream_upload_to_disk's hash — not rehashed
    assert captured["sha256"] == resource["file_hash"]
    # tmp file cleaned up (finally: tmp_path.unlink still runs)
    assert not Path(captured["source_path"]).exists()


@pytest.mark.asyncio
async def test_flag_off_keeps_legacy_filesystem_path(service, monkeypatch):
    monkeypatch.setattr(rs.settings, "FEATURE_UNIFIED_STORAGE", False)

    called = {"n": 0}

    async def fake_store_local_file(*args, **kwargs):
        called["n"] += 1
        raise AssertionError("store_local_file must not be called when flag is off")

    monkeypatch.setattr(rs, "store_local_file", fake_store_local_file)

    file = FakeUploadFile("notes.txt", b"plain legacy bytes", "text/plain")
    resource = await service.upload_resource(
        user_id="u1", file=file, scope_id="7", folder_id=None, library_id=None
    )

    assert called["n"] == 0
    resource_id = str(resource["id"])
    expected = f"teams/7/uploads/{resource_id}/v1/notes.txt"
    assert resource["file_path"] == expected
    assert service.repo.versions[0]["file_path"] == expected

    on_disk = Path(rs.settings.DOWNLOAD_PATH) / expected
    assert on_disk.exists()
    assert on_disk.read_bytes() == b"plain legacy bytes"


@pytest.mark.asyncio
async def test_flag_on_store_failure_falls_back_to_filesystem(service, monkeypatch):
    monkeypatch.setattr(rs.settings, "FEATURE_UNIFIED_STORAGE", True)

    async def failing_store_local_file(*args, **kwargs):
        raise RuntimeError("storage-api unreachable")

    monkeypatch.setattr(rs, "store_local_file", failing_store_local_file)

    warn_mock = MagicMock()
    monkeypatch.setattr(rs.logger, "warning", warn_mock)

    file = FakeUploadFile("photo.png", b"fallback-bytes", "image/png")
    resource = await service.upload_resource(
        user_id="u1", file=file, scope_id="9", folder_id=None, library_id=None
    )

    resource_id = str(resource["id"])
    expected = f"teams/9/uploads/{resource_id}/v1/photo.png"
    assert resource["file_path"] == expected

    on_disk = Path(rs.settings.DOWNLOAD_PATH) / expected
    assert on_disk.exists()
    assert on_disk.read_bytes() == b"fallback-bytes"

    warn_mock.assert_called_once()
    assert "unified-storage write failed" in warn_mock.call_args[0][0]
