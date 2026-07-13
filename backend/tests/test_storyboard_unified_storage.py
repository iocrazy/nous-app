"""Task 3.3 — dual-track storyboard originals + retire NAS_BASE_PATH.

Exercises StoryboardService against stubbed repos (in-memory dicts) so the
tests pin ONLY the storage-selection logic — flag on routes the uploaded
image bytes through store_local_file() into the "sb://library/..." shape,
flag off (and any storage failure, including scope-id coercion) preserves
the legacy teams/{team}/storyboard/{project}/images/... filesystem path.
Mirrors the FakeRepo style of test_projects_unified_storage.py.

Derived-vs-original judgement call (documented per the task brief): the
generated storyboard image (``storyboard_assets.file_path`` from
``upload_image``) is the ONLY thing dual-tracked. Preview thumbnails and
split-grid cells are regenerable derivatives (same policy as
thumbnails/HLS elsewhere — given the source bytes/asset, they can always
be reproduced deterministically) and ALWAYS stay on the local filesystem
under settings.DOWNLOAD_PATH, regardless of the storage track the source
asset used. ``split_image_asset`` reads its source through materialize()
so it transparently handles both a legacy fs-relative source and an
sb://-shaped source.

Also pins the NAS_BASE_PATH config-source retirement: the legacy env var is
no longer read anywhere in these files — the fs-fallback root is
settings.DOWNLOAD_PATH exclusively, even if NAS_BASE_PATH is set in the
environment to a different value.
"""

import hashlib
import os
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PIL import Image

import app.services.storyboard.storyboard_service as sbs
from app.services.library.media_storage import StoredObject


def _png_bytes(width: int = 20, height: int = 10, color=(200, 50, 50)) -> bytes:
    """A tiny real PNG — small enough to be fast, big enough to 2x2-split."""
    img = Image.new("RGB", (width, height), color=color)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class FakeProjectRepo:
    def __init__(self):
        self.projects: dict[int, dict] = {}

    async def get_by_id(self, project_id):
        row = self.projects.get(int(project_id))
        return dict(row) if row else None

    async def create(self, data: dict) -> dict:
        pid = data.get("id") or (max(self.projects.keys(), default=0) + 1)
        row = {"id": pid, **data}
        self.projects[pid] = row
        return dict(row)


class FakeAssetRepo:
    def __init__(self):
        self.assets: dict[int, dict] = {}
        self._next_id = 1000

    async def find_by_hash(self, project_id, file_hash):
        for row in self.assets.values():
            if (
                str(row["project_id"]) == str(project_id)
                and row["file_hash"] == file_hash
            ):
                return dict(row)
        return None

    async def create(self, data: dict) -> dict:
        aid = self._next_id
        self._next_id += 1
        row = {"id": aid, **data}
        self.assets[aid] = row
        return dict(row)


class FakeFrameRepo:
    def __init__(self):
        self.frames: list[dict] = []

    async def create(self, data: dict) -> dict:
        row = {"id": len(self.frames) + 1, **data}
        self.frames.append(row)
        return row


def _seed_project(repo: FakeProjectRepo, project_id: int, *, team_id) -> dict:
    row = {"id": project_id, "team_id": team_id, "name": "P", "created_by": "user-1"}
    repo.projects[project_id] = row
    return row


@pytest.fixture
def service(monkeypatch, tmp_path):
    monkeypatch.setattr(sbs.settings, "DOWNLOAD_PATH", str(tmp_path))
    # Poison NAS_BASE_PATH in the environment — if any code path still reads
    # it, the test's path assertions (which key off settings.DOWNLOAD_PATH)
    # would fail, pinning the config-source retirement.
    monkeypatch.setenv("NAS_BASE_PATH", "/should/never/be/read")
    svc = sbs.StoryboardService()
    svc.project_repo = FakeProjectRepo()
    svc.asset_repo = FakeAssetRepo()
    svc.frame_repo = FakeFrameRepo()
    return svc


