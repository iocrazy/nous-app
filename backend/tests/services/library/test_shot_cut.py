"""shot_cut: cuts on synthetic frame sequences.

Frames are solid colours with a little noise so the intra-scene distance is
small but non-zero (a real video never repeats a frame exactly). One frame
per second, the way the workflow samples.
"""

from __future__ import annotations

import random

import pytest
from PIL import Image

from app.services.library.shot_cut import (
    ALGO_VERSION,
    SIGNATURE_LEN,
    CutParams,
    FrameSig,
    cut_video,
    detect_cuts,
    distance,
    frame_signature,
)

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
    shots = cut_video(sigs, duration)
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
    shots = cut_video(sigs, 13_000)
    assert [(s.start_ms, s.end_ms) for s in shots] == [(0, 13_000)]


def test_static_video_has_no_cuts_even_with_ratio_spikes():
    # Baseline ~0 makes any tiny wobble a huge ratio; min_abs must veto it.
    sigs, duration = _sequence((BLUE, 40))
    assert detect_cuts(sigs) == []


def test_shots_shorter_than_min_length_merge_into_a_neighbour():
    # 1 s of GREEN between two RED scenes: below min_shot_ms, folded away.
    sigs, duration = _sequence((RED, 8), (GREEN, 1), (BLUE, 8))
    shots = cut_video(sigs, duration, CutParams(min_shot_ms=1500))
    assert len(shots) == 2
    assert shots[0].start_ms == 0 and shots[-1].end_ms == duration


def test_long_shots_are_split_hard():
    sigs, duration = _sequence((BLUE, 100))
    shots = cut_video(sigs, duration, CutParams(max_shot_ms=30_000))
    assert [(s.start_ms, s.end_ms) for s in shots] == [
        (0, 30_000),
        (30_000, 60_000),
        (60_000, 90_000),
        (90_000, 100_000),
    ]
    assert all(s.cut_score == 0.0 for s in shots)


def test_cap_keeps_the_strongest_cuts():
    sigs, duration = _sequence(*[((RED, GREEN, BLUE)[i % 3], 3) for i in range(12)])
    all_shots = cut_video(sigs, duration)
    assert len(all_shots) == 12
    # merge_threshold=0: dropping cuts makes look-alike neighbours, which is
    # the merge step's job, not the cap's — keep the two separable here.
    capped = cut_video(sigs, duration, CutParams(max_shots=5, merge_threshold=0.0))
    assert len(capped) == 5
    assert capped[0].start_ms == 0 and capped[-1].end_ms == duration


def test_lookalike_neighbours_merge_only_across_detected_cuts():
    # A generous merge threshold folds two scenes the detector separated ...
    sigs, duration = _sequence((RED, 8), (GREEN, 8))
    assert len(cut_video(sigs, duration)) == 2
    folded = cut_video(sigs, duration, CutParams(merge_threshold=0.99))
    assert [(s.start_ms, s.end_ms) for s in folded] == [(0, 16_000)]
    # ... but never a hard split: that boundary exists to keep length down.
    sigs, duration = _sequence((RED, 40))
    kept = cut_video(
        sigs, duration, CutParams(max_shot_ms=20_000, merge_threshold=0.99)
    )
    assert [(s.start_ms, s.end_ms) for s in kept] == [(0, 20_000), (20_000, 40_000)]


def test_empty_and_degenerate_inputs():
    assert cut_video([], 10_000) == []
    rng = random.Random(0)
    one = [frame_signature(_frame(RED, rng), 0)]
    assert cut_video(one, 0) == []
    assert [(s.start_ms, s.end_ms) for s in cut_video(one, 900)] == [(0, 900)]


def test_algo_version_is_named():
    assert ALGO_VERSION == "hist_v3"


def test_signature_length_mismatch_is_an_error():
    a = FrameSig(0, (0.5, 0.5))
    b = FrameSig(1000, (1.0,))
    with pytest.raises(ValueError):
        distance(a, b)
