"""Tasks 2.1 + 2.2 — dual-track ``upload_resource`` / ``upload_new_version``.

Exercises ResourcesService against a stubbed repo (in-memory dict) so the
tests pin ONLY the storage-selection logic — flag on routes bytes through
store_local_file() into the "sb://library/..." shape, flag off preserves the
legacy filesystem move, and a storage FAILURE with the flag on is a hard,
typed failure (2026-09-07 — the transit dir is not a durable store). Mirrors the
FakeStore style of test_media_storage_unified.py.
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

import app.services.library.resources_service as rs
from app.services.library.media_storage import StoredObject
from app.services.library.storage_errors import ObjectStoreWriteFailed


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
    upload_resource / upload_new_version actually call."""

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

    async def get_resource_by_id(self, resource_id):
        row = self.resources.get(int(resource_id))
        return dict(row) if row else None

    async def get_next_version_number(self, resource_id) -> int:
        rid = int(resource_id)
        numbers = [
            v["version_number"] for v in self.versions if int(v["resource_id"]) == rid
        ]
        return (max(numbers) if numbers else 0) + 1

    async def delete_resource(self, resource_id) -> bool:
        self.resources.pop(int(resource_id), None)
        return True

    async def get_first_resource_item(self, resource_id):
        rid = int(resource_id)
        for item in self.items:
            if int(item["resource_id"]) == rid:
                return dict(item)
        return None


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
async def test_flag_on_store_failure_raises_typed_error_and_writes_nothing(
    service, monkeypatch
):
    """S3 down is a HARD failure: no filesystem fallback, nothing on disk,
    and the resource row created ahead of the bytes is discarded again.
    The typed error carries the developer context (see
    test_object_store_hard_fail.py for the HTTP/log contract)."""
    monkeypatch.setattr(rs.settings, "FEATURE_UNIFIED_STORAGE", True)

    async def failing_store_local_file(*args, **kwargs):
        raise RuntimeError("storage-api unreachable")

    monkeypatch.setattr(rs, "store_local_file", failing_store_local_file)

    file = FakeUploadFile("photo.png", b"never-saved", "image/png")
    with pytest.raises(ObjectStoreWriteFailed) as excinfo:
        await service.upload_resource(
            user_id="u1", file=file, scope_id="9", folder_id=None, library_id=None
        )

    err = excinfo.value
    assert err.details["where"] == "upload_resource"
    assert err.details["scope_id"] == "9"
    assert err.details["filename"] == "photo.png"
    assert isinstance(err.__cause__, RuntimeError)
    assert [p for p in Path(rs.settings.DOWNLOAD_PATH).rglob("*") if p.is_file()] == []
    assert service.repo.resources == {}
    assert service.repo.versions == [] and service.repo.items == []


# ── Task 2.2: upload_new_version dual-track ─────────────────────────────────


async def _seed_resource(repo, scope_id: str, file_path: str) -> str:
    """Seed an existing resource + v1 version + resource_item, the state
    upload_new_version starts from. Returns the resource id as str."""
    resource = await repo.create_resource(
        {
            "creator_id": "u1",
            "source_type": "upload",
            "filename": "orig.txt",
            "file_type": "document",
            "mime_type": "text/plain",
            "file_size_bytes": 4,
            "current_version": 1,
            "file_hash": "orig-hash",
            "file_path": file_path,
        }
    )
    rid = str(resource["id"])
    await repo.create_version(
        {
            "resource_id": rid,
            "version_number": 1,
            "filename": "orig.txt",
            "file_path": file_path,
            "file_size_bytes": 4,
            "mime_type": "text/plain",
            "uploaded_by": "u1",
            "file_hash": "orig-hash",
        }
    )
    await repo.create_resource_item(
        {"resource_id": rid, "scope_id": scope_id, "added_by": "u1"}
    )
    return rid


@pytest.mark.asyncio
async def test_new_version_flag_on_writes_object_store_path(service, monkeypatch):
    monkeypatch.setattr(rs.settings, "FEATURE_UNIFIED_STORAGE", True)

    rid = await _seed_resource(
        service.repo, scope_id="42", file_path="teams/42/uploads/x/v1/orig.txt"
    )
    rid_path = f"teams/42/uploads/{rid}/v1/orig.txt"
    service.repo.resources[int(rid)]["file_path"] = rid_path

    captured = {}

    async def fake_store_local_file(
        *, scope_id, source_path, mime, filename=None, sha256=None, store=None
    ):
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

    file = FakeUploadFile("clip-v2.mp4", b"version two bytes", "video/mp4")
    version = await service.upload_new_version(
        resource_id=rid, user_id="u1", file=file, notes="v2"
    )

    assert version["version_number"] == 2
    assert version["file_path"].startswith("sb://library/t42/")
    # resource row updated with the same sb:// path
    assert service.repo.resources[int(rid)]["file_path"] == version["file_path"]
    assert service.repo.resources[int(rid)]["current_version"] == 2
    # scope came from get_first_resource_item, coerced to int
    assert captured["scope_id"] == 42
    # sha256 kwarg reused stream_upload_to_disk's hash — not rehashed
    assert captured["sha256"] == version["file_hash"]
    # tmp file cleaned up (finally: tmp_path.unlink still runs)
    assert not Path(captured["source_path"]).exists()


