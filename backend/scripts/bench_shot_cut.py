#!/usr/bin/env python
"""Shot-cut benchmark: our ``hist_v2`` cutter vs PySceneDetect's
``ContentDetector`` on a directory of videos (spec §8.1: F1 ≥ 0.8 at ±0.5 s).

Reference = PySceneDetect (pip: ``scenedetect[opencv]``), run on the same
files at its default threshold (27). It is not ground truth either; it is
the reproducible, locally runnable stand-in the spec settled on. Disagreement
is reported per video so a systematic miss (dissolves, very short shots) can
be told from noise.

Usage (from ``backend/``):

  uv run --with 'scenedetect[opencv]' python scripts/bench_shot_cut.py \
      /path/to/videos --limit 30 [--tolerance-ms 500] [--json out.json]

Only the cut *times* are compared (a shot boundary is the start of every
shot but the first). Hard splits at ``max_shot_ms`` are excluded from ours,
since the reference has no such notion.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}


def _reference_cuts_ms(path: Path) -> list[int]:
    from scenedetect import ContentDetector, detect

    scenes = detect(str(path), ContentDetector())
    # Boundaries = the start of every scene but the first.
    return [int(round(start.get_seconds() * 1000)) for start, _ in scenes[1:]]


async def _our_cuts_ms(path: Path) -> tuple[list[int], int, float]:
    from app.services.library.shot_frames import cut_video_file

    t0 = time.monotonic()
    async with cut_video_file(str(path)) as result:
        cuts = [s.start_ms for s in result.shots[1:] if s.cut_score > 0.0]
        return cuts, result.duration_ms, time.monotonic() - t0


def _f1(ours: list[int], ref: list[int], tol_ms: int) -> tuple[float, float, float]:
    """Greedy one-to-one matching within ``tol_ms``."""
    if not ours and not ref:
        return 1.0, 1.0, 1.0
    unmatched_ref = list(ref)
    tp = 0
    for t in ours:
        best = None
        for j, r in enumerate(unmatched_ref):
            if abs(r - t) <= tol_ms and (
                best is None or abs(r - t) < abs(unmatched_ref[best] - t)
            ):
                best = j
        if best is not None:
            tp += 1
            unmatched_ref.pop(best)
    precision = tp / len(ours) if ours else (1.0 if not ref else 0.0)
    recall = tp / len(ref) if ref else (1.0 if not ours else 0.0)
    f1 = (
        0.0
        if precision + recall == 0
        else 2 * precision * recall / (precision + recall)
    )
    return precision, recall, f1


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("videos", type=Path, help="directory of video files")
    ap.add_argument("--limit", type=int, default=30)
    ap.add_argument("--tolerance-ms", type=int, default=500)
    ap.add_argument("--json", type=Path, default=None, help="write per-video rows here")
    args = ap.parse_args()

    files = sorted(
        p for p in args.videos.iterdir() if p.suffix.lower() in VIDEO_SUFFIXES
    )[: args.limit]
    if not files:
        print(f"no videos under {args.videos}", file=sys.stderr)
        return 2

    rows = []
    print(
        f"{'video':40} {'dur':>7} {'ours':>5} {'ref':>5} {'P':>5} {'R':>5} {'F1':>5} {'s':>6}"
    )
    for path in files:
        try:
            ours, duration_ms, secs = await _our_cuts_ms(path)
            ref = _reference_cuts_ms(path)
        except Exception as e:  # noqa: BLE001 — one bad file, keep going
            print(f"{path.name[:40]:40} ERROR {e}")
            rows.append({"video": path.name, "error": str(e)})
            continue
        p, r, f1 = _f1(ours, ref, args.tolerance_ms)
        rows.append(
            {
                "video": path.name,
                "duration_ms": duration_ms,
                "ours": len(ours),
                "ref": len(ref),
                "precision": p,
                "recall": r,
                "f1": f1,
                "seconds": secs,
            }
        )
        print(
            f"{path.name[:40]:40} {duration_ms / 1000:7.1f} {len(ours):5d} {len(ref):5d} "
            f"{p:5.2f} {r:5.2f} {f1:5.2f} {secs:6.1f}"
        )
    scored = [x for x in rows if "f1" in x]
    if scored:
        mean_f1 = sum(x["f1"] for x in scored) / len(scored)
        print(
            f"\n{len(scored)} videos · mean F1 {mean_f1:.3f} · tolerance ±{args.tolerance_ms} ms"
        )
    if args.json:
        args.json.write_text(json.dumps(rows, indent=2))
    return 0 if scored and mean_f1 >= 0.8 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
