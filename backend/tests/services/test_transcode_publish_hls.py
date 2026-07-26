"""Publishing HLS output to the object store.

The fast path marks a version ``completed`` the instant phase 1 publishes, so
these tests exist mainly to pin ONE property: master.m3u8 must become visible
only after every object it references. Get that wrong and a player fetches a
playlist whose segments 404.

Also covered: the feature flag keeps the filesystem shape byte-identical, and
the local tree survives publishing (phase 2 re-encodes tiers into it).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from app.services.media.transcode.transcode_service import TranscodeService


class _RecordingStore:
    """Captures publish order without touching a real store."""

    bucket = "library"

    def __init__(self):
        self.events: list[tuple[str, str]] = []  # (kind, key-or-rel)
        self.removed_prefixes: list[str] = []

    async def put_dir(self, local_dir, key_for, **_kw):
        root = Path(local_dir)
        files = sorted(p for p in root.rglob("*") if p.is_file())
        for p in files:
            self.events.append(("dir", key_for(p.relative_to(root).as_posix())))
        return len(files)

    async def put_file(self, key, _path, _mime, **_kw):
        self.events.append(("file", key))

    async def remove_prefix(self, prefix):
        self.removed_prefixes.append(prefix)
        return 0

    @property
    def keys(self) -> list[str]:
        return [k for _, k in self.events]


def _make_tree(root: Path) -> None:
    """A realistic phase-2 tree: master + two tiers of segments."""
    (root / "480p").mkdir(parents=True)
    (root / "source").mkdir(parents=True)
    (root / "master.m3u8").write_text("#EXTM3U\n480p/stream.m3u8\n")
    (root / "480p" / "stream.m3u8").write_text("#EXTM3U\nsegment_000.ts\n")
    (root / "480p" / "segment_000.ts").write_bytes(b"\x00" * 8)
    (root / "source" / "stream.m3u8").write_text("#EXTM3U\nsegment_000.ts\n")
    (root / "source" / "segment_000.ts").write_bytes(b"\x00" * 8)


@pytest.fixture
def svc():
    return TranscodeService()


class TestFilesystemMode:
    @pytest.mark.asyncio
    async def test_returns_download_path_relative_and_uploads_nothing(
        self, svc, tmp_path
    ):
        hls_dir = tmp_path / "res" / "hls"
        _make_tree(hls_dir)
        store = _RecordingStore()

        with (
            patch(
                "app.services.media.transcode.transcode_service.settings.HLS_OBJECT_STORE",
                False,
            ),
            patch(
                "app.services.media.transcode.transcode_service.library_store",
                return_value=store,
            ),
        ):
            out = await svc._publish_hls(hls_dir, tmp_path, "r1", "v1")

        assert out == "res/hls/master.m3u8"
        assert store.events == [], "filesystem mode must not touch the store"


class TestObjectStoreMode:
    @pytest.mark.asyncio
    async def test_master_is_published_last(self, svc, tmp_path):
        """The property that makes publishing atomic for a reader."""
        hls_dir = tmp_path / "res" / "hls"
        _make_tree(hls_dir)
        store = _RecordingStore()

        with (
            patch(
                "app.services.media.transcode.transcode_service.settings.HLS_OBJECT_STORE",
                True,
            ),
            patch(
                "app.services.media.transcode.transcode_service.library_store",
                return_value=store,
            ),
        ):
            await svc._publish_hls(hls_dir, tmp_path, "r1", "v1")

        master_key = "hls/r1/v1/master.m3u8"
        assert master_key in store.keys
        assert store.keys[-1] == master_key, (
            "master.m3u8 uploaded before its segments — a player reloading "
            "mid-publish would fetch objects that do not exist yet"
        )
        # And it went up on its own, not swept into the batch.
        assert ("file", master_key) in store.events

    @pytest.mark.asyncio
    async def test_returns_sb_path_and_preserves_relative_layout(self, svc, tmp_path):
        hls_dir = tmp_path / "res" / "hls"
        _make_tree(hls_dir)
        store = _RecordingStore()

        with (
            patch(
                "app.services.media.transcode.transcode_service.settings.HLS_OBJECT_STORE",
                True,
            ),
            patch(
                "app.services.media.transcode.transcode_service.library_store",
                return_value=store,
            ),
        ):
            out = await svc._publish_hls(hls_dir, tmp_path, "r1", "v1")

        assert out == "sb://library/hls/r1/v1/master.m3u8"
        # m3u8 files reference siblings by bare relative path — the keys must
        # mirror the tree or those references break.
        assert "hls/r1/v1/480p/segment_000.ts" in store.keys
        assert "hls/r1/v1/480p/stream.m3u8" in store.keys
        assert "hls/r1/v1/source/segment_000.ts" in store.keys

    @pytest.mark.asyncio
    async def test_local_tree_is_intact_afterwards(self, svc, tmp_path):
        """Phase 2 re-encodes tiers into the same dir and republishes."""
        hls_dir = tmp_path / "res" / "hls"
        _make_tree(hls_dir)
        store = _RecordingStore()

        with (
            patch(
                "app.services.media.transcode.transcode_service.settings.HLS_OBJECT_STORE",
                True,
            ),
            patch(
                "app.services.media.transcode.transcode_service.library_store",
                return_value=store,
            ),
        ):
            await svc._publish_hls(hls_dir, tmp_path, "r1", "v1")

        assert (hls_dir / "master.m3u8").exists(), "master not restored locally"
        assert (hls_dir / "480p" / "segment_000.ts").exists()
        # No temp leftovers beside the tree.
        assert not list(hls_dir.parent.glob(".*master.m3u8"))

    @pytest.mark.asyncio
    async def test_publishing_twice_is_stable(self, svc, tmp_path):
        """Phase 1 then phase 2 — the second pass must still end with master."""
        hls_dir = tmp_path / "res" / "hls"
        _make_tree(hls_dir)
        store = _RecordingStore()

        with (
            patch(
                "app.services.media.transcode.transcode_service.settings.HLS_OBJECT_STORE",
                True,
            ),
            patch(
                "app.services.media.transcode.transcode_service.library_store",
                return_value=store,
            ),
        ):
            await svc._publish_hls(hls_dir, tmp_path, "r1", "v1")
            store.events.clear()
            await svc._publish_hls(hls_dir, tmp_path, "r1", "v1")

        assert store.keys[-1] == "hls/r1/v1/master.m3u8"


class TestClearPublished:
    @pytest.mark.asyncio
    async def test_clears_prefix_when_enabled(self, svc):
        store = _RecordingStore()
        with (
            patch(
                "app.services.media.transcode.transcode_service.settings.HLS_OBJECT_STORE",
                True,
            ),
            patch(
                "app.services.media.transcode.transcode_service.library_store",
                return_value=store,
            ),
        ):
            await svc._clear_published_hls("r1", "v1")
        assert store.removed_prefixes == ["hls/r1/v1"]

    @pytest.mark.asyncio
    async def test_noop_when_disabled(self, svc):
        store = _RecordingStore()
        with (
            patch(
                "app.services.media.transcode.transcode_service.settings.HLS_OBJECT_STORE",
                False,
            ),
            patch(
                "app.services.media.transcode.transcode_service.library_store",
                return_value=store,
            ),
        ):
            await svc._clear_published_hls("r1", "v1")
        assert store.removed_prefixes == []

    @pytest.mark.asyncio
    async def test_cleanup_failure_never_fails_the_transcode(self, svc):
        """Worst case is leaked objects — losing the encode would be worse."""

        class _Broken(_RecordingStore):
            async def remove_prefix(self, prefix):
                raise RuntimeError("store down")

        with (
            patch(
                "app.services.media.transcode.transcode_service.settings.HLS_OBJECT_STORE",
                True,
            ),
            patch(
                "app.services.media.transcode.transcode_service.library_store",
                return_value=_Broken(),
            ),
        ):
            await svc._clear_published_hls("r1", "v1")  # must not raise
