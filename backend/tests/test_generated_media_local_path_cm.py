"""Task 2 of the generated-media object-store-only refactor.

``generated_media_local_path`` replaces ``resolve_generated_media_local_path``
(a plain awaitable that returned None for every object-store row — the
"participant image works for i2v, generated image silently degrades to
text2video" bug). It's now an async contextmanager: filesystem rows yield
their real path (no cleanup — the file lives on), sb:// rows stream through
the shared ``materialize()`` helper into a temp file that's deleted when the
block exits. Any miss (bad URL, missing row, wrong kind, containment
violation) yields None so callers keep their existing i2v→t2v fallback.
"""

from __future__ import annotations

import os

import pytest

import app.services.library.generated_media_service as gm_svc
from app.services.library import media_storage as ms


class _FakeRepo:
    def __init__(self, row):
        self._row = row

    async def get_by_id(self, gen_id):
        return self._row


def _patch_repo(monkeypatch, row):
    import app.repositories.generated_media_repository as repo_mod

    monkeypatch.setattr(repo_mod, "GeneratedMediaRepository", lambda: _FakeRepo(row))


@pytest.mark.asyncio
async def test_filesystem_row_yields_real_path_and_survives_exit(monkeypatch, tmp_path):
    monkeypatch.setattr(gm_svc.settings, "DOWNLOAD_PATH", str(tmp_path))
    rel = "teams/9/generations/2026/07/28/abc/media.png"
    abs_path = tmp_path / rel
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_bytes(b"IMG")

    _patch_repo(monkeypatch, {"id": 5, "media_kind": "image", "file_path": rel})

    async with gm_svc.generated_media_local_path(
        "/api/v1/generated-media/5/cover", media_kind="image"
    ) as path:
        assert path == str(abs_path.resolve())
        assert os.path.isfile(path)

    # filesystem rows are never cleaned up.
    assert abs_path.exists()


@pytest.mark.asyncio
async def test_object_store_row_yields_temp_file_deleted_on_exit(monkeypatch):
    async def _fake_get_stream(self, key, *, start=None, end=None, chunk_size=None):
        yield b"object-store-bytes"

    monkeypatch.setattr(ms.ObjectStore, "get_stream", _fake_get_stream)
    _patch_repo(
        monkeypatch,
        {
            "id": 7,
            "media_kind": "image",
            "file_path": "sb://chat-media/t9/ab/cd/deadbeef.png",
        },
    )

    captured_path = None
    async with gm_svc.generated_media_local_path(
        "/api/v1/generated-media/7/stream", media_kind="image"
    ) as path:
        captured_path = path
        assert path is not None
        assert path.endswith(".png")
        assert os.path.isfile(path)
        assert open(path, "rb").read() == b"object-store-bytes"

    # materialize()'s temp file is cleaned up once the block exits.
    assert not os.path.exists(captured_path)


@pytest.mark.asyncio
async def test_downstream_exception_propagates_and_still_cleans_up(monkeypatch):
    """A failure in the CALLER's own code (e.g. the video provider) inside the
    `async with` block must propagate untouched — not be swallowed as a miss."""

    async def _fake_get_stream(self, key, *, start=None, end=None, chunk_size=None):
        yield b"bytes"

    monkeypatch.setattr(ms.ObjectStore, "get_stream", _fake_get_stream)
    _patch_repo(
        monkeypatch,
        {
            "id": 8,
            "media_kind": "image",
            "file_path": "sb://chat-media/t9/ab/cd/feedface.png",
        },
    )

    captured_path = None
    with pytest.raises(RuntimeError, match="provider exploded"):
        async with gm_svc.generated_media_local_path(
            "/api/v1/generated-media/8/stream", media_kind="image"
        ) as path:
            captured_path = path
            raise RuntimeError("provider exploded")

    assert captured_path is not None
    assert not os.path.exists(captured_path)  # cleanup still ran


