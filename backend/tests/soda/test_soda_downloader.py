# backend/tests/soda/test_soda_downloader.py
import asyncio
import contextlib

import pytest

from app.services.media.parsers.soda_music.soda_downloader import (
    download_and_decrypt,
)


class _FakeStreamResp:
    """Mimics an httpx streaming response: raise_for_status + aiter_bytes."""

    def __init__(self, content: bytes, *, chunk: int = 8):
        self._content = content
        self._chunk = chunk

    def raise_for_status(self):
        pass

    async def aiter_bytes(self):
        for i in range(0, len(self._content), self._chunk):
            yield self._content[i : i + self._chunk]


class _FakeClient:
    def __init__(self, content: bytes):
        self._content = content

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def stream(self, method, url, **kw):
        content = self._content

        @contextlib.asynccontextmanager
        async def _cm():
            yield _FakeStreamResp(content)

        return _cm()


def test_download_and_decrypt_writes_decrypted_file(tmp_path):
    from tests.soda.test_soda_decrypt import _build_mp4, make_play_auth

    hex_key = "00112233445566778899aabbccddeeff"
    play_auth = make_play_auth(hex_key)
    samples = [b"HELLOWORLD012345"]  # 16 bytes
    ivs = [b"\x00" * 8]
    mp4 = _build_mp4(bytes.fromhex(hex_key), samples, ivs)

    dest = tmp_path / "audio.flac"
    size = asyncio.run(
        download_and_decrypt(
            url="https://cdn/enc",
            play_auth=play_auth,
            dest_path=str(dest),
            client_factory=lambda: _FakeClient(mp4),
        )
    )

    assert dest.exists()
    assert size > 0
    assert b"HELLOWORLD012345" in dest.read_bytes()
    # Atomic write leaves no .part behind on success.
    assert not (tmp_path / "audio.flac.part").exists()


def test_download_and_decrypt_raises_on_empty_url(tmp_path):
    with pytest.raises(ValueError):
        asyncio.run(
            download_and_decrypt(url="", play_auth="x", dest_path=str(tmp_path / "a"))
        )


def test_download_and_decrypt_caps_oversized_stream(tmp_path, monkeypatch):
    """A stream exceeding the cap raises and leaves no file (no .part, no dest)."""
    import app.services.media.parsers.soda_music.soda_downloader as dl
    from app.boundary import MaxBytesExceededError

    # Shrink the cap so we don't have to allocate 512 MiB in the test.
    monkeypatch.setattr(dl, "MAX_SODA_AUDIO_BYTES", 16)
    dest = tmp_path / "audio.flac"
    with pytest.raises(MaxBytesExceededError):
        asyncio.run(
            download_and_decrypt(
                url="https://cdn/enc",
                play_auth="x",
                dest_path=str(dest),
                client_factory=lambda: _FakeClient(b"\x00" * 64),
            )
        )
    assert not dest.exists()
    assert not (tmp_path / "audio.flac.part").exists()


def test_download_and_decrypt_cleans_part_on_decrypt_failure(tmp_path, monkeypatch):
    """If decrypt fails, no truncated file/.part is left for the skip-guard."""
    import app.services.media.parsers.soda_music.soda_downloader as dl

    def _boom(_enc, _auth):
        raise ValueError("bad key")

    monkeypatch.setattr(dl, "decrypt_audio", _boom)
    dest = tmp_path / "audio.flac"
    with pytest.raises(ValueError):
        asyncio.run(
            download_and_decrypt(
                url="https://cdn/enc",
                play_auth="x",
                dest_path=str(dest),
                client_factory=lambda: _FakeClient(b"\x00" * 32),
            )
        )
    assert not dest.exists()
    assert not (tmp_path / "audio.flac.part").exists()