@pytest.mark.asyncio
async def test_new_version_flag_off_keeps_legacy_filesystem_path(service, monkeypatch):
    monkeypatch.setattr(rs.settings, "FEATURE_UNIFIED_STORAGE", False)

    called = {"n": 0}

    async def fake_store_local_file(*args, **kwargs):
        called["n"] += 1
        raise AssertionError("store_local_file must not be called when flag is off")

    monkeypatch.setattr(rs, "store_local_file", fake_store_local_file)

    rid = await _seed_resource(
        service.repo, scope_id="7", file_path="teams/7/uploads/RID/v1/orig.txt"
    )
    existing = f"teams/7/uploads/{rid}/v1/orig.txt"
    service.repo.resources[int(rid)]["file_path"] = existing

    file = FakeUploadFile("notes-v2.txt", b"version two legacy", "text/plain")
    version = await service.upload_new_version(resource_id=rid, user_id="u1", file=file)

    assert called["n"] == 0
    expected = f"teams/7/uploads/{rid}/v2/notes-v2.txt"
    assert version["file_path"] == expected
    assert service.repo.resources[int(rid)]["file_path"] == expected

    on_disk = Path(rs.settings.DOWNLOAD_PATH) / expected
    assert on_disk.exists()
    assert on_disk.read_bytes() == b"version two legacy"


@pytest.mark.asyncio
async def test_new_version_store_failure_raises_typed_error_and_writes_nothing(
    service, monkeypatch
):
    monkeypatch.setattr(rs.settings, "FEATURE_UNIFIED_STORAGE", True)

    async def failing_store_local_file(*args, **kwargs):
        raise RuntimeError("storage-api unreachable")

    monkeypatch.setattr(rs, "store_local_file", failing_store_local_file)

    rid = await _seed_resource(
        service.repo, scope_id="9", file_path="teams/9/uploads/RID/v1/orig.txt"
    )
    existing = f"teams/9/uploads/{rid}/v1/orig.txt"
    service.repo.resources[int(rid)]["file_path"] = existing

    file = FakeUploadFile("photo-v2.png", b"never-saved", "image/png")
    with pytest.raises(ObjectStoreWriteFailed) as excinfo:
        await service.upload_new_version(resource_id=rid, user_id="u1", file=file)

    assert excinfo.value.details["where"] == "upload_new_version"
    assert excinfo.value.details["resource_id"] == rid
    assert [p for p in Path(rs.settings.DOWNLOAD_PATH).rglob("*") if p.is_file()] == []
    # No v2 row, and the resource still points at v1.
    assert [v["version_number"] for v in service.repo.versions] == [1]
    assert service.repo.resources[int(rid)]["file_path"] == existing


@pytest.mark.asyncio
async def test_new_version_on_sb_resource_flag_off_uses_scope_branch(
    service, monkeypatch
):
    """PIN: an existing sb:// file_path has no "/v" dir semantics — the
    base_relative derivation must fall through to the resource_items scope
    branch and land the new version under teams/{scope}/uploads/{rid}/v{n}/."""
    monkeypatch.setattr(rs.settings, "FEATURE_UNIFIED_STORAGE", False)

    rid = await _seed_resource(
        service.repo,
        scope_id="13",
        file_path="sb://library/t13/ab/cd/abcdef0123456789.bin",
    )

    file = FakeUploadFile("doc-v2.txt", b"post-sb version bytes", "text/plain")
    version = await service.upload_new_version(resource_id=rid, user_id="u1", file=file)

    expected = f"teams/13/uploads/{rid}/v2/doc-v2.txt"
    assert version["file_path"] == expected
    on_disk = Path(rs.settings.DOWNLOAD_PATH) / expected
    assert on_disk.exists()
    assert on_disk.read_bytes() == b"post-sb version bytes"


@pytest.mark.asyncio
async def test_new_version_flag_on_item_none_skips_storage_without_failure_log(
    service, monkeypatch
):
    """Task 2.4c: no resource_item is NOT a storage failure. Before the
    guard, ``item["scope_id"]`` raised TypeError inside the try, which was
    swallowed as a misleading "unified-storage write failed" warning. Now
    the object-store attempt is skipped quietly and the fs branch raises the
    legit "Resource has no scope association" ValueError."""
    monkeypatch.setattr(rs.settings, "FEATURE_UNIFIED_STORAGE", True)

    store_mock = MagicMock()
    monkeypatch.setattr(rs, "store_local_file", store_mock)
    err_mock = MagicMock()
    monkeypatch.setattr(rs.logger, "error", err_mock)

    # Seed a resource with NO resource_item and no "/v" dir semantics in its
    # file_path (sb:// shape), so BOTH branches need the item row.
    resource = await service.repo.create_resource(
        {
            "creator_id": "u1",
            "source_type": "upload",
            "filename": "orig.bin",
            "file_type": "document",
            "mime_type": "application/octet-stream",
            "file_size_bytes": 3,
            "current_version": 1,
            "file_hash": "x",
            "file_path": "sb://library/t13/ab/cd/abcdef0123456789.bin",
        }
    )
    rid = str(resource["id"])

    file = FakeUploadFile("v2.bin", b"v2 bytes", "application/octet-stream")
    with pytest.raises(ValueError, match="no scope association"):
        await service.upload_new_version(resource_id=rid, user_id="u1", file=file)

    # The object-store write was never attempted …
    store_mock.assert_not_called()
    # … and no misleading storage-failure line was emitted.
    err_mock.assert_not_called()