# ── upload_image dual-track ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_upload_image_flag_on_writes_object_store_path(service, monkeypatch):
    monkeypatch.setattr(sbs.settings, "FEATURE_UNIFIED_STORAGE", True)
    _seed_project(service.project_repo, 1, team_id=42)

    captured = {}

    async def fake_store_local_file(
        *, scope_id, source_path, mime, filename=None, sha256=None, store=None
    ):
        assert Path(source_path).exists()
        captured["scope_id"] = scope_id
        captured["source_path"] = source_path
        return StoredObject(
            file_path=f"sb://library/t{scope_id}/ab/cd/{sha256}.png",
            size_bytes=Path(source_path).stat().st_size,
            sha256=sha256,
        )

    monkeypatch.setattr(sbs, "store_local_file", fake_store_local_file)

    data = _png_bytes()
    result = await service.upload_image(
        project_id="1", file_bytes=data, filename="shot.png", content_type="image/png"
    )

    created = service.asset_repo.assets[list(service.asset_repo.assets.keys())[0]]
    assert created["file_path"].startswith("sb://library/t42/")
    assert captured["scope_id"] == 42
    # tmp file cleaned up (finally: tmp_path.unlink still runs)
    assert not Path(captured["source_path"]).exists()
    assert result["asset_id"] == str(created["id"])


@pytest.mark.asyncio
async def test_upload_image_preview_always_written_to_filesystem(service, monkeypatch):
    """Even with the flag on and the original routed to object storage, the
    preview thumbnail is a regenerable derivative and always lands on the
    local filesystem (HLS/thumbnail precedent)."""
    monkeypatch.setattr(sbs.settings, "FEATURE_UNIFIED_STORAGE", True)
    _seed_project(service.project_repo, 2, team_id=7)

    async def fake_store_local_file(**kwargs):
        return StoredObject(
            file_path=f"sb://library/t7/ab/cd/{kwargs['sha256']}.png",
            size_bytes=len(_png_bytes()),
            sha256=kwargs["sha256"],
        )

    monkeypatch.setattr(sbs, "store_local_file", fake_store_local_file)

    data = _png_bytes()
    result = await service.upload_image(
        project_id="2", file_bytes=data, filename="shot.png", content_type="image/png"
    )

    created = next(iter(service.asset_repo.assets.values()))
    assert created["file_path"].startswith("sb://library/")
    assert created["preview_path"].startswith("teams/7/storyboard/2/previews/")
    on_disk = Path(sbs.settings.DOWNLOAD_PATH) / created["preview_path"]
    assert on_disk.exists()
    assert result["preview_url"]


@pytest.mark.asyncio
async def test_upload_image_flag_off_keeps_legacy_filesystem_path(service, monkeypatch):
    monkeypatch.setattr(sbs.settings, "FEATURE_UNIFIED_STORAGE", False)
    _seed_project(service.project_repo, 3, team_id=7)

    called = {"n": 0}

    async def fake_store_local_file(*args, **kwargs):
        called["n"] += 1
        raise AssertionError("store_local_file must not be called when flag is off")

    monkeypatch.setattr(sbs, "store_local_file", fake_store_local_file)

    data = _png_bytes()
    file_hash = hashlib.sha256(data).hexdigest()
    result = await service.upload_image(
        project_id="3", file_bytes=data, filename="shot.png", content_type="image/png"
    )

    assert called["n"] == 0
    created = next(iter(service.asset_repo.assets.values()))
    expected = f"teams/7/storyboard/3/images/{file_hash}.png"
    assert created["file_path"] == expected

    on_disk = Path(sbs.settings.DOWNLOAD_PATH) / expected
    assert on_disk.exists()
    assert on_disk.read_bytes() == data
    assert result["image_url"] == f"/api/v1/storyboard/assets/{created['id']}/file"


