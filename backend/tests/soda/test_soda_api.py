"""Tests for soda_music.soda_api — dynamic device params/headers + endpoints.

Network methods are exercised against an injected fake httpx client (no real
requests). The signable surface (device params, headers, URL/body construction)
is asserted directly, which is what keeps the LunaPC signature valid.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.services.media.parsers.soda_music.soda_api import (
    BASE_URL,
    SodaApiClient,
    SodaApiError,
    SodaContent,
    SodaPreviewError,
    build_pc_headers,
    build_pc_params,
    classify_landing_url,
    cover_url,
)

# --- pure: device params -----------------------------------------------------


def test_build_pc_params_uses_dynamic_device_ids():
    params = build_pc_params(now_ms=1700000000000)
    assert params["device_id"] == "1700000000000"
    assert params["fp"] == "1700000000000"
    assert params["iid"] == "1700000000001"  # now + 1


def test_build_pc_params_has_fixed_luna_values():
    params = build_pc_params(now_ms=1)
    assert params["aid"] == "386088"
    assert params["app_name"] == "luna_pc"
    assert params["version_name"] == "3.3.0"
    assert params["version_code"] == "30030000"
    assert params["device_platform"] == "windows"


def test_build_pc_params_changes_each_call_without_fixed_now():
    a = build_pc_params(now_ms=10)
    b = build_pc_params(now_ms=20)
    assert a["device_id"] != b["device_id"]


# --- pure: headers -----------------------------------------------------------


def test_build_pc_headers_carry_luna_headers_and_cookie():
    h = build_pc_headers("sessionid=abc")
    assert h["User-Agent"] == "LunaPC/3.3.0(359450208)"
    assert h["Cookie"] == "sessionid=abc"
    assert h["x-luna-background-type"] == "foreground"
    assert h["x-luna-is-background-req"] == "0"
    assert h["x-luna-is-local-user"] == "1"
    assert "Content-Type" not in h  # GET has no body


def test_build_pc_headers_post_adds_content_type():
    h = build_pc_headers("c", post=True)
    assert h["Content-Type"] == "application/json; charset=utf-8"


def test_build_pc_headers_does_not_hardcode_legacy_signatures():
    h = build_pc_headers("c", post=True)
    assert "X-Helios" not in h
    assert "X-Medusa" not in h
    assert "a_bogus" not in h


# --- pure: cover_url ---------------------------------------------------------


def test_cover_url_concatenates_url_uri_and_size():
    album_cover = {"urls": ["https://p.cdn/"], "uri": "img123"}
    assert cover_url(album_cover) == "https://p.cdn/img123~c5_375x375.jpg"


def test_cover_url_custom_size():
    album_cover = {"urls": ["https://p.cdn/"], "uri": "img123"}
    assert cover_url(album_cover, size="~c5_720x720.jpg").endswith("~c5_720x720.jpg")


# --- pure: short-link classification ----------------------------------------


def test_classify_landing_url_track():
    c = classify_landing_url(
        "https://music.douyin.com/qishui/share/track?track_id=7123"
    )
    assert c == SodaContent(kind="track", content_id="7123")


def test_classify_landing_url_ugc_video():
    c = classify_landing_url(
        "https://music.douyin.com/qishui/share/ugc_video?ugc_video_id=99"
    )
    assert c == SodaContent(kind="ugc_video", content_id="99")


def test_classify_landing_url_playlist():
    c = classify_landing_url(
        "https://music.douyin.com/qishui/share/playlist?playlist_id=PL123"
    )
    assert c == SodaContent(kind="playlist", content_id="PL123")


def test_classify_landing_url_unknown_returns_none():
    assert classify_landing_url("https://music.douyin.com/qishui/home") is None


# --- network (injected fake client) -----------------------------------------


class _FakeResponse:
    def __init__(self, payload: dict, status: int = 200):
        self._payload = payload
        self.status_code = status

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("err", request=None, response=None)  # type: ignore[arg-type]


class _FakeClient:
    """Records the last request and returns a queued response per method."""

    def __init__(self, post_response=None, get_responses=None):
        self._post_response = post_response
        self._get_responses = list(get_responses or [])
        self.calls: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, **kwargs):
        self.calls.append({"method": "POST", "url": url, **kwargs})
        return self._post_response

    async def get(self, url, **kwargs):
        self.calls.append({"method": "GET", "url": url, **kwargs})
        return self._get_responses.pop(0)


def _client_with(fake: _FakeClient) -> SodaApiClient:
    return SodaApiClient(cookie="sessionid=xyz", client_factory=lambda: fake)


def test_get_track_v2_posts_correct_request_and_parses():
    fake = _FakeClient(
        post_response=_FakeResponse(
            {
                "track": {"id": "7123", "name": "Song"},
                "track_player": {
                    "url_player_info": "https://api.qishui.com/player?x=1"
                },
            }
        )
    )
    client = _client_with(fake)

    import asyncio

    result = asyncio.run(client.get_track_v2("7123"))

    call = fake.calls[0]
    assert call["url"] == f"{BASE_URL}/luna/pc/track_v2"
    assert call["params"]["device_id"]  # dynamic params attached
    body = json.loads(call["content"])
    assert body["track_id"] == "7123"
    assert body["media_type"] == "track"
    assert call["headers"]["Content-Type"].startswith("application/json")
    assert result["track"]["name"] == "Song"
    assert result["url_player_info"] == "https://api.qishui.com/player?x=1"


def test_get_track_v2_injects_root_lyric_into_track():
    # track_v2 returns `lyric` as a SIBLING of `track` at the response root.
    # get_track_v2 must inject it into the track dict so format_track sees it.
    fake = _FakeClient(
        post_response=_FakeResponse(
            {
                "track": {"id": "7123", "name": "Song"},
                "lyric": {"content": "[1000,1000]<0,1000,0>Hi"},
                "track_player": {
                    "url_player_info": "https://api.qishui.com/player?x=1"
                },
            }
        )
    )
    client = _client_with(fake)

    import asyncio

    result = asyncio.run(client.get_track_v2("7123"))

    assert result["track"]["lyric"]["content"] == "[1000,1000]<0,1000,0>Hi"


def test_get_play_info_returns_play_info_list():
    fake = _FakeClient(
        get_responses=[
            _FakeResponse(
                {
                    "Result": {
                        "Data": {
                            "PlayInfoList": [{"Quality": "lossless", "Bitrate": 729}]
                        }
                    }
                }
            )
        ]
    )
    client = _client_with(fake)

    import asyncio

    infos = asyncio.run(client.get_play_info("https://api.qishui.com/player?x=1"))

    assert infos == [{"Quality": "lossless", "Bitrate": 729}]


def test_get_track_with_play_info_selects_quality():
    track_resp = _FakeResponse(
        {
            "track": {"id": "7", "name": "S", "duration": 200000},
            "track_player": {"url_player_info": "https://api.qishui.com/p"},
        }
    )
    play_resp = _FakeResponse(
        {
            "Result": {
                "Data": {
                    "PlayInfoList": [
                        {
                            "Quality": "medium",
                            "Bitrate": 68,
                            "Duration": 200,
                            "MainPlayUrl": "u1",
                            "PlayAuth": "a1",
                        },
                        {
                            "Quality": "lossless",
                            "Bitrate": 729,
                            "Duration": 200,
                            "MainPlayUrl": "u2",
                            "PlayAuth": "a2",
                        },
                    ]
                }
            }
        }
    )
    fake = _FakeClient(post_response=track_resp, get_responses=[play_resp])
    client = _client_with(fake)

    import asyncio

    track, chosen = asyncio.run(
        client.get_track_with_play_info("7", want_quality="lossless")
    )

    assert track["name"] == "S"
    assert chosen["MainPlayUrl"] == "u2"


def test_get_track_with_play_info_raises_on_preview_only():
    track_resp = _FakeResponse(
        {
            "track": {"id": "7", "name": "S", "duration": 200000},  # 200s track
            "track_player": {"url_player_info": "https://api.qishui.com/p"},
        }
    )
    play_resp = _FakeResponse(
        {
            "Result": {
                "Data": {
                    "PlayInfoList": [
                        {
                            "Quality": "medium",
                            "Bitrate": 68,
                            "Duration": 30,
                            "MainPlayUrl": "u1",
                            "PlayAuth": "a1",
                        },
                    ]
                }
            }
        }
    )  # only a 30s preview
    fake = _FakeClient(post_response=track_resp, get_responses=[play_resp])
    client = _client_with(fake)

    import asyncio

    with pytest.raises(SodaPreviewError):
        asyncio.run(client.get_track_with_play_info("7", want_quality="lossless"))


def test_get_track_with_play_info_raises_when_no_player_info():
    track_resp = _FakeResponse({"track": {"id": "7"}, "track_player": {}})
    fake = _FakeClient(post_response=track_resp)
    client = _client_with(fake)

    import asyncio

    with pytest.raises(SodaApiError):
        asyncio.run(client.get_track_with_play_info("7", want_quality="lossless"))
