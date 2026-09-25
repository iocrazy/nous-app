"""Index the shots of one video: materialize → cut → embed representative
frames → write. The unit of work behind the ``index_shots`` workflow.

Spec: docs/superpowers/specs/2026-09-25-pr3-shots-index-design.md §5.

Cost shape: one embedding call per shot (the representative frame as a
``data:image/jpeg;base64`` item), never per sampled frame — that is the
whole reason the cutter works on pixels. A network provider pays ≈ one image
per 4–5 s of video.

Failure is typed: :class:`ShotIndexError.reason` is a stable code the
workflow writes into the task subtitle and the endpoints return as
``details.code``. Process-wide reasons (``embedder_unconfigured`` /
``provider_no_image`` / ``dimension_mismatch`` / ``store_missing``) abort at
the first shot — every later shot would end the same way — and
``MAX_PROVIDER_ERRORS`` failures in a row are read as "the provider is
down", not as flaky shots.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
from dataclasses import asdict, dataclass
from typing import Any, Awaitable, Callable, Optional

from loguru import logger

from app.services.ai.providers.embedding_capabilities import capabilities_for
from app.services.ai.providers.embedding_config import resolve_embedding_config
from app.services.ai.providers.embedding_items import ImageUrlItem
from app.services.ai.providers.embedding_service import (
    EmbeddingService,
    classify_embed_reason,
)
from app.services.library.shot_cut import ALGO_VERSION, DEFAULT_PARAMS, CutParams
from app.services.library.shot_frames import ShotFramesError, cut_video_file

#: Representative frames in flight at once against the provider. Network
#: providers (doubao) rate-limit; a local engine would take more, but the
#: cutter is the slow part there anyway.
FRAME_EMBED_CONCURRENCY = 3
#: Consecutive ``provider_error`` results that mean "down, stop".
MAX_PROVIDER_ERRORS = 3
#: Reasons that are about the embedder / store, not one frame.
ABORT_REASONS = frozenset(
    {
        "embedder_unconfigured",
        "dimension_mismatch",
        "store_missing",
        "provider_no_image",
    }
)

ProgressFn = Callable[[int, str], Awaitable[None]]


class ShotIndexError(RuntimeError):
    """``reason`` is a stable code: ``resource_not_found`` / ``not_a_video`` /
    ``no_video_file`` / ``embedder_unconfigured`` / ``provider_no_image`` /
    ``dimension_mismatch`` / ``store_missing`` / ``provider_error`` / the
    :class:`ShotFramesError` reasons (``ffmpeg_missing`` / ``video_missing``
    / ``probe_failed`` / ``ffmpeg_failed`` / ``timeout``)."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class ShotIndexResult:
    resource_id: int
    space_id: int
    algo_version: str
    duration_ms: int
    shots: int
    embedded: int
    #: Shots whose frame the provider refused (``provider_error``); the shot
    #: row exists, its vector does not, so the video counts as covered only
    #: if at least one landed.
    skipped: int

    def as_metadata(self) -> dict[str, Any]:
        d = asdict(self)
        d["resource_id"] = str(self.resource_id)  # Snowflake > 2^53 in JS
        d["space_id"] = str(self.space_id)
        return d


async def resolve_space_and_embedder() -> tuple[dict, EmbeddingService]:
    """The ACTIVE space and its embedder, checked for image input. Typed
    ``embedder_unconfigured`` / ``provider_no_image`` / ``store_missing``."""
    from app.repositories.embedding_space_repository import (
        get_embedding_space_repository,
    )
    from app.repositories.resource_embeddings_repository import EmbeddingStoreMissing

    cfg = await resolve_embedding_config()
    if cfg is None:
        raise ShotIndexError("embedder_unconfigured")
    caps = capabilities_for(cfg)
    if "image" not in caps.modalities:
        raise ShotIndexError(
            "provider_no_image",
            f"{cfg.model} declares {sorted(caps.modalities)}; shots need image input",
        )
    embedder = EmbeddingService(cfg=cfg)
    spec = await embedder.space_spec()
    if spec is None:
        raise ShotIndexError(
            "embedder_unconfigured", "no client for the configured embedder"
        )
    try:
        space = await get_embedding_space_repository().get_or_create(spec)
    except EmbeddingStoreMissing as e:
        raise ShotIndexError("store_missing", str(e)) from e
    return space, embedder


def _data_uri(jpeg: bytes) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")


def _frame_hash(jpeg: bytes) -> str:
    return f"{ALGO_VERSION}:{hashlib.sha1(jpeg).hexdigest()}"


async def _embed_frame(
    embedder: EmbeddingService, jpeg: bytes
) -> tuple[Optional[list[float]], Optional[str]]:
    vec, reason = await embedder.try_embed_items([ImageUrlItem(_data_uri(jpeg))])
    if vec is not None:
        return vec, None
    code = classify_embed_reason(reason)
    if reason == "unconfigured":
        code = "embedder_unconfigured"
    if reason and reason.startswith("modality_unsupported"):
        code = "provider_no_image"
    return None, code or "provider_error"