@pytest.mark.asyncio
async def test_upload_image_store_failure_falls_back_to_filesystem(
    service, monkeypatch
):
    monkeypatch.setattr(sbs.settings, "FEATURE_UNIFIED_STORAGE", True)
    _seed_project(service.project_repo, 4, team_id=9)

    async def failing_store_local_file(*args, **kwargs):
        raise RuntimeError("storage-api unreachable")

    monkeypatch.setattr(sbs, "store_local_file", failing_store_local_file)

    err_mock = MagicMock()
    monkeypatch.setattr(sbs.logger, "error", err_mock)

    data = _png_bytes()
    file_hash = hashlib.sha256(data).hexdigest()
    await service.upload_image(
        project_id="4", file_bytes=data, filename="shot.png", content_type="image/png"
    )

    created = next(iter(service.asset_repo.assets.values()))
    expected = f"teams/9/storyboard/4/images/{file_hash}.png"
    assert created["file_path"] == expected

    on_disk = Path(sbs.settings.DOWNLOAD_PATH) / expected
    assert on_disk.exists()

    err_mock.assert_called_once()
    assert "unified-storage write failed" in err_mock.call_args[0][0]


@pytest.mark.asyncio
async def test_upload_image_scope_resolution_failure_falls_back_to_filesystem(
    service, monkeypatch
):
    """PIN (mirrors the projects_service review fix): scope-id coercion is
    part of the storage track. A project with no usable team_id must
    degrade to the fs fallback, never let the exception escape."""
    monkeypatch.setattr(sbs.settings, "FEATURE_UNIFIED_STORAGE", True)
    # No team_id at all → team_id_raw is None → int(None) raises TypeError
    # inside the flag-on try block.
    service.project_repo.projects[5] = {"id": 5, "team_id": None, "name": "P"}

    store_mock = MagicMock()
    monkeypatch.setattr(sbs, "store_local_file", store_mock)
    err_mock = MagicMock()
    monkeypatch.setattr(sbs.logger, "error", err_mock)

    data = _png_bytes()
    file_hash = hashlib.sha256(data).hexdigest()
    await service.upload_image(
        project_id="5", file_bytes=data, filename="shot.png", content_type="image/png"
    )

    created = next(iter(service.asset_repo.assets.values()))
    expected = f"teams/unknown/storyboard/5/images/{file_hash}.png"
    assert created["file_path"] == expected
    on_disk = Path(sbs.settings.DOWNLOAD_PATH) / expected
    assert on_disk.exists()

    store_mock.assert_not_called()
    err_mock.assert_called_once()
    assert "unified-storage write failed" in err_mock.call_args[0][0]


@pytest.mark.asyncio
async def test_upload_image_missing_project_raises(service):
    data = _png_bytes()
    with pytest.raises(Exception):
        await service.upload_image(
            project_id="999",
            file_bytes=data,
            filename="shot.png",
            content_type="image/png",
        )


@pytest.mark.asyncio
async def test_upload_image_dedup_hit_skips_storage_entirely(service, monkeypatch):
    monkeypatch.setattr(sbs.settings, "FEATURE_UNIFIED_STORAGE", True)
    _seed_project(service.project_repo, 6, team_id=1)
    data = _png_bytes()
    file_hash = hashlib.sha256(data).hexdigest()
    service.asset_repo.assets[1] = {
        "id": 1,
        "project_id": "6",
        "file_hash": file_hash,
        "file_path": "sb://library/t1/ab/cd/existing.png",
        "preview_path": None,
    }

    store_mock = MagicMock()
    monkeypatch.setattr(sbs, "store_local_file", store_mock)

    result = await service.upload_image(
        project_id="6", file_bytes=data, filename="shot.png", content_type="image/png"
    )

    store_mock.assert_not_called()
    assert result["asset_id"] == "1"


# ── split_image_asset: derivative outputs always stay fs ───────────────────


