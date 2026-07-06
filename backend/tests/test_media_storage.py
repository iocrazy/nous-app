"""Unit tests for the media_storage abstraction (Phase 1a foundation).

Pins the single-interpreter contract (resolve_media_source), content-addressed
key derivation, and the ObjectStore wrapper's response handling — all without a
live storage-api (SDK calls are mocked).
"""

from __future__ import annotations

import hashlib
from unittest.mock import AsyncMock, patch

import pytest

from app.services.library.media_storage import (
    CHAT_MEDIA_BUCKET,
    MediaLocation,
    ObjectStore,
    content_key,
    resolve_media_source,
    to_file_path,
)

# ── resolve_media_source ─────────────────────────────────────────────────────


def test_legacy_filesystem_path_resolves_local():
    loc = resolve_media_source("teams/42/chat/2026/07/05/abc/shot.png")
    assert loc.backend == "filesystem"
    assert loc.is_object_store is False
    assert loc.rel_path == "teams/42/chat/2026/07/05/abc/shot.png"
    assert loc.bucket is None and loc.key is None


def test_sb_path_resolves_object_store():
    loc = resolve_media_source("sb://chat-media/t42/ab/cd/deadbeef.png")
    assert loc.backend == "object_store"
    assert loc.is_object_store is True
    assert loc.bucket == "chat-media"
    assert loc.key == "t42/ab/cd/deadbeef.png"
    assert loc.rel_path is None


def test_sb_key_with_slashes_kept_whole():
    loc = resolve_media_source("sb://b/a/b/c/d.png")
    assert loc.bucket == "b"
    assert loc.key == "a/b/c/d.png"


@pytest.mark.parametrize("bad", ["sb://", "sb://only-bucket", "sb:///no-bucket"])
def test_malformed_sb_degrades_to_filesystem(bad: str):
    # A corrupt sb:// value must NOT crash a reader — it degrades to a
    # filesystem path (which 404s at the file layer) instead of 500ing.
    loc = resolve_media_source(bad)
    assert loc.backend == "filesystem"
    assert loc.rel_path == bad


# ── content_key ──────────────────────────────────────────────────────────────


def test_content_key_is_deterministic_and_addressed():
    data = b"the same bytes"
    sha, key = content_key(scope_id=42, data=data, mime="image/png", filename="a.png")
    expect_sha = hashlib.sha256(data).hexdigest()
    assert sha == expect_sha
    assert key == f"t42/{expect_sha[:2]}/{expect_sha[2:4]}/{expect_sha}.png"
    # same bytes → same key (dedup)
    sha2, key2 = content_key(
        scope_id=42, data=data, mime="image/png", filename="totally-different-name.png"
    )
    assert (sha2, key2) == (sha, key)


def test_content_key_different_scope_differs():
    data = b"x"
    _, k1 = content_key(scope_id=1, data=data, mime="image/png")
    _, k2 = content_key(scope_id=2, data=data, mime="image/png")
    assert k1 != k2 and k1.startswith("t1/") and k2.startswith("t2/")


def test_content_key_ext_from_mime_when_no_filename():
    _, key = content_key(scope_id=1, data=b"x", mime="image/jpeg")
    assert key.endswith(".jpg") or key.endswith(".jpeg")


def test_content_key_rejects_weird_filename_ext():
    # A filename with a non-alnum / overlong "ext" falls back to mime sniffing.
    _, key = content_key(
        scope_id=1, data=b"x", mime="image/png", filename="evil.pn g/../etc"
    )
    assert key.endswith(".png")


def test_to_file_path_roundtrips_through_resolver():
    fp = to_file_path("chat-media", "t1/ab/cd/hash.png")
    assert fp == "sb://chat-media/t1/ab/cd/hash.png"
    loc = resolve_media_source(fp)
    assert loc.bucket == "chat-media" and loc.key == "t1/ab/cd/hash.png"


# ── ObjectStore (SDK mocked) ─────────────────────────────────────────────────


def _mock_proxy(**methods) -> AsyncMock:
    proxy = AsyncMock()
    for name, val in methods.items():
        getattr(proxy, name).return_value = val
    return proxy


@pytest.mark.asyncio
async def test_signed_url_reads_signedURL_key():
    store = ObjectStore(CHAT_MEDIA_BUCKET)
    proxy = _mock_proxy(create_signed_url={"signedURL": "https://x/y?token=abc"})
    with patch.object(store, "_proxy", new=AsyncMock(return_value=proxy)):
        url = await store.signed_url("t1/ab/cd/h.png", ttl_seconds=120)
    assert url == "https://x/y?token=abc"
    proxy.create_signed_url.assert_awaited_once_with("t1/ab/cd/h.png", 120)


