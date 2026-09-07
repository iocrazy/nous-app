"""Dual-track ``persist_derived_image`` (derive services, storage unification).

Derived images are user-kept assets (real resources + resource_versions
rows), NOT regenerable derivatives — so with FEATURE_UNIFIED_STORAGE on
they must land in the object store like uploads do, not keep minting
legacy filesystem rows. Exercises persist_derived_image against a stubbed
repo: flag on routes bytes through store_local_file() into the
"sb://library/..." shape, flag off preserves the legacy atomic filesystem
write, and a storage FAILURE with the flag on is a hard, typed failure
(2026-09-07 — the transit dir is not a durable store). Mirrors test_resources_unified_storage.py.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest

import app.services.canvas.derive_persistence as dp
from app.services.canvas.derive_persistence import persist_derived_image
from app.services.library.media_storage import StoredObject
from app.services.library.storage_errors import ObjectStoreWriteFailed

pytestmark = pytest.mark.unit

PNG_BYTES = b"\x89PNG-not-really-but-bytes"


class FakeRepo:
    """In-memory stand-in for ResourcesRepository — only the methods
    persist_derived_image actually calls."""

    def __init__(self):
        self.created_resource: Dict[str, Any] | None = None
        self.updated_resource: Dict[str, Any] | None = None
        self.versions: List[Dict[str, Any]] = []
        self.items: List[Dict[str, Any]] = []
        self.deleted: List[str] = []

    async def delete_resource(self, resource_id: str) -> bool:
        self.deleted.append(str(resource_id))
        return True

    async def get_resource_by_id(self, resource_id: str):  # pragma: no cover
        return None

    async def get_first_resource_item(self, resource_id: str):  # pragma: no cover
        return None

    async def create_resource(self, data: Dict[str, Any]):
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
def repo(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> FakeRepo:
    monkeypatch.setattr(dp.settings, "DOWNLOAD_PATH", str(tmp_path))
    return FakeRepo()


async def _persist(repo: FakeRepo, scope_id: str = "42") -> dict:
    return await persist_derived_image(
        repo,
        user_id="u1",
        scope_id=scope_id,
        folder_id=None,
        library_id=None,
        filename="crop-orig.png",
        image_bytes=PNG_BYTES,
        mime_type="image/png",
    )


@pytest.mark.asyncio
async def test_flag_on_writes_object_store_path(repo, monkeypatch):
    monkeypatch.setattr(dp.settings, "FEATURE_UNIFIED_STORAGE", True)

    captured = {}

    async def fake_store_local_file(
        *, scope_id, source_path, mime, filename=None, sha256=None, store=None
    ):
        # Called before the finally-block unlink — the tmp file must still
        # be present (with the derived bytes) for the real implementation
        # to hash + stream from.
        assert Path(source_path).read_bytes() == PNG_BYTES
        captured["scope_id"] = scope_id
        captured["source_path"] = source_path
        return StoredObject(
            file_path=f"sb://library/t{scope_id}/ab/cd/deadbeef.png",
            size_bytes=len(PNG_BYTES),
            sha256="deadbeef",
        )

    monkeypatch.setattr(dp, "store_local_file", fake_store_local_file)

    resource = await _persist(repo)

    assert resource["file_path"] == "sb://library/t42/ab/cd/deadbeef.png"
    assert repo.versions[0]["file_path"] == resource["file_path"]
    # scope_id reached store_local_file as an int
    assert captured["scope_id"] == 42
    # tmp file cleaned up (store_local_file only reads, never deletes)
    assert not Path(captured["source_path"]).exists()
    # No legacy filesystem file was written
    assert not (Path(dp.settings.DOWNLOAD_PATH) / "teams").exists()
    # The dedupe-index decision survives: file_hash is never written to the
    # row even though store_local_file hashed the tmp for its content key.
    assert "file_hash" not in repo.created_resource
    assert "file_hash" not in (repo.updated_resource or {})


@pytest.mark.asyncio
async def test_flag_off_keeps_legacy_filesystem_path(repo, monkeypatch):
    monkeypatch.setattr(dp.settings, "FEATURE_UNIFIED_STORAGE", False)

    async def fail_if_called(*args, **kwargs):
        raise AssertionError("store_local_file must not be called when flag is off")

    monkeypatch.setattr(dp, "store_local_file", fail_if_called)

    resource = await _persist(repo, scope_id="7")

    rid = str(repo.created_resource["id"])
    expected = f"teams/7/derived/{rid}/v1/crop-orig.png"
    assert resource["file_path"] == expected
    assert repo.versions[0]["file_path"] == expected

    on_disk = Path(dp.settings.DOWNLOAD_PATH) / expected
    assert on_disk.exists()
    assert on_disk.read_bytes() == PNG_BYTES
    # No stray tmp blob left under the download root
    leftovers = [p for p in Path(dp.settings.DOWNLOAD_PATH).iterdir() if p.is_file()]
    assert leftovers == []


@pytest.mark.asyncio
async def test_flag_on_store_failure_raises_typed_error_and_discards_row(
    repo, monkeypatch
):
    monkeypatch.setattr(dp.settings, "FEATURE_UNIFIED_STORAGE", True)

    async def failing_store_local_file(*args, **kwargs):
        raise RuntimeError("storage-api unreachable")

    monkeypatch.setattr(dp, "store_local_file", failing_store_local_file)

    with pytest.raises(ObjectStoreWriteFailed) as excinfo:
        await _persist(repo, scope_id="9")

    rid = str(repo.created_resource["id"])
    assert excinfo.value.details["where"] == "persist_derived_image"
    assert excinfo.value.details["resource_id"] == rid
    assert [p for p in Path(dp.settings.DOWNLOAD_PATH).rglob("*") if p.is_file()] == []
    assert repo.deleted == [rid]
    assert repo.versions == [] and repo.items == []


@pytest.mark.asyncio
async def test_flag_on_non_numeric_scope_is_a_storage_failure(repo, monkeypatch):
    """A scope id the object key cannot encode (legacy 'scope-1') has
    nowhere to be stored: typed failure with the ValueError as cause, the
    store never called, the row discarded — not a silent filesystem row."""
    monkeypatch.setattr(dp.settings, "FEATURE_UNIFIED_STORAGE", True)

    store_mock = MagicMock()
    monkeypatch.setattr(dp, "store_local_file", store_mock)

    with pytest.raises(ObjectStoreWriteFailed) as excinfo:
        await _persist(repo, scope_id="scope-1")

    assert isinstance(excinfo.value.__cause__, ValueError)
    store_mock.assert_not_called()  # int('scope-1') raised before the call
    assert repo.deleted == [str(repo.created_resource["id"])]


# ─── load_source_image read side (sb:// dual-track) ──────────────────
#
# 1333 dual-tracked the WRITE half only; the read half kept joining
# file_path under DOWNLOAD_PATH, so deriving FROM an sb:// source 404'd
# with the flag on. Reads now go through materialize() for both shapes.


class ReadFakeRepo(FakeRepo):
    """FakeRepo whose source lookup returns a real image resource row."""

    def __init__(self, file_path: str):
        super().__init__()
        self._file_path = file_path

    async def get_resource_by_id(self, resource_id: str):
        return {
            "id": resource_id,
            "file_type": "image",
            "mime_type": "image/png",
            "filename": "src.png",
            "file_path": self._file_path,
        }

    async def get_first_resource_item(self, resource_id: str):
        return {"scope_id": "42", "folder_id": None, "library_id": None}


@pytest.mark.asyncio
async def test_load_source_reads_sb_row_via_materialize(repo, monkeypatch, tmp_path):
    from contextlib import asynccontextmanager

    src = tmp_path / "materialized.png"
    src.write_bytes(PNG_BYTES)
    seen = {}

    @asynccontextmanager
    async def fake_materialize(file_path: str):
        seen["file_path"] = file_path
        yield src

    monkeypatch.setattr(dp, "materialize", fake_materialize)

    frepo = ReadFakeRepo("sb://library/t42/ab/cd/deadbeef.png")
    source = await dp.load_source_image(frepo, "123")

    assert source.file_bytes == PNG_BYTES
    assert seen["file_path"] == "sb://library/t42/ab/cd/deadbeef.png"


@pytest.mark.asyncio
async def test_load_source_sb_missing_object_maps_to_404(repo, monkeypatch):
    from contextlib import asynccontextmanager

    import httpx

    @asynccontextmanager
    async def fake_materialize(file_path: str):
        req = httpx.Request("GET", "http://storage/object")
        raise httpx.HTTPStatusError(
            "not found", request=req, response=httpx.Response(404, request=req)
        )
        yield  # pragma: no cover

    monkeypatch.setattr(dp, "materialize", fake_materialize)

    frepo = ReadFakeRepo("sb://library/t42/ab/cd/gone.png")
    with pytest.raises(dp.DeriveError) as exc:
        await dp.load_source_image(frepo, "123")
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_load_source_fs_row_unchanged(repo, monkeypatch, tmp_path):
    # No materialize stub: exercises the REAL fs branch under DOWNLOAD_PATH
    # (repo fixture already points settings.DOWNLOAD_PATH at tmp_path).
    rel = "teams/42/src.png"
    abs_path = tmp_path / rel
    abs_path.parent.mkdir(parents=True)
    abs_path.write_bytes(PNG_BYTES)

    frepo = ReadFakeRepo(rel)
    source = await dp.load_source_image(frepo, "123")
    assert source.file_bytes == PNG_BYTES


@pytest.mark.asyncio
async def test_load_source_fs_missing_is_404(repo):
    frepo = ReadFakeRepo("teams/42/nope.png")
    with pytest.raises(dp.DeriveError) as exc:
        await dp.load_source_image(frepo, "123")
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_load_source_escape_is_400(repo):
    frepo = ReadFakeRepo("../../etc/passwd")
    with pytest.raises(dp.DeriveError) as exc:
        await dp.load_source_image(frepo, "123")
    assert exc.value.status_code == 400
