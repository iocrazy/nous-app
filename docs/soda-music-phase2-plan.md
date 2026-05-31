# Soda Music Phase 2 — Single-Track End-to-End Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Paste one Soda (汽水音乐) single-track link → it parses into `parsed_media` (`source_platform='qishui'`, `media_type='audio'`), downloads the encrypted stream, decrypts it (Phase 1 `soda_decrypt`), writes the playable audio + cover + lyrics to disk, and persists a `resources` row — all through the existing DBOS task pipeline.

**Architecture:** A new `qishui` platform is detected **before** `douyin` (it's a `douyin.com` subdomain) and routed to a dedicated Soda parse path instead of yt-dlp. The Phase 1 `soda_music` package (api/decrypt/quality) is the engine. A new `soda_download_workflow` (DBOS) orchestrates download→decrypt→persist, following the CLAUDE.md 路线C task-tracking discipline (manager API only, `raise` on failure). The login cookie is threaded as a parameter from a **stopgap source** (`SODA_COOKIE` env) so Phase 2 stays decoupled from `user_cookies` — Phase 3 swaps the source, nothing else.

**Tech Stack:** FastAPI, DBOS workflows, httpx via `app.boundary.safe_async_client` (SSRF-safe), Supabase REST repositories, pytest + pytest-asyncio.

**Expected migrations:** ZERO. `source_platform` is free-text VARCHAR; `music_download_path` / `music_download_status` / `music_name` / `music_play_urls` / `cover_download_path` already exist on `parsed_media`; `resources.create_resource` takes a free dict.

---

## Integration Map (verified against the codebase — read these before touching them)

| Concern | File:line | Note |
|---------|-----------|------|
| Platform detection | `backend/app/services/media/parsers/url_router.py:20-58` | `PLATFORM_PATTERNS` dict + `detect_platform` returns `(platform, handler_type)` |
| Fetch entry | `backend/app/api/media_fetch_router.py:45-113` | `fetch_video` → `detect_platform` → `handle_media_fetch_dispatch` |
| Dispatch | `backend/app/api/media_fetch_helpers.py:402-583` | threads `platform` into DBOS `parse_workflow` |
| Parse branch | `backend/app/workflows/parse.py:45-83` | `fetch_and_parse_step(...)` branches `if platform=="douyin" … else ytdlp` — **add `qishui` branch here** |
| Formatter contract | `backend/app/services/media/parsers/douyin_parse/formatter.py:109-446` | the `parsed_data` dict shape consumed downstream |
| MediaCreate schema | `backend/app/schemas/media.py:19-129` | allowed keys; `source_platform` default `"douyin"`; has `music_name`, `music_play_urls`, `cover_urls`, `media_type`, `duration` |
| Save metadata | `backend/app/services/media/parsers/media_service.py:372-502` | `save_metadata_only(platform_id, parsed_data)` builds + writes `parsed_media` |
| Download dispatch | `backend/app/services/media/parsers/media_service.py:310-364` | `_execute_downloads` branches on `int(media_type)` — audio needs its own route |
| Music download (ref) | `backend/app/services/media/downloader/downloader.py:1055-1171` | `download_music_by_platform_id`; URL from `repo.get_music_data`, writes via `download_file` |
| Storage path helper | `backend/app/core/utils.py:318-339` | `Utils.create_web_resource_path(platform, identifier) -> (Path, str)` |
| DBOS download workflow (ref) | `backend/app/workflows/download.py:543-750` | pattern for `manager.create/start/update_progress/complete/fail`; **failure must `raise`** |
| parsed_media repo | `backend/app/repositories/media_repository.py:65-369` | REST; `create / get_by_platform_id / get_by_id / update / mark_music_as_downloaded` |
| resources repo | `backend/app/repositories/resources_repository.py:107-115` | `create_resource(data)`; needs `creator_id, source_type, media_id, filename, file_type, mime_type, file_path, file_size_bytes`. **No `scope_type` column (dropped).** |
| File serving | `backend/app/api/media_download_router.py:240-343` | `/download_music_file` reads `extract_audio_path` → `music_download_path` |

**CLAUDE.md 路线C reminders (apply to Task 7):** UI reads only `task_tracking`; never PATCH `phase/status/progress/started_at/completed_at/error_msg` (the trigger owns them); business fields (subtitle/metadata/media_id) go through manager; **failure raises, never `return {"status":"failed"}`**; extra business data goes to `task_tracking.metadata`, not DBOS input/output.

---

## File Structure (created/modified by this plan)

**New files (isolated, fully specified here):**
- `backend/app/services/media/parsers/soda_music/formatter.py` — `track` dict → `parsed_data` dict
- `backend/app/services/media/parsers/soda_music/soda_parser.py` — orchestration → `(parsed_data, SodaDownloadPlan)`
- `backend/app/services/media/parsers/soda_music/soda_downloader.py` — download encrypted bytes + decrypt + write file
- `backend/app/services/media/parsers/soda_music/cookie_source.py` — stopgap cookie provider (Phase 3 replaces body)
- `backend/app/workflows/soda_download.py` — DBOS `soda_download_workflow`
- Tests under `backend/tests/soda/` (one file per module)

**Modified files (integration — read first, then apply the exact change):**
- `backend/app/services/media/parsers/url_router.py` — add `qishui` before `douyin`
- `backend/app/workflows/parse.py` — add `qishui` branch in `fetch_and_parse_step`
- `backend/app/services/media/parsers/media_service.py` — route `media_type=='audio'` / `source_platform=='qishui'` to soda download
- `backend/app/api/media_fetch_helpers.py` — let the `qishui` platform reach the soda parse/download path

---

## Task 1: Detect the `qishui` platform (before `douyin`)

**Files:**
- Modify: `backend/app/services/media/parsers/url_router.py:20-58`
- Test: `backend/tests/soda/test_url_router_qishui.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/soda/test_url_router_qishui.py
from app.services.media.parsers.url_router import URLRouter


def test_qishui_short_link_detected_as_soda():
    platform, handler = URLRouter.detect_platform("https://qishui.douyin.com/s/iABCDEF/")
    assert platform == "qishui"
    assert handler == "soda"


def test_music_douyin_detected_as_qishui_not_douyin():
    # music.douyin.com is a douyin.com subdomain — qishui MUST win
    platform, handler = URLRouter.detect_platform(
        "https://music.douyin.com/qishui/share/track?track_id=7123"
    )
    assert platform == "qishui"


def test_plain_douyin_still_detected_as_douyin():
    platform, handler = URLRouter.detect_platform("https://www.douyin.com/video/7123")
    assert platform == "douyin"
    assert handler == "ytdlp"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/soda/test_url_router_qishui.py -v`
Expected: FAIL — `music.douyin.com` currently matches `douyin`; `qishui` handler is `ytdlp` not `soda`.

- [ ] **Step 3: Implement — add `qishui` first, return `soda` handler**

In `url_router.py`, put `qishui` as the **first** key in `PLATFORM_PATTERNS` (dict iteration is insertion-ordered, so it's checked before `douyin`):

```python
    PLATFORM_PATTERNS: dict[str, list[str]] = {
        "qishui": ["qishui.douyin.com", "music.douyin.com"],  # MUST precede douyin
        "douyin": ["douyin.com", "iesdouyin.com"],
        "youtube": ["youtube.com", "youtu.be"],
        "bilibili": ["bilibili.com", "b23.tv"],
        "twitter": ["twitter.com", "x.com"],
        "tiktok": ["tiktok.com"],
        "instagram": ["instagram.com"],
        "xiaohongshu": ["xiaohongshu.com", "xhslink.com"],
    }
```

In `detect_platform`, set the handler per-platform instead of hardcoding `"ytdlp"`:

```python
        for platform, domains in URLRouter.PLATFORM_PATTERNS.items():
            for domain in domains:
                if hostname == domain or hostname.endswith(f".{domain}"):
                    handler_type = "soda" if platform == "qishui" else "ytdlp"
                    logger.info(
                        f"[URLRouter] Detected platform: {platform}, handler: {handler_type} for {url}"
                    )
                    return (platform, handler_type)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/soda/test_url_router_qishui.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/media/parsers/url_router.py backend/tests/soda/test_url_router_qishui.py
git commit -m "feat(soda): route qishui platform before douyin (handler=soda)"
```

---

## Task 2: `formatter.py` — map a `track` dict to `parsed_data`

**Files:**
- Create: `backend/app/services/media/parsers/soda_music/formatter.py`
- Test: `backend/tests/soda/test_soda_formatter.py`

The formatter turns the `track` dict (from `SodaApiClient.get_track_with_play_info`) into a dict accepted by `MediaCreate` / `save_metadata_only`. Use `media_type="audio"`, `source_platform="qishui"`, and pack Soda-specific extras (stats, colors, album, lyrics, quality) into `metadata`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/soda/test_soda_formatter.py
from app.services.media.parsers.soda_music.formatter import format_track


TRACK = {
    "id": "7123",
    "name": "Test Song",
    "duration": 200000,  # ms
    "artists": [{"name": "Artist A", "url_avatar": {"urls": ["https://p/"], "uri": "av"}}],
    "album": {"name": "Album X", "release_date": "2024-01-01",
              "url_cover": {"urls": ["https://p/"], "uri": "cov"}},
    "stats": {"count_collected": 10, "count_comment": 2, "count_shared": 3},
    "colors": {"cover_gradient_effect_color": "#fff"},
    "tags": ["pop"],
}
CHOSEN = {"Quality": "lossless", "Format": "flac", "Bitrate": 729,
          "MainPlayUrl": "https://cdn/flac", "PlayAuth": "auth", "Duration": 200}


def test_format_track_core_fields():
    pd = format_track(TRACK, CHOSEN, original_url="https://qishui.douyin.com/s/x/")
    assert pd["platform_id"] == "7123"
    assert pd["source_platform"] == "qishui"
    assert pd["media_type"] == "audio"
    assert pd["title"] == "Test Song"
    assert pd["author"] == "Artist A"
    assert pd["original_url"] == "https://qishui.douyin.com/s/x/"


def test_format_track_cover_url_assembled():
    pd = format_track(TRACK, CHOSEN, original_url="u")
    assert pd["cover_urls"] == ["https://p/cov~c5_375x375.jpg"]


def test_format_track_packs_metadata():
    pd = format_track(TRACK, CHOSEN, original_url="u")
    meta = pd["metadata"]
    assert meta["album"]["name"] == "Album X"
    assert meta["stats"]["count_collected"] == 10
    assert meta["quality"]["Quality"] == "lossless"
    assert meta["colors"]["cover_gradient_effect_color"] == "#fff"
    # engagement mirrored to top-level columns too
    assert pd["favorite_count"] == 10
    assert pd["comment_count"] == 2
    assert pd["share_count"] == 3


def test_format_track_ext_from_format():
    pd = format_track(TRACK, CHOSEN, original_url="u")
    assert pd["metadata"]["ext"] == "flac"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/soda/test_soda_formatter.py -v`
Expected: FAIL — `ModuleNotFoundError: ...soda_music.formatter`.

- [ ] **Step 3: Implement `formatter.py`**

```python
# backend/app/services/media/parsers/soda_music/formatter.py
"""Map a Soda `track` dict (from track_v2) into a parsed_media-shaped dict.

Output is consumed by MediaService.save_metadata_only via the MediaCreate
schema (app/schemas/media.py). Soda-specific extras (album, stats, colors,
credits, lyrics, chosen quality) live under `metadata`; engagement counts are
also mirrored to the top-level columns that already exist on parsed_media.
"""

from __future__ import annotations

from typing import Any

from app.services.media.parsers.soda_music.soda_api import cover_url

EXT_BY_FORMAT = {"flac": "flac", "mp4": "m4a", "m4a": "m4a", "aac": "m4a", "mp3": "mp3"}


def _ext_for(chosen: dict[str, Any]) -> str:
    fmt = str(chosen.get("Format") or chosen.get("format") or "").lower()
    return EXT_BY_FORMAT.get(fmt, "m4a")


def format_track(
    track: dict[str, Any], chosen: dict[str, Any], *, original_url: str
) -> dict[str, Any]:
    """Build the parsed_data dict for a single Soda track."""
    artists = track.get("artists") or []
    album = track.get("album") or {}
    stats = track.get("stats") or {}

    cover_urls: list[str] = []
    if album.get("url_cover"):
        cover_urls = [cover_url(album["url_cover"])]

    return {
        "platform_id": str(track.get("id")),
        "original_url": original_url,
        "source_platform": "qishui",
        "media_type": "audio",
        "title": track.get("name") or "untitled",
        "author": artists[0].get("name") if artists else None,
        "music_name": track.get("name"),
        "cover_urls": cover_urls or None,
        "favorite_count": stats.get("count_collected"),
        "comment_count": stats.get("count_comment"),
        "share_count": stats.get("count_shared"),
        "metadata": {
            "ext": _ext_for(chosen),
            "album": album,
            "artists": artists,
            "stats": stats,
            "colors": track.get("colors") or {},
            "tags": track.get("tags") or [],
            "song_maker_team": track.get("song_maker_team") or {},
            "duration_ms": track.get("duration"),
            "quality": {
                "Quality": chosen.get("Quality") or chosen.get("quality"),
                "Format": chosen.get("Format") or chosen.get("format"),
                "Bitrate": chosen.get("Bitrate") or chosen.get("bitrate"),
            },
        },
    }
```

> Note: confirm `metadata` is an accepted key on `MediaCreate` when wiring Task 8. If the schema rejects unknown keys, the executor adds `metadata: Optional[dict] = None` to `MediaBase` in `app/schemas/media.py` (parsed_media has a `metadata`/jsonb column per the schema map) as part of Task 8, with its own test.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/soda/test_soda_formatter.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/media/parsers/soda_music/formatter.py backend/tests/soda/test_soda_formatter.py
git commit -m "feat(soda): formatter — track dict to parsed_media-shaped dict"
```

---

## Task 3: `soda_parser.py` — orchestrate resolve → (parsed_data, download plan)

**Files:**
- Create: `backend/app/services/media/parsers/soda_music/soda_parser.py`
- Test: `backend/tests/soda/test_soda_parser.py`

This composes Phase 1 `SodaApiClient` + Task 2 `format_track`. It resolves a track id (or short link) to the full track + chosen stream, returns `parsed_data` plus a `SodaDownloadPlan` (the URL + PlayAuth + ext the downloader needs). `SodaApiClient` is injected for testability.

- [ ] **Step 1: Write the failing test**

```python
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
    track = {"id": "7123", "name": "S", "duration": 200000, "artists": [{"name": "A"}],
             "album": {}}
    chosen = {"Quality": "lossless", "Format": "flac", "Bitrate": 729,
              "MainPlayUrl": "https://cdn/flac", "PlayAuth": "auth", "Duration": 200}
    api = _FakeApi(track, chosen)

    parsed_data, plan = asyncio.run(
        parse_track(api, track_id="7123", want_quality="lossless",
                    original_url="https://qishui.douyin.com/s/x/")
    )

    assert parsed_data["platform_id"] == "7123"
    assert parsed_data["source_platform"] == "qishui"
    assert isinstance(plan, SodaDownloadPlan)
    assert plan.url == "https://cdn/flac"
    assert plan.play_auth == "auth"
    assert plan.ext == "flac"
    assert plan.track_id == "7123"
    assert api.calls == [("7123", "lossless")]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/soda/test_soda_parser.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement `soda_parser.py`**

```python
# backend/app/services/media/parsers/soda_music/soda_parser.py
"""Orchestrate a single Soda track: resolve → parsed_data + download plan.

Pure orchestration over the injected SodaApiClient + formatter. No DB, no disk.
The returned SodaDownloadPlan carries everything the downloader needs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.services.media.parsers.soda_music.formatter import format_track
from app.services.media.parsers.soda_music.soda_quality import play_url


@dataclass(frozen=True)
class SodaDownloadPlan:
    track_id: str
    url: str           # MainPlayUrl (or BackupPlayUrl)
    play_auth: str     # decryption-key carrier
    ext: str           # flac / m4a / mp3


async def parse_track(
    api: Any, *, track_id: str, want_quality: str, original_url: str
) -> tuple[dict[str, Any], SodaDownloadPlan]:
    """Resolve a track to (parsed_data, download_plan).

    Raises whatever SodaApiClient raises (SodaApiError / SodaPreviewError) —
    callers convert those into task failures (Task 7).
    """
    track, chosen = await api.get_track_with_play_info(track_id, want_quality)
    parsed_data = format_track(track, chosen, original_url=original_url)
    plan = SodaDownloadPlan(
        track_id=str(track.get("id")),
        url=play_url(chosen),
        play_auth=chosen.get("PlayAuth") or chosen.get("play_auth") or "",
        ext=parsed_data["metadata"]["ext"],
    )
    return parsed_data, plan
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/soda/test_soda_parser.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/media/parsers/soda_music/soda_parser.py backend/tests/soda/test_soda_parser.py
git commit -m "feat(soda): single-track parser orchestration → parsed_data + download plan"
```

---

## Task 4: `soda_downloader.py` — download encrypted bytes, decrypt, write file

**Files:**
- Create: `backend/app/services/media/parsers/soda_music/soda_downloader.py`
- Test: `backend/tests/soda/test_soda_downloader.py`

Downloads the encrypted stream (via injected client factory, defaulting to `safe_async_client`), decrypts with Phase 1 `decrypt_audio`, and writes the playable file. Returns the bytes written (size). The HTTP fetch is injected so the test is hermetic.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/soda/test_soda_downloader.py
import asyncio
from pathlib import Path

import pytest

from app.services.media.parsers.soda_music.soda_downloader import (
    download_and_decrypt,
)


class _FakeResp:
    def __init__(self, content): self.content = content
    def raise_for_status(self): pass


class _FakeClient:
    def __init__(self, content): self._content = content
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def get(self, url, **kw): return _FakeResp(self._content)


def test_download_and_decrypt_writes_decrypted_file(tmp_path, monkeypatch):
    # Build a real encrypted MP4 + matching PlayAuth using the decrypt test helpers
    from tests.soda.test_soda_decrypt import _build_mp4, make_play_auth
    hex_key = "00112233445566778899aabbccddeeff"
    play_auth = make_play_auth(hex_key)
    samples = [b"HELLOWORLD012345"]  # 16 bytes
    ivs = [b"\x00" * 8]
    mp4 = _build_mp4(bytes.fromhex(hex_key), samples, ivs)

    dest = tmp_path / "audio.flac"
    size = asyncio.run(
        download_and_decrypt(
            url="https://cdn/enc",
            play_auth=play_auth,
            dest_path=str(dest),
            client_factory=lambda: _FakeClient(mp4),
        )
    )

    assert dest.exists()
    assert size > 0
    assert b"HELLOWORLD012345" in dest.read_bytes()  # decrypted plaintext present


def test_download_and_decrypt_raises_on_empty_url(tmp_path):
    with pytest.raises(ValueError):
        asyncio.run(
            download_and_decrypt(url="", play_auth="x", dest_path=str(tmp_path / "a"))
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/soda/test_soda_downloader.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement `soda_downloader.py`**

```python
# backend/app/services/media/parsers/soda_music/soda_downloader.py
"""Download an encrypted Soda audio stream, decrypt it, write the playable file.

The encrypted bytes are fetched whole (audio files are small), decrypted via
the Phase 1 soda_decrypt module, then written to disk. HTTP transport is
injectable (defaults to the SSRF-safe client) so tests run offline.
"""

from __future__ import annotations

import os
from typing import Any, Callable

import aiofiles
from loguru import logger

from app.boundary import safe_async_client
from app.services.media.parsers.soda_music.soda_api import USER_AGENT
from app.services.media.parsers.soda_music.soda_decrypt import decrypt_audio


async def download_and_decrypt(
    *,
    url: str,
    play_auth: str,
    dest_path: str,
    cookie: str = "",
    client_factory: Callable[[], Any] | None = None,
) -> int:
    """Fetch the encrypted stream, decrypt, write to dest_path. Returns bytes written.

    Raises:
        ValueError: if url is empty.
        SodaDecryptError: if decryption fails (propagated from decrypt_audio).
    """
    if not url:
        raise ValueError("soda download url is empty")

    factory = client_factory or (lambda: safe_async_client())
    headers = {"User-Agent": USER_AGENT}
    if cookie:
        headers["Cookie"] = cookie

    async with factory() as client:
        resp = await client.get(url, headers=headers, timeout=120.0)
        resp.raise_for_status()
        encrypted = resp.content

    decrypted = decrypt_audio(encrypted, play_auth)

    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    async with aiofiles.open(dest_path, "wb") as fp:
        await fp.write(decrypted)
    logger.info("soda: wrote {} bytes to {}", len(decrypted), dest_path)
    return len(decrypted)
```

> Note: confirm `aiofiles` is a dependency (the existing downloader uses it — see `downloader.py`). If not present, `uv add aiofiles` is part of this task's commit.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/soda/test_soda_downloader.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/media/parsers/soda_music/soda_downloader.py backend/tests/soda/test_soda_downloader.py
git commit -m "feat(soda): download encrypted stream + decrypt + write file"
```

---

## Task 5: `cookie_source.py` — stopgap cookie provider

**Files:**
- Create: `backend/app/services/media/parsers/soda_music/cookie_source.py`
- Test: `backend/tests/soda/test_soda_cookie_source.py`

Phase 2 needs a login cookie to fetch full (non-preview) streams, but cookie storage is Phase 3. This isolates the source behind one function so Phase 3 only edits this file's body (swap env read for `user_cookies` repo).

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/soda/test_soda_cookie_source.py
import asyncio

from app.services.media.parsers.soda_music.cookie_source import get_soda_cookie


def test_get_soda_cookie_reads_env(monkeypatch):
    monkeypatch.setenv("SODA_COOKIE", "sessionid=abc")
    assert asyncio.run(get_soda_cookie(user_id="u1")) == "sessionid=abc"


def test_get_soda_cookie_empty_when_unset(monkeypatch):
    monkeypatch.delenv("SODA_COOKIE", raising=False)
    assert asyncio.run(get_soda_cookie(user_id="u1")) == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/soda/test_soda_cookie_source.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement `cookie_source.py`**

```python
# backend/app/services/media/parsers/soda_music/cookie_source.py
"""Soda login cookie provider.

PHASE 2 STOPGAP: reads the SODA_COOKIE env var. PHASE 3 will replace the body
with a read from the user_cookies table (platform='qishui') keyed by user_id —
the signature stays the same so no caller changes.
"""

from __future__ import annotations

import os


async def get_soda_cookie(user_id: str | None) -> str:
    """Return the Soda cookie string for a user (empty if none configured)."""
    return os.environ.get("SODA_COOKIE", "")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/soda/test_soda_cookie_source.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/media/parsers/soda_music/cookie_source.py backend/tests/soda/test_soda_cookie_source.py
git commit -m "feat(soda): stopgap cookie source (env) — Phase 3 swaps to user_cookies"
```

---

## Task 6: Add the `qishui` branch to the parse workflow

**Files:**
- Read first: `backend/app/workflows/parse.py:45-83` (understand `fetch_and_parse_step` and what `parse_workflow` does with its return `{"aweme_detail", "parsed_data"}`)
- Modify: `backend/app/workflows/parse.py`
- Test: `backend/tests/soda/test_parse_qishui_branch.py`

`fetch_and_parse_step` currently branches `if platform=="douyin" … else (ytdlp)`. Add a `qishui` branch that classifies the URL, resolves the track, and returns `parsed_data` in the same `{"aweme_detail": ..., "parsed_data": ...}` envelope. The synchronous DBOS step calls the async soda parser via `asyncio.run` (match how the existing step bridges async — verify when reading the file; if the step is already `async`, await directly).

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/soda/test_parse_qishui_branch.py
import asyncio

from app.services.media.parsers.soda_music import soda_parser
from app.services.media.parsers.soda_music.soda_api import SodaContent


def test_resolve_qishui_url_to_parsed_data(monkeypatch):
    """The qishui parse helper turns a track URL into a parsed_data envelope."""
    from app.services.media.parsers.soda_music.parse_entry import resolve_qishui

    track = {"id": "7123", "name": "S", "duration": 200000, "artists": [], "album": {}}
    chosen = {"Quality": "lossless", "Format": "flac", "MainPlayUrl": "u", "PlayAuth": "a",
              "Duration": 200, "Bitrate": 729}

    class _Api:
        async def get_track_with_play_info(self, tid, q): return track, chosen
        async def resolve_short_link(self, url): return SodaContent("track", "7123")

    parsed_data, plan = asyncio.run(
        resolve_qishui(
            url="https://music.douyin.com/qishui/share/track?track_id=7123",
            want_quality="lossless",
            cookie="",
            api=_Api(),
        )
    )
    assert parsed_data["platform_id"] == "7123"
    assert parsed_data["source_platform"] == "qishui"
    assert plan.track_id == "7123"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/soda/test_parse_qishui_branch.py -v`
Expected: FAIL — `parse_entry` missing.

- [ ] **Step 3: Implement the qishui resolve helper, then wire the branch**

Create `backend/app/services/media/parsers/soda_music/parse_entry.py`:

```python
# backend/app/services/media/parsers/soda_music/parse_entry.py
"""Entry helper: a qishui URL → (parsed_data, download_plan).

Classifies the URL (track_id in query, or short link needing a HEAD redirect),
then delegates to soda_parser.parse_track. UGC-video links are out of scope for
Phase 2 (deferred to Phase 6) — raise a clear error for now.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlparse

from app.services.media.parsers.soda_music.soda_api import (
    SodaApiClient,
    SodaApiError,
    classify_landing_url,
)
from app.services.media.parsers.soda_music.soda_parser import SodaDownloadPlan, parse_track


async def resolve_qishui(
    *, url: str, want_quality: str, cookie: str, api: Any | None = None
) -> tuple[dict[str, Any], SodaDownloadPlan]:
    client = api or SodaApiClient(cookie=cookie)

    query = parse_qs(urlparse(url).query)
    if "track_id" in query:
        track_id = query["track_id"][0]
    else:
        content = await client.resolve_short_link(url)
        if content is None:
            raise SodaApiError(f"could not classify qishui url: {url}")
        if content.kind != "track":
            raise SodaApiError(
                f"qishui {content.kind} not supported in Phase 2 (UGC video is Phase 6)"
            )
        track_id = content.content_id

    return await parse_track(
        client, track_id=track_id, want_quality=want_quality, original_url=url
    )
```

Then in `parse.py::fetch_and_parse_step`, add the branch (read the file first to match the exact async/sync bridge and signature). Conceptually:

```python
    if platform == "qishui":
        import asyncio

        from app.services.media.parsers.soda_music.cookie_source import get_soda_cookie
        from app.services.media.parsers.soda_music.parse_entry import resolve_qishui

        cookie = asyncio.run(get_soda_cookie(user_id))
        parsed_data, plan = asyncio.run(
            resolve_qishui(url=valid_url, want_quality="lossless", cookie=cookie)
        )
        # carry the download plan to the download workflow via parsed_data metadata
        parsed_data.setdefault("metadata", {})["soda_download_plan"] = {
            "url": plan.url, "play_auth": plan.play_auth, "ext": plan.ext,
            "track_id": plan.track_id,
        }
        return {"aweme_detail": {}, "parsed_data": parsed_data}
    elif platform == "douyin":
        ...  # unchanged
    else:
        ...  # unchanged (ytdlp)
```

> The PlayAuth is short-lived; carrying it in `parsed_data.metadata` lets the download workflow use it without re-resolving. If the executor finds PlayAuth expires before download, the alternative is to re-resolve inside the download workflow (Task 7) — note this tradeoff in the task's commit message.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/soda/test_parse_qishui_branch.py -v`
Then run the existing parse tests to confirm no regression: `cd backend && uv run pytest -k parse -q`
Expected: PASS, no regressions.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/media/parsers/soda_music/parse_entry.py backend/app/workflows/parse.py backend/tests/soda/test_parse_qishui_branch.py
git commit -m "feat(soda): qishui branch in parse workflow → parsed_data + download plan"
```

---

## Task 7: `soda_download_workflow` — download → decrypt → persist (DBOS)

**Files:**
- Read first: `backend/app/workflows/download.py:543-750` (manager API usage, step structure, finalize/resource-create pattern) and `backend/app/repositories/resources_repository.py:107-115`
- Create: `backend/app/workflows/soda_download.py`
- Test: `backend/tests/soda/test_soda_download_workflow.py` (test the pure helpers, not the DBOS decorator)

Build the workflow per CLAUDE.md 路线C: `manager.create/start/update_progress/complete/fail`; never PATCH `phase/...`; **failure raises**. Decompose so the testable logic lives in plain async helpers and the `@DBOS.workflow` is a thin shell.

- [ ] **Step 1: Write the failing test (pure persistence helper)**

```python
# backend/tests/soda/test_soda_download_workflow.py
import asyncio

from app.workflows.soda_download import build_resource_row, build_audio_dest


def test_build_audio_dest_uses_platform_and_ext():
    full, rel = build_audio_dest(platform_id="7123", media_id="999", ext="flac",
                                 base_dir="/tmp/dl")
    assert rel == "global/resources/web/qishui/999/audio.flac"
    assert str(full).endswith("global/resources/web/qishui/999/audio.flac")


def test_build_resource_row_required_fields():
    row = build_resource_row(
        creator_id="user-1", media_id="999", file_path="global/.../audio.flac",
        ext="flac", size_bytes=12345, title="Song",
    )
    assert row["creator_id"] == "user-1"
    assert row["media_id"] == "999"
    assert row["source_type"] == "web"
    assert row["mime_type"] == "audio/flac"
    assert row["filename"] == "Song.flac"
    assert row["file_size_bytes"] == 12345
    assert "scope_type" not in row  # column was dropped
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/soda/test_soda_download_workflow.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement `soda_download.py` (helpers + thin workflow)**

```python
# backend/app/workflows/soda_download.py
"""DBOS workflow: download + decrypt + persist a single Soda track.

Pure helpers (build_audio_dest / build_resource_row) are unit-tested; the
@DBOS.workflow shell wires them to the task manager. Follows CLAUDE.md 路线C:
manager API only, failures raise (never return a failed dict).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from dbos import DBOS
from loguru import logger

from app.core.utils import Utils
from app.repositories.media_repository import MediaRepository
from app.repositories.resources_repository import ResourcesRepository
from app.services.media.parsers.soda_music.soda_downloader import download_and_decrypt
from app.services.task_tracker import get_task_manager

MIME_BY_EXT = {"flac": "audio/flac", "m4a": "audio/mp4", "mp3": "audio/mpeg"}


def build_audio_dest(
    *, platform_id: str, media_id: str, ext: str, base_dir: str
) -> tuple[Path, str]:
    """Compute (full_path, relative_path) for the decrypted audio file."""
    rel = f"global/resources/web/qishui/{media_id}/audio.{ext}"
    return Path(base_dir) / rel, rel


def build_resource_row(
    *, creator_id: str, media_id: str, file_path: str, ext: str,
    size_bytes: int, title: str,
) -> dict[str, Any]:
    """Build the resources insert dict (no scope_type — column dropped)."""
    return {
        "creator_id": creator_id,
        "media_id": media_id,
        "source_type": "web",
        "file_type": "audio",
        "mime_type": MIME_BY_EXT.get(ext, "audio/mpeg"),
        "filename": f"{title}.{ext}",
        "file_path": file_path,
        "file_size_bytes": size_bytes,
        "music_download_status": "completed",
    }


@DBOS.workflow()
async def soda_download_workflow(
    platform_id: str,
    user_id: str,
    *,
    media_id: str,
    title: str,
    plan: dict[str, Any],   # {url, play_auth, ext, track_id}
    cookie: str = "",
    flow_id: str | None = None,
) -> dict[str, Any]:
    """Download + decrypt + persist. Raises on any failure (路线C)."""
    manager = get_task_manager()
    workflow_id = DBOS.workflow_id
    await manager.start(workflow_id)

    base_dir = Utils.get_download_base_path()
    full_path, rel_path = build_audio_dest(
        platform_id=platform_id, media_id=media_id, ext=plan["ext"], base_dir=base_dir
    )

    await manager.update_progress(workflow_id, 20, subtitle=f"Downloading {title}")
    size = await download_and_decrypt(
        url=plan["url"], play_auth=plan["play_auth"], dest_path=str(full_path),
        cookie=cookie,
    )

    await manager.update_progress(workflow_id, 80, subtitle="Saving to library")
    await MediaRepository().update(
        platform_id,
        {"music_download_status": "completed", "music_download_path": rel_path},
    )
    await ResourcesRepository().create_resource(
        build_resource_row(
            creator_id=user_id, media_id=media_id, file_path=rel_path,
            ext=plan["ext"], size_bytes=size, title=title,
        )
    )

    await manager.complete(workflow_id, subtitle=f"Downloaded {title}")
    logger.success("soda: downloaded+decrypted track {} ({} bytes)", platform_id, size)
    return {"platform_id": platform_id, "media_id": media_id, "size": size}
```

> Read `download.py` first to confirm exact manager method names/signatures (`start`/`update_progress`/`complete`/`fail`) and the `DBOS.workflow_id` accessor — adjust the shell to match. Keep the helpers exactly as tested.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/soda/test_soda_download_workflow.py -v`
Expected: PASS (2 helper tests). The `@DBOS.workflow` shell is covered by the Task 8 integration smoke + manual run.

- [ ] **Step 5: Commit**

```bash
git add backend/app/workflows/soda_download.py backend/tests/soda/test_soda_download_workflow.py
git commit -m "feat(soda): soda_download_workflow — download+decrypt+persist (route C)"
```

---

## Task 8: Wire fetch dispatch → parse → soda download (integration)

**Files:**
- Read first: `backend/app/api/media_fetch_helpers.py:402-583`, `backend/app/services/media/parsers/media_service.py:310-502`, `backend/app/schemas/media.py:19-129`
- Modify: `media_fetch_helpers.py` (let `qishui` reach soda download after parse), `media_service.py` (route `media_type=='audio'`/`source_platform=='qishui'` to `soda_download_workflow`), `app/schemas/media.py` (add `metadata` field if absent — see Task 2 note)
- Test: `backend/tests/soda/test_soda_fetch_integration.py`

This connects the parse output (Task 6) to the download workflow (Task 7) so a single `POST /api/v1/media/fetch` with a qishui URL parses → saves `parsed_media` → enqueues `soda_download_workflow`.

- [ ] **Step 1: Write the failing test (dispatch routing decision)**

```python
# backend/tests/soda/test_soda_fetch_integration.py
from app.services.media.parsers.media_service import MediaService


def test_audio_media_type_routes_to_soda_download():
    """A parsed_media with source_platform=qishui + media_type=audio selects soda."""
    assert MediaService.is_soda_audio({"source_platform": "qishui", "media_type": "audio"}) is True
    assert MediaService.is_soda_audio({"source_platform": "douyin", "media_type": "0"}) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/soda/test_soda_fetch_integration.py -v`
Expected: FAIL — `is_soda_audio` not defined.

- [ ] **Step 3: Implement the routing predicate + wire the workflow**

Add to `MediaService` (in `media_service.py`):

```python
    @staticmethod
    def is_soda_audio(parsed_data: dict) -> bool:
        return (
            parsed_data.get("source_platform") == "qishui"
            and str(parsed_data.get("media_type")) == "audio"
        )
```

Then, after reading `media_fetch_helpers.py:402-583` and `media_service.py:310-364`, wire the dispatch so that when `is_soda_audio(parsed_data)` is true, the pipeline:
1. calls `save_metadata_only(platform_id, parsed_data)` to create the `parsed_media` row and get `media_id`,
2. enqueues `soda_download_workflow` (via the same `start_workflow_routed` mechanism the existing download path uses — see `media_fetch_helpers.py`) with `platform_id, user_id, media_id, title, plan=parsed_data["metadata"]["soda_download_plan"], cookie`,
   instead of the douyin/ytdlp `download_workflow`.

If `MediaCreate` rejects the `metadata` key (Task 2 note), add to `MediaBase` in `app/schemas/media.py`:

```python
    metadata: Optional[dict] = None
```

with this test in the same file:

```python
def test_media_create_accepts_metadata():
    from app.schemas.media import MediaCreate
    m = MediaCreate(platform_id="1", original_url="u", metadata={"k": "v"})
    assert m.metadata == {"k": "v"}
```

- [ ] **Step 4: Run tests to verify they pass + full regression**

Run: `cd backend && uv run pytest tests/soda/ -q`
Then: `cd backend && uv run pytest --collect-only -q` (no import breakage)
Expected: all soda tests PASS, full suite collects clean.

- [ ] **Step 5: Manual end-to-end smoke (documented, not automated)**

With `SODA_COOKIE` set to a valid VIP cookie and the backend running on port 8081:
```bash
curl -X POST http://localhost:8081/api/v1/media/fetch \
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"url":"https://qishui.douyin.com/s/<track>/","need_download_music":true}'
```
Expected: a `task_tracking` row goes queued → in_progress → completed; `parsed_media` row has `source_platform='qishui'`, `music_download_path` set; a `resources` row exists; the file is playable via `/api/v1/download/download_music_file?platform_id=<id>`.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/media/parsers/media_service.py backend/app/api/media_fetch_helpers.py backend/app/schemas/media.py backend/tests/soda/test_soda_fetch_integration.py
git commit -m "feat(soda): wire qishui audio fetch → parse → soda_download_workflow"
```

---

## Final verification (before /ship)

- [ ] `cd backend && uv run pytest tests/soda/ -q` — all green
- [ ] `cd backend && uv run pytest --collect-only -q` — no import errors
- [ ] `cd backend && uv run black --check app/services/media/parsers/soda_music/ app/workflows/soda_download.py tests/soda/ && uv run isort --check-only app/services/media/parsers/soda_music/ tests/soda/ && uv run ruff check app/services/media/parsers/soda_music/ tests/soda/` — clean (CI gate is black + isort + ruff)
- [ ] Manual smoke (Task 8 Step 5) with a real cookie
- [ ] `/ship` → PR to master (merge #393 already landed; this is a fresh branch off updated master)

---

## Self-Review

**Spec coverage (B.10 item 2):** url_router qishui priority (Task 1 ✓), media_service branch (Task 8 ✓), soda_parser (Task 3 ✓), formatter (Task 2 ✓), single track → parsed_media `source_platform='qishui'` (Tasks 2/6/8 ✓), download + decrypt (Tasks 4/7 ✓), cover/lyrics to resources (cover via cover_urls + resource row Task 7; **lyrics: deferred — see gap below**), path via `music_download_path` (Task 7 ✓), zero migration (✓, except the additive `metadata` schema field which maps to an existing column). Cookie is a stopgap (Task 5) since real cookie wiring is Phase 3.

**Known gaps / deferrals (intentional, not placeholders):**
- **Lyrics**: B.6 lists lyrics as a resource. Phase 1 has no lyrics parser; the `track_v2` lyric field handling + LRC file write is small but not yet specified. Either add a Task 9 (parse `lyric` → write `.lrc` beside the audio + store path in metadata) or defer lyrics display to Phase 5 (Lyrics tab). **Recommend deferring the lyrics file to Phase 5** to keep Phase 2 focused on the audio path; flag at execution.
- **PlayAuth expiry**: carried in `parsed_media.metadata` between parse and download. If it expires before download runs, Task 7 must re-resolve via `SodaApiClient` instead of using the carried URL/auth. Decide at Task 7 based on observed expiry.
- **UGC video** (`share/ugc_video`): explicitly out of scope (Phase 6) — `resolve_qishui` raises a clear error.

**Placeholder scan:** No "TBD"/"implement later" in code steps; integration tasks (6/7/8) give concrete code + an explicit "read this file:line first" instruction because they touch existing functions whose exact bodies must be matched. No fabricated symbols — `SodaApiClient`, `decrypt_audio`, `play_url`, `cover_url`, `select_play_info` all exist in the Phase 1 package.

**Type consistency:** `SodaDownloadPlan(track_id, url, play_auth, ext)` used identically in Tasks 3/6/7. `parsed_data` keys consistent with `MediaCreate`. `build_audio_dest`/`build_resource_row` signatures match their tests.