@pytest.mark.asyncio
async def test_signed_url_falls_back_to_signedUrl_key():
    store = ObjectStore(CHAT_MEDIA_BUCKET)
    proxy = _mock_proxy(create_signed_url={"signedUrl": "https://x/y"})
    with patch.object(store, "_proxy", new=AsyncMock(return_value=proxy)):
        url = await store.signed_url("k")
    assert url == "https://x/y"


@pytest.mark.asyncio
async def test_signed_url_missing_raises():
    store = ObjectStore(CHAT_MEDIA_BUCKET)
    proxy = _mock_proxy(create_signed_url={})
    with patch.object(store, "_proxy", new=AsyncMock(return_value=proxy)):
        with pytest.raises(RuntimeError, match="signed url missing"):
            await store.signed_url("k")


@pytest.mark.asyncio
async def test_put_bytes_passes_content_type_and_upsert():
    store = ObjectStore(CHAT_MEDIA_BUCKET)
    proxy = _mock_proxy(upload=None)
    with patch.object(store, "_proxy", new=AsyncMock(return_value=proxy)):
        await store.put_bytes("k", b"data", "image/png")
    args, _ = proxy.upload.call_args
    assert args[0] == "k" and args[1] == b"data"
    assert args[2]["content-type"] == "image/png"
    assert args[2]["upsert"] == "true"


@pytest.mark.asyncio
async def test_exists_true_when_download_succeeds():
    store = ObjectStore(CHAT_MEDIA_BUCKET)
    proxy = _mock_proxy(download=b"bytes")
    with patch.object(store, "_proxy", new=AsyncMock(return_value=proxy)):
        assert await store.exists("k") is True


@pytest.mark.asyncio
async def test_exists_false_when_download_raises():
    store = ObjectStore(CHAT_MEDIA_BUCKET)
    proxy = AsyncMock()
    proxy.download.side_effect = Exception("404")
    with patch.object(store, "_proxy", new=AsyncMock(return_value=proxy)):
        assert await store.exists("k") is False


def test_location_dataclass_is_frozen():
    loc = MediaLocation(backend="filesystem", rel_path="a")
    with pytest.raises(Exception):
        loc.backend = "object_store"  # type: ignore[misc]


@pytest.mark.asyncio
async def test_remove_calls_proxy_with_key_list():
    store = ObjectStore(CHAT_MEDIA_BUCKET)
    proxy = _mock_proxy(remove=[])
    with patch.object(store, "_proxy", new=AsyncMock(return_value=proxy)):
        await store.remove("t1/ab/cd/h.png")
    proxy.remove.assert_awaited_once_with(["t1/ab/cd/h.png"])


@pytest.mark.asyncio
async def test_put_file_uploads_from_path(tmp_path):
    f = tmp_path / "vid.mp4"
    f.write_bytes(b"MP4")
    store = ObjectStore(CHAT_MEDIA_BUCKET)
    proxy = _mock_proxy(upload=None)
    with patch.object(store, "_proxy", new=AsyncMock(return_value=proxy)):
        await store.put_file("t1/ab/cd/h.mp4", str(f), "video/mp4")
    args, _ = proxy.upload.call_args
    assert args[0] == "t1/ab/cd/h.mp4"
    assert str(args[1]) == str(f)  # a Path to the file, not bytes
    assert args[2]["content-type"] == "video/mp4"


def test_content_key_from_sha_matches_content_key():
    import hashlib as _h

    data = b"same bytes"
    sha, key = content_key(scope_id=3, data=data, mime="video/mp4")
    sha2 = _h.sha256(data).hexdigest()
    from app.services.library.media_storage import content_key_from_sha

    key2 = content_key_from_sha(scope_id=3, sha=sha2, mime="video/mp4")
    assert key == key2 and sha == sha2


@pytest.mark.asyncio
async def test_calls_are_time_capped():
    """A hung storage-api must not hang callers — 2026-07-06 incident: a
    deleted data dir made uploads hang until kong's 60s timeout. Every
    ObjectStore call is capped so the filesystem fallback kicks in fast."""
    import asyncio

    async def never_returns(*a, **k):
        await asyncio.sleep(3600)

    store = ObjectStore(CHAT_MEDIA_BUCKET)
    proxy = AsyncMock()
    proxy.upload.side_effect = never_returns
    import app.services.library.media_storage as ms

    with (
        patch.object(store, "_proxy", new=AsyncMock(return_value=proxy)),
        patch.object(ms, "_STORAGE_CALL_TIMEOUT_S", 0.05),
    ):
        with pytest.raises(asyncio.TimeoutError):
            await store.put_bytes("k", b"x", "image/png")
