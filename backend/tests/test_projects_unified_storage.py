"""Tasks 3.1 + 3.2 — dual-track ``upload_file`` / ``upload_new_version`` for
project_files.

Exercises ProjectsService against a stubbed repo (in-memory dict) so the
tests pin ONLY the storage-selection logic — flag on routes bytes through
store_local_file() into the "sb://library/..." shape, flag off (and any
storage failure) preserves the legacy mediatrack/{project_id}/... filesystem
path. Mirrors the FakeRepo style of test_resources_unified_storage.py.

Task 3.2's audit found no serve/tooling-read points in the grep'd files
(projects_router.py has no file-serving route at all) — see the comment
block near the bottom of this file for the full finding.
"""

import hashlib
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import app.services.library.projects_service as ps
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


class FakeProjectsRepo:
    """In-memory stand-in for ProjectsRepository — only the methods
    upload_file / upload_new_version actually call."""

    def __init__(self):
        self.projects: dict[int, dict] = {}
        self.files: dict[int, dict] = {}
        self.versions: list[dict] = []
        self._next_file_id = 100

    async def get_project_by_id(self, project_id):
        row = self.projects.get(int(project_id))
        return dict(row) if row else None

    async def create_file(self, data: dict) -> dict:
        fid = self._next_file_id
        self._next_file_id += 1
        row = {"id": fid, **data}
        self.files[fid] = row
        return dict(row)

    async def get_file_by_id(self, file_id):
        row = self.files.get(int(file_id))
        return dict(row) if row else None

    async def update_file(self, file_id, data: dict) -> dict:
        row = self.files[int(file_id)]
        row.update(data)
        return dict(row)

    async def create_version(self, data: dict) -> dict:
        self.versions.append(dict(data))
        return dict(data)

    async def get_next_version_number(self, file_id) -> int:
        fid = int(file_id)
        numbers = [
            v["version_number"] for v in self.versions if int(v["file_id"]) == fid
        ]
        return (max(numbers) if numbers else 0) + 1


@pytest.fixture
def service(monkeypatch, tmp_path):
    monkeypatch.setattr(ps.settings, "DOWNLOAD_PATH", str(tmp_path))
    svc = ps.ProjectsService()
    svc.repo = FakeProjectsRepo()
    return svc


def _seed_project(repo, project_id: int, *, team_id=None, owner_id="owner-1") -> dict:
    row = {"id": project_id, "team_id": team_id, "owner_id": owner_id, "name": "P"}
    repo.projects[project_id] = row
    return row


# ── Task 3.1: upload_file dual-track ────────────────────────────────────────


@pytest.mark.asyncio
async def test_upload_file_flag_on_writes_object_store_path(service, monkeypatch):
    monkeypatch.setattr(ps.settings, "FEATURE_UNIFIED_STORAGE", True)
    _seed_project(service.repo, 1, team_id=42)

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

    monkeypatch.setattr(ps, "store_local_file", fake_store_local_file)

    file = FakeUploadFile("clip.mp4", b"hello unified storage", "video/mp4")
    created = await service.upload_file(project_id="1", user_id="u1", file=file)

    assert created["file_path"].startswith("sb://library/t42/")
    assert service.repo.versions[0]["file_path"] == created["file_path"]
    # scope_id reached store_local_file as an int, derived from team_id
    assert captured["scope_id"] == 42
    # sha256 kwarg was populated from stream_upload_to_disk's hash (not
    # rehashed) — ProjectFiles has no file_hash column to cross-check
    # against, so just assert it's the sha256 of the uploaded bytes.
    assert captured["sha256"] == hashlib.sha256(b"hello unified storage").hexdigest()
    # tmp file cleaned up (finally: tmp_path.unlink still runs)
    assert not Path(captured["source_path"]).exists()


