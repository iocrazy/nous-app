"""Fold adjacent shots whose representative-frame vectors say "same picture".

The cutter sees pixels; a screen recording with a talking-head inset changes
pixels all the time (a gesture, a cursor, a rotating title) without changing
what the shot IS. The embedder already looked at one frame per shot, so the
index step can ask it: two neighbours whose frame vectors are near-identical
are one shot. This costs nothing extra — the vectors exist either way.

Rules (mirroring the cutter's own look-alike merge):

* only across a boundary the detector found (``cut_score > 0``); a hard
  split exists to keep a long shot apart and stays;
* only when both shots have a vector (a skipped frame gives no evidence);
* the merged shot runs from the first start to the last end, keeps the
  opening boundary's ``cut_score``, and takes its representative frame and
  vector from whichever side had the higher ``cut_score`` (the more
  confident boundary; ties keep the earlier one). Chains fold left to right,
  comparing against the kept vector.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Optional, Sequence

from app.services.library.shot_cut import Shot

#: Cosine at or above which two adjacent representative frames are the same
#: picture. 0.95 is a starting point, not a measurement (the Mac has no
#: WeMM / doubao vectors): image embedders put two frames of one static
#: screen — differing in a cursor, a gesture in an inset, a few changed
#: characters — very close to 1, while another slide or scene of the same
#: video lands clearly lower. High on purpose: a missed merge leaves today's
#: behaviour, a wrong merge hides a real cut from search. Tune against the
#: production re-index of the 17-shot screen recording that motivated it.
VECTOR_MERGE_COSINE = 0.95

#: One shot's vector and frame hash as the index step computed it.
Embedded = Optional[tuple[list[float], str]]


@dataclass(frozen=True)
class MergeResult:
    shots: tuple[Shot, ...]
    vectors: tuple[Embedded, ...]
    merged: int


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity; 0.0 for a zero vector or a length mismatch."""
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def merge_similar_neighbours(
    shots: Sequence[Shot],
    vectors: Sequence[Embedded],
    *,
    threshold: float = VECTOR_MERGE_COSINE,
) -> MergeResult:
    """Fold adjacent shots whose vectors reach ``threshold``. ``vectors[k]``
    belongs to ``shots[k]``; the result keeps that pairing."""
    if len(shots) != len(vectors):
        raise ValueError("one vector slot per shot")
    out_shots: list[Shot] = []
    out_vecs: list[Embedded] = []
    #: cut_score of the shot each kept frame + vector came from (differs
    #: from the merged shot's own, opening, cut_score once a chain folds).
    rep_scores: list[float] = []
    merged = 0
    for shot, vec in zip(shots, vectors):
        if out_shots and shot.cut_score > 0.0:
            prev, prev_vec = out_shots[-1], out_vecs[-1]
            if (
                prev_vec is not None
                and vec is not None
                and cosine(prev_vec[0], vec[0]) >= threshold
            ):
                take_new = shot.cut_score > rep_scores[-1]
                out_shots[-1] = replace(
                    prev,
                    end_ms=shot.end_ms,
                    rep_frame_ms=shot.rep_frame_ms if take_new else prev.rep_frame_ms,
                )
                if take_new:
                    out_vecs[-1] = vec
                    rep_scores[-1] = shot.cut_score
                merged += 1
                continue
        out_shots.append(shot)
        out_vecs.append(vec)
        rep_scores.append(shot.cut_score)
    return MergeResult(shots=tuple(out_shots), vectors=tuple(out_vecs), merged=merged)


__all__ = [
    "VECTOR_MERGE_COSINE",
    "MergeResult",
    "cosine",
    "merge_similar_neighbours",
]
