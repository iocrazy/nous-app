#!/usr/bin/env python
"""Shot-cut benchmark on PRODUCTION data (spec §8.1, T7): sample N of one
user's video downloads, materialize each, run our cutter (``ALGO_VERSION``)
against PySceneDetect's ``ContentDetector`` and print per-video P / R / F1.

The reference IS ContentDetector, so F1 here is agreement with it, not with
human-labelled cuts — scoring ContentDetector itself would read 1.0.

Read-only: nothing is written to the database or the object store. sb://
rows are streamed to a temp file that ``materialize`` deletes per video;
filesystem rows are read in place.

Run inside the backend image (ffmpeg + the app are there). scenedetect is
not in the image — put it in a throwaway target dir so the venv stays as
built:

  docker exec -i -w /app nous-backend sh -c '
    uv pip install -q --python /app/.venv/bin/python --target /tmp/sd "scenedetect[opencv-headless]" \\
    && PYTHONPATH=/tmp/sd /app/.venv/bin/python scripts/bench_shot_cut_prod.py --limit 30'

Options
  --user-id     creator to sample from; default = the creator with the most
                video downloads (the main account on a single-tenant box)
  --limit       videos to score (default 30)
  --min-seconds / --max-seconds   skip shorts and hour-long streams
  --seed        sample is deterministic per seed (default 7)
  --tolerance-ms  matching window (default 500, as in spec §8.1)
  --variants    extra CutParams sets scored on the SAME decode, e.g.
                "detector=hist;scene_threshold=0.2;scene_threshold=0.4" —
                one ffmpeg pass per video (frames + scene scores), one cut
                per variant; ``detector=hist`` alone is exactly hist_v3.
                The per-video table is the default's, the variants table
                compares means
  --json        write per-video rows here
Exit 0 when mean F1 >= 0.8 (default params), 1 otherwise, 2 when nothing
could be scored.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import statistics
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Sequence

from sqlalchemy import func, or_, select

from app.services.library.shot_cut import (
    DEFAULT_PARAMS,
    HIST_V3_PARAMS,
    CutParams,
    cut_video,
)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bench_shot_cut import _f1  # noqa: E402 — sibling script, same matching rule

F1_TARGET = 0.8
DEFAULT_LABEL = "default"

_INT_FIELDS = {"window", "min_shot_ms", "max_shot_ms", "max_shots"}
_STR_FIELDS = {"detector"}
_DETECTORS = {"scene", "hist"}


def _typed(key: str, raw: str) -> Any:
    if key in _STR_FIELDS:
        value = raw.strip()
        if key == "detector" and value not in _DETECTORS:
            raise ValueError(f"unknown detector in variant: {value!r}")
        return value
    return int(raw) if key in _INT_FIELDS else float(raw)


def parse_variants(spec: str | None) -> list[tuple[str, CutParams]]:
    """``"min_shot_ms=800;min_shot_ms=800,ratio=2.0"`` → ``[(label, params)]``,
    always starting with ``("default", DEFAULT_PARAMS)``. Unknown field or bad
    value is a ValueError (a typo must not silently score the defaults)."""
    out: list[tuple[str, CutParams]] = [(DEFAULT_LABEL, DEFAULT_PARAMS)]
    for chunk in (spec or "").split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        overrides: dict[str, Any] = {}
        for pair in chunk.split(","):
            key, sep, raw = pair.strip().partition("=")
            if not sep or not hasattr(DEFAULT_PARAMS, key):
                raise ValueError(f"unknown CutParams field in variant: {pair!r}")
            overrides[key] = _typed(key, raw)
        # ``detector=hist`` starts from hist_v3's own params (its min shot
        # length differs from scene_v1's), so it alone IS the old cutter.
        base = HIST_V3_PARAMS if overrides.get("detector") == "hist" else DEFAULT_PARAMS
        out.append((chunk, replace(base, **overrides)))
    return out


def parse_duration_seconds(value: str | None) -> float | None:
    """``parsed_media.duration`` is free text: ``"93"``, ``"93.5"`` or
    ``"1:33"`` / ``"0:01:33"``. None when it is not a duration."""
    if not value:
        return None
    text = str(value).strip()
    try:
        parts = [float(p) for p in text.split(":")]
    except ValueError:
        return None
    if not parts or any(p < 0 for p in parts):
        return None
    seconds = 0.0
    for p in parts:
        seconds = seconds * 60 + p
    return seconds if seconds > 0 else None


def choose_sample(
    rows: Sequence[dict[str, Any]],
    *,
    limit: int,
    seed: int,
    min_seconds: float,
    max_seconds: float,
) -> list[dict[str, Any]]:
    """Deterministic sample of ``limit`` rows whose duration is known and
    inside ``[min_seconds, max_seconds]``. Rows without a parseable duration
    are skipped: they cannot be filtered, and an hour-long stream would eat
    the whole run."""
    eligible = []
    for r in rows:
        secs = parse_duration_seconds(r.get("duration"))
        if secs is None or secs < min_seconds or secs > max_seconds:
            continue
        eligible.append({**r, "seconds": secs})
    eligible.sort(key=lambda r: int(r["resource_id"]))
    if len(eligible) <= limit:
        return eligible
    return random.Random(seed).sample(eligible, limit)


def _video_rows_stmt(user_id: str | None):
    """Same population as ``video_shots_repository._video_resources_of``
    (the Visual layer's denominator), plus title / duration for the table."""
    from app.models import ParsedMedia, Resources

    stmt = (
        select(
            Resources.id.label("resource_id"),
            Resources.creator_id.label("creator_id"),
            ParsedMedia.title.label("title"),
            ParsedMedia.duration.label("duration"),
        )
        .join(ParsedMedia, ParsedMedia.id == Resources.media_id)
        .where(Resources.source_type == "web")
        .where(Resources.is_trashed.is_(False))
        .where(Resources.media_id.isnot(None))
        .where(
            or_(
                Resources.file_type == "video",
                func.lower(func.coalesce(Resources.mime_type, "")).like("video/%"),
            )
        )
    )
    if user_id:
        stmt = stmt.where(Resources.creator_id == user_id)
    return stmt


async def _list_videos(user_id: str | None) -> list[dict[str, Any]]:
    from app.db.scope import system_request_scope
    from app.db.session import read_scope

    async with system_request_scope(reason="bench-shot-cut: read-only sample"):
        async with read_scope() as session:
            result = await session.execute(_video_rows_stmt(user_id))
            return [dict(r._mapping) for r in result]


def _top_creator(rows: Sequence[dict[str, Any]]) -> str | None:
    counts: dict[str, int] = {}
    for r in rows:
        cid = str(r["creator_id"])
        counts[cid] = counts.get(cid, 0) + 1
    if not counts:
        return None
    return max(counts, key=lambda c: counts[c])


def _reference_cuts_ms(path: str) -> list[int]:
    from scenedetect import ContentDetector, detect

    scenes = detect(path, ContentDetector())
    # ``seconds`` is the current property; ``get_seconds()`` the deprecated one.
    return [
        int(round((getattr(start, "seconds", None) or start.get_seconds()) * 1000))
        for start, _ in scenes[1:]
    ]


def _cuts_of(shots) -> list[int]:
    return [s.start_ms for s in shots[1:] if s.cut_score > 0.0]


async def _score_one(
    user_id: str,
    resource_id: int,
    tolerance_ms: int,
    variants: Sequence[tuple[str, CutParams]],
) -> dict[str, Any]:
    from app.db.scope import Scope, request_scope
    from app.repositories.resources_repository import ResourcesRepository
    from app.services.distribution.cover_frames import load_source_video
    from app.services.library.media_storage import materialize
    from app.services.library.shot_frames import cut_video_file

    async with request_scope(Scope(user_id=user_id)):
        source = await load_source_video(ResourcesRepository(), str(resource_id))
    t0 = time.monotonic()
    per_variant: dict[str, list[int]] = {}
    async with materialize(source.file_path) as local:
        async with cut_video_file(str(local)) as result:
            duration_ms = result.duration_ms
            per_variant[DEFAULT_LABEL] = _cuts_of(result.shots)
            # Same decode, other params: one ffmpeg pass, N cuts.
            for label, params in variants[1:]:
                shots = cut_video(result.sigs, duration_ms, params, result.scene)
                per_variant[label] = _cuts_of(shots)
        # OpenCV decode is blocking CPU work; keep the loop free.
        ref = await asyncio.to_thread(_reference_cuts_ms, str(local))
    scored = {}
    for label, cuts in per_variant.items():
        p, r, f1 = _f1(cuts, ref, tolerance_ms)
        scored[label] = {"ours": len(cuts), "precision": p, "recall": r, "f1": f1}
    default = scored[DEFAULT_LABEL]
    return {
        "resource_id": str(resource_id),
        "duration_ms": duration_ms,
        "ref": len(ref),
        "seconds": time.monotonic() - t0,
        **default,
        "variants": scored,
    }


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--user-id", default=None)
    ap.add_argument("--limit", type=int, default=30)
    ap.add_argument("--min-seconds", type=float, default=20.0)
    ap.add_argument("--max-seconds", type=float, default=900.0)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--tolerance-ms", type=int, default=500)
    ap.add_argument("--variants", default="")
    ap.add_argument("--json", type=Path, default=None)
    args = ap.parse_args()
    variants = parse_variants(args.variants)

    rows = await _list_videos(args.user_id)
    user_id = args.user_id or _top_creator(rows)
    if not user_id:
        print("no video downloads found", file=sys.stderr)
        return 2
    mine = [r for r in rows if str(r["creator_id"]) == user_id]
    sample = choose_sample(
        mine,
        limit=args.limit,
        seed=args.seed,
        min_seconds=args.min_seconds,
        max_seconds=args.max_seconds,
    )
    print(
        f"user {user_id}: {len(mine)} video downloads, "
        f"{len(sample)} sampled ({args.min_seconds:.0f}–{args.max_seconds:.0f} s, seed {args.seed})"
    )
    if not sample:
        return 2

    out: list[dict[str, Any]] = []
    print(
        f"{'resource':18} {'title':28} {'dur':>7} {'ours':>5} {'ref':>5} {'P':>5} {'R':>5} {'F1':>5} {'s':>6}"
    )
    for row in sample:
        title = (row.get("title") or "")[:28]
        try:
            scored = await _score_one(
                user_id, int(row["resource_id"]), args.tolerance_ms, variants
            )
        except Exception as e:  # noqa: BLE001 — one bad file, keep going
            print(
                f"{str(row['resource_id']):18} {title:28} ERROR {type(e).__name__}: {str(e)[:80]}"
            )
            out.append(
                {
                    "resource_id": str(row["resource_id"]),
                    "title": title,
                    "error": str(e)[:200],
                }
            )
            continue
        scored["title"] = title
        out.append(scored)
        print(
            f"{scored['resource_id']:18} {title:28} {scored['duration_ms'] / 1000:7.1f} "
            f"{scored['ours']:5d} {scored['ref']:5d} {scored['precision']:5.2f} "
            f"{scored['recall']:5.2f} {scored['f1']:5.2f} {scored['seconds']:6.1f}"
        )
    scored_rows = [x for x in out if "f1" in x]
    if args.json:
        args.json.write_text(json.dumps(out, ensure_ascii=False, indent=2))
    if not scored_rows:
        print("nothing scored", file=sys.stderr)
        return 2
    mean_f1 = statistics.mean(x["f1"] for x in scored_rows)
    with_cuts = [x for x in scored_rows if x["ref"] > 0]
    line = f"\n{len(scored_rows)} videos · mean F1 {mean_f1:.3f} · tolerance ±{args.tolerance_ms} ms"
    if with_cuts:
        cut_mean = statistics.mean(x["f1"] for x in with_cuts)
        line += (
            f" · videos with reference cuts: {len(with_cuts)}, mean F1 {cut_mean:.3f}"
        )
    print(line)
    if len(variants) > 1:
        print(
            f"\n{'variant':44} {'meanP':>6} {'meanR':>6} {'meanF1':>7} {'F1(ref>0)':>10}"
        )
        for label, _ in variants:
            rows_v = [x["variants"][label] for x in scored_rows]
            rows_c = [x["variants"][label] for x in with_cuts]
            mp = statistics.mean(v["precision"] for v in rows_v)
            mr = statistics.mean(v["recall"] for v in rows_v)
            mf = statistics.mean(v["f1"] for v in rows_v)
            mc = statistics.mean(v["f1"] for v in rows_c) if rows_c else float("nan")
            print(f"{label[:44]:44} {mp:6.3f} {mr:6.3f} {mf:7.3f} {mc:10.3f}")
    return 0 if mean_f1 >= F1_TARGET else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
