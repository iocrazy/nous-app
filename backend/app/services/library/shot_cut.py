"""Shot boundary detection from sampled frames — local, free, pure Python.

Spec: docs/superpowers/specs/2026-09-25-pr3-shots-index-design.md §4.

Why pixels and not embeddings: the design doc's cut signal was the cosine
distance between 1 fps frame embeddings, which costs one paid embedding call
per second of video on a network provider. A 32×32 HSV histogram per frame
gives the same *shape* of signal (a spike where the picture changes) for
nothing, so only the representative frame of each shot is ever embedded.

Skeleton after PySceneDetect's ``AdaptiveDetector``: the frame-to-frame
distance is divided by the rolling mean of its neighbours, a cut is where the
ratio AND the absolute distance both clear a threshold; a one-frame flash
(cut, then straight back) is dropped. Then shots are shaped: shorter than
``min_shot_ms`` merge into a neighbour (the weaker cut goes), longer than
``max_shot_ms`` split hard, more than ``max_shots`` keep the strongest cuts,
adjacent shots whose representative frames look alike merge.

Everything here is deterministic on the input signatures; ``ALGO_VERSION``
names the parameters so a later change (a real ``hist_v2``, or the
embedding-distance cutter once a local provider exists) can coexist with rows
cut by this one (``video_shot_indexes.algo_version``).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Sequence

from PIL import Image

#: Names THIS cutter + THESE defaults. Bump when the output for the same
#: frames would change.
ALGO_VERSION = "hist_v1"

_THUMB = 32
_H_BINS, _S_BINS, _V_BINS = 16, 8, 8
#: Length of a signature vector.
SIGNATURE_LEN = _H_BINS + _S_BINS + _V_BINS


@dataclass(frozen=True)
class FrameSig:
    """A sampled frame reduced to its colour signature."""

    t_ms: int
    hist: tuple[float, ...]


@dataclass(frozen=True)
class Shot:
    start_ms: int
    end_ms: int
    rep_frame_ms: int
    #: Frame distance at the cut that OPENED this shot (0 for the first shot
    #: and for hard splits): how confident the boundary is.
    cut_score: float


@dataclass(frozen=True)
class CutParams:
    #: d[i] / rolling-mean(d around i) must reach this.
    ratio: float = 3.0
    #: ... and d[i] itself must reach this (kills ratio spikes on static video
    #: where the baseline is ~0).
    min_abs: float = 0.15
    #: Half-width of the rolling window, in frames.
    window: int = 2
    #: A cut followed by a frame that returns to the pre-cut picture within
    #: this distance is a flash, not two cuts.
    flash_threshold: float = 0.10
    min_shot_ms: int = 1500
    max_shot_ms: int = 30_000
    max_shots: int = 600
    #: Adjacent shots whose representative frames are closer than this merge.
    merge_threshold: float = 0.05


DEFAULT_PARAMS = CutParams()


# ── signatures ───────────────────────────────────────────────────────────────


def frame_signature(image: Image.Image, t_ms: int) -> FrameSig:
    """32×32 HSV histogram (H16 + S8 + V8), each block L1-normalised so the
    three channels weigh the same regardless of bin count."""
    small = image.convert("RGB").resize((_THUMB, _THUMB), Image.Resampling.BILINEAR)
    hsv = small.convert("HSV")
    h_counts = [0] * _H_BINS
    s_counts = [0] * _S_BINS
    v_counts = [0] * _V_BINS
    for h, s, v in hsv.getdata():
        h_counts[h * _H_BINS // 256] += 1
        s_counts[s * _S_BINS // 256] += 1
        v_counts[v * _V_BINS // 256] += 1
    n = float(_THUMB * _THUMB)
    hist = (
        tuple(c / n for c in h_counts)
        + tuple(c / n for c in s_counts)
        + tuple(c / n for c in v_counts)
    )
    return FrameSig(t_ms=int(t_ms), hist=hist)


def distance(a: FrameSig, b: FrameSig) -> float:
    """Half the L1 distance of the signatures, in [0, 1] per channel block →
    averaged over the three blocks so the result stays in [0, 1]."""
    if len(a.hist) != len(b.hist):
        raise ValueError("signatures of different lengths")
    blocks = (
        (0, _H_BINS),
        (_H_BINS, _H_BINS + _S_BINS),
        (_H_BINS + _S_BINS, len(a.hist)),
    )
    total = 0.0
    for lo, hi in blocks:
        total += 0.5 * sum(abs(a.hist[i] - b.hist[i]) for i in range(lo, hi))
    return total / len(blocks)


# ── cut detection ────────────────────────────────────────────────────────────


def frame_distances(sigs: Sequence[FrameSig]) -> list[float]:
    """``d[i]`` = distance between frame i-1 and frame i; ``d[0] = 0``."""
    return [0.0] + [distance(sigs[i - 1], sigs[i]) for i in range(1, len(sigs))]


def detect_cuts(
    sigs: Sequence[FrameSig], params: CutParams = DEFAULT_PARAMS
) -> list[tuple[int, float]]:
    """Indices ``i`` such that a shot boundary lies BEFORE frame ``i``, with
    the distance that triggered it. Sorted by index."""
    n = len(sigs)
    if n < 2:
        return []
    d = frame_distances(sigs)
    w = max(1, params.window)
    candidates: list[tuple[int, float]] = []
    for i in range(1, n):
        lo, hi = max(1, i - w), min(n - 1, i + w)
        neighbours = [d[j] for j in range(lo, hi + 1) if j != i]
        baseline = (sum(neighbours) / len(neighbours)) if neighbours else 0.0
        ratio = d[i] / max(baseline, 1e-6)
        if d[i] >= params.min_abs and ratio >= params.ratio:
            candidates.append((i, d[i]))
    # Flash suppression: a cut at i whose next frame returns to the picture
    # before the cut (i+1 close to i-1) is one flash, not two boundaries.
    cut_at = {i for i, _ in candidates}
    kept: list[tuple[int, float]] = []
    skip: set[int] = set()
    for i, score in candidates:
        if i in skip:
            continue
        if (i + 1) in cut_at and i + 1 < n and i - 1 >= 0:
            if distance(sigs[i - 1], sigs[i + 1]) < params.flash_threshold:
                skip.add(i + 1)
                continue
        kept.append((i, score))
    return kept


# ── shot shaping ─────────────────────────────────────────────────────────────


def _rep_frame_ms(sigs: Sequence[FrameSig], start_ms: int, end_ms: int) -> int:
    """The sampled frame nearest the shot's midpoint, inside the span."""
    mid = (start_ms + end_ms) // 2
    inside = [s for s in sigs if start_ms <= s.t_ms < end_ms]
    if not inside:
        return start_ms
    return min(inside, key=lambda s: abs(s.t_ms - mid)).t_ms