@pytest.mark.asyncio
async def test_upload_file_flag_on_no_team_uses_personal_team(service, monkeypatch):
    monkeypatch.setattr(ps.settings, "FEATURE_UNIFIED_STORAGE", True)
    _seed_project(service.repo, 2, team_id=None, owner_id="owner-2")

    async def fake_resolve_personal_team_id(user_id):
        assert user_id == "owner-2"
        return "99"

    monkeypatch.setattr(ps, "_resolve_personal_team_id", fake_resolve_personal_team_id)

    captured = {}

    async def fake_store_local_file(
        *, scope_id, source_path, mime, filename=None, sha256=None, store=None
    ):
        captured["scope_id"] = scope_id
        return StoredObject(
            file_path=f"sb://library/t{scope_id}/ab/cd/{sha256}.bin",
            size_bytes=Path(source_path).stat().st_size,
            sha256=sha256,
        )

    monkeypatch.setattr(ps, "store_local_file", fake_store_local_file)

    file = FakeUploadFile("doc.txt", b"personal project bytes", "text/plain")
    created = await service.upload_file(project_id="2", user_id="u1", file=file)

    assert captured["scope_id"] == 99
    assert created["file_path"].startswith("sb://library/t99/")


@pytest.mark.asyncio
async def test_upload_file_flag_off_keeps_legacy_filesystem_path(service, monkeypatch):
    monkeypatch.setattr(ps.settings, "FEATURE_UNIFIED_STORAGE", False)
    _seed_project(service.repo, 3, team_id=7)

    called = {"n": 0}

    async def fake_store_local_file(*args, **kwargs):
        called["n"] += 1
        raise AssertionError("store_local_file must not be called when flag is off")

    monkeypatch.setattr(ps, "store_local_file", fake_store_local_file)

    file = FakeUploadFile("notes.txt", b"plain legacy bytes", "text/plain")
    created = await service.upload_file(project_id="3", user_id="u1", file=file)

    assert called["n"] == 0
    expected = "mediatrack/3/notes.txt"
    assert created["file_path"] == expected
    assert service.repo.versions[0]["file_path"] == expected

    on_disk = Path(ps.settings.DOWNLOAD_PATH) / expected
    assert on_disk.exists()
    assert on_disk.read_bytes() == b"plain legacy bytes"


@pytest.mark.asyncio
async def test_upload_file_flag_off_dedups_duplicate_names(service, monkeypatch):
    monkeypatch.setattr(ps.settings, "FEATURE_UNIFIED_STORAGE", False)
    _seed_project(service.repo, 4, team_id=7)

    file1 = FakeUploadFile("dup.txt", b"first", "text/plain")
    created1 = await service.upload_file(project_id="4", user_id="u1", file=file1)
    assert created1["file_path"] == "mediatrack/4/dup.txt"

    file2 = FakeUploadFile("dup.txt", b"second", "text/plain")
    created2 = await service.upload_file(project_id="4", user_id="u1", file=file2)
    assert created2["file_path"] == "mediatrack/4/dup_1.txt"

    file3 = FakeUploadFile("dup.txt", b"third", "text/plain")
    created3 = await service.upload_file(project_id="4", user_id="u1", file=file3)
    assert created3["file_path"] == "mediatrack/4/dup_2.txt"


@pytest.mark.asyncio
async def test_upload_file_store_failure_raises_typed_error_and_writes_nothing(
    service, monkeypatch
):
    """S3 down is a HARD failure (2026-09-07): no mediatrack fallback, no
    project_files row."""
    monkeypatch.setattr(ps.settings, "FEATURE_UNIFIED_STORAGE", True)
    _seed_project(service.repo, 5, team_id=9)

    async def failing_store_local_file(*args, **kwargs):
        raise RuntimeError("storage-api unreachable")

    monkeypatch.setattr(ps, "store_local_file", failing_store_local_file)
    files_before = dict(service.repo.files)

    file = FakeUploadFile("photo.png", b"never-saved", "image/png")
    with pytest.raises(ObjectStoreWriteFailed) as excinfo:
        await service.upload_file(project_id="5", user_id="u1", file=file)

    assert excinfo.value.details["where"] == "project_upload_file"
    assert excinfo.value.details["project_id"] == "5"
    assert excinfo.value.details["filename"] == "photo.png"
    assert [p for p in Path(ps.settings.DOWNLOAD_PATH).rglob("*") if p.is_file()] == []
    assert service.repo.files == files_before


@pytest.mark.asyncio
async def test_upload_file_missing_project_raises(service):
    file = FakeUploadFile("x.txt", b"data", "text/plain")
    with pytest.raises(ValueError, match="Project not found"):
        await service.upload_file(project_id="999", user_id="u1", file=file)


