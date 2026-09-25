"""shot_frames against a real ffmpeg: a synthetic two-scene clip is sampled,
signed and cut; the scratch directory is gone afterwards.

Skips when ffmpeg is not installed (CI's backend job has it; the sandboxed
unit lane may not)."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path

import pytest

from app.services.library import shot_frames
from app.services.library.shot_frames import (
    MAX_FRAMES,
    ShotFramesError,
    choose_fps,
    cut_video_file,
)

pytestmark = pytest.mark.asyncio

_needs_ffmpeg = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
    reason="ffmpeg / ffprobe not installed",
)


def _make_clip(path: Path, *, red_s: int = 4, blue_s: int = 6) -> None:
    """red for ``red_s`` seconds then blue for ``blue_s``, 64×48, 10 fps."""
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color=c=red:s=64x48:r=10:d={red_s}",
            "-f",
            "lavfi",
            "-i",
            f"color=c=blue:s=64x48:r=10:d={blue_s}",
            "-filter_complex",
            "[0:v][1:v]concat=n=2:v=1:a=0[v]",
            "-map",
            "[v]",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
        timeout=60,
    )


def test_choose_fps_caps_the_frame_count():
    assert choose_fps(600) == 1.0
    assert choose_fps(MAX_FRAMES) == 1.0
    assert choose_fps(MAX_FRAMES * 4) == pytest.approx(0.25)
    assert choose_fps(0) == 1.0


@_needs_ffmpeg
async def test_two_scene_clip_cuts_at_the_scene_change(tmp_path):
    clip = tmp_path / "clip.mp4"
    _make_clip(clip)
    scratch_before = set(Path(shot_frames.tempfile.gettempdir()).glob("shots_*"))
    async with cut_video_file(str(clip)) as result:
        assert result.duration_ms == pytest.approx(10_000, abs=200)
        assert result.fps == 1.0
        assert len(result.frames) in (10, 11)
        assert [f.t_ms for f in result.frames[:3]] == [0, 1000, 2000]
        spans = [(s.start_ms, s.end_ms) for s in result.shots]
        assert len(spans) == 2, spans
        assert spans[0][0] == 0 and spans[-1][1] == result.duration_ms
        assert spans[0][1] == 4000
        rep = result.frame_at(result.shots[1].rep_frame_ms)
        assert rep is not None and rep.path.is_file()
        scratch_dir = rep.path.parent
        assert oct(scratch_dir.stat().st_mode & 0o777) == "0o700"
    assert not scratch_dir.exists()
    assert (
        set(Path(shot_frames.tempfile.gettempdir()).glob("shots_*")) == scratch_before
    )


@_needs_ffmpeg
async def test_missing_file_is_a_typed_error(tmp_path):
    with pytest.raises(ShotFramesError) as exc:
        async with cut_video_file(str(tmp_path / "nope.mp4")):
            pass
    assert exc.value.reason == "video_missing"


@_needs_ffmpeg
async def test_sampling_timeout_is_a_typed_error(tmp_path, monkeypatch):
    clip = tmp_path / "clip.mp4"
    _make_clip(clip, red_s=2, blue_s=2)

    async def slow_communicate(self):
        await asyncio.sleep(5)
        return b"", b""

    async def probe(_path):
        return 4.0

    # The slow communicate would also stall ffprobe (which swallows its own
    # timeout into "probe_failed"); pin the probe so the timeout under test
    # is the sampling one.
    monkeypatch.setattr(shot_frames, "_probe_duration", probe)
    monkeypatch.setattr(asyncio.subprocess.Process, "communicate", slow_communicate)
    with pytest.raises(ShotFramesError) as exc:
        async with cut_video_file(str(clip), timeout_seconds=0.2):
            pass
    assert exc.value.reason == "timeout"
