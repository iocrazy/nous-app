"""shot_cut: cuts on synthetic frame sequences.

Frames are solid colours with a little noise so the intra-scene distance is
small but non-zero (a real video never repeats a frame exactly). One frame
per second. The histogram cutter (``hist_v3``, still the benchmark's
baseline) is exercised with ``detector="hist"``; the scene cutter
(``scene_v1``, the default) gets hand-written ffmpeg scene scores.
"""

from __future__ import annotations

import random

import pytest
from PIL import Image

from app.services.library.shot_cut import (
    ALGO_VERSION,
    DEFAULT_PARAMS,
    HIST_V3_PARAMS,
    SIGNATURE_LEN,
    CutParams,
    FrameSig,
    SceneScore,
    algo_version,
    cut_video,
    detect_cuts,
    detect_scene_cuts,
    distance,
    frame_signature,
)

HIST = HIST_V3_PARAMS


def _hist(**overrides) -> CutParams:
    return CutParams(detector="hist", **overrides)


RED, GREEN, BLUE, WHITE = (200, 30, 30), (30, 180, 40), (30, 40, 200), (250, 250, 250)


def _frame(rgb, rng: random.Random, noise: int = 12) -> Image.Image:
    """A 64×48 solid frame with per-pixel noise."""
    img = Image.new("RGB", (64, 48))
    px = img.load()
    for y in range(48):
        for x in range(64):
            px[x, y] = tuple(
                max(0, min(255, c + rng.randint(-noise, noise))) for c in rgb
            )
    return img


def _scene(rgb, seconds: int, start_s: int, rng: random.Random) -> list[FrameSig]:
    return [
        frame_signature(_frame(rgb, rng), (start_s + i) * 1000) for i in range(seconds)
    ]


def _sequence(*scenes: tuple[tuple[int, int, int], int]) -> tuple[list[FrameSig], int]:
    rng = random.Random(7)
    sigs: list[FrameSig] = []
    t = 0
    for rgb, seconds in scenes:
        sigs += _scene(rgb, seconds, t, rng)
        t += seconds
    return sigs, t * 1000


def test_signature_shape_and_distance_bounds():
    rng = random.Random(1)
    a = frame_signature(_frame(RED, rng), 0)
    b = frame_signature(_frame(RED, rng), 1000)
    c = frame_signature(_frame(BLUE, rng), 2000)
    assert len(a.hist) == SIGNATURE_LEN
    assert 0.0 <= distance(a, b) < 0.1  # same scene, only noise
    assert distance(a, c) > 0.3  # different scene
    assert distance(a, a) == 0.0


def test_three_scenes_give_three_shots_at_the_boundaries():
    sigs, duration = _sequence((RED, 10), (GREEN, 8), (BLUE, 12))
    shots = cut_video(sigs, duration, HIST)
    assert [(s.start_ms, s.end_ms) for s in shots] == [
        (0, 10_000),
        (10_000, 18_000),
        (18_000, 30_000),
    ]
    assert shots[0].cut_score == 0.0 and shots[1].cut_score > 0.15
    for s in shots:
        assert s.start_ms <= s.rep_frame_ms < s.end_ms
    assert shots[0].rep_frame_ms == 5000


def test_a_single_flash_frame_is_not_a_cut():
    rng = random.Random(3)
    sigs = _scene(RED, 6, 0, rng)
    sigs.append(frame_signature(_frame(WHITE, rng), 6000))
    sigs += _scene(RED, 6, 7, rng)
    assert detect_cuts(sigs) == []
    shots = cut_video(sigs, 13_000, HIST)
    assert [(s.start_ms, s.end_ms) for s in shots] == [(0, 13_000)]


def test_static_video_has_no_cuts_even_with_ratio_spikes():
    # Baseline ~0 makes any tiny wobble a huge ratio; min_abs must veto it.
    sigs, duration = _sequence((BLUE, 40))
    assert detect_cuts(sigs) == []


def test_shots_shorter_than_min_length_merge_into_a_neighbour():
    # 1 s of GREEN between two RED scenes: below min_shot_ms, folded away.
    sigs, duration = _sequence((RED, 8), (GREEN, 1), (BLUE, 8))
    shots = cut_video(sigs, duration, _hist(min_shot_ms=1500))
    assert len(shots) == 2
    assert shots[0].start_ms == 0 and shots[-1].end_ms == duration


def test_long_shots_are_split_hard():
    sigs, duration = _sequence((BLUE, 100))
    shots = cut_video(sigs, duration, _hist(max_shot_ms=30_000))
    assert [(s.start_ms, s.end_ms) for s in shots] == [
        (0, 30_000),
        (30_000, 60_000),
        (60_000, 90_000),
        (90_000, 100_000),
    ]
    assert all(s.cut_score == 0.0 for s in shots)


def test_cap_keeps_the_strongest_cuts():
    sigs, duration = _sequence(*[((RED, GREEN, BLUE)[i % 3], 3) for i in range(12)])
    all_shots = cut_video(sigs, duration, HIST)
    assert len(all_shots) == 12
    # merge_threshold=0: dropping cuts makes look-alike neighbours, which is
    # the merge step's job, not the cap's — keep the two separable here.
    capped = cut_video(sigs, duration, _hist(max_shots=5, merge_threshold=0.0))
    assert len(capped) == 5
    assert capped[0].start_ms == 0 and capped[-1].end_ms == duration


