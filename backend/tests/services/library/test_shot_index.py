"""shot_index: the per-video unit of work, with the cutter, storage and
repositories stubbed at their module boundaries.

What is pinned: one embedding call per shot (never per frame), the
``"<algo>:<sha1>"`` hash, the abort ladder (process-wide reason → stop at
once; N provider errors in a row → stop; one flaky frame → skipped, not
fatal), the typed mapping of cutter failures, and the phase subtitles the
Task Center shows."""

from __future__ import annotations

import hashlib
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.library import shot_index
from app.services.library.shot_cut import ALGO_VERSION, Shot
from app.services.library.shot_frames import CutResult, SampledFrame, ShotFramesError
from app.services.library.shot_index import (
    ShotIndexError,
    estimate_shots,
    index_resource_shots,
    resolve_space_and_embedder,
)

pytestmark = pytest.mark.asyncio

SPACE = {"id": 353371990308425, "actual_model": "doubao-embedding-vision-251215"}


class _Embedder:
    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    async def try_embed_items(self, items):
        self.calls += 1
        assert len(items) == 1 and items[0].url.startswith("data:image/jpeg;base64,")
        out = self.script.pop(0)
        return out


class _ShotsRepo:
    def __init__(self):
        self.replaced = None

    async def replace(self, *, resource_id, shots, algo_version, duration_ms):
        self.replaced = (resource_id, shots, algo_version, duration_ms)
        return [1000 + k for k in range(len(shots))]


class _VecRepo:
    def __init__(self):
        self.rows = None

    async def upsert_many(self, *, space_id, kind, rows):
        self.rows = (space_id, kind, list(rows))


@pytest.fixture
def stack(monkeypatch, tmp_path):
    """Three shots with distinct JPEG bytes; cutter and storage stubbed."""
    frames = []
    for k, t in enumerate((2000, 6500, 12_000)):
        p = tmp_path / f"f{k}.jpg"
        p.write_bytes(b"jpeg-bytes-%d" % k)
        frames.append(SampledFrame(t_ms=t, path=p))
    shots = (
        Shot(0, 4000, 2000, 0.0),
        Shot(4000, 9000, 6500, 0.4),
        Shot(9000, 15_000, 12_000, 0.6),
    )
    result = CutResult(duration_ms=15_000, fps=1.0, frames=tuple(frames), shots=shots)

    @asynccontextmanager
    async def fake_cut(path, *, params=None):
        yield result

    @asynccontextmanager
    async def fake_materialize(file_path):
        yield Path("/tmp/video.mp4")

    shots_repo, vec_repo = _ShotsRepo(), _VecRepo()
    monkeypatch.setattr(shot_index, "cut_video_file", fake_cut)
    monkeypatch.setattr(
        "app.services.library.media_storage.materialize", fake_materialize
    )
    monkeypatch.setattr(
        "app.repositories.video_shots_repository.get_video_shots_repository",
        lambda: shots_repo,
    )
    monkeypatch.setattr(
        "app.repositories.video_shots_repository.get_video_shot_embeddings_repository",
        lambda: vec_repo,
    )
    return SimpleNamespace(shots_repo=shots_repo, vec_repo=vec_repo, frames=frames)


async def test_one_call_per_shot_and_versioned_hash(stack):
    embedder = _Embedder([([1.0], None)] * 3)
    said = []

    async def progress(pct, subtitle):
        said.append((pct, subtitle))

    result = await index_resource_shots(
        resource_id=42,
        file_path="sb://library/v.mp4",
        space=SPACE,
        embedder=embedder,
        progress=progress,
    )
    assert embedder.calls == 3
    assert (result.shots, result.embedded, result.skipped) == (3, 3, 0)
    assert result.duration_ms == 15_000 and result.algo_version == ALGO_VERSION
    rid, rows, algo, duration = stack.shots_repo.replaced
    assert rid == 42 and algo == ALGO_VERSION and duration == 15_000
    assert [r.shot_index for r in rows] == [0, 1, 2]
    space_id, kind, vec_rows = stack.vec_repo.rows
    assert space_id == SPACE["id"] and kind == "frame"
    expected = f"{ALGO_VERSION}:{hashlib.sha1(b'jpeg-bytes-1').hexdigest()}"
    assert vec_rows[1] == (1001, [1.0], expected)
    subtitles = [s for _, s in said]
    assert "Extracting frames 100% → 3 cuts" in subtitles
    assert "Embedding 3 / 3" in subtitles and subtitles[-1] == "Writing"
    meta = result.as_metadata()
    assert meta["resource_id"] == "42" and meta["space_id"] == str(SPACE["id"])