async def index_resource_shots(
    *,
    resource_id: int,
    file_path: str,
    space: dict,
    embedder: EmbeddingService,
    progress: Optional[ProgressFn] = None,
    params: CutParams = DEFAULT_PARAMS,
) -> ShotIndexResult:
    """Cut ``file_path`` (any storage shape; materialized here), embed one
    frame per shot into ``space`` and replace the resource's shot rows.

    ``progress(pct, subtitle)`` is called at the phase changes the Task Center
    shows (extract → cuts → embedding i/n → writing)."""
    from app.repositories.video_shots_repository import (
        FRAME_KIND,
        ShotRow,
        get_video_shot_embeddings_repository,
        get_video_shots_repository,
    )
    from app.services.library.media_storage import materialize

    async def _say(pct: int, subtitle: str) -> None:
        if progress is not None:
            try:
                await progress(pct, subtitle)
            except Exception as e:  # noqa: BLE001 — progress is decoration
                logger.warning(f"[shot_index] progress callback failed: {e}")

    space_id = int(space["id"])
    await _say(5, "Extracting frames")
    try:
        async with materialize(file_path) as local_path:
            async with cut_video_file(str(local_path), params=params) as cut:
                shots = list(cut.shots)
                await _say(30, f"Extracting frames 100% → {len(shots)} cuts")
                # Read the representative frames while the scratch dir lives.
                frames: list[bytes] = []
                for shot in shots:
                    frame = cut.frame_at(shot.rep_frame_ms)
                    frames.append(frame.path.read_bytes() if frame is not None else b"")
                duration_ms = cut.duration_ms
    except ShotFramesError as e:
        raise ShotIndexError(e.reason, e.detail) from e
    except ValueError as e:  # materialize's containment guard
        raise ShotIndexError("no_video_file", str(e)) from e

    vectors: list[Optional[tuple[list[float], str]]] = [None] * len(shots)
    total = len(shots)
    done = 0
    consecutive_errors = 0
    sem = asyncio.Semaphore(FRAME_EMBED_CONCURRENCY)

    async def one(i: int) -> tuple[int, Optional[list[float]], Optional[str]]:
        async with sem:
            if not frames[i]:
                return i, None, "provider_error"
            vec, code = await _embed_frame(embedder, frames[i])
            return i, vec, code

    # Chunked so a process-wide failure aborts after at most one chunk of
    # wasted calls, and so consecutive-error counting stays in order.
    for start in range(0, total, FRAME_EMBED_CONCURRENCY):
        chunk = list(range(start, min(total, start + FRAME_EMBED_CONCURRENCY)))
        results = await asyncio.gather(*(one(i) for i in chunk))
        for i, vec, code in results:
            done += 1
            if vec is not None:
                vectors[i] = (vec, _frame_hash(frames[i]))
                consecutive_errors = 0
                continue
            if code in ABORT_REASONS:
                raise ShotIndexError(code, f"shot {i}")
            consecutive_errors += 1
            if consecutive_errors >= MAX_PROVIDER_ERRORS:
                raise ShotIndexError(
                    "provider_error", f"{MAX_PROVIDER_ERRORS} frames in a row"
                )
        await _say(30 + int(60 * done / max(total, 1)), f"Embedding {done} / {total}")

    await _say(92, "Writing")
    shot_rows = [
        ShotRow(
            shot_index=k,
            start_ms=s.start_ms,
            end_ms=s.end_ms,
            rep_frame_ms=s.rep_frame_ms,
            cut_score=s.cut_score,
        )
        for k, s in enumerate(shots)
    ]
    ids = await get_video_shots_repository().replace(
        resource_id=resource_id,
        shots=shot_rows,
        algo_version=ALGO_VERSION,
        duration_ms=duration_ms,
    )
    landed = [
        (ids[k], vectors[k][0], vectors[k][1])
        for k in range(len(shots))
        if vectors[k] is not None
    ]
    await get_video_shot_embeddings_repository().upsert_many(
        space_id=space_id, kind=FRAME_KIND, rows=landed
    )
    result = ShotIndexResult(
        resource_id=resource_id,
        space_id=space_id,
        algo_version=ALGO_VERSION,
        duration_ms=duration_ms,
        shots=len(shots),
        embedded=len(landed),
        skipped=len(shots) - len(landed),
    )
    logger.info(f"[shot_index] resource={resource_id} {result}")
    return result


def estimate_shots(duration_ms: Optional[int], *, seconds_per_shot: float = 4.5) -> int:
    """≈ shots for a video of ``duration_ms`` (mirrors the frontend's
    ``estimateShots``: 4.5 s per shot)."""
    if not duration_ms or duration_ms <= 0:
        return 0
    return max(1, int(-(-duration_ms // int(seconds_per_shot * 1000))))


#: Rough doubao spend per representative frame (spec §11: an empirical
#: constant, calibrated against real runs later).
TOKENS_PER_SHOT = 300


def estimate_tokens(shots: int) -> int:
    return shots * TOKENS_PER_SHOT


__all__ = [
    "ABORT_REASONS",
    "FRAME_EMBED_CONCURRENCY",
    "MAX_PROVIDER_ERRORS",
    "TOKENS_PER_SHOT",
    "ShotIndexError",
    "ShotIndexResult",
    "estimate_shots",
    "estimate_tokens",
    "index_resource_shots",
    "resolve_space_and_embedder",
]
