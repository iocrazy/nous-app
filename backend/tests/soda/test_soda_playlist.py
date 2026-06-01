"""Tests for soda_music.soda_api playlist methods.

Covers the three playlist-layer methods:
- ``get_user_playlists`` must resolve user_id from ``/me`` and pass it (the
  endpoint returns ERR_INVALID_PARAM without user_id).
- ``find_favorites_playlist_id`` picks the type==1 (「我喜欢的音乐」) playlist.
- ``get_playlist_tracks`` pages ``playlist/detail`` and extracts music-track
  summaries only, skipping UGC video entries.

Each method is exercised via a SodaApiClient subclass overriding the underlying
network calls with canned dicts that match the REAL Luna PC response shapes.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.services.media.parsers.soda_music.soda_api import SodaApiClient


def _make_client() -> SodaApiClient:
    return SodaApiClient(cookie="sessionid=xyz")


# --- get_user_playlists passes user_id --------------------------------------


def test_get_user_playlists_passes_user_id():
    captured: dict[str, Any] = {}

    class _Client(SodaApiClient):
        async def get_me(self) -> dict[str, Any]:
            return {"my_info": {"id": "user-42"}}

        async def _get_json(self, url, *, with_params=True, extra_params=None):
            captured["url"] = url
            captured["params"] = extra_params
            return {"playlists": [{"id": "p1", "type": 1}], "total_num": 1}

    client = _Client(cookie="sessionid=xyz")
    data = asyncio.run(client.get_user_playlists())

    assert data["playlists"] == [{"id": "p1", "type": 1}]
    assert captured["url"].endswith("/luna/pc/user/playlist")
    assert captured["params"]["user_id"] == "user-42"
    assert captured["params"]["cursor"] == 0


# --- find_favorites_playlist_id ---------------------------------------------


def test_find_favorites_returns_type1():
    class _Client(SodaApiClient):
        async def get_user_playlists(self, cursor: int = 0) -> dict[str, Any]:
            return {
                "playlists": [
                    {"id": "douyin-fav", "type": 4},
                    {"id": "my-fav", "type": 1},
                    {"id": "user-pl", "type": 2},
                ],
                "total_num": 3,
            }

    client = _Client(cookie="sessionid=xyz")
    assert asyncio.run(client.find_favorites_playlist_id()) == "my-fav"


def test_find_favorites_returns_none_when_no_type1():
    class _Client(SodaApiClient):
        async def get_user_playlists(self, cursor: int = 0) -> dict[str, Any]:
            return {"playlists": [{"id": "x", "type": 2}], "total_num": 1}

    client = _Client(cookie="sessionid=xyz")
    assert asyncio.run(client.find_favorites_playlist_id()) is None


# --- get_playlist_tracks: skip videos, paginate -----------------------------


def test_get_playlist_tracks_skips_videos_and_paginates():
    page1 = {
        "media_resources": [
            {"type": "video", "entity": {"video": {}}},
            {
                "type": "track",
                "entity": {
                    "track_wrapper": {
                        "track": {
                            "id": "1",
                            "name": "A",
                            "duration": 1000,
                            "artists": [{"name": "X"}],
                            "album": {},
                        }
                    }
                },
            },
        ],
        "has_more": True,
        "next_cursor": 2,
    }
    page2 = {
        "media_resources": [
            {
                "type": "track",
                "entity": {
                    "track_wrapper": {
                        "track": {
                            "id": "2",
                            "name": "B",
                            "duration": 2000,
                            "artists": [],
                            "album": {},
                        }
                    }
                },
            },
        ],
        "has_more": False,
    }

    calls: list[int] = []

    class _Client(SodaApiClient):
        async def get_playlist_detail(
            self, playlist_id: str, cursor: int = 0, count: int = 30
        ) -> dict[str, Any]:
            calls.append(cursor)
            return page1 if cursor == 0 else page2

    client = _Client(cookie="sessionid=xyz")
    tracks = asyncio.run(client.get_playlist_tracks("pl"))

    assert [t["track_id"] for t in tracks] == ["1", "2"]  # video skipped
    assert tracks[0]["title"] == "A"
    assert tracks[0]["artist"] == "X"
    assert tracks[0]["duration_ms"] == 1000
    assert tracks[1]["artist"] is None  # no artists
    assert len(calls) == 2  # paged twice
    assert calls == [0, 2]  # used next_cursor


def test_get_playlist_tracks_respects_max():
    def _track(i: int) -> dict[str, Any]:
        return {
            "type": "track",
            "entity": {
                "track_wrapper": {
                    "track": {
                        "id": str(i),
                        "name": f"T{i}",
                        "duration": 1000,
                        "artists": [{"name": "X"}],
                        "album": {},
                    }
                }
            },
        }

    class _Client(SodaApiClient):
        async def get_playlist_detail(
            self, playlist_id: str, cursor: int = 0, count: int = 30
        ) -> dict[str, Any]:
            base = cursor
            return {
                "media_resources": [_track(base + j) for j in range(count)],
                "has_more": True,
                "next_cursor": cursor + count,
            }

    client = _Client(cookie="sessionid=xyz")
    tracks = asyncio.run(client.get_playlist_tracks("pl", max_tracks=5, count=3))

    assert len(tracks) == 5
