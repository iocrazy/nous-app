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
from app.services.library.shot_cut import HIST_V3_PARAMS, SceneScore, cut_video
from app.services.library.shot_frames import (
    BASE_FPS,
    MAX_FRAMES,
    ShotFramesError,
    choose_fps,
    cut_video_file,
    parse_scene_scores,
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


def test_parse_scene_scores_shifts_to_zero_and_skips_noise():
    text = (
        "frame:0    pts:1536    pts_time:1.5\n"
        "lavfi.scene_score=0.000000\n"
        "garbage line\n"
        "frame:1    pts:2560    pts_time:1.54\n"
        "lavfi.scene_score=0.412000\n"
        "lavfi.scene_score=0.9\n"  # no pts before it: skipped
        "frame:2    pts:3584    pts_time:1.58\n"
        "lavfi.scene_score=0.01\n"
    )
    assert parse_scene_scores(text) == [
        SceneScore(0, 0.0),
        SceneScore(40, 0.412),
        SceneScore(80, 0.01),
    ]
    assert parse_scene_scores("") == []


def test_choose_fps_caps_the_frame_count():
    assert choose_fps(600) == BASE_FPS == 3.0
    assert choose_fps(MAX_FRAMES / BASE_FPS) == BASE_FPS
    assert choose_fps(MAX_FRAMES * 4) == pytest.approx(0.25)
    assert choose_fps(0) == BASE_FPS


@_needs_ffmpeg
async def test_two_scene_clip_cuts_at_the_scene_change(tmp_path):
    clip = tmp_path / "clip.mp4"
    _make_clip(clip)
    scratch_before = set(Path(shot_frames.tempfile.gettempdir()).glob("shots_*"))
    async with cut_video_file(str(clip)) as result:
        assert result.duration_ms == pytest.approx(10_000, abs=200)
        assert result.fps == BASE_FPS == 3.0
        assert len(result.frames) in (30, 31)
        assert [f.t_ms for f in result.frames[:3]] == [0, 333, 667]
        spans = [(s.start_ms, s.end_ms) for s in result.shots]
        assert len(spans) == 2, spans
        assert spans[0][0] == 0 and spans[-1][1] == result.duration_ms
        assert spans[0][1] == 4000
        # scene_v1 read the cut off the native-rate (10 fps) score track ...
        assert len(result.scene) in (99, 100, 101)
        assert max(result.scene, key=lambda x: x.score).t_ms == 4000
        assert len(result.sigs) == len(result.frames)
        # ... and the same decode re-cuts with hist_v3 without ffmpeg.
        hist = cut_video(result.sigs, result.duration_ms, HIST_V3_PARAMS)
        assert [s.start_ms for s in hist] == [0, 4000]
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
