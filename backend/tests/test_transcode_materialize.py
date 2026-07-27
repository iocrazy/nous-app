"""Task 2.4 — HLS transcode source read via ``materialize()`` + derived HLS
output directory for sb:// rows.

``TranscodeService.transcode_version`` used to join ``Path(DOWNLOAD_PATH) /
file_path`` directly to find the source video, then always put the HLS
output next to it (``source.parent / "hls"``) — both impossible for a
Supabase-Storage-backed (``sb://``) source. The fix wraps the source read in
``materialize()`` (real fs path for legacy rows, downloaded temp file for
sb:// rows) and, for sb:// rows only, derives the HLS output directory from
resource/version identity instead of the source's (nonexistent) parent dir.
Legacy fs rows keep the exact ``{source.parent}/hls`` layout — zero
behavior change (pinned by the second test below).

House style matches test_transcode_size_gate.py (in-memory `_FakeRepo`,
`_settings_stub` for the async DB-setting reads).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.services.media.transcode import transcode_service as ts_mod
from app.services.media.transcode.transcode_probe import TranscodeProbe
from app.services.media.transcode.transcode_service import TranscodeService

pytestmark = pytest.mark.unit

_MB = 1024 * 1024


class _FakeRepo:
    """Records update_version calls so tests can assert on transcode_status."""

    def __init__(self, version: dict) -> None:
        self._version = version
        self.updates: list[tuple[str, dict]] = []

    async def get_version_by_id(self, version_id: str) -> dict:
        return self._version

    async def update_version(self, version_id: str, data: dict) -> dict:
        self.updates.append((version_id, data))
        return {}


def _settings_stub(min_size_mb: str = "0"):
    table = {"transcode_enabled": "true", "transcode_min_size_mb": min_size_mb}

    async def _stub(key):
        return table.get(key)

    return staticmethod(_stub)


async def test_hls_dir_derived_for_sb_source(monkeypatch, tmp_path):
    """sb:// source: HLS output lands under
    derived/hls/{resource_id}/{version_id}/, NOT next to a (nonexistent)
    source.parent — and the source is read via materialize(), never a raw
    DOWNLOAD_PATH join."""
    monkeypatch.setattr(ts_mod.settings, "DOWNLOAD_PATH", str(tmp_path))
    monkeypatch.setattr(TranscodeService, "_get_db_setting", _settings_stub())

    file_path = "sb://library/t1/ab/cd/deadbeef.mp4"
    svc = TranscodeService()
    svc.repo = _FakeRepo({"file_path": file_path, "file_size_bytes": 500 * _MB})

    # materialize() for an sb:// row streams the object to a temp file that
    # is NOT anywhere under DOWNLOAD_PATH (deleted on exit in the real
    # implementation) — simulate that shape with a fixture living outside
    # DOWNLOAD_PATH entirely, so `source.parent / "hls"` would 404 if the
    # implementation still tried it.
    materialized_dir = tmp_path.parent / "materialize-tmp"
    materialized_dir.mkdir(exist_ok=True)
    real_src = materialized_dir / "downloaded.mp4"
    real_src.write_bytes(b"fake video bytes")

    seen: dict = {}

    @asynccontextmanager
    async def fake_materialize(fp: str):
        seen["file_path"] = fp
        yield real_src

    monkeypatch.setattr(ts_mod, "materialize", fake_materialize)
    monkeypatch.setattr(ts_mod, "resolve_media_source", lambda fp: _ObjectStoreLoc())

    probe_calls: list[str] = []

    async def fake_probe_resolution(self, filepath):
        probe_calls.append(filepath)
        return 1920, 1080

    monkeypatch.setattr(TranscodeProbe, "probe_resolution", fake_probe_resolution)
    monkeypatch.setattr(
        TranscodeProbe, "probe_duration", AsyncMock(return_value=10.0)
    )
    # non-h264 codec skips the fast path; empty tier selection short-circuits
    # to "skipped" right after the hls_dir mkdir — no real ffmpeg needed.
    monkeypatch.setattr(
        TranscodeProbe, "probe_codecs", AsyncMock(return_value=("hevc", "aac"))
    )
    monkeypatch.setattr(TranscodeService, "_select_tiers", AsyncMock(return_value=[]))

    result = await svc.transcode_version("r1", "v1")

    assert seen["file_path"] == file_path, "source must be read via materialize()"
    assert probe_calls == [str(real_src)], "ffprobe must run on the materialized path"
    assert result["status"] == "skipped"

    expected_dir = tmp_path / "derived" / "hls" / "r1" / "v1"
    assert expected_dir.exists(), f"expected derived HLS dir at {expected_dir}"
    # Confirms the (impossible) legacy layout was never attempted.
    assert not (materialized_dir / "hls").exists()


async def test_hls_dir_legacy_fs_source_unchanged(monkeypatch, tmp_path):
    """Legacy fs row: HLS output stays at {source.parent}/hls exactly as
    before — zero behavior change. Source is still read through the real
    materialize() (fs branch), not stubbed."""
    monkeypatch.setattr(ts_mod.settings, "DOWNLOAD_PATH", str(tmp_path))
    monkeypatch.setattr(TranscodeService, "_get_db_setting", _settings_stub())

    rel = "teams/1/uploads/9/v1/video.mp4"
    src_dir = tmp_path / "teams/1/uploads/9/v1"
    src_dir.mkdir(parents=True)
    (src_dir / "video.mp4").write_bytes(b"fake video bytes")

    svc = TranscodeService()
    svc.repo = _FakeRepo({"file_path": rel, "file_size_bytes": 500 * _MB})

    monkeypatch.setattr(
        TranscodeProbe,
        "probe_resolution",
        AsyncMock(return_value=(1920, 1080)),
    )
    monkeypatch.setattr(
        TranscodeProbe, "probe_duration", AsyncMock(return_value=10.0)
    )
    monkeypatch.setattr(
        TranscodeProbe, "probe_codecs", AsyncMock(return_value=("hevc", "aac"))
    )
    monkeypatch.setattr(TranscodeService, "_select_tiers", AsyncMock(return_value=[]))

    result = await svc.transcode_version("r1", "v1")

    assert result["status"] == "skipped"
    expected_dir = src_dir / "hls"
    assert expected_dir.exists(), f"expected legacy HLS dir at {expected_dir}"
    derived_dir = tmp_path / "derived"
    assert not derived_dir.exists(), "legacy row must never use the derived/ tree"


class _ObjectStoreLoc:
    is_object_store = True


# ─── Task 2.4b — _trigger_transcode_async gate works for sb:// rows ─────────
#
# The automatic post-upload HLS dispatch gate
# (ResourcesService._trigger_transcode_async) used to join
# Path(DOWNLOAD_PATH)/version["file_path"] and bail on `.exists()` — always
# False for an sb:// row, so large object-store videos silently never got
# transcoded. Fixed: the gate resolves through resolve_media_source; sb://
# rows gate from row data already in hand (file_size_bytes written at
# upload, duration_seconds persisted by postprocess Phase A before this
# Phase C dispatch), fs rows keep the byte-identical legacy stat/ffprobe
# path.

_MIN_GATE_BYTES = 200 * _MB  # comfortably above the 100 MB dispatch gate


def _dispatch_env(monkeypatch, version_row: dict):
    """Build a ResourcesService with a stubbed repo + captured dispatch."""
    import app.services.library.resources_service as rs

    svc = rs.ResourcesService()

    updates: list[tuple[str, dict]] = []
    dispatched: list[dict] = []

    class _Repo:
        async def get_version_by_id(self, version_id):
            return version_row

        async def update_version(self, version_id, data):
            updates.append((version_id, data))
            return {}

    svc.repo = _Repo()

    async def _fake_start_workflow_routed(name, **kwargs):
        dispatched.append({"name": name, **kwargs})
        return "wf-1"

    monkeypatch.setattr(
        "app.services.infra.dbos_orchestrator.start_workflow_routed",
        _fake_start_workflow_routed,
    )
    return svc, updates, dispatched


async def test_gate_sb_version_meeting_size_gate_dispatches(monkeypatch, tmp_path):
    """sb:// version row with file_size_bytes above the gate -> transcode
    workflow IS dispatched (no local file exists anywhere)."""
    import app.services.library.resources_service as rs

    monkeypatch.setattr(rs.settings, "DOWNLOAD_PATH", str(tmp_path))
    svc, updates, dispatched = _dispatch_env(
        monkeypatch,
        {
            "id": "v1",
            "file_path": "sb://library/t1/ab/cd/deadbeef.mp4",
            "file_size_bytes": _MIN_GATE_BYTES,
            "duration_seconds": 1200,
        },
    )

    await svc._trigger_transcode_async("r1", "v1", "video/mp4", user_id="u1")

    assert len(dispatched) == 1, "sb:// row above the gate must dispatch"
    assert dispatched[0]["name"] == "transcode"
    assert dispatched[0]["dbos_workflow_kwargs"]["version_id"] == "v1"
    assert ("v1", {"transcode_status": "pending"}) in updates


async def test_gate_sb_version_below_gate_not_dispatched(monkeypatch, tmp_path):
    """sb:// version row below both size and duration gates -> skipped."""
    import app.services.library.resources_service as rs

    monkeypatch.setattr(rs.settings, "DOWNLOAD_PATH", str(tmp_path))
    svc, updates, dispatched = _dispatch_env(
        monkeypatch,
        {
            "id": "v1",
            "file_path": "sb://library/t1/ab/cd/deadbeef.mp4",
            "file_size_bytes": 10 * _MB,
            "duration_seconds": 60,
        },
    )

    await svc._trigger_transcode_async("r1", "v1", "video/mp4", user_id="u1")

    assert dispatched == [], "sb:// row below the gate must not dispatch"
    assert updates == []


