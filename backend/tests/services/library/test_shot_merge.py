"""shot_merge: adjacent shots fold when their frame vectors match.

Hand-written vectors: the threshold itself is a production call (see
``VECTOR_MERGE_COSINE``); what is pinned here is the folding rule."""

from __future__ import annotations

import pytest

from app.services.library.shot_cut import Shot
from app.services.library.shot_merge import (
    VECTOR_MERGE_COSINE,
    cosine,
    merge_similar_neighbours,
)

A, A2, B = [1.0, 0.0, 0.0], [0.99, 0.05, 0.0], [0.0, 1.0, 0.0]


def _v(vec: list[float], tag: str) -> tuple[list[float], str]:
    return vec, f"scene_v1:{tag}"


def test_cosine_basics():
    assert cosine(A, A) == pytest.approx(1.0)
    assert cosine(A, B) == 0.0
    assert cosine(A, [0.0, 0.0, 0.0]) == 0.0
    assert cosine(A, [1.0]) == 0.0
    assert cosine(A, A2) >= VECTOR_MERGE_COSINE


def test_three_lookalike_shots_fold_into_one():
    # The motivating case: 0:33–0:51, 0:51–1:00, 1:00–1:35 of one screen.
    shots = [
        Shot(0, 33_000, 16_000, 0.0),
        Shot(33_000, 51_000, 42_000, 0.35),
        Shot(51_000, 60_000, 55_000, 0.5),
        Shot(60_000, 95_000, 77_000, 0.4),
    ]
    vectors = [_v(B, "0"), _v(A, "1"), _v(A2, "2"), _v(A, "3")]
    out = merge_similar_neighbours(shots, vectors)
    assert out.merged == 2
    assert [(s.start_ms, s.end_ms) for s in out.shots] == [
        (0, 33_000),
        (33_000, 95_000),
    ]
    folded = out.shots[1]
    # Opening boundary keeps its score; frame + vector from the strongest cut.
    assert folded.cut_score == 0.35
    assert folded.rep_frame_ms == 55_000 and out.vectors[1] == _v(A2, "2")


def test_ties_keep_the_earlier_frame():
    shots = [Shot(0, 5000, 2000, 0.3), Shot(5000, 9000, 7000, 0.3)]
    out = merge_similar_neighbours(shots, [_v(A, "0"), _v(A, "1")])
    assert out.shots == (Shot(0, 9000, 2000, 0.3),)
    assert out.vectors == (_v(A, "0"),)


def test_hard_splits_and_missing_vectors_never_fold():
    shots = [
        Shot(0, 30_000, 15_000, 0.0),
        Shot(30_000, 60_000, 45_000, 0.0),  # hard split
        Shot(60_000, 70_000, 65_000, 0.4),
        Shot(70_000, 80_000, 75_000, 0.4),
    ]
    vectors = [_v(A, "0"), _v(A, "1"), None, _v(A, "3")]
    out = merge_similar_neighbours(shots, vectors)
    assert out.merged == 0 and out.shots == tuple(shots)


def test_below_threshold_stays_apart_and_threshold_is_a_parameter():
    shots = [Shot(0, 5000, 2000, 0.0), Shot(5000, 9000, 7000, 0.3)]
    vectors = [_v(A, "0"), _v([0.8, 0.6, 0.0], "1")]  # cosine 0.8
    assert merge_similar_neighbours(shots, vectors).merged == 0
    assert merge_similar_neighbours(shots, vectors, threshold=0.8).merged == 1


def test_empty_and_mismatched_inputs():
    out = merge_similar_neighbours([], [])
    assert out.shots == () and out.merged == 0
    with pytest.raises(ValueError):
        merge_similar_neighbours([Shot(0, 1000, 0, 0.0)], [])
