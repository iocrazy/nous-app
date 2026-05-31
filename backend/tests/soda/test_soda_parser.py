# backend/tests/soda/test_soda_parser.py
import asyncio

from app.services.media.parsers.soda_music.soda_parser import (
    SodaDownloadPlan,
    parse_track,
)


class _FakeApi:
    def __init__(self, track, chosen):
        self._track, self._chosen = track, chosen
        self.calls = []

    async def get_track_with_play_info(self, track_id, want_quality):
        self.calls.append((track_id, want_quality))
        return self._track, self._chosen


def test_parse_track_returns_parsed_data_and_plan():
    track = {
        "id": "7123",
        "name": "S",
        "duration": 200000,
        "artists": [{"name": "A"}],
        "album": {},
    }
    chosen = {
        "Quality": "lossless",
        "Format": "flac",
        "Bitrate": 729,
        "MainPlayUrl": "https://cdn/flac",
        "PlayAuth": "auth",
        "Duration": 200,
    }
    api = _FakeApi(track, chosen)

    parsed_data, plan = asyncio.run(
        parse_track(
            api,
            track_id="7123",
            want_quality="lossless",
            original_url="https://qishui.douyin.com/s/x/",
        )
    )

    assert parsed_data["platform_id"] == "7123"
    assert parsed_data["source_platform"] == "qishui"
    assert isinstance(plan, SodaDownloadPlan)
    assert plan.url == "https://cdn/flac"
    assert plan.play_auth == "auth"
    assert plan.ext == "flac"
    assert plan.track_id == "7123"
    assert api.calls == [("7123", "lossless")]
