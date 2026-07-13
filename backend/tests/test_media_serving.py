"""Shared media reader tests — fs / stream-proxy / 302 branches.

`serve_stored_file` is the ONE read endpoint helper for unified storage
(spec 2026-07-12): filesystem rows keep FileResponse, sb:// rows are either
302-redirected to a signed URL (when STORAGE_SIGNED_URL_PUBLIC_BASE is set)
or stream-proxied with Range passthrough.
"""

import pytest
from fastapi import HTTPException
from starlette.requests import Request

import app.services.library.media_storage as ms
from app.core.config import settings
from app.services.library.media_serving import serve_stored_file


def _request(headers: dict | None = None) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request({"type": "http", "method": "GET", "path": "/", "headers": raw})


async def _drain(resp) -> bytes:
    chunks = []
    async for chunk in resp.body_iterator:
        chunks.append(chunk if isinstance(chunk, bytes) else chunk.encode())
    return b"".join(chunks)


@pytest.mark.asyncio
async def test_serve_fs_file(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DOWNLOAD_PATH", str(tmp_path))
    rel = "teams/1/uploads/9/v1/a.txt"
    f = tmp_path / rel
    f.parent.mkdir(parents=True)
    f.write_text("hello")

    resp = await serve_stored_file(rel, mime="text/plain", request=_request())
    assert resp.status_code == 200
    assert resp.path == str(f)  # FileResponse pointing at the real file


@pytest.mark.asyncio
async def test_serve_fs_missing_404(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DOWNLOAD_PATH", str(tmp_path))
    with pytest.raises(HTTPException) as exc:
        await serve_stored_file(
            "teams/1/uploads/9/v1/gone.txt", mime="text/plain", request=_request()
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_serve_sb_stream_proxy_range(monkeypatch):
    monkeypatch.setattr(settings, "STORAGE_SIGNED_URL_PUBLIC_BASE", "")
    blob = bytes(range(10))

    async def fake_get_size(self, key):
        return len(blob)

    async def fake_get_stream(self, key, *, start=None, end=None, **kw):
        stop = len(blob) - 1 if end is None else end
        yield blob[(start or 0) : stop + 1]

    monkeypatch.setattr(ms.ObjectStore, "get_size", fake_get_size)
    monkeypatch.setattr(ms.ObjectStore, "get_stream", fake_get_stream)

    resp = await serve_stored_file(
        "sb://library/t1/ab/cd/abc.png",
        mime="image/png",
        request=_request({"Range": "bytes=0-3"}),
    )
    assert resp.status_code == 206
    assert resp.headers["content-range"] == f"bytes 0-3/{len(blob)}"
    assert resp.headers["content-length"] == "4"
    assert await _drain(resp) == blob[0:4]


@pytest.mark.asyncio
async def test_serve_sb_redirect_when_public_base(monkeypatch):
    monkeypatch.setattr(
        settings, "STORAGE_SIGNED_URL_PUBLIC_BASE", "https://pub.example.com:88"
    )

    async def fake_signed_url(self, key, *, ttl_seconds=300):
        return f"http://lan-supabase:8000/storage/v1/object/sign/library/{key}?token=x"

    monkeypatch.setattr(ms.ObjectStore, "signed_url", fake_signed_url)

    resp = await serve_stored_file(
        "sb://library/t1/ab/cd/abc.png", mime="image/png", request=_request()
    )
    assert resp.status_code == 302
    loc = resp.headers["location"]
    assert loc.startswith("https://pub.example.com:88/")
    assert "token=x" in loc
