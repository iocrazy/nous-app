"""I1(discard-review): H.264 快通道也必须回收本地 HLS 目录,且只能在两个
phase 都 publish 完之后才删。

标准全编码路径早就有 ``discard_local_source(hls_dir, relative_hls)``,但
h264 快通道(``is_h264`` 分支 —— 绝大多数下载的 MP4 都会命中)完全没接,
是 ``derived/hls/`` 泄漏的主要来源。修复把同一句 discard 加到快通道
return 之前;因为快通道内部有两次 publish(phase-1 copy-only 段落 +
phase-2 增强 tiers 的 republish),discard 必须放在第二次 publish 之后 ——
提前删会让 phase 2 republish 拿不到本地源文件。

本测试用一个记录 publish 调用次数的假 HlsPublisher,钉住:discard 发生时
publish_calls 必须已经是 2(两个 phase 都发布完),而不是 1(只发布了
phase-1 就被删)。
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.services.media.transcode import transcode_service as ts_mod
from app.services.media.transcode.transcode_probe import TranscodeProbe
from app.services.media.transcode.transcode_service import TranscodeService

pytestmark = pytest.mark.unit

_MB = 1024 * 1024


class _FakeRepo:
    def __init__(self, version: dict) -> None:
        self._version = version
        self.updates: list[tuple[str, dict]] = []

    async def get_version_by_id(self, version_id: str) -> dict:
        return self._version

    async def update_version(self, version_id: str, data: dict) -> dict:
        self.updates.append((version_id, data))
        return {}


class _FakeHls:
    """Records publish call count so the test can pin discard's position
    relative to phase-1 / phase-2 without touching real ffmpeg/storage."""

    def __init__(self) -> None:
        self.publish_calls = 0

    async def publish(self, hls_dir, base, resource_id, version_id):
        self.publish_calls += 1
        return "sb://library/hls/r1/v1/master.m3u8"

    async def clear(self, resource_id, version_id):
        return None

    def write_master_playlist(self, *args, **kwargs):
        return None


def _settings_stub():
    table = {"transcode_enabled": "true", "transcode_min_size_mb": "0"}

    async def _stub(key):
        return table.get(key)

    return staticmethod(_stub)


@pytest.mark.asyncio
async def test_h264_fast_path_discards_only_after_both_phases_publish(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(ts_mod.settings, "DOWNLOAD_PATH", str(tmp_path))
    monkeypatch.setattr(TranscodeService, "_get_db_setting", _settings_stub())

    rel = "teams/1/uploads/9/v1/video.mp4"
    src_dir = tmp_path / "teams/1/uploads/9/v1"
    src_dir.mkdir(parents=True)
    (src_dir / "video.mp4").write_bytes(b"fake video bytes")

    svc = TranscodeService()
    svc.repo = _FakeRepo({"file_path": rel, "file_size_bytes": 500 * _MB})
    fake_hls = _FakeHls()
    svc._hls = fake_hls

    monkeypatch.setattr(
        TranscodeProbe, "probe_resolution", AsyncMock(return_value=(1920, 1080))
    )
    monkeypatch.setattr(TranscodeProbe, "probe_duration", AsyncMock(return_value=10.0))
    monkeypatch.setattr(
        TranscodeProbe, "probe_codecs", AsyncMock(return_value=("h264", "aac"))
    )
    monkeypatch.setattr(TranscodeProbe, "probe_bitrate", AsyncMock(return_value=2000))
    monkeypatch.setattr(
        TranscodeService, "_transcode_passthrough", AsyncMock(return_value=True)
    )

    # One applicable tier -> forces phase 2 to actually run and publish a
    # second time (the scenario the finding warns about).
    tier = ts_mod.TranscodeTier(
        name="480p", width=854, height=480, bitrate=1500, audio_bitrate=128
    )
    monkeypatch.setattr(
        TranscodeService, "_select_tiers", AsyncMock(return_value=[tier])
    )
    monkeypatch.setattr(
        TranscodeService, "_encode_tiers", AsyncMock(return_value=[tier])
    )

    call_order: list[tuple] = []
    real_discard = ts_mod.discard_local_source

    def _tracking_discard(local_path, stored_path):
        call_order.append(("discard", fake_hls.publish_calls, stored_path))
        return real_discard(local_path, stored_path)

    monkeypatch.setattr(ts_mod, "discard_local_source", _tracking_discard)

    result = await svc.transcode_version("r1", "v1")

    assert result == {
        "status": "completed",
        "hls_path": "sb://library/hls/r1/v1/master.m3u8",
    }
    assert fake_hls.publish_calls == 2, "phase-1 and phase-2 must both publish"
    assert call_order == [("discard", 2, "sb://library/hls/r1/v1/master.m3u8")], (
        "discard must run exactly once, and only after BOTH phase-1 and "
        "phase-2 have published (publish_calls must already be 2 when it runs)"
    )

    # The local hls_dir really was recycled (real discard_local_source ran).
    hls_dir = src_dir / "hls"
    assert not hls_dir.exists()


@pytest.mark.asyncio
async def test_h264_fast_path_no_applicable_tiers_still_discards_after_phase1(
    monkeypatch, tmp_path
):
    """无可用 enhancement tier(phase 2 跳过)时,phase-1 单独发布一次后仍要
    回收本地目录——不能因为没有 phase 2 就漏掉 discard。"""
    monkeypatch.setattr(ts_mod.settings, "DOWNLOAD_PATH", str(tmp_path))
    monkeypatch.setattr(TranscodeService, "_get_db_setting", _settings_stub())

    rel = "teams/1/uploads/9/v1/video.mp4"
    src_dir = tmp_path / "teams/1/uploads/9/v1"
    src_dir.mkdir(parents=True)
    (src_dir / "video.mp4").write_bytes(b"fake video bytes")

    svc = TranscodeService()
    svc.repo = _FakeRepo({"file_path": rel, "file_size_bytes": 500 * _MB})
    fake_hls = _FakeHls()
    svc._hls = fake_hls

    monkeypatch.setattr(
        TranscodeProbe, "probe_resolution", AsyncMock(return_value=(1920, 1080))
    )
    monkeypatch.setattr(TranscodeProbe, "probe_duration", AsyncMock(return_value=10.0))
    monkeypatch.setattr(
        TranscodeProbe, "probe_codecs", AsyncMock(return_value=("h264", "aac"))
    )
    monkeypatch.setattr(TranscodeProbe, "probe_bitrate", AsyncMock(return_value=2000))
    monkeypatch.setattr(
        TranscodeService, "_transcode_passthrough", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(TranscodeService, "_select_tiers", AsyncMock(return_value=[]))

    discard_calls: list[str] = []
    real_discard = ts_mod.discard_local_source

    def _tracking_discard(local_path, stored_path):
        discard_calls.append(stored_path)
        return real_discard(local_path, stored_path)

    monkeypatch.setattr(ts_mod, "discard_local_source", _tracking_discard)

    result = await svc.transcode_version("r1", "v1")

    assert result["status"] == "completed"
    assert fake_hls.publish_calls == 1, "no applicable tiers -> phase 2 never runs"
    assert discard_calls == ["sb://library/hls/r1/v1/master.m3u8"]

    hls_dir = src_dir / "hls"
    assert not hls_dir.exists()
