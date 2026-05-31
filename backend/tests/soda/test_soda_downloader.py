# backend/tests/soda/test_soda_downloader.py
import asyncio

import pytest

from app.services.media.parsers.soda_music.soda_downloader import (
    download_and_decrypt,
)


class _FakeResp:
    def __init__(self, content):
        self.content = content

    def raise_for_status(self):
        pass


class _FakeClient:
    def __init__(self, content):
        self._content = content

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, **kw):
        return _FakeResp(self._content)


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


def test_download_and_decrypt_raises_on_empty_url(tmp_path):
    with pytest.raises(ValueError):
        asyncio.run(
            download_and_decrypt(url="", play_auth="x", dest_path=str(tmp_path / "a"))
        )
