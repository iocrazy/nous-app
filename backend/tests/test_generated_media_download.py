import pytest

from app.services.library import generated_media_service as gm


def test_media_kind_from_mime():
    assert gm.media_kind_from_mime("image/png") == "image"
    assert gm.media_kind_from_mime("video/mp4") == "video"
    assert gm.media_kind_from_mime("application/octet-stream") == "image"  # default


def test_ext_for():
    assert gm.ext_for("image/png", "image") == ".png"
    assert gm.ext_for("video/mp4", "video") == ".mp4"
    assert gm.ext_for("", "image") == ".png"


@pytest.mark.asyncio
async def test_download_to_writes_atomically(tmp_path, monkeypatch):
    import app.services.library.generated_media_service as gm

    class _Resp:
        def raise_for_status(self): ...

        async def aiter_bytes(self):
            yield b"hello "
            yield b"world"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def stream(self, *a, **k):
            return _Resp()

    monkeypatch.setattr(gm.httpx, "AsyncClient", lambda *a, **k: _Client())
    dest = tmp_path / "x" / "media.png"
    n = await gm._download_to(str(dest), "http://example/x.png")
    assert n == 11
    assert dest.read_bytes() == b"hello world"
    assert not (tmp_path / "x" / "media.png.part").exists()
