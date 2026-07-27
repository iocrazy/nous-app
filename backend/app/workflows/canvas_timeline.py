"""canvas_timeline DBOS workflow — the timeline director (G8, Phase 5).

Infinite's LTX director describes a multi-segment video in one node and
hands the whole timeline to a local LTX model. We have no LTX (decision ③ —
align to jimeng/Ark), so the semantics adapt to what the stack CAN do:

  segment 0        → text2video (prompt + seconds)
  segment i>0      → image2video guided by segment i-1's TAIL FRAME
  all segments     → ffmpeg concat (re-encode, uniform params) → one film
  the film         → generated-media store → durable /stream URL

Route C discipline (same as canvas_generation):
  - task_tracking row created by the dispatching endpoint;
  - phase/status/progress mirror from DBOS — never touched here (per-segment
    progress goes through the manager's update_progress helper);
  - the durable result is patched into task METADATA on success;
  - failures raise — a returned failed-dict would read as SUCCESS.

V1 deliberately drops Infinite's audio track and guide_strength (no provider
support) — parked in the epic memo.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from typing import Any, Dict, List, Optional

from dbos import DBOS
from loguru import logger

from app.services.library.generated_media_service import (
    GenerationOrigin,
    register_generated_media,
)
from app.services.library.resources_service import _resolve_personal_team_id
from app.workflows.script_shot_generate import _reap_scratch_dir

_MAX_SEGMENTS = 12
_MAX_SEGMENT_SECONDS = 10


async def _run_ffmpeg(args: List[str]) -> None:
    """Run ffmpeg, raising with its stderr tail on failure."""
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        tail = (stderr or b"")[-800:].decode(errors="replace")
        raise RuntimeError(f"ffmpeg failed (rc={proc.returncode}): {tail}")


@DBOS.step(retries_allowed=True, max_attempts=2)
async def generate_segment_step(
    prompt: str,
    seconds: int,
    model: str,
    aspect: str,
    guide_url: Optional[str],
) -> Dict[str, Any]:
    """Generate ONE timeline segment through the DB-catalog video provider.

    ``guide_url`` is the previous segment's durable tail-frame URL — bridged
    back to a local file for i2v; None (first segment) runs t2v.
    """
    from app.services.media.parsers.video_providers import db_registry

    provider, actual_model = await db_registry.resolve_video_provider(model or None)
    gen_model = model or actual_model

    from app.services.library.generated_media_service import (
        generated_media_local_path,
    )

    async with generated_media_local_path(guide_url, media_kind="image") as image_path:
        result = await provider.generate_video(
            prompt=prompt,
            aspect=aspect or "",
            model_version=gen_model or None,
            image_path=image_path,
            duration_seconds=min(max(int(seconds), 1), _MAX_SEGMENT_SECONDS),
        )
    local_path = getattr(result, "local_path", None)
    if not local_path:
        raise RuntimeError("video provider returned no file for timeline segment")
    return {
        "local_path": str(local_path),
        "model": gen_model or "",
    }


@DBOS.step(retries_allowed=True, max_attempts=2)
async def extract_tail_frame_step(
    segment_path: str,
    user_id: str,
    canvas_id: Optional[int],
    node_id: Optional[str],
    index: int,
) -> str:
    """ffmpeg the segment's last frame out and register it durable.

    The durable /cover URL both guides the NEXT segment (i2v bridge only
    accepts generated-media URLs) and stays inspectable in the library.
    """
    frame_path = os.path.join(
        tempfile.mkdtemp(prefix="mh-timeline-"), f"tail-{index}.png"
    )
    await _run_ffmpeg(
        [
            "ffmpeg",
            "-y",
            "-sseof",
            "-0.25",
            "-i",
            segment_path,
            "-frames:v",
            "1",
            "-q:v",
            "2",
            frame_path,
        ]
    )
    if not os.path.exists(frame_path) or os.path.getsize(frame_path) == 0:
        raise RuntimeError("ffmpeg produced no tail frame")

    scope_id = int(await _resolve_personal_team_id(str(user_id)))
    row = await register_generated_media(
        user_id=str(user_id),
        scope_id=scope_id,
        source_path=frame_path,
        source_url=None,
        mime="image/png",
        origin=GenerationOrigin(
            kind="canvas_run",
            run_id=None,
            canvas_id=canvas_id,
            node_id=node_id,
            prompt=f"timeline segment {index} tail frame",
            model=None,
            provider="ffmpeg",
            params={"segment_index": index},
            derivation_kind="timeline_tail_frame",
        ),
    )
    gen_id = row.get("id")
    if gen_id is None:
        raise RuntimeError("tail frame registration returned no id")
    return f"/api/v1/generated-media/{gen_id}/cover"


@DBOS.step()
async def concat_segments_step(segment_paths: List[str]) -> str:
    """Concat the segment files into one mp4 (re-encode for uniformity).

    Provider outputs are usually parameter-identical, but a filter-graph
    concat re-encodes defensively — mismatched fps/size would make the
    stream-copy variant silently glitch at the joins.
    """
    if len(segment_paths) == 1:
        return segment_paths[0]

    out_path = os.path.join(tempfile.mkdtemp(prefix="mh-timeline-"), "film.mp4")
    inputs: List[str] = []
    for p in segment_paths:
        inputs.extend(["-i", p])
    n = len(segment_paths)
    filtergraph = (
        "".join(f"[{i}:v:0]" for i in range(n)) + f"concat=n={n}:v=1:a=0[outv]"
    )
    await _run_ffmpeg(
        [
            "ffmpeg",
            "-y",
            *inputs,
            "-filter_complex",
            filtergraph,
            "-map",
            "[outv]",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            out_path,
        ]
    )
    if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
        raise RuntimeError("ffmpeg concat produced no output")
    return out_path


@DBOS.step()
async def persist_timeline_film_step(
    film_path: str,
    user_id: str,
    canvas_id: Optional[int],
    node_id: Optional[str],
    prompt_summary: str,
    segment_count: int,
) -> Dict[str, Any]:
    """Register the finished film → durable /stream URL."""
    try:
        scope_id = int(await _resolve_personal_team_id(str(user_id)))
        row = await register_generated_media(
            user_id=str(user_id),
            scope_id=scope_id,
            source_path=film_path,
            source_url=None,
            mime="video/mp4",
            origin=GenerationOrigin(
                kind="canvas_run",
                run_id=None,
                canvas_id=canvas_id,
                node_id=node_id,
                prompt=prompt_summary,
                model=None,
                provider="timeline",
                params={"segments": segment_count},
                derivation_kind="timeline_film",
            ),
        )
        gen_id = row.get("id")
        if gen_id is None:
            raise RuntimeError("film registration returned no id")
        return {
            "generated_media_id": gen_id,
            "result_url": f"/api/v1/generated-media/{gen_id}/stream",
            "media_kind": "video",
        }
    finally:
        _reap_scratch_dir(film_path)


@DBOS.step()
async def record_timeline_result_step(result: Dict[str, Any]) -> None:
    """Patch the durable result into the task row (business decoration)."""
    from app.services.infra.unified_task_manager import get_task_manager

    task_id = DBOS.workflow_id
    if not task_id:
        return
    await get_task_manager().patch_metadata(task_id, result)


def _progress_payload(
    done: int, total: int, segment_frames: Optional[List[str]] = None
) -> Dict[str, Any]:
    """Progress metadata (P2-1): tail frames ride along incrementally so the
    frontend shows per-segment thumbnails mid-run and on failure."""
    payload: Dict[str, Any] = {"segments_done": done, "segments_total": total}
    if segment_frames:
        payload["segment_frames"] = list(segment_frames)
    return payload


@DBOS.step()
async def note_timeline_progress_step(
    done: int, total: int, segment_frames: Optional[List[str]] = None
) -> None:
    """Business progress decoration (segments finished / total / frames)."""
    from app.services.infra.unified_task_manager import get_task_manager

    task_id = DBOS.workflow_id
    if not task_id:
        return
    await get_task_manager().patch_metadata(
        task_id, _progress_payload(done, total, segment_frames)
    )


@DBOS.workflow()
async def canvas_timeline_workflow(
    segments: List[Dict[str, Any]],
    model: str,
    aspect: str,
    canvas_id: Optional[int],
    node_id: Optional[str],
    user_id: Optional[str],
) -> Dict[str, Any]:
    if not user_id:
        raise ValueError("timeline run has no user_id")
    if not segments:
        raise ValueError("timeline run has no segments")
    segments = segments[:_MAX_SEGMENTS]

    total = len(segments)
    segment_paths: List[str] = []
    segment_frames: List[str] = []
    guide_url: Optional[str] = None

    for index, seg in enumerate(segments):
        media = await generate_segment_step(
            prompt=str(seg.get("prompt") or ""),
            seconds=int(seg.get("seconds") or 5),
            model=model,
            aspect=aspect,
            guide_url=guide_url,
        )
        segment_paths.append(media["local_path"])
        # Tail frame guides the NEXT segment; the last segment skips it.
        if index < total - 1:
            guide_url = await extract_tail_frame_step(
                segment_path=media["local_path"],
                user_id=str(user_id),
                canvas_id=canvas_id,
                node_id=node_id,
                index=index,
            )
            segment_frames.append(guide_url)
        await note_timeline_progress_step(index + 1, total, segment_frames)

    film_path = await concat_segments_step(segment_paths)
    prompt_summary = " / ".join(str(s.get("prompt") or "")[:40] for s in segments[:3])
    result = await persist_timeline_film_step(
        film_path=film_path,
        user_id=str(user_id),
        canvas_id=canvas_id,
        node_id=node_id,
        prompt_summary=prompt_summary,
        segment_count=total,
    )
    result["segment_frames"] = segment_frames
    await record_timeline_result_step(result)

    logger.info(
        "[canvas_timeline] canvas={} node={} segments={} → {}",
        canvas_id,
        node_id,
        total,
        result.get("result_url"),
    )
    return result