@pytest.mark.asyncio
async def test_split_reads_object_store_source_via_materialize(
    service, monkeypatch, tmp_path
):
    """Source asset is sb://-shaped (uploaded while the flag was on) —
    split_image_asset must resolve it via materialize() rather than joining
    it onto settings.DOWNLOAD_PATH directly, and the split outputs still
    land on the local filesystem regardless."""
    _seed_project(service.project_repo, 7, team_id=3)

    source_local = tmp_path / "source_upload.png"
    source_local.write_bytes(_png_bytes(width=20, height=10))

    source_asset = {
        "id": "500",
        "project_id": "7",
        "file_path": "sb://library/t3/ab/cd/somehash.png",
        "file_hash": "somehash" + "0" * 56,
    }
    monkeypatch.setattr(service, "get_asset", lambda asset_id: _async(source_asset))

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def fake_materialize(file_path):
        assert file_path == source_asset["file_path"]
        yield source_local

    monkeypatch.setattr(sbs, "materialize", fake_materialize)

    result = await service.split_image_asset(
        project_id="7", asset_id="500", rows=2, cols=2
    )

    assert result["rows"] == 2
    assert result["cols"] == 2
    assert len(result["frames"]) == 4
    for frame in result["frames"]:
        asset_row = service.asset_repo.assets[int(frame["asset_id"])]
        # Split cells are derivatives — they ALWAYS stay on the local
        # filesystem under settings.DOWNLOAD_PATH, never sb://, even though
        # the source asset itself was object-store-backed.
        assert not asset_row["file_path"].startswith("sb://")
        assert asset_row["file_path"].startswith("teams/3/storyboard/7/splits/")
        on_disk = Path(sbs.settings.DOWNLOAD_PATH) / asset_row["file_path"]
        assert on_disk.exists()


@pytest.mark.asyncio
async def test_split_reads_legacy_filesystem_source(service, monkeypatch, tmp_path):
    """Source asset uses the legacy fs-relative shape — materialize() (real,
    unmocked here) must resolve it under settings.DOWNLOAD_PATH exactly like
    before."""
    _seed_project(service.project_repo, 8, team_id=4)

    rel_path = "teams/4/storyboard/8/images/legacyhash.png"
    abs_path = Path(sbs.settings.DOWNLOAD_PATH) / rel_path
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_bytes(_png_bytes(width=20, height=10))

    source_asset = {
        "id": "501",
        "project_id": "8",
        "file_path": rel_path,
        "file_hash": "legacyhash" + "0" * 54,
    }
    monkeypatch.setattr(service, "get_asset", lambda asset_id: _async(source_asset))

    result = await service.split_image_asset(
        project_id="8", asset_id="501", rows=1, cols=2
    )

    assert len(result["frames"]) == 2
    for frame in result["frames"]:
        asset_row = service.asset_repo.assets[int(frame["asset_id"])]
        assert asset_row["file_path"].startswith("teams/4/storyboard/8/splits/")
        on_disk = Path(sbs.settings.DOWNLOAD_PATH) / asset_row["file_path"]
        assert on_disk.exists()


async def _async(value):
    return value


# ── _ensure_nas_directories: fs-fallback-only provisioning ─────────────────


@pytest.mark.asyncio
async def test_create_project_flag_on_skips_directory_provisioning(
    service, monkeypatch
):
    monkeypatch.setattr(sbs.settings, "FEATURE_UNIFIED_STORAGE", True)
    ensure_mock = MagicMock()
    monkeypatch.setattr(service, "_ensure_nas_directories", ensure_mock)

    await service.create_project(team_id="42", user_id="u1", name="Proj")

    ensure_mock.assert_not_called()


@pytest.mark.asyncio
async def test_create_project_flag_off_provisions_directories_via_download_path(
    service, monkeypatch
):
    monkeypatch.setattr(sbs.settings, "FEATURE_UNIFIED_STORAGE", False)

    await service.create_project(team_id="42", user_id="u1", name="Proj")

    project = next(iter(service.project_repo.projects.values()))
    project_root = (
        Path(sbs.settings.DOWNLOAD_PATH)
        / "teams"
        / "42"
        / "storyboard"
        / str(project["id"])
    )
    assert project_root.exists()
    # NAS_BASE_PATH env is poisoned by the fixture — this proves it was
    # never consulted.
    assert os.environ["NAS_BASE_PATH"] == "/should/never/be/read"
    assert not Path("/should/never/be/read").exists()
