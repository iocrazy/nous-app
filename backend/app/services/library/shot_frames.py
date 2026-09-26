"""Frame sampling for the shot index: one ffmpeg pass → JPEG frames in a
private scratch directory + ffmpeg's per-frame scene scores → colour
signatures → shots.

The same decode feeds two branches (``split``): the 3 fps JPEGs, and a
small (``SCENE_WIDTH``) copy of EVERY frame through ``select`` whose scene
score ``metadata=print`` writes to a text file (scene_v1's cut signal).

Spec §4 step 1: 3 fps (since hist_v2; was 1 fps), short side 448, capped at ``MAX_FRAMES`` frames (a
video longer than an hour is sampled more sparsely). The JPEGs are what the
cutter reads and what the representative frame is served from; nothing here
is persisted, the caller deletes the directory when it is done (``async
with cut_video_file(...)``).

Scratch is ``tempfile.mkdtemp`` (mode 0700, random name): the frames are the
user's own video, a world-readable predictable path would leak them to
other local users and invite symlink races (CLAUDE.md 「绝不把宿主环境和
可预测路径交给不可信输出」).
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator, Sequence

from loguru import logger
from PIL import Image

from app.agent_framework.process_runner import current_workflow_id, run_process
from app.services.library.shot_cut import (
    DEFAULT_PARAMS,
    CutParams,
    FrameSig,
    SceneScore,
    Shot,
    cut_video,
    frame_signature,
)
from app.services.media.render.video_frame_extractor import _probe_duration

#: Sampling rate for videos up to an hour.
#: 3 fps: frames 333 ms apart keep the within-shot histogram distance low
#: on grainy / fast-moving footage, which is what lets a cut stand out (1 fps
#: lost most cuts there — hist_v1). Only representative frames are embedded,
#: so this costs decode time, not provider tokens.
BASE_FPS = 3.0
#: Frames per video, whatever its length (an hour at 3 fps; ~30 KB each).
MAX_FRAMES = 10800
#: Short side of a sampled frame, in pixels.
SHORT_SIDE = 448
#: ffmpeg ``-q:v`` for the JPEGs (2 best … 31 worst).
JPEG_QUALITY = 4
#: One ffmpeg pass over the whole file. Decoding dominates; 10 minutes of
#: 1080p takes well under a minute on the deploy host.
DEFAULT_TIMEOUT_SECONDS = 600.0

#: Width of the copy the scene score is computed on (height keeps aspect).
#: The score is a mean absolute frame difference, so resolution barely moves
#: it; 256 px is what PySceneDetect downscales to by default, and it keeps a
#: native-rate pass cheap.
SCENE_WIDTH = 256
_SCORES_FILE = "scene_scores.txt"
#: Characters that would end an option value inside the filtergraph string;
#: ``mkdtemp`` names never contain them, a hostile TMPDIR might.
_FILTER_UNSAFE = frozenset(":,;[]'\\=")

_SCRATCH_PREFIX = "shots_"


class ShotFramesError(RuntimeError):
    """Sampling failed: ``reason`` is a stable code (``ffmpeg_missing`` /
    ``video_missing`` / ``probe_failed`` / ``ffmpeg_failed`` / ``timeout`` /
    ``cancelled``)."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class SampledFrame:
    t_ms: int
    path: Path


@dataclass(frozen=True)
class CutResult:
    duration_ms: int
    fps: float
    frames: tuple[SampledFrame, ...]
    shots: tuple[Shot, ...]
    #: ffmpeg's scene score of every decoded frame (scene detector input).
    scene: tuple[SceneScore, ...] = ()
    #: Colour signatures of ``frames`` (so a caller can re-cut the same
    #: frames with other params without decoding again — the benchmark).
    sigs: tuple[FrameSig, ...] = ()

    def frame_at(self, t_ms: int) -> SampledFrame | None:
        """The sampled frame at exactly ``t_ms`` (representative frames are
        always sampled timestamps)."""
        for f in self.frames:
            if f.t_ms == t_ms:
                return f
        return None


def choose_fps(duration_seconds: float) -> float:
    """``BASE_FPS`` while that stays under ``MAX_FRAMES`` frames, then just
    enough to stay under the cap."""
    if duration_seconds <= 0:
        return BASE_FPS
    return min(BASE_FPS, MAX_FRAMES / duration_seconds)


def _sample_cmd(
    video_path: str, out_pattern: str, fps: float, scores_path: str
) -> list[str]:
    # scale: short side → SHORT_SIDE, keep aspect, even dimensions (-2).
    scale = f"scale='if(gt(iw,ih),-2,{SHORT_SIDE})':'if(gt(iw,ih),{SHORT_SIDE},-2)'"
    graph = (
        "[0:v]split=2[a][b];"
        f"[a]fps={fps:.6f},{scale}[s];"
        f"[b]scale={SCENE_WIDTH}:-2,select='gte(scene\\,0)',"
        f"metadata=print:key=lavfi.scene_score:file={scores_path},nullsink"
    )
    return [
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-v",
        "error",
        "-i",
        video_path,
        "-filter_complex",
        graph,
        "-map",
        "[s]",
        "-q:v",
        str(JPEG_QUALITY),
        "-f",
        "image2",
        out_pattern,
    ]


