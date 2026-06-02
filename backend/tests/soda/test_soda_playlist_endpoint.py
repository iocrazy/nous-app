"""Tests for the Soda playlist resolve endpoint + its pure helper.

`resolve_playlist_id` is exercised against a fake client (no network). The
endpoint is exercised through FastAPI's TestClient with the auth dependency
and cookie source overridden.
"""

from __future__ import annotations

import asyncio

from app.api.media_soda_router import resolve_playlist_id
from app.services.media.parsers.soda_music.soda_api import SodaContent


class _FakeClient:
    """Fake SodaApiClient: canned short-link resolution + playlist tracks."""

    def __init__(self, *, short_link_content=None, tracks=None):
        self._short_link_content = short_link_content
        self._tracks = tracks or []

    async def resolve_short_link(self, url: str):
        return self._short_link_content

    async def get_playlist_tracks(self, playlist_id: str, **kwargs):
        return self._tracks


# --- pure: resolve_playlist_id ----------------------------------------------


def test_resolve_playlist_id_from_query():
    client = _FakeClient()
    pid = asyncio.run(
        resolve_playlist_id(
            "https://music.douyin.com/qishui/share/playlist?playlist_id=PL999",
            client,
        )
    )
    assert pid == "PL999"


def test_resolve_playlist_id_extracts_url_from_messy_share_text():
    # Regression (2026-06-02 prod 400): qishui share text wraps the URL, e.g.
    # "歌单｜深夜 emo https://...share/playlist?playlist_id=X @汽水音乐". Without
    # URL extraction, urlparse sees an empty scheme → SSRF rejects → 400.
    client = _FakeClient()
    messy = (
        "歌单｜深夜 emo "
        "https://music.douyin.com/qishui/share/playlist?playlist_id=PL777&sec=x "
        "@汽水音乐"
    )
    pid = asyncio.run(resolve_playlist_id(messy, client))
    assert pid == "PL777"


def test_resolve_playlist_id_track_url_returns_none():
    client = _FakeClient()
    pid = asyncio.run(
        resolve_playlist_id(
            "https://music.douyin.com/qishui/share/track?track_id=7123",
            client,
        )
    )
    assert pid is None


def test_resolve_playlist_id_short_link():
    client = _FakeClient(
        short_link_content=SodaContent(kind="playlist", content_id="PLSHORT")
    )
    pid = asyncio.run(resolve_playlist_id("https://v.douyin.com/abc/", client))
    assert pid == "PLSHORT"


def test_resolve_playlist_id_short_link_not_playlist_returns_none():
    client = _FakeClient(short_link_content=SodaContent(kind="track", content_id="7"))
    pid = asyncio.run(resolve_playlist_id("https://v.douyin.com/abc/", client))
    assert pid is None


# --- endpoint ----------------------------------------------------------------


def _make_client(
    monkeypatch, *, tracks, resolve_to="PL999", owned=None, owned_raises=False
):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api import media_soda_router
    from app.core.deps import AuthContext, get_auth

    app = FastAPI()
    app.include_router(media_soda_router.router, prefix="/api/v1/media")

    async def _fake_auth():
        return AuthContext(user_id="user-123", auth_type="jwt")

    app.dependency_overrides[get_auth] = _fake_auth

    async def _fake_cookie(user_id, **kwargs):
        return "sessionid=fake"

    monkeypatch.setattr(media_soda_router, "get_soda_cookie", _fake_cookie)

    # Stub the ownership probe so no DB is touched.
    class _FakeRepo:
        async def get_owned_platform_ids(self, ids, creator_id):
            if owned_raises:
                raise RuntimeError("dedup probe boom")
            return set(owned or [])

    monkeypatch.setattr(
        media_soda_router, "get_resources_repository", lambda: _FakeRepo()
    )

    class _Client(_FakeClient):
        pass

    def _fake_api(cookie):
        return _Client(
            short_link_content=(
                SodaContent(kind="playlist", content_id=resolve_to)
                if resolve_to
                else None
            ),
            tracks=tracks,
        )

    monkeypatch.setattr(media_soda_router, "SodaApiClient", _fake_api)
    return TestClient(app)


def test_playlist_endpoint_returns_tracks(monkeypatch):
    tracks = [
        {
            "track_id": "1",
            "title": "Song A",
            "artist": "Artist",
            "cover_url": "https://c/1.jpg",
            "duration_ms": 200000,
        },
        {
            "track_id": "2",
            "title": "Song B",
            "artist": None,
            "cover_url": None,
            "duration_ms": None,
        },
    ]
    client = _make_client(monkeypatch, tracks=tracks)
    resp = client.post(
        "/api/v1/media/soda/playlist",
        json={
            "url": "https://music.douyin.com/qishui/share/playlist?playlist_id=PL999"
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["playlist_id"] == "PL999"
    assert body["total"] == 2
    assert body["tracks"][0]["track_id"] == "1"
    assert body["tracks"][1]["artist"] is None
    # Nothing owned → all new.
    assert body["downloaded_count"] == 0
    assert body["new_count"] == 2
    assert body["tracks"][0]["downloaded"] is False


def test_playlist_endpoint_marks_owned_tracks(monkeypatch):
    tracks = [
        {"track_id": "1", "title": "Song A"},
        {"track_id": "2", "title": "Song B"},
        {"track_id": "3", "title": "Song C"},
    ]
    # Track "2" is already downloaded.
    client = _make_client(monkeypatch, tracks=tracks, owned=["2"])
    resp = client.post(
        "/api/v1/media/soda/playlist",
        json={
            "url": "https://music.douyin.com/qishui/share/playlist?playlist_id=PL999"
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 3
    assert body["downloaded_count"] == 1
    assert body["new_count"] == 2
    by_id = {t["track_id"]: t for t in body["tracks"]}
    assert by_id["1"]["downloaded"] is False
    assert by_id["2"]["downloaded"] is True
    assert by_id["3"]["downloaded"] is False


def test_playlist_endpoint_owned_probe_failure_is_graceful(monkeypatch):
    """A failing ownership probe must never fail the load — all tracks
    fall back to not-downloaded."""
    tracks = [
        {"track_id": "1", "title": "Song A"},
        {"track_id": "2", "title": "Song B"},
    ]
    client = _make_client(monkeypatch, tracks=tracks, owned_raises=True)
    resp = client.post(
        "/api/v1/media/soda/playlist",
        json={
            "url": "https://music.douyin.com/qishui/share/playlist?playlist_id=PL999"
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["downloaded_count"] == 0
    assert body["new_count"] == 2
    assert all(t["downloaded"] is False for t in body["tracks"])


def test_playlist_endpoint_rejects_non_playlist(monkeypatch):
    client = _make_client(monkeypatch, tracks=[], resolve_to=None)
    resp = client.post(
        "/api/v1/media/soda/playlist",
        json={"url": "https://music.douyin.com/qishui/share/track?track_id=7"},
    )
    assert resp.status_code == 400
