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


UGC_VIDEO_OPTIONS = {
    "url": "https://x.douyinvod.com/v.mp4",
    "videoName": "Clip",
    "artistName": "Bob",
    "coverURL": "https://c/cover.jpg",
    "duration": 12000,
}


class _Api:
    def __init__(self, content=None):
        self._content = content
        self.resolved = []
        self.ugc_calls = []

    async def get_track_with_play_info(self, tid, q):
        self.resolved.append((tid, q))
        return TRACK, CHOSEN

    async def get_ugc_video(self, ugc_video_id):
        self.ugc_calls.append(ugc_video_id)
        return UGC_VIDEO_OPTIONS

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


def test_resolve_qishui_ugc_video_via_short_link():
    # Phase 6: ugc_video links now resolve to a video parsed_data dict (was a
    # "not supported in Phase 2" raise).
    api = _Api(content=SodaContent("ugc_video", "999"))
    pd = asyncio.run(
        resolve_qishui_metadata(
            url="https://qishui.douyin.com/s/iUGC/",
            user_id="u1",
            api=api,
        )
    )
    assert pd["media_type"] == "video"
    assert pd["platform_id"] == "999"
    assert api.ugc_calls == ["999"]


def test_resolve_qishui_rejects_playlist():
    api = _Api(content=SodaContent("playlist", "PL1"))
    with pytest.raises(SodaApiError):
        asyncio.run(
            resolve_qishui_metadata(
                url="https://qishui.douyin.com/s/iPL/",
                user_id="u1",
                api=api,
            )
        )