@pytest.mark.asyncio
async def test_upload_file_scope_resolution_failure_is_a_storage_failure(
    service, monkeypatch
):
    """Scope resolution is part of the storage track: without a scope there
    is no object key. A personal project whose owner lacks a personal-team
    row fails the same typed way — the ValueError is the CAUSE, and must not
    escape raw as a bogus router-level "404 Project not found"."""
    monkeypatch.setattr(ps.settings, "FEATURE_UNIFIED_STORAGE", True)
    _seed_project(service.repo, 6, team_id=None, owner_id="orphan-owner")

    async def failing_resolve_personal_team_id(user_id):
        raise ValueError(f"No personal team found for user {user_id}")

    monkeypatch.setattr(
        ps, "_resolve_personal_team_id", failing_resolve_personal_team_id
    )
    store_mock = MagicMock()
    monkeypatch.setattr(ps, "store_local_file", store_mock)

    file = FakeUploadFile("orphan.txt", b"never-saved", "text/plain")
    with pytest.raises(ObjectStoreWriteFailed) as excinfo:
        await service.upload_file(project_id="6", user_id="u1", file=file)

    assert isinstance(excinfo.value.__cause__, ValueError)
    store_mock.assert_not_called()  # scope resolution raised first
    assert [p for p in Path(ps.settings.DOWNLOAD_PATH).rglob("*") if p.is_file()] == []


# ── Task 3.2: upload_new_version dual-track (the "另一子路径" write point) ──


@pytest.mark.asyncio
async def test_new_version_flag_on_writes_object_store_path(service, monkeypatch):
    monkeypatch.setattr(ps.settings, "FEATURE_UNIFIED_STORAGE", True)
    _seed_project(service.repo, 10, team_id=42)

    file_row = {
        "id": 500,
        "project_id": 10,
        "filename": "orig.mp4",
        "file_path": "mediatrack/10/orig.mp4",
        "current_version": 1,
    }
    service.repo.files[500] = file_row
    service.repo.versions.append(
        {
            "file_id": "500",
            "version_number": 1,
            "filename": "orig.mp4",
            "file_path": "mediatrack/10/orig.mp4",
        }
    )

    captured = {}

    async def fake_store_local_file(
        *, scope_id, source_path, mime, filename=None, sha256=None, store=None
    ):
        assert Path(source_path).exists()
        captured["scope_id"] = scope_id
        captured["source_path"] = source_path
        return StoredObject(
            file_path=f"sb://library/t{scope_id}/ab/cd/{sha256}.bin",
            size_bytes=Path(source_path).stat().st_size,
            sha256=sha256,
        )

    monkeypatch.setattr(ps, "store_local_file", fake_store_local_file)

    file = FakeUploadFile("clip-v2.mp4", b"version two bytes", "video/mp4")
    version = await service.upload_new_version(
        project_id="10", file_id="500", user_id="u1", file=file, notes="v2"
    )

    assert version["version_number"] == 2
    assert version["file_path"].startswith("sb://library/t42/")
    assert service.repo.files[500]["file_path"] == version["file_path"]
    assert service.repo.files[500]["current_version"] == 2
    assert captured["scope_id"] == 42
    assert not Path(captured["source_path"]).exists()


@pytest.mark.asyncio
async def test_new_version_flag_off_keeps_legacy_filesystem_path(service, monkeypatch):
    monkeypatch.setattr(ps.settings, "FEATURE_UNIFIED_STORAGE", False)
    _seed_project(service.repo, 11, team_id=7)

    service.repo.files[501] = {
        "id": 501,
        "project_id": 11,
        "filename": "orig.txt",
        "file_path": "mediatrack/11/orig.txt",
        "current_version": 1,
    }
    service.repo.versions.append(
        {
            "file_id": "501",
            "version_number": 1,
            "filename": "orig.txt",
            "file_path": "mediatrack/11/orig.txt",
        }
    )

    called = {"n": 0}

    async def fake_store_local_file(*args, **kwargs):
        called["n"] += 1
        raise AssertionError("store_local_file must not be called when flag is off")

    monkeypatch.setattr(ps, "store_local_file", fake_store_local_file)

    file = FakeUploadFile("notes-v2.txt", b"version two legacy", "text/plain")
    version = await service.upload_new_version(
        project_id="11", file_id="501", user_id="u1", file=file
    )

    assert called["n"] == 0
    expected = "mediatrack/11/versions/501/v2_notes-v2.txt"
    assert version["file_path"] == expected
    assert service.repo.files[501]["file_path"] == expected

    on_disk = Path(ps.settings.DOWNLOAD_PATH) / expected
    assert on_disk.exists()
    assert on_disk.read_bytes() == b"version two legacy"


