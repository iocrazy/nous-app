"""Q2 — video frame extraction service tests.

ffmpeg is mocked at the asyncio.create_subprocess_exec layer; we don't
shell out for unit tests. A separate (manual) integration test would
exercise real ffmpeg.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.agent_framework.multimodal import AttachmentKind
from app.services.media.render import video_frame_extractor as vfx


@pytest.fixture
def fake_video(tmp_path: Path) -> Path:
    """Create a fake video file (just needs to exist for path checks)."""
    p = tmp_path / "fake.mp4"
    p.write_bytes(b"\x00\x00\x00\x14ftypisom")
    return p


def _mock_proc(returncode=0, stdout=b"", stderr=b""):
    proc = AsyncMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    return proc


# ─── Validation ──────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_missing_file_returns_error():
    result = await vfx.extract_frames("/nope/does-not-exist.mp4")
    assert result.attachments == []
    assert result.error and "not found" in result.error


@pytest.mark.unit
@pytest.mark.asyncio
async def test_missing_ffmpeg_returns_error(fake_video, monkeypatch):
    monkeypatch.setattr(vfx.shutil, "which", lambda _: None)
    result = await vfx.extract_frames(str(fake_video))
    assert result.attachments == []
    assert result.error and "ffmpeg" in result.error.lower()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_num_frames_clamped_high(fake_video, monkeypatch):
    """num_frames=999 → clamped to 32 (verified via probe ffmpeg call count)."""
    monkeypatch.setattr(vfx.shutil, "which", lambda b: f"/usr/local/bin/{b}")

    # Simulate probe (duration=10s) then per-frame ffmpeg calls
    call_count = 0

    async def _fake_exec(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if "ffprobe" in args[0]:
            return _mock_proc(0, b"10.0\n")
        # ffmpeg seek+extract — write nothing (extraction "fails")
        # so we count calls without needing real frames
        return _mock_proc(1, b"", b"forced fail")

    with patch("asyncio.create_subprocess_exec", side_effect=_fake_exec):
        result = await vfx.extract_frames(str(fake_video), num_frames=999)

    # 1 probe + 32 frame extraction attempts
    assert call_count == 33, f"clamp should hit 32 frames, got {call_count - 1}"
    assert result.duration_seconds == 10.0


# ─── Successful extraction ──────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_three_frames_with_duration(fake_video, monkeypatch, tmp_path):
    """Probe returns 30s; extractor samples 3 evenly-spaced frames."""
    monkeypatch.setattr(vfx.shutil, "which", lambda b: f"/usr/local/bin/{b}")

    # We need ffmpeg to actually produce a JPEG file at the expected path.
    # The simplest way: write a tiny valid JPEG to the temp dir before the
    # mock returns success. We use a side_effect that captures the output
    # path argument and creates the file.
    fake_jpeg = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x00fakejpeg\xff\xd9"

    async def _fake_exec(*args, **kwargs):
        if "ffprobe" in args[0]:
            return _mock_proc(0, b"30.0\n")
        # ffmpeg: last arg is the output path
        out_path = Path(args[-1])
        out_path.write_bytes(fake_jpeg)
        return _mock_proc(0, b"", b"")

    with patch("asyncio.create_subprocess_exec", side_effect=_fake_exec):
        result = await vfx.extract_frames(str(fake_video), num_frames=3)

    assert result.error is None
    assert len(result.attachments) == 3
    assert all(a.kind == AttachmentKind.VIDEO_THUMBNAIL for a in result.attachments)
    assert all(
        a.data_url and a.data_url.startswith("data:image/jpeg;base64,")
        for a in result.attachments
    )
    assert len(result.sampled_at_seconds) == 3
    # Should sample evenly across the usable middle 90% of duration
    # (margin = 5%, so [1.5, 28.5])
    assert result.sampled_at_seconds[0] == pytest.approx(1.5, abs=0.5)
    assert result.sampled_at_seconds[-1] == pytest.approx(28.5, abs=0.5)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_unknown_duration_uses_fps_fallback(fake_video, monkeypatch):
    """ffprobe fails → fps-based fallback path."""
    monkeypatch.setattr(vfx.shutil, "which", lambda b: f"/usr/local/bin/{b}")
    fake_jpeg = b"\xff\xd8\xff\xe0fakejpeg"

    async def _fake_exec(*args, **kwargs):
        if "ffprobe" in args[0]:
            return _mock_proc(1, b"", b"probe fail")
        # ffmpeg fps fallback writes pattern frame_001.jpg etc to tmp dir
        # The output template is the last positional arg
        template = args[-1]
        # Resolve %03d → emit 4 fake frames
        if "%03d" in str(template):
            base = str(template).replace("%03d", "")
            for i in range(1, 5):
                p = Path(base.replace(".jpg", "") + f"{i:03d}.jpg")
                p.write_bytes(fake_jpeg)
        return _mock_proc(0, b"", b"")

    with patch("asyncio.create_subprocess_exec", side_effect=_fake_exec):
        result = await vfx.extract_frames(str(fake_video), num_frames=4)

    assert result.duration_seconds is None  # probe failed
    assert len(result.attachments) == 4
    # In fallback path, sampled_at is empty (ffmpeg picked spacing)
    assert result.sampled_at_seconds == []


# ─── Single-frame edge case ─────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_single_frame_samples_midpoint(fake_video, monkeypatch):
    monkeypatch.setattr(vfx.shutil, "which", lambda b: f"/usr/local/bin/{b}")
    fake_jpeg = b"\xff\xd8\xff\xe0jpg"

    async def _fake_exec(*args, **kwargs):
        if "ffprobe" in args[0]:
            return _mock_proc(0, b"60.0\n")
        out_path = Path(args[-1])
        out_path.write_bytes(fake_jpeg)
        return _mock_proc(0, b"", b"")

    with patch("asyncio.create_subprocess_exec", side_effect=_fake_exec):
        result = await vfx.extract_frames(str(fake_video), num_frames=1)

    assert len(result.attachments) == 1
    assert result.sampled_at_seconds == [30.0]  # midpoint


# ─── Timeout handling ───────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_per_frame_timeout_skips_failing_frame(fake_video, monkeypatch):
    """One frame times out → others still extract."""
    monkeypatch.setattr(vfx.shutil, "which", lambda b: f"/usr/local/bin/{b}")
    fake_jpeg = b"\xff\xd8\xff\xe0jpg"

    call_idx = -1

    async def _fake_exec(*args, **kwargs):
        nonlocal call_idx
        call_idx += 1
        if "ffprobe" in args[0]:
            return _mock_proc(0, b"30.0\n")
        # Fail the 2nd frame extraction (call_idx 2 — probe is 0,
        # frame 0 is 1, frame 1 is 2)
        if call_idx == 2:
            proc = AsyncMock()
            proc.returncode = 0
            proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError())
            return proc
        out_path = Path(args[-1])
        out_path.write_bytes(fake_jpeg)
        return _mock_proc(0, b"", b"")

    with patch("asyncio.create_subprocess_exec", side_effect=_fake_exec):
        result = await vfx.extract_frames(str(fake_video), num_frames=3)

    # Probe + 3 frame attempts; 1 timed out → 2 success
    assert len(result.attachments) == 2
    assert result.error is None  # per-frame failures don't fail the whole batch


# ─── Probe duration parsing ─────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_probe_duration_handles_garbage(monkeypatch):
    monkeypatch.setattr(vfx.shutil, "which", lambda _: "/usr/local/bin/ffprobe")

    async def _fake_exec(*args, **kwargs):
        return _mock_proc(0, b"not-a-number\n")

    with patch("asyncio.create_subprocess_exec", side_effect=_fake_exec):
        dur = await vfx._probe_duration("/some/path")
    assert dur is None
