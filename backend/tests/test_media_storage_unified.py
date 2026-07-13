"""Unified-storage foundation tests — pure logic + mocked ObjectStore."""

import pytest

from app.services.library.media_storage import (
    LIBRARY_BUCKET,
    StoredObject,
    library_store,
    materialize,
    resolve_media_source,
    sha256_file,
    store_local_file,
)


def test_library_store_targets_library_bucket():
    assert LIBRARY_BUCKET == "library"
    assert library_store().bucket == "library"


def test_sha256_file_matches_hashlib(tmp_path):
    import hashlib

    p = tmp_path / "a.bin"
    p.write_bytes(b"hello unified storage")
    assert sha256_file(str(p)) == hashlib.sha256(b"hello unified storage").hexdigest()


@pytest.mark.asyncio
async def test_store_local_file_dedup_skips_put(tmp_path, monkeypatch):
    p = tmp_path / "v.mp4"
    p.write_bytes(b"x" * 32)

    class FakeStore:
        bucket = "library"
        puts: list = []

        async def exists(self, key):
            return True  # already present → dedup

        async def put_file(self, key, path, mime):
            self.puts.append(key)

    fake = FakeStore()
    stored = await store_local_file(
        scope_id=42, source_path=str(p), mime="video/mp4", store=fake
    )
    assert isinstance(stored, StoredObject)
    assert stored.file_path.startswith("sb://library/t42/")
    assert stored.size_bytes == 32
    assert fake.puts == []  # dedup: no PUT
    # sb:// path round-trips through the resolver
    loc = resolve_media_source(stored.file_path)
    assert loc.is_object_store and loc.bucket == "library"


@pytest.mark.asyncio
async def test_store_local_file_puts_when_missing(tmp_path):
    p = tmp_path / "img.png"
    p.write_bytes(b"png-bytes")

    class FakeStore:
        bucket = "library"

        def __init__(self):
            self.puts = []

        async def exists(self, key):
            return False

        async def put_file(self, key, path, mime):
            self.puts.append((key, path, mime))

    fake = FakeStore()
    stored = await store_local_file(
        scope_id=7, source_path=str(p), mime="image/png", store=fake
    )
    assert len(fake.puts) == 1
    assert fake.puts[0][0].endswith(".png")
    assert stored.sha256 in stored.file_path


@pytest.mark.asyncio
async def test_materialize_filesystem_yields_download_path(tmp_path, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "DOWNLOAD_PATH", str(tmp_path))
    rel = "teams/1/uploads/9/v1/a.txt"
    f = tmp_path / rel
    f.parent.mkdir(parents=True)
    f.write_text("hi")
    async with materialize(rel) as local:
        assert local == f
    assert f.exists()  # fs path is NOT cleaned up


@pytest.mark.asyncio
async def test_materialize_object_store_pulls_temp_and_cleans(monkeypatch):
    import app.services.library.media_storage as ms

    async def fake_stream(self, key, **kw):
        yield b"chunk1"
        yield b"chunk2"

    monkeypatch.setattr(ms.ObjectStore, "get_stream", fake_stream)
    seen = {}
    async with materialize("sb://library/t1/ab/cd/abc.png") as local:
        seen["path"] = local
        assert local.read_bytes() == b"chunk1chunk2"
        assert local.suffix == ".png"
    assert not seen["path"].exists()  # temp cleaned up
