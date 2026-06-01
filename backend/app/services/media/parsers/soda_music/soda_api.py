"""Soda (汽水音乐 / Luna) PC API client.

Talks to ``api.qishui.com/luna/pc/*`` using **dynamic device parameters** + the
``LunaPC/3.3.0`` UA and ``x-luna-*`` headers (§A.2). Crucially it does NOT send
the legacy ``X-Helios`` / ``X-Medusa`` / ``a_bogus`` signatures — those are what
broke the musicdl port (``ERR_REQUEST_FORBIDDEN``).

The signable surface (device params + headers + request shape) lives in pure
module-level functions so it can be unit-tested without network. ``SodaApiClient``
wraps the endpoints; its HTTP transport is injectable (defaults to the project's
SSRF-safe client) so tests run against a fake.

See ``docs/soda-music-integration.md`` §A.2 / §A.3 / §B.13.
"""

from __future__ import annotations

import html as _html
import json
import re
import time
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

import httpx
from loguru import logger

from app.boundary import safe_async_client
from app.services.media.parsers.soda_music.soda_quality import (
    is_preview,
    normalize_duration_seconds,
    select_play_info,
)

BASE_URL = "https://api.qishui.com"
SHARE_BASE_URL = "https://music.douyin.com/qishui"
SHARE_BASE = "https://music.douyin.com"
USER_AGENT = "LunaPC/3.3.0(359450208)"
SHARE_PAGE_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
DEFAULT_TIMEOUT = 30.0
DEFAULT_COVER_SIZE = "~c5_375x375.jpg"

# Byte ceiling for the UGC share-page HTML body. Share pages are small; 16 MiB
# is a generous cap that guards against an upstream streaming an unbounded body
# into ``resp.text`` (memory exhaustion).
MAX_SHARE_PAGE_BYTES = 16 * 1024 * 1024  # 16 MiB

# Fixed Luna PC device parameters (§A.2). Dynamic ids are filled per request.
_FIXED_PC_PARAMS: dict[str, str] = {
    "aid": "386088",
    "app_name": "luna_pc",
    "region": "cn",
    "geo_region": "cn",
    "os_region": "cn",
    "sim_region": "",
    "cdid": "",
    "version_name": "3.3.0",
    "version_code": "30030000",
    "channel": "official",
    "build_mode": "master",
    "network_carrier": "",
    "ac": "wifi",
    "tz_name": "Asia/Shanghai",
    "resolution": "",
    "device_platform": "windows",
    "device_type": "Windows",
    "os_version": "Windows 11",
}


class SodaApiError(Exception):
    """A Soda API request failed or returned an unusable response."""


class SodaPreviewError(SodaApiError):
    """The only stream available is a 30s preview (试听) — full track requires VIP."""


@dataclass(frozen=True)
class SodaContent:
    """Result of classifying a short-link landing URL (§A.2.1)."""

    kind: str  # "track" | "ugc_video" | "playlist"
    content_id: str


# ---------------------------------------------------------------------------
# Pure: signable surface
# ---------------------------------------------------------------------------


def build_pc_params(now_ms: int | None = None) -> dict[str, str]:
    """Build the per-request query params with fresh dynamic device ids (§A.2).

    ``device_id`` == ``fp`` == now (ms); ``iid`` == now + 1. A new id every
    request is what keeps rate-limiting/risk-control happy on batch downloads.
    """
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    device = str(now)
    return {
        **_FIXED_PC_PARAMS,
        "device_id": device,
        "fp": device,
        "iid": str(now + 1),
    }


def build_pc_headers(cookie: str, post: bool = False) -> dict[str, str]:
    """Build the LunaPC request headers (§A.2). POST adds a JSON content type."""
    headers = {
        "User-Agent": USER_AGENT,
        "Cookie": cookie,
        "x-luna-background-type": "foreground",
        "x-luna-is-background-req": "0",
        "x-luna-is-local-user": "1",
    }
    if post:
        headers["Content-Type"] = "application/json; charset=utf-8"
    return headers


def cover_url(album_url_cover: dict[str, Any], size: str = DEFAULT_COVER_SIZE) -> str:
    """Assemble a cover/avatar URL: urls[0] + uri + size suffix (§A.3.1)."""
    urls = album_url_cover.get("urls") or [""]
    return f"{urls[0]}{album_url_cover.get('uri', '')}{size}"


def classify_landing_url(url: str) -> SodaContent | None:
    """Classify a redirect landing URL into track vs ugc_video (§A.2.1)."""
    query = parse_qs(urlparse(url).query)
    if "track_id" in query:
        return SodaContent(kind="track", content_id=query["track_id"][0])
    if "ugc_video_id" in query:
        return SodaContent(kind="ugc_video", content_id=query["ugc_video_id"][0])
    if "playlist_id" in query:
        return SodaContent(kind="playlist", content_id=query["playlist_id"][0])
    return None