async def test_gate_legacy_fs_missing_file_still_skips(monkeypatch, tmp_path):
    """Legacy fs row whose file is missing on disk -> skip, exactly the
    pre-existing behavior (the fs existence check is retained for fs rows)."""
    import app.services.library.resources_service as rs

    monkeypatch.setattr(rs.settings, "DOWNLOAD_PATH", str(tmp_path))
    svc, updates, dispatched = _dispatch_env(
        monkeypatch,
        {
            "id": "v1",
            "file_path": "teams/1/uploads/9/v1/video.mp4",
            "file_size_bytes": _MIN_GATE_BYTES,
            "duration_seconds": 1200,
        },
    )

    await svc._trigger_transcode_async("r1", "v1", "video/mp4", user_id="u1")

    assert dispatched == [], "missing fs file must still skip dispatch"
    assert updates == []


async def test_gate_legacy_fs_large_file_dispatches(monkeypatch, tmp_path):
    """Legacy fs row with a real on-disk file above the size gate ->
    dispatched, from the file's actual stat (not row metadata)."""
    import app.services.library.resources_service as rs

    monkeypatch.setattr(rs.settings, "DOWNLOAD_PATH", str(tmp_path))
    rel = "teams/1/uploads/9/v1/video.mp4"
    abs_path = tmp_path / rel
    abs_path.parent.mkdir(parents=True)
    # Sparse file: 200 MB apparent size without writing 200 MB of bytes.
    with open(abs_path, "wb") as fh:
        fh.truncate(_MIN_GATE_BYTES)

    svc, updates, dispatched = _dispatch_env(
        monkeypatch,
        {
            "id": "v1",
            "file_path": rel,
            # Row size deliberately BELOW the gate: proves the fs branch
            # still gates on the actual file stat, unchanged.
            "file_size_bytes": 1 * _MB,
            "duration_seconds": None,
        },
    )

    await svc._trigger_transcode_async("r1", "v1", "video/mp4", user_id="u1")

    assert len(dispatched) == 1, "large on-disk legacy file must dispatch"
    assert ("v1", {"transcode_status": "pending"}) in updates
