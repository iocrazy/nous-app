import asyncio

import pytest

from app.services.media.parsers.soda_music.parse_entry import resolve_qishui_metadata
from app.services.media.parsers.soda_music.soda_api import SodaApiError, SodaContent

TRACK = {
    "id": "7123",
    "name": "S",
    "duration": 200000,
    "artists": [{"name": "A"}],
    "album": {},
}
CHOSEN = {
    "Quality": "lossless",
    "Format": "flac",
    "MainPlayUrl": "u",
    "PlayAuth": "a",
    "Duration": 200,
    "Bitrate": 729,
}


class _Api:
    def __init__(self, content=None):
        self._content = content
        self.resolved = []

    async def get_track_with_play_info(self, tid, q):
        self.resolved.append((tid, q))
        return TRACK, CHOSEN

    async def resolve_short_link(self, url):
        return self._content


def test_resolve_qishui_with_explicit_track_id():
    api = _Api()
    pd = asyncio.run(
        resolve_qishui_metadata(
            url="https://music.douyin.com/qishui/share/track?track_id=7123",
            user_id="u1",
            want_quality="lossless",
            api=api,
        )
    )
    assert pd["platform_id"] == "7123"
    assert pd["source_platform"] == "qishui"
    assert pd["media_type"] == "audio"
    assert api.resolved == [("7123", "lossless")]


def test_resolve_qishui_via_short_link_redirect():
    api = _Api(content=SodaContent("track", "7123"))
    pd = asyncio.run(
        resolve_qishui_metadata(
            url="https://qishui.douyin.com/s/iXYZ/",
            user_id="u1",
            want_quality="lossless",
            api=api,
        )
    )
    assert pd["platform_id"] == "7123"


def test_resolve_qishui_rejects_ugc_video():
    api = _Api(content=SodaContent("ugc_video", "999"))
    with pytest.raises(SodaApiError):
        asyncio.run(
            resolve_qishui_metadata(
                url="https://qishui.douyin.com/s/iUGC/",
                user_id="u1",
                want_quality="lossless",
                api=api,
            )
        )
