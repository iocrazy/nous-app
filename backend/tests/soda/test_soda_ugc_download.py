"""Tests for the Soda UGC video download workflow's pure helpers + streamer.

Mirrors the coverage depth of ``test_soda_download_workflow.py`` — the
``@DBOS.workflow`` body is not unit-tested (DBOS context needed); only the
pure helpers and the (injectable) streaming download are asserted.
"""

from __future__ import annotations

import asyncio

import pytest

from app.workflows.soda_ugc_download import (
    build_ugc_resource_fields,
    build_video_dest,
)


def test_build_video_dest_uses_media_id():
    full, rel = build_video_dest(media_id="999", base_dir="/tmp/dl")
    assert rel == "global/resources/web/qishui/999/video.mp4"
    assert str(full).endswith("global/resources/web/qishui/999/video.mp4")
    assert str(full).startswith("/tmp/dl")


def test_build_ugc_resource_fields_are_valid_resources_columns():
    fields = build_ugc_resource_fields(
        file_path="global/x/video.mp4", size_bytes=12345, title="Clip"
    )
    assert fields["file_type"] == "video"
    assert fields["mime_type"] == "video/mp4"
    assert fields["filename"] == "Clip.mp4"
    assert fields["file_path"] == "global/x/video.mp4"
    assert fields["file_size_bytes"] == 12345


def test_build_ugc_resource_fields_omit_nonexistent_columns():
    # Regression mirror of the audio path: `resources` has NO
    # music_download_status / video_download_status column; the completion
    # fields must only contain real resources columns and must not carry the
    # create-only keys (creator_id/media_id/source_type).
    fields = build_ugc_resource_fields(file_path="p", size_bytes=1, title="t")
    assert "video_download_status" not in fields
    assert "music_download_status" not in fields
    assert "scope_type" not in fields
    assert "creator_id" not in fields
    assert "media_id" not in fields


def test_already_downloaded_true(tmp_path):
    from app.workflows.soda_ugc_download import already_downloaded

    f = tmp_path / "v.mp4"
    f.write_bytes(b"x")
    assert already_downloaded({"download_path": "v.mp4"}, str(tmp_path)) is True


def test_already_downloaded_false_no_path():
    from app.workflows.soda_ugc_download import already_downloaded

    assert already_downloaded({"download_path": None}, "/tmp") is False
    assert already_downloaded({}, "/tmp") is False


def test_already_downloaded_false_missing_file(tmp_path):
    from app.workflows.soda_ugc_download import already_downloaded

    assert already_downloaded({"download_path": "nope.mp4"}, str(tmp_path)) is False


# --- streaming download ------------------------------------------------------


class _FakeStreamResp:
    def __init__(self, chunks):
        self._chunks = chunks

    def raise_for_status(self):
        pass

    async def aiter_bytes(self):
        for c in self._chunks:
            yield c


class _StreamCtx:
    def __init__(self, resp, recorder):
        self._resp = resp
        self._recorder = recorder

    async def __aenter__(self):
        return self._resp

    async def __aexit__(self, *a):
        return False


class _FakeStreamClient:
    def __init__(self, chunks, recorder):
        self._chunks = chunks
        self._recorder = recorder

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def stream(self, method, url, **kw):
        self._recorder["method"] = method
        self._recorder["url"] = url
        self._recorder["headers"] = kw.get("headers")
        return _StreamCtx(_FakeStreamResp(self._chunks), self._recorder)


def test_download_video_file_streams_to_disk(tmp_path):
    from app.workflows.soda_ugc_download import download_video_file

    rec: dict = {}
    chunks = [b"abc", b"defgh", b"i"]
    dest = tmp_path / "out" / "video.mp4"
    size = asyncio.run(
        download_video_file(
            url="https://x.douyinvod.com/v.mp4",
            dest_path=str(dest),
            cookie="ck=1",
            client_factory=lambda: _FakeStreamClient(chunks, rec),
        )
    )

    assert size == 9
    assert dest.exists()
    assert dest.read_bytes() == b"abcdefghi"
    assert rec["method"] == "GET"
    assert rec["url"] == "https://x.douyinvod.com/v.mp4"
    # uses the share-page (browser-ish) headers carrying the cookie
    assert rec["headers"]["Cookie"] == "ck=1"


def test_download_video_file_raises_on_http_error(tmp_path):
    import httpx

    from app.workflows.soda_ugc_download import download_video_file

    class _BadResp(_FakeStreamResp):
        def raise_for_status(self):
            raise httpx.HTTPStatusError("boom", request=None, response=None)

    class _BadClient(_FakeStreamClient):
        def stream(self, method, url, **kw):
            return _StreamCtx(_BadResp([]), {})

    with pytest.raises(httpx.HTTPError):
        asyncio.run(
            download_video_file(
                url="https://x/v.mp4",
                dest_path=str(tmp_path / "v.mp4"),
                cookie="",
                client_factory=lambda: _BadClient([], {}),
            )
        )