@pytest.mark.asyncio
async def test_url_not_matching_serving_pattern_yields_none(monkeypatch):
    async with gm_svc.generated_media_local_path(
        "https://elsewhere.example/img.png", media_kind="image"
    ) as path:
        assert path is None


@pytest.mark.asyncio
async def test_missing_row_yields_none(monkeypatch):
    _patch_repo(monkeypatch, None)
    async with gm_svc.generated_media_local_path(
        "/api/v1/generated-media/999/cover", media_kind="image"
    ) as path:
        assert path is None


@pytest.mark.asyncio
async def test_media_kind_mismatch_yields_none(monkeypatch):
    _patch_repo(
        monkeypatch,
        {"id": 5, "media_kind": "video", "file_path": "teams/9/x/v.mp4"},
    )
    async with gm_svc.generated_media_local_path(
        "/api/v1/generated-media/5/cover", media_kind="image"
    ) as path:
        assert path is None


@pytest.mark.asyncio
async def test_containment_guard_blocks_path_escape(monkeypatch, tmp_path):
    monkeypatch.setattr(gm_svc.settings, "DOWNLOAD_PATH", str(tmp_path / "downloads"))
    (tmp_path / "downloads").mkdir()
    # A hostile rel_path trying to escape DOWNLOAD_PATH via ".." segments.
    outside = tmp_path / "secret.txt"
    outside.write_bytes(b"nope")

    _patch_repo(
        monkeypatch,
        {"id": 5, "media_kind": "image", "file_path": "../secret.txt"},
    )

    async with gm_svc.generated_media_local_path(
        "/api/v1/generated-media/5/cover", media_kind="image"
    ) as path:
        assert path is None


@pytest.mark.asyncio
async def test_object_store_get_stream_failure_yields_none(monkeypatch):
    async def _fake_get_stream(self, key, *, start=None, end=None, chunk_size=None):
        raise RuntimeError("storage-api unreachable")
        yield  # pragma: no cover - unreachable, keeps this an async generator

    monkeypatch.setattr(ms.ObjectStore, "get_stream", _fake_get_stream)
    _patch_repo(
        monkeypatch,
        {
            "id": 9,
            "media_kind": "image",
            "file_path": "sb://chat-media/t9/ab/cd/aaaa.png",
        },
    )

    async with gm_svc.generated_media_local_path(
        "/api/v1/generated-media/9/stream", media_kind="image"
    ) as path:
        assert path is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "suffix",
    ["", "?v=2", "?v=2&full=1", "#frag"],
)
async def test_query_string_does_not_defeat_the_url_match(
    monkeypatch, tmp_path, suffix
):
    """The front end mints `/cover?v=2` (cache bust) and `?v=2&full=1`.

    A `$`-anchored pattern cannot match either, and the miss is a silent
    `yield None` — the exact shape that once degraded i2v to text2video.
    `mediaUrl.COVER_RE` on the front end already uses the `(?=$|[?#])`
    lookahead; both sides of the contract have to agree.
    """
    monkeypatch.setattr(gm_svc.settings, "DOWNLOAD_PATH", str(tmp_path))
    rel = "teams/9/generations/2026/07/28/abc/media.png"
    abs_path = tmp_path / rel
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_bytes(b"IMG")

    _patch_repo(monkeypatch, {"id": 5, "media_kind": "image", "file_path": rel})

    async with gm_svc.generated_media_local_path(
        f"/api/v1/generated-media/5/cover{suffix}", media_kind="image"
    ) as path:
        assert path == str(abs_path.resolve())


def test_serving_url_pattern_still_refuses_a_longer_segment():
    """The lookahead must not turn `/coverage` into a match."""
    assert gm_svc.GENERATED_MEDIA_URL_RE.search("/generated-media/5/coverage") is None
    assert gm_svc.GENERATED_MEDIA_URL_RE.search("/generated-media/5/cover-x") is None