def _summarize_track(tr: dict[str, Any]) -> dict[str, Any] | None:
    """Summarize a ``track_wrapper.track`` entity into a playlist item."""
    if not tr.get("id"):
        return None
    artists = tr.get("artists") or []
    album = tr.get("album") or {}
    return {
        "track_id": str(tr.get("id")),
        "title": tr.get("name"),
        "artist": artists[0].get("name") if artists else None,
        "cover_url": (
            cover_url(album["url_cover"]) if album.get("url_cover") else None
        ),
        "duration_ms": tr.get("duration"),
        "kind": "track",
    }


def _summarize_video(v: dict[str, Any]) -> dict[str, Any] | None:
    """Summarize an ``entity.video`` UGC entity into a playlist item.

    The ``entity.video`` shape is not fully live-probed — read defensively:
    id, title (``name``/``videoName``), duration (``duration``), cover
    (``coverURL`` or assembled ``url_cover``), artist (``artistName`` or
    ``artists[0].name``).
    """
    if not v.get("id"):
        return None
    artists = v.get("artists") or []
    cover = v.get("coverURL")
    if not cover and v.get("url_cover"):
        cover = cover_url(v["url_cover"])
    return {
        "track_id": str(v.get("id")),
        "title": v.get("name") or v.get("videoName"),
        "artist": v.get("artistName") or (artists[0].get("name") if artists else None),
        "cover_url": cover,
        "duration_ms": v.get("duration"),
        "kind": "video",
    }


def _summarize_media_resource(mr: dict[str, Any]) -> dict[str, Any] | None:
    """Summarize one ``media_resources[]`` entry (track or UGC video).

    Returns ``None`` for unknown types or entries missing an id.
    """
    entity = mr.get("entity") or {}
    mr_type = mr.get("type")
    if mr_type == "track":
        tr = (entity.get("track_wrapper") or {}).get("track") or {}
        return _summarize_track(tr)
    if mr_type == "video":
        return _summarize_video(entity.get("video") or {})
    return None


# ---------------------------------------------------------------------------
# Pure: UGC video share-page scrape (§A.2.1)
# ---------------------------------------------------------------------------


def _share_page_headers(cookie: str) -> dict[str, str]:
    """Browser-ish headers for the UGC share page (a web page, not the JSON API).

    The LunaPC ``build_pc_headers`` is for ``api.qishui.com``; the share page at
    ``music.douyin.com`` wants a normal browser UA + cookie + referer.
    """
    return {
        "User-Agent": SHARE_PAGE_USER_AGENT,
        "Cookie": cookie,
        "Referer": "https://music.douyin.com/",
    }


def _balanced_json_object(text: str, start: int) -> str | None:
    """Return the JSON object substring starting at the ``{`` at ``start``.

    Balance-scans braces (respecting string literals + escapes) to find the
    matching ``}``. Returns ``None`` if no balanced object is found.
    """
    depth = 0
    in_str = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


_ROUTER_DATA_ASSIGN = re.compile(r"_ROUTER_DATA\s*=\s*\{")


def extract_router_data(html: str) -> dict[str, Any] | None:
    """Scrape ``_ROUTER_DATA`` → ``loaderData.ugc_video_page.videoOptions``.

    The real share page emits ``_ROUTER_DATA = {"loaderData":...}`` inside an
    inline ``<script>`` (no ``window.`` prefix — the blueprint was off), and
    ALSO carries decoy hydration JS later in the page (``_ROUTER_DATA=JSON.parse
    (...)``, ``_ROUTER_DATA={}``). So we iterate every ``_ROUTER_DATA = {``
    assignment and return the first one that actually yields a ``videoOptions``
    dict — robust to source ordering and the empty-object decoy. Retries with
    ``html.unescape`` for entity-escaped JSON. Returns ``None`` on any failure;
    callers raise their own error.
    """
    try:
        for m in _ROUTER_DATA_ASSIGN.finditer(html):
            brace = m.end() - 1  # the '{' the regex matched
            blob = _balanced_json_object(html, brace)
            if not blob:
                continue
            try:
                data = json.loads(blob)
            except json.JSONDecodeError:
                try:
                    data = json.loads(_html.unescape(blob))
                except json.JSONDecodeError:
                    continue
            vo = (
                ((data.get("loaderData") or {}).get("ugc_video_page") or {}).get(
                    "videoOptions"
                )
                if isinstance(data, dict)
                else None
            )
            if isinstance(vo, dict):
                return vo
        return None
    except (ValueError, KeyError, TypeError):
        return None