_PTS_RE = re.compile(r"pts_time:\s*(-?[0-9.]+)")
_SCORE_RE = re.compile(r"lavfi\.scene_score=\s*([0-9.eE+-]+)")


def parse_scene_scores(text: str) -> list[SceneScore]:
    """``metadata=print`` output → scores, times shifted so the first
    decoded frame is 0 ms (the sampled JPEGs count from 0 as well). Lines
    that do not parse are skipped."""
    raw: list[tuple[float, float]] = []
    pending_t: float | None = None
    for line in text.splitlines():
        m = _PTS_RE.search(line)
        if m:
            try:
                pending_t = float(m.group(1))
            except ValueError:
                pending_t = None
            continue
        m = _SCORE_RE.search(line)
        if m and pending_t is not None:
            try:
                raw.append((pending_t, float(m.group(1))))
            except ValueError:
                pass
            pending_t = None
    if not raw:
        return []
    t0 = raw[0][0]
    return [SceneScore(t_ms=int(round((t - t0) * 1000)), score=v) for t, v in raw]


async def sample_frames(
    video_path: str,
    out_dir: Path,
    *,
    fps: float,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> tuple[list[SampledFrame], list[SceneScore]]:
    """One ffmpeg pass: ``fps`` frames per second as JPEGs in ``out_dir``
    (frame k, 1-based, sits at ``(k - 1) / fps`` seconds) and the scene
    score of every decoded frame."""
    if not shutil.which("ffmpeg"):
        raise ShotFramesError("ffmpeg_missing")
    pattern = str(out_dir / "f%06d.jpg")
    scores_path = out_dir / _SCORES_FILE
    if _FILTER_UNSAFE.intersection(str(scores_path)):
        raise ShotFramesError("ffmpeg_failed", "scratch path not filtergraph-safe")
    cmd = _sample_cmd(video_path, pattern, fps, str(scores_path))
    res = await run_process(
        cmd, timeout_s=timeout_seconds, workflow_id=current_workflow_id()
    )
    if res.cancelled:
        raise ShotFramesError("cancelled", "ffmpeg sampling: workflow cancelled")
    if res.timed_out:
        raise ShotFramesError("timeout", f"ffmpeg sampling > {timeout_seconds}s")
    if res.exit_code != 0:
        raise ShotFramesError(
            "ffmpeg_failed",
            f"{res.describe()}: {res.stderr_text()[:300].strip()}",
        )
    frames = sorted(out_dir.glob("f*.jpg"))
    out: list[SampledFrame] = []
    for k, path in enumerate(frames):
        out.append(SampledFrame(t_ms=int(round(k * 1000.0 / fps)), path=path))
    try:
        scene = parse_scene_scores(scores_path.read_text(errors="replace"))
    except OSError as e:
        # ffmpeg exited 0 but wrote no scores: say so, the cutter then sees
        # no cut (one shot + hard splits) rather than failing the index.
        logger.warning(f"[shot_frames] no scene scores ({e}); cutting without")
        scene = []
    return out, scene


def _signature_of(frame: SampledFrame) -> FrameSig:
    with Image.open(frame.path) as img:
        return frame_signature(img, frame.t_ms)


async def signatures(frames: Sequence[SampledFrame]) -> list[FrameSig]:
    """Colour signatures of the frames, computed off the event loop."""
    return await asyncio.to_thread(lambda: [_signature_of(f) for f in frames])


@contextlib.asynccontextmanager
async def cut_video_file(
    video_path: str,
    *,
    params: CutParams = DEFAULT_PARAMS,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> AsyncIterator[CutResult]:
    """Sample → sign → cut one local video file. The frames live in a private
    scratch directory for the duration of the ``with`` block and are deleted
    on exit, success or not."""
    src = Path(video_path)
    if not src.is_file():
        raise ShotFramesError("video_missing", video_path)
    duration_s = await _probe_duration(str(src))
    if duration_s is None or duration_s <= 0:
        raise ShotFramesError("probe_failed", video_path)
    fps = choose_fps(duration_s)
    scratch = Path(tempfile.mkdtemp(prefix=_SCRATCH_PREFIX))  # mode 0700
    try:
        frames, scene = await sample_frames(
            str(src), scratch, fps=fps, timeout_seconds=timeout_seconds
        )
        sigs = await signatures(frames)
        duration_ms = int(round(duration_s * 1000))
        shots = cut_video(sigs, duration_ms, params, scene)
        logger.info(
            f"[shot_frames] {src.name}: {len(frames)} frames @ {fps:.3f} fps, "
            f"{len(scene)} scene scores ({params.detector}) → {len(shots)} shots"
        )
        yield CutResult(
            duration_ms=duration_ms,
            fps=fps,
            frames=tuple(frames),
            shots=tuple(shots),
            scene=tuple(scene),
            sigs=tuple(sigs),
        )
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