@pytest.mark.asyncio
async def test_new_version_store_failure_raises_typed_error_and_writes_nothing(
    service, monkeypatch
):
    monkeypatch.setattr(ps.settings, "FEATURE_UNIFIED_STORAGE", True)
    _seed_project(service.repo, 12, team_id=9)

    service.repo.files[502] = {
        "id": 502,
        "project_id": 12,
        "filename": "orig.png",
        "file_path": "mediatrack/12/orig.png",
        "current_version": 1,
    }
    service.repo.versions.append(
        {
            "file_id": "502",
            "version_number": 1,
            "filename": "orig.png",
            "file_path": "mediatrack/12/orig.png",
        }
    )

    async def failing_store_local_file(*args, **kwargs):
        raise RuntimeError("storage-api unreachable")

    monkeypatch.setattr(ps, "store_local_file", failing_store_local_file)

    file = FakeUploadFile("photo-v2.png", b"never-saved", "image/png")
    with pytest.raises(ObjectStoreWriteFailed) as excinfo:
        await service.upload_new_version(
            project_id="12", file_id="502", user_id="u1", file=file
        )

    assert excinfo.value.details["where"] == "project_upload_new_version"
    assert excinfo.value.details["file_id"] == "502"
    assert [p for p in Path(ps.settings.DOWNLOAD_PATH).rglob("*") if p.is_file()] == []
    assert [v["version_number"] for v in service.repo.versions] == [1]
    assert service.repo.files[502]["current_version"] == 1


@pytest.mark.asyncio
async def test_new_version_scope_resolution_failure_is_a_storage_failure(
    service, monkeypatch
):
    """Same contract as upload_file: a scope-resolution raise inside the
    flag-on track is the typed storage failure, with the ValueError as cause."""
    monkeypatch.setattr(ps.settings, "FEATURE_UNIFIED_STORAGE", True)
    _seed_project(service.repo, 13, team_id=None, owner_id="orphan-owner")

    service.repo.files[503] = {
        "id": 503,
        "project_id": 13,
        "filename": "orig.txt",
        "file_path": "mediatrack/13/orig.txt",
        "current_version": 1,
    }
    service.repo.versions.append(
        {
            "file_id": "503",
            "version_number": 1,
            "filename": "orig.txt",
            "file_path": "mediatrack/13/orig.txt",
        }
    )

    async def failing_resolve_personal_team_id(user_id):
        raise ValueError(f"No personal team found for user {user_id}")

    monkeypatch.setattr(
        ps, "_resolve_personal_team_id", failing_resolve_personal_team_id
    )
    store_mock = MagicMock()
    monkeypatch.setattr(ps, "store_local_file", store_mock)

    file = FakeUploadFile("orphan-v2.txt", b"never-saved", "text/plain")
    with pytest.raises(ObjectStoreWriteFailed) as excinfo:
        await service.upload_new_version(
            project_id="13", file_id="503", user_id="u1", file=file
        )

    assert isinstance(excinfo.value.__cause__, ValueError)
    store_mock.assert_not_called()
    assert [p for p in Path(ps.settings.DOWNLOAD_PATH).rglob("*") if p.is_file()] == []
    assert [v["version_number"] for v in service.repo.versions] == [1]


# ── Task 3.2 audit note: no serve/tooling-read points exist in the grep'd
# files ───────────────────────────────────────────────────────────────────
#
# `grep -rn "mediatrack\|DOWNLOAD_PATH" backend/app/api/projects_router.py
#  backend/app/services/library/projects_service.py`
#
# turns up exactly the write points covered above (upload_file @ 3.1,
# upload_new_version @ 3.2) — zero FileResponse/StreamingResponse/serve
# endpoints and zero "read an existing stored file for tooling" points in
# either file. projects_router.py has no file-serving route at all; the
# only runtime consumer of a project_files.file_path value is the generic
# legacy-fallback `@app.get("/media/{file_path:path}")` catch-all in
# app/main.py (`serve_media_by_path`), which lives outside this task's
# grep scope, is untested today, and is shared by multiple unrelated
# routes (`/media/{id}`, `/media/{id}/cover`) in one try/except block.
# Left unmodified — see the task report's Concerns section for the
# follow-up recommendation.