def test_lookalike_neighbours_merge_only_across_detected_cuts():
    # A generous merge threshold folds two scenes the detector separated ...
    sigs, duration = _sequence((RED, 8), (GREEN, 8))
    assert len(cut_video(sigs, duration, HIST)) == 2
    folded = cut_video(sigs, duration, _hist(merge_threshold=0.99))
    assert [(s.start_ms, s.end_ms) for s in folded] == [(0, 16_000)]
    # ... but never a hard split: that boundary exists to keep length down.
    sigs, duration = _sequence((RED, 40))
    kept = cut_video(sigs, duration, _hist(max_shot_ms=20_000, merge_threshold=0.99))
    assert [(s.start_ms, s.end_ms) for s in kept] == [(0, 20_000), (20_000, 40_000)]


def test_empty_and_degenerate_inputs():
    assert cut_video([], 10_000, HIST) == []
    rng = random.Random(0)
    one = [frame_signature(_frame(RED, rng), 0)]
    assert cut_video(one, 0, HIST) == []
    assert [(s.start_ms, s.end_ms) for s in cut_video(one, 900, HIST)] == [(0, 900)]


def test_algo_version_is_named():
    assert ALGO_VERSION == "scene_v1"
    assert algo_version(DEFAULT_PARAMS) == "scene_v1"
    assert algo_version(HIST) == "hist_v3"
    assert DEFAULT_PARAMS.detector == "scene" and HIST.detector == "hist"


def test_signature_length_mismatch_is_an_error():
    a = FrameSig(0, (0.5, 0.5))
    b = FrameSig(1000, (1.0,))
    with pytest.raises(ValueError):
        distance(a, b)


# ── scene detector (scene_v1) ────────────────────────────────────────────────


def _scores(duration_s: int, spikes: dict[int, float], *, fps: int = 25, base=0.01):
    """A native-rate score track: ``base`` everywhere, ``spikes`` at ms."""
    out = []
    for k in range(duration_s * fps):
        t = k * 1000 // fps
        out.append(SceneScore(t_ms=t, score=spikes.get(t, base if k else 0.0)))
    return out


def test_scene_cuts_land_on_the_spiking_frames_not_the_sampled_ones():
    sigs, duration = _sequence((RED, 10), (GREEN, 8), (BLUE, 12))
    # Real cuts rarely fall on a sampled (1 fps here) timestamp.
    scores = _scores(30, {10_040: 0.45, 18_080: 0.6})
    shots = cut_video(sigs, duration, DEFAULT_PARAMS, scores)
    assert [(s.start_ms, s.end_ms) for s in shots] == [
        (0, 10_040),
        (10_040, 18_080),
        (18_080, 30_000),
    ]
    assert [s.cut_score for s in shots] == [0.0, 0.45, 0.6]
    for s in shots:
        assert s.start_ms <= s.rep_frame_ms < s.end_ms


def test_steady_change_below_the_threshold_never_cuts():
    # A screen recording: the cursor / the inset speaker keep every frame's
    # score non-zero, but well under a cut. Only the 30 s hard splits stay.
    sigs, duration = _sequence((BLUE, 70))
    scores = _scores(70, {}, base=0.2)
    shots = cut_video(sigs, duration, DEFAULT_PARAMS, scores)
    assert [(s.start_ms, s.end_ms) for s in shots] == [
        (0, 30_000),
        (30_000, 60_000),
        (60_000, 70_000),
    ]
    assert all(s.cut_score == 0.0 for s in shots)


def test_scene_threshold_is_the_knob():
    sigs, duration = _sequence((RED, 10), (GREEN, 10))
    scores = _scores(20, {10_000: 0.15})
    assert len(cut_video(sigs, duration, DEFAULT_PARAMS, scores)) == 1
    lower = CutParams(scene_threshold=0.12)
    assert len(cut_video(sigs, duration, lower, scores)) == 2


def test_a_scene_flash_is_dropped_but_a_quick_real_cut_pair_is_not():
    rng = random.Random(3)
    sigs = _scene(RED, 6, 0, rng)
    sigs.append(frame_signature(_frame(WHITE, rng), 6000))
    sigs += _scene(RED, 6, 7, rng)
    flash = _scores(13, {6000: 0.5, 6400: 0.5})
    assert detect_scene_cuts(flash, sigs) == []
    assert [
        (s.start_ms, s.end_ms) for s in cut_video(sigs, 13_000, DEFAULT_PARAMS, flash)
    ] == [(0, 13_000)]
    # RED → (0.4 s of GREEN) → BLUE: two cuts, the pictures differ, keep
    # them; the min-length shaping then folds the short shot.
    sigs2, duration = _sequence((RED, 6), (GREEN, 1), (BLUE, 6))
    quick = _scores(13, {6000: 0.5, 6400: 0.4})
    assert [t for t, _ in detect_scene_cuts(quick, sigs2)] == [6000, 6400]
    assert len(cut_video(sigs2, duration, DEFAULT_PARAMS, quick)) == 2


def test_scene_detector_without_scores_is_one_shot_plus_hard_splits():
    sigs, duration = _sequence((RED, 10), (GREEN, 10))
    assert [(s.start_ms, s.end_ms) for s in cut_video(sigs, duration)] == [(0, 20_000)]


def test_unknown_detector_is_an_error():
    sigs, duration = _sequence((RED, 3))
    with pytest.raises(ValueError):
        cut_video(sigs, duration, CutParams(detector="magic"))