async def test_one_flaky_frame_is_skipped_not_fatal(stack):
    embedder = _Embedder([([1.0], None), (None, "provider_error: 502"), ([1.0], None)])
    result = await index_resource_shots(
        resource_id=42, file_path="v.mp4", space=SPACE, embedder=embedder
    )
    assert (result.embedded, result.skipped) == (2, 1)
    assert [r[0] for r in stack.vec_repo.rows[2]] == [1000, 1002]


async def test_three_provider_errors_in_a_row_abort(stack):
    embedder = _Embedder([(None, "provider_error: 502")] * 3)
    with pytest.raises(ShotIndexError) as exc:
        await index_resource_shots(
            resource_id=42, file_path="v.mp4", space=SPACE, embedder=embedder
        )
    assert exc.value.reason == "provider_error"
    assert stack.shots_repo.replaced is None  # nothing written


@pytest.mark.parametrize(
    "reason, code",
    [
        ("dimension_mismatch: got 1536", "dimension_mismatch"),
        ("unconfigured", "embedder_unconfigured"),
        ("modality_unsupported: image", "provider_no_image"),
    ],
)
async def test_process_wide_reasons_abort_at_once(stack, reason, code):
    embedder = _Embedder([(None, reason)] * 3)
    with pytest.raises(ShotIndexError) as exc:
        await index_resource_shots(
            resource_id=42, file_path="v.mp4", space=SPACE, embedder=embedder
        )
    assert exc.value.reason == code
    assert embedder.calls <= shot_index.FRAME_EMBED_CONCURRENCY


async def test_cutter_failure_is_mapped(monkeypatch):
    @asynccontextmanager
    async def boom(path, *, params=None):
        raise ShotFramesError("ffmpeg_failed", "boom")
        yield  # pragma: no cover

    @asynccontextmanager
    async def fake_materialize(file_path):
        yield Path("/tmp/video.mp4")

    monkeypatch.setattr(shot_index, "cut_video_file", boom)
    monkeypatch.setattr(
        "app.services.library.media_storage.materialize", fake_materialize
    )
    with pytest.raises(ShotIndexError) as exc:
        await index_resource_shots(
            resource_id=1, file_path="v.mp4", space=SPACE, embedder=_Embedder([])
        )
    assert exc.value.reason == "ffmpeg_failed" and exc.value.detail == "boom"


async def test_resolve_refuses_unconfigured_and_text_only(monkeypatch):
    from unittest.mock import AsyncMock

    monkeypatch.setattr(
        shot_index, "resolve_embedding_config", AsyncMock(return_value=None)
    )
    with pytest.raises(ShotIndexError) as exc:
        await resolve_space_and_embedder()
    assert exc.value.reason == "embedder_unconfigured"

    cfg = SimpleNamespace(model="wemm-embedding-2b", multimodal=False, dimensions=2048)
    monkeypatch.setattr(
        shot_index, "resolve_embedding_config", AsyncMock(return_value=cfg)
    )
    with pytest.raises(ShotIndexError) as exc:
        await resolve_space_and_embedder()
    assert exc.value.reason == "provider_no_image"


def test_estimate_shots_mirrors_the_frontend():
    assert estimate_shots(None) == 0
    assert estimate_shots(192_000) == 43  # 3:12 at 4.5 s per shot
    assert estimate_shots(1000) == 1