# ---------------------------------------------------------------------------
# API client
# ---------------------------------------------------------------------------


class SodaApiClient:
    """Thin async client over the Luna PC endpoints.

    ``client_factory`` returns an async-context-managed httpx-like client; it
    defaults to the project's SSRF-safe client. Inject a fake in tests.
    """

    def __init__(
        self,
        cookie: str,
        *,
        client_factory: Callable[[], Any] | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ):
        self._cookie = cookie
        self._client_factory = client_factory or (lambda: safe_async_client())
        self._timeout = timeout

    async def _post_json(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        url = f"{BASE_URL}{path}"
        async with self._client_factory() as client:
            resp = await client.post(
                url,
                params=build_pc_params(),
                content=json.dumps(body),
                headers=build_pc_headers(self._cookie, post=True),
                timeout=self._timeout,
            )
            try:
                resp.raise_for_status()
                return resp.json()
            except (httpx.HTTPError, ValueError) as e:
                raise SodaApiError(f"qishui request failed: {e}") from e

    async def _get_json(
        self,
        url: str,
        *,
        with_params: bool = True,
        extra_params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        async with self._client_factory() as client:
            kwargs: dict[str, Any] = {
                "headers": build_pc_headers(self._cookie),
                "timeout": self._timeout,
            }
            if with_params:
                kwargs["params"] = {**build_pc_params(), **(extra_params or {})}
            resp = await client.get(url, **kwargs)
            try:
                resp.raise_for_status()
                return resp.json()
            except (httpx.HTTPError, ValueError) as e:
                raise SodaApiError(f"qishui request failed: {e}") from e

    async def get_track_v2(self, track_id: str) -> dict[str, Any]:
        """POST /luna/pc/track_v2 — returns {track, url_player_info, raw} (§A.3)."""
        data = await self._post_json(
            "/luna/pc/track_v2",
            {
                "track_id": track_id,
                "media_type": "track",
                "queue_type": "favorite_track_playlist",
                "scene_name": "library",
            },
        )
        track = data.get("track", {}) or {}
        if "lyric" in data and "lyric" not in track:
            # lyric lives at the response root, not inside track — inject it so
            # it flows through to format_track (§A.3).
            track["lyric"] = data.get("lyric")
        url_player_info = (data.get("track_player", {}) or {}).get("url_player_info")
        return {"track": track, "url_player_info": url_player_info, "raw": data}

    async def get_play_info(self, url_player_info: str) -> list[dict[str, Any]]:
        """GET the player-info URL — returns Result.Data.PlayInfoList (§A.3)."""
        # The url_player_info is a fully-formed signed URL; don't re-attach params.
        data = await self._get_json(url_player_info, with_params=False)
        return ((data.get("Result", {}) or {}).get("Data", {}) or {}).get(
            "PlayInfoList", []
        ) or []

    async def get_track_with_play_info(
        self, track_id: str, want_quality: str
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Resolve a track to (track_metadata, chosen_play_info).

        Raises:
            SodaApiError: when no playable stream URL is returned.
            SodaPreviewError: when the only available stream is a preview (§A.4).
        """
        resolved = await self.get_track_v2(track_id)
        track = resolved["track"]
        url_player_info = resolved["url_player_info"]
        if not url_player_info:
            raise SodaApiError(f"track {track_id} returned no url_player_info")

        play_info_list = await self.get_play_info(url_player_info)
        chosen = select_play_info(play_info_list, want_quality)
        if chosen is None:
            raise SodaApiError(f"track {track_id} returned no play_info_list")

        full_seconds = normalize_duration_seconds(track.get("duration", 0) or 0)
        stream_seconds = float(chosen.get("Duration") or chosen.get("duration") or 0)
        if is_preview(stream_seconds, full_seconds):
            raise SodaPreviewError(
                f"track {track_id}: only a preview stream is available "
                f"({stream_seconds}s of {full_seconds}s) — full track needs VIP"
            )
        return track, chosen

    async def get_playlist_detail(
        self, playlist_id: str, cursor: int = 0, count: int = 30
    ) -> dict[str, Any]:
        """GET /luna/pc/playlist/detail — paged playlist tracks (§A.3)."""
        return await self._get_json(
            f"{BASE_URL}/luna/pc/playlist/detail",
            extra_params={"playlist_id": playlist_id, "cursor": cursor, "count": count},
        )

    async def get_user_playlists(self, cursor: int = 0) -> dict[str, Any]:
        """GET /luna/pc/user/playlist — the user's playlists. Requires user_id
        (from /me), else ERR_INVALID_PARAM."""
        me = await self.get_me()
        user_id = (me.get("my_info") or {}).get("id")
        if not user_id:
            raise SodaApiError("could not resolve user_id from /me")
        return await self._get_json(
            f"{BASE_URL}/luna/pc/user/playlist",
            extra_params={"user_id": user_id, "cursor": cursor, "count": 50},
        )

    async def find_favorites_playlist_id(self) -> str | None:
        """Resolve the 「我喜欢的音乐」 playlist id (type==1)."""
        data = await self.get_user_playlists()
        for p in data.get("playlists") or []:
            if p.get("type") == 1:
                return str(p.get("id"))
        return None

    async def get_playlist_tracks(
        self, playlist_id: str, *, max_tracks: int = 500, count: int = 30
    ) -> list[dict[str, Any]]:
        """Flat list of playlist item summaries — both tracks and UGC videos.

        Each item: ``{track_id, title, artist, cover_url, duration_ms, kind}``
        where ``kind`` is ``"track"`` or ``"video"``. The ``track_id`` key holds
        the item id (track id or ugc_video id) for response uniformity.
        """
        out: list[dict[str, Any]] = []
        cursor = 0
        while len(out) < max_tracks:
            det = await self.get_playlist_detail(
                playlist_id, cursor=cursor, count=count
            )
            resources = det.get("media_resources") or []
            if not resources:
                break
            for mr in resources:
                item = _summarize_media_resource(mr)
                if item is None:
                    continue  # unknown type or missing id
                out.append(item)
                if len(out) >= max_tracks:
                    break
            if not det.get("has_more"):
                break
            nxt = det.get("next_cursor")
            cursor = nxt if isinstance(nxt, int) and nxt > cursor else cursor + count
        return out

    async def get_me(self) -> dict[str, Any]:
        """GET /luna/pc/me — current user info (my_info.id) (§A.3)."""
        return await self._get_json(f"{BASE_URL}/luna/pc/me")

    async def search_track(self, keyword: str, cursor: int = 0) -> dict[str, Any]:
        """GET /luna/pc/search/track — track search (§A.3)."""
        return await self._get_json(
            f"{BASE_URL}/luna/pc/search/track",
            extra_params={"keyword": keyword, "cursor": cursor},
        )

    async def resolve_short_link(self, short_url: str) -> SodaContent | None:
        """HEAD-follow a qishui short link and classify the landing URL (§A.2.1)."""
        async with self._client_factory() as client:
            resp = await client.get(
                short_url, headers=build_pc_headers(self._cookie), timeout=self._timeout
            )
            landing = str(getattr(resp, "url", short_url))
        content = classify_landing_url(landing)
        if content is None:
            logger.warning("soda: could not classify landing url {}", landing)
        return content

    async def get_ugc_video(self, ugc_video_id: str) -> dict[str, Any]:
        """Scrape the UGC share page → ``videoOptions`` dict (§A.2.1).

        A qishui UGC video is a plain unencrypted MP4; the share page HTML
        carries ``window._ROUTER_DATA`` with the direct (expiring) URL. This is
        an HTML fetch, not the LunaPC JSON API — uses browser-ish headers.

        Raises:
            SodaApiError: on HTTP failure, when the share page exceeds
                ``MAX_SHARE_PAGE_BYTES``, or when videoOptions can't be parsed.
        """
        url = f"{SHARE_BASE}/qishui/share/ugc_video?ugc_video_id={ugc_video_id}"
        async with self._client_factory() as client:
            resp = await client.get(
                url, headers=_share_page_headers(self._cookie), timeout=self._timeout
            )
            try:
                resp.raise_for_status()
            except (httpx.HTTPError, ValueError) as e:
                raise SodaApiError(f"ugc_video fetch failed: {e}") from e
            # Cap the body size before/after buffering it into ``resp.text``.
            # First the advertised content-length (cheap, catches honest
            # upstreams early), then the decoded body itself (catches lying /
            # chunked upstreams). Either overrun → SodaApiError.
            declared = int(
                (getattr(resp, "headers", None) or {}).get("content-length") or 0
            )
            if declared > MAX_SHARE_PAGE_BYTES:
                raise SodaApiError(
                    f"ugc_video share page too large "
                    f"(content-length {declared} > {MAX_SHARE_PAGE_BYTES})"
                )
            text = resp.text
            if len(text.encode("utf-8", "ignore")) > MAX_SHARE_PAGE_BYTES:
                raise SodaApiError(
                    f"ugc_video share page too large "
                    f"(body > {MAX_SHARE_PAGE_BYTES})"
                )
        vo = extract_router_data(text)
        if vo is None:
            raise SodaApiError(
                f"could not extract videoOptions for ugc_video {ugc_video_id}"
            )
        return vo
