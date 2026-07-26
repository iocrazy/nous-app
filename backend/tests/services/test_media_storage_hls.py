"""HLS object-store primitives: path-addressed keys + directory operations.

HLS is the one sb:// writer that cannot content-address its keys — an m3u8
references its siblings by bare relative path, so hashing a segment's name
breaks every playlist pointing at it. That forces a second key namespace and a
set of directory-shaped store operations. These tests pin the parts that would
fail silently or dangerously: key escaping, prefix isolation, list pagination,
and upload concurrency.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.services.library.media_storage import (
    ObjectStore,
    hls_key,
    hls_key_prefix,
)


class TestHlsKeys:
    def test_prefix_is_scoped_per_version(self):
        assert hls_key_prefix("res1", "ver1") == "hls/res1/ver1"

    def test_key_preserves_relative_structure(self):
        # The m3u8 says "480p/stream.m3u8"; the key must end the same way or
        # relative resolution breaks once the tree is in the store.
        assert hls_key("r", "v", "480p/stream.m3u8") == "hls/r/v/480p/stream.m3u8"
        assert hls_key("r", "v", "segment_000.ts") == "hls/r/v/segment_000.ts"

    def test_namespace_cannot_collide_with_content_addressed_keys(self):
        # Content-addressed keys always start "t{scope_id}/".
        assert hls_key("r", "v", "a.ts").startswith("hls/")

    @pytest.mark.parametrize(
        "rel",
        [
            "../escape.ts",
            "480p/../../escape.ts",
            "",
            "  ",
            "seg\x00.ts",
        ],
    )
    def test_rejects_path_traversal_and_junk(self, rel):
        with pytest.raises(ValueError):
            hls_key("r", "v", rel)

    def test_leading_slash_is_normalised_not_rejected(self):
        # A leading slash is stripped, so the key still lands inside the
        # version's prefix — normalisation, not escape. Pinned so a future
        # refactor cannot turn it into a real absolute path.
        assert hls_key("r", "v", "/absolute.ts") == "hls/r/v/absolute.ts"

    @pytest.mark.parametrize("bad", ["../x", "a/b", "", "x" * 65, "id;rm -rf"])
    def test_rejects_unsafe_ids(self, bad):
        with pytest.raises(ValueError):
            hls_key_prefix(bad, "v")
        with pytest.raises(ValueError):
            hls_key_prefix("r", bad)


class _FakeProxy:
    """Minimal stand-in for the storage-api proxy used by ObjectStore."""

    def __init__(self, tree: dict[str, list[dict]] | None = None):
        self.tree = tree or {}
        self.removed: list[list[str]] = []
        self.uploaded: list[str] = []
        self.max_in_flight = 0
        self._in_flight = 0

    async def list(self, path, opts):
        entries = self.tree.get(path, [])
        offset, limit = opts["offset"], opts["limit"]
        return entries[offset : offset + limit]

    async def remove(self, keys):
        self.removed.append(list(keys))

    async def upload(self, key, _path, _opts):
        self._in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self._in_flight)
        try:
            await asyncio.sleep(0.01)  # let siblings overlap
            self.uploaded.append(key)
        finally:
            self._in_flight -= 1


def _store_with(proxy: _FakeProxy) -> ObjectStore:
    store = ObjectStore("library")

    async def _proxy():
        return proxy

    store._proxy = _proxy  # type: ignore[method-assign]
    return store


def _obj(name: str) -> dict:
    return {"name": name, "id": "obj-id"}


def _folder(name: str) -> dict:
    # storage-api marks folder placeholders with a null id.
    return {"name": name, "id": None}


class TestListPrefix:
    @pytest.mark.asyncio
    async def test_walks_nested_folders(self):
        proxy = _FakeProxy(
            {
                "hls/r/v": [_obj("master.m3u8"), _folder("480p")],
                "hls/r/v/480p": [_obj("stream.m3u8"), _obj("segment_000.ts")],
            }
        )
        keys = await _store_with(proxy).list_prefix("hls/r/v")
        assert sorted(keys) == [
            "hls/r/v/480p/segment_000.ts",
            "hls/r/v/480p/stream.m3u8",
            "hls/r/v/master.m3u8",
        ]

    @pytest.mark.asyncio
    async def test_paginates_past_the_page_limit(self):
        # A 1080p tier can exceed one page; a single list call would silently
        # truncate and leave orphans behind on cleanup.
        many = [_obj(f"segment_{i:03d}.ts") for i in range(250)]
        proxy = _FakeProxy({"hls/r/v": many})
        keys = await _store_with(proxy).list_prefix("hls/r/v")
        assert len(keys) == 250

    @pytest.mark.asyncio
    async def test_empty_prefix_is_not_an_error(self):
        assert await _store_with(_FakeProxy()).list_prefix("hls/none/none") == []


class TestRemove:
    @pytest.mark.asyncio
    async def test_remove_prefix_deletes_everything_and_counts(self):
        proxy = _FakeProxy({"hls/r/v": [_obj("a.ts"), _obj("b.ts")]})
        store = _store_with(proxy)
        assert await store.remove_prefix("hls/r/v") == 2
        assert sorted(sum(proxy.removed, [])) == ["hls/r/v/a.ts", "hls/r/v/b.ts"]

    @pytest.mark.asyncio
    async def test_remove_many_chunks_large_batches(self):
        proxy = _FakeProxy()
        await _store_with(proxy).remove_many([f"k{i}" for i in range(250)])
        assert len(proxy.removed) == 3  # 100 + 100 + 50
        assert all(len(batch) <= 100 for batch in proxy.removed)

    @pytest.mark.asyncio
    async def test_remove_many_noop_on_empty(self):
        proxy = _FakeProxy()
        await _store_with(proxy).remove_many([])
        assert proxy.removed == []


class TestPutDir:
    @pytest.mark.asyncio
    async def test_uploads_tree_with_caller_supplied_keys(self, tmp_path: Path):
        (tmp_path / "480p").mkdir()
        (tmp_path / "master.m3u8").write_text("#EXTM3U")
        (tmp_path / "480p" / "segment_000.ts").write_bytes(b"\x00" * 16)

        proxy = _FakeProxy()
        count = await _store_with(proxy).put_dir(
            str(tmp_path), lambda rel: hls_key("r", "v", rel)
        )

        assert count == 2
        assert sorted(proxy.uploaded) == [
            "hls/r/v/480p/segment_000.ts",
            "hls/r/v/master.m3u8",
        ]

    @pytest.mark.asyncio
    async def test_uploads_concurrently_but_bounded(self, tmp_path: Path):
        for i in range(20):
            (tmp_path / f"segment_{i:03d}.ts").write_bytes(b"x")

        proxy = _FakeProxy()
        await _store_with(proxy).put_dir(
            str(tmp_path), lambda rel: hls_key("r", "v", rel), concurrency=4
        )

        # Serial would take ~20 * 10ms; unbounded would exhaust connections.
        assert proxy.max_in_flight > 1, "uploads ran serially"
        assert proxy.max_in_flight <= 4, "semaphore did not bound fan-out"

    @pytest.mark.asyncio
    async def test_empty_dir_is_not_an_error(self, tmp_path: Path):
        proxy = _FakeProxy()
        assert await _store_with(proxy).put_dir(str(tmp_path), lambda r: r) == 0
