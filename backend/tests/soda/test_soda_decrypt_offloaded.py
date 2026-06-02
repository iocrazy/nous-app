"""Regression: the heavy synchronous decrypt_audio must run OFF the event loop
(via asyncio.to_thread). Running it inline blocked the shared DBOS loop, which
starved concurrent soda downloads' asyncio.wait_for(getaddrinfo) → spurious
TimeoutError that looked like a DNS failure (2026-06-02 incident)."""

from __future__ import annotations

import threading

import pytest

from app.services.media.parsers.soda_music import soda_downloader


class _FakeResp:
    def raise_for_status(self):
        return None

    async def aiter_bytes(self):
        yield b"encrypted-mdat-bytes"


class _StreamCtx:
    async def __aenter__(self):
        return _FakeResp()

    async def __aexit__(self, *exc):
        return False


class _FakeClient:
    def stream(self, *args, **kwargs):
        return _StreamCtx()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


@pytest.mark.asyncio
async def test_decrypt_runs_in_worker_thread_not_event_loop(tmp_path, monkeypatch):
    captured: dict[str, object] = {}

    def _fake_decrypt(data: bytes, play_auth: str) -> bytes:
        captured["thread"] = threading.current_thread()
        captured["data"] = data
        return b"decrypted-output"

    monkeypatch.setattr(soda_downloader, "decrypt_audio", _fake_decrypt)

    dest = tmp_path / "track.m4a"
    n = await soda_downloader.download_and_decrypt(
        url="https://cdn.example/track",
        play_auth="pa",
        dest_path=str(dest),
        client_factory=lambda: _FakeClient(),
    )

    # decrypt must NOT have run on the main (event-loop) thread.
    assert captured["thread"] is not threading.main_thread()
    # sanity: it actually decrypted the streamed bytes and wrote the file.
    assert captured["data"] == b"encrypted-mdat-bytes"
    assert dest.read_bytes() == b"decrypted-output"
    assert n == len(b"decrypted-output")