def _sig_at(sigs: Sequence[FrameSig], t_ms: int) -> FrameSig | None:
    for s in sigs:
        if s.t_ms == t_ms:
            return s
    return None


def build_shots(
    sigs: Sequence[FrameSig],
    cuts: Sequence[tuple[int, float]],
    duration_ms: int,
    params: CutParams = DEFAULT_PARAMS,
) -> list[Shot]:
    """Turn cut indices into shots over ``[0, duration_ms)`` and shape them
    (min / max length, cap, look-alike merge). Empty when the video has no
    positive duration."""
    if duration_ms <= 0 or not sigs:
        return []
    # boundaries: (t_ms, score); the first boundary is the start of the video
    bounds: list[tuple[int, float]] = [(0, 0.0)]
    for i, score in cuts:
        t = sigs[i].t_ms
        if 0 < t < duration_ms:
            bounds.append((t, score))
    bounds.sort()

    # 1. Too-short shots: drop the weaker of the two cuts around the shot.
    changed = True
    while changed and len(bounds) > 1:
        changed = False
        for k in range(len(bounds)):
            start = bounds[k][0]
            end = bounds[k + 1][0] if k + 1 < len(bounds) else duration_ms
            if end - start < params.min_shot_ms:
                if k == 0:
                    # The first shot is short: drop the cut that closes it.
                    if len(bounds) > 1:
                        del bounds[1]
                        changed = True
                        break
                    continue
                if k + 1 < len(bounds) and bounds[k + 1][1] < bounds[k][1]:
                    del bounds[k + 1]
                else:
                    del bounds[k]
                changed = True
                break

    # 2. Too-long shots: hard split every max_shot_ms (score 0).
    split: list[tuple[int, float]] = []
    for k, (start, score) in enumerate(bounds):
        end = bounds[k + 1][0] if k + 1 < len(bounds) else duration_ms
        split.append((start, score))
        t = start + params.max_shot_ms
        while end - t >= params.min_shot_ms and t < end:
            split.append((t, 0.0))
            t += params.max_shot_ms
    bounds = split

    # 3. Cap: keep the video start plus the strongest cuts.
    if len(bounds) > params.max_shots:
        head, rest = bounds[0], bounds[1:]
        rest = sorted(rest, key=lambda b: -b[1])[: params.max_shots - 1]
        bounds = [head] + sorted(rest)

    # 4. Shots with representative frames; merge look-alike neighbours.
    shots: list[Shot] = []
    for k, (start, score) in enumerate(bounds):
        end = bounds[k + 1][0] if k + 1 < len(bounds) else duration_ms
        if end <= start:
            continue
        shots.append(
            Shot(
                start_ms=start,
                end_ms=end,
                rep_frame_ms=_rep_frame_ms(sigs, start, end),
                cut_score=float(score),
            )
        )
    merged: list[Shot] = []
    for shot in shots:
        # Only a boundary the detector found can be folded; a hard split
        # (score 0) exists precisely to keep a long shot apart.
        if merged and shot.cut_score > 0.0:
            prev = merged[-1]
            a, b = _sig_at(sigs, prev.rep_frame_ms), _sig_at(sigs, shot.rep_frame_ms)
            if (
                a is not None
                and b is not None
                and distance(a, b) < params.merge_threshold
            ):
                joined = replace(prev, end_ms=shot.end_ms)
                merged[-1] = replace(
                    joined,
                    rep_frame_ms=_rep_frame_ms(sigs, joined.start_ms, joined.end_ms),
                )
                continue
        merged.append(shot)
    return merged


def cut_video(
    sigs: Sequence[FrameSig],
    duration_ms: int,
    params: CutParams = DEFAULT_PARAMS,
) -> list[Shot]:
    """Signatures in time order → shaped shot list. The one entry point the
    workflow uses."""
    return build_shots(sigs, detect_cuts(sigs, params), duration_ms, params)
