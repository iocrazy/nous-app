# Soda Music Phase 6 — UGC Video Download

> Subagent-driven, TDD. Branch `feature/soda-ugc-video` off master (Phases 1-5 + Phase 4 #420 merged).

**Goal:** Download the UGC short-videos that live in qishui playlists / share links. Single UGC link (`share/ugc_video?ugc_video_id=…`) → resolve → download MP4 → persist as a `media_type='video'` resource. Playlist batch (Phase 4) now also offers the video entries (`mr.type=='video'`) it currently skips.

**Architecture:** A qishui UGC video is a **plain unencrypted MP4** (no PlayAuth, no CENC). It is resolved by scraping the share page HTML: `GET …/qishui/share/ugc_video?ugc_video_id=<id>` → `window._ROUTER_DATA` JSON → `loaderData.ugc_video_page.videoOptions` = `{ url, videoName, artistName, coverURL, duration, width, height, group_download_level, hasCopyright }` (blueprint §A.2.1, field-tested). `videoOptions.url` is a `*.douyinvod.com` direct MP4 that **expires**, so — exactly like the audio workflow re-resolves the track for fresh PlayAuth — the download workflow re-resolves the share page at download time for a fresh URL. No decryption: just stream the MP4 to disk. Persist with the generic video columns (`download_path` / `video_download_status`), `media_type='video'`, resource `file_type='video'`. This reuses the entire qishui parse routing + flow grouping + resource creation already built in Phases 2-5; the only new download primitive is a no-decrypt stream.

**Why not route to the douyin/ytdlp pipeline (blueprint's first suggestion):** that pipeline re-fetches `video_download_urls` from the douyin API by aweme_id; a qishui `ugc_video_id` is not a douyin aweme_id and the MP4 is already handed to us by the share page. A dedicated soda UGC workflow (mirroring `soda_download_workflow`) is less coupling and reuses the proven qishui routing. The MP4 is a `douyinvod.com` link, downloaded via the SSRF-safe client.

## Integration Map (verified by reading code)
- `soda_api.py`: `classify_landing_url` already returns `kind="ugc_video"` for `ugc_video_id` in query (line 125). `resolve_short_link` HEAD-follows a short link → classify. `_get_json`/`_post_json` are JSON helpers — UGC needs a NEW **HTML** fetch (the share page is a web page, not the LunaPC JSON API). `SodaApiClient(cookie=...)` + `self._client_factory()` (defaults to `safe_async_client`). `build_pc_headers(cookie)` is for the API; the share page wants a browser-ish UA + cookie.
- `parse_entry.py`: `_track_id_from_url` raises `SodaApiError("qishui {kind} not supported in Phase 2 (UGC video is Phase 6)")` for `content.kind != "track"` (line 25-28). `resolve_qishui_metadata` is the track path. **Phase 6 adds the ugc branch.**
- `parse.py`: `parse_workflow` step 5 routes `is_soda_platform(platform)` → `dispatch_soda_download_step` (audio). Both `int(media_type)` sites are guarded for qishui. **Phase 6 branches qishui by media_type:** `'video'` → new `dispatch_soda_ugc_download_step`, else audio. `fetch_and_parse_step` for qishui calls `fetch_and_parse_qishui` → `resolve_qishui_metadata` (parse_helpers.py:263).
- `soda_download.py` (the template): `@DBOS.workflow soda_download_workflow`, `already_downloaded(row, base_dir)` skip-guard, `build_audio_dest`, `build_resource_fields`, re-resolve via `parse_track`, `MediaRepository().update(platform_id, {...})`, `ResourcesRepository().get_resource_by_media_id_and_creator` + `update_resource`/`create_resource`, `_download_cover` best-effort, route-C manager API (`manager.start/update_progress/complete`), failures `raise`.
- Video persist columns (verified): parsed_media `download_path` + `video_download_status`; resource `file_type="video"`, `mime_type="video/mp4"`.
- Playlist (Phase 4): `get_playlist_tracks` skips `mr.type != "track"`. `media_resources[].entity.video` holds the UGC video (`{id, ...}` — `id` is the `ugc_video_id`). `media_soda_router.py` `batch_plan` builds `share/track?track_id=` per item; UGC needs `share/ugc_video?ugc_video_id=`.

---

## Task 1: `extract_router_data` + `get_ugc_video` (HTML scrape)

**Files:** `soda_api.py`; test `backend/tests/soda/test_soda_ugc.py` (new).

Add a module-level pure helper + a client method.

- [ ] Step 1 — failing tests:
  - `extract_router_data(html)` pure fn: given an HTML string containing `<script>window._ROUTER_DATA = {"loaderData":{"ugc_video_page":{"videoOptions":{"url":"https://x.douyinvod.com/v.mp4","videoName":"Clip","artistName":"Bob","coverURL":"https://c/cover.jpg","duration":12000,"width":720,"height":1280}}}}</script>` → returns the `videoOptions` dict. Missing/garbage HTML → `None`.
  - `get_ugc_video(ugc_video_id)` on a SodaApiClient subclass whose `_client_factory` yields a fake client returning that HTML → returns the videoOptions dict; asserts the GET URL is `https://music.douyin.com/qishui/share/ugc_video?ugc_video_id=<id>`.
- [ ] Step 2 — run, FAIL.
- [ ] Step 3 — implement:
  - `def extract_router_data(html: str) -> dict | None`: regex `window\._ROUTER_DATA\s*=\s*(\{.*?\})\s*</script>` (DOTALL, non-greedy won't span — use a balanced-brace scan or `(\{.*\})` greedy up to `</script>`; simplest robust approach: find `window._ROUTER_DATA =`, then take the substring to the next `</script>`, strip, `json.loads`). Navigate `data["loaderData"]["ugc_video_page"]["videoOptions"]` with `.get` chaining; return `None` on KeyError/JSONDecodeError. Defensive `try/except (ValueError, KeyError, TypeError)`.
  - `async def get_ugc_video(self, ugc_video_id: str) -> dict[str, Any]`: build `url = f"{SHARE_BASE}/qishui/share/ugc_video?ugc_video_id={ugc_video_id}"` (SHARE_BASE = `https://music.douyin.com`); `async with self._client_factory() as client: resp = await client.get(url, headers=_share_page_headers(self._cookie), timeout=self._timeout)`; wrap `resp.raise_for_status()` + `resp.text` in `try/except httpx.HTTPError → raise SodaApiError`; `vo = extract_router_data(resp.text)`; if `vo is None` → `raise SodaApiError(f"could not extract videoOptions for ugc_video {ugc_video_id}")`; return `vo`.
  - `_share_page_headers(cookie)`: a browser UA (`Mozilla/5.0 … Safari`) + `Cookie: cookie` + `Referer: https://music.douyin.com/`. (The LunaPC `build_pc_headers` is for the JSON API; the share page is a web page.)
- [ ] Step 4 — run (pass) + `uv run pytest tests/soda/ -q` no regression.
- [ ] Step 5 — `uv run python -c "import app.services.media.parsers.soda_music.soda_api"`; black/isort/ruff; commit `feat(soda): get_ugc_video — scrape share page videoOptions`.

> RISK: the exact `window._ROUTER_DATA` shape is blueprint-documented ("实测") but not live-probed this session. Build the extractor defensively (handle both `ugc_video_page` and a possible nested `loaderData` variant; tolerate the JSON being HTML-entity-escaped — if `json.loads` fails, try `html.unescape` first). Flag for smoke verification with a real cookie.

---

## Task 2: `format_ugc_video` → parsed_data

**Files:** new `soda_music/ugc_formatter.py`; test `test_soda_ugc.py`.

- [ ] Step 1 — failing test: `format_ugc_video(video_options, ugc_video_id="UV1", original_url="https://…")` → dict with `platform_id=="UV1"`, `source_platform=="qishui"`, `media_type=="video"`, `title=="Clip"`, `author=="Bob"`, `video_download_urls==["https://x.douyinvod.com/v.mp4"]`, `cover_urls==["https://c/cover.jpg"]`, `duration=="00:12"` (via `Utils.format_duration(12000)`), `metadata` containing `width/height/duration_ms/group_download_level/hasCopyright/ugc_video_id`.
- [ ] Step 2 — run, FAIL.
- [ ] Step 3 — implement `def format_ugc_video(vo: dict, *, ugc_video_id: str, original_url: str) -> dict`: mirror `formatter.format_track` shape but video. `duration = Utils.format_duration(int(vo.get("duration") or 0))`. `cover_urls = [vo["coverURL"]] if vo.get("coverURL") else None`. `video_download_urls = [vo["url"]] if vo.get("url") else []`. No `published_at` (UGC page has none — omit/None). `metadata = {"ext":"mp4","width":vo.get("width"),"height":vo.get("height"),"duration_ms":vo.get("duration"),"group_download_level":vo.get("group_download_level"),"hasCopyright":vo.get("hasCopyright"),"ugc_video_id":ugc_video_id}`.
- [ ] Step 4 — run + no regression.
- [ ] Step 5 — commit `feat(soda): format_ugc_video → video parsed_data`.

---

## Task 3: `parse_entry` ugc branch

**Files:** `parse_entry.py`; test extend `test_soda_ugc.py`.

- [ ] Step 1 — failing test: `resolve_qishui_metadata(url="https://music.douyin.com/qishui/share/ugc_video?ugc_video_id=UV1", user_id=None, api=<fake returning videoOptions via get_ugc_video>)` → returns the `format_ugc_video` dict (media_type=="video"). (Add `get_ugc_video` to the fake.)
- [ ] Step 2 — run, FAIL (currently raises "Phase 6").
- [ ] Step 3 — refactor `resolve_qishui_metadata`: classify the url (track_id-in-query OR `resolve_short_link`). If kind=="track" → existing `parse_track` path. If kind=="ugc_video" → `vo = await client.get_ugc_video(ugc_video_id); return format_ugc_video(vo, ugc_video_id=ugc_video_id, original_url=url)`. Keep the `_track_id_from_url` helper for the track path; add a parallel classify that returns `(kind, content_id)`. Remove the hard raise for ugc_video; keep raising for `playlist` (a playlist url shouldn't reach single-resolve) and unknown.
- [ ] Step 4 — run + `uv run pytest tests/soda/ -q` no regression (the old "Phase 6 raise" test, if any, must be updated to expect success — find and update it).
- [ ] Step 5 — commit `feat(soda): resolve ugc_video in parse_entry (was Phase 6 stub)`.

---

## Task 4: `soda_ugc_download_workflow` (stream MP4, no decrypt)

**Files:** new `app/workflows/soda_ugc_download.py`; test `backend/tests/soda/test_soda_ugc_download.py` (new). Mirror `soda_download.py` closely.

- [ ] Step 1 — failing tests (pure helpers + a workflow-body test with fakes, matching the existing soda workflow test style — read `tests/soda/` for the pattern):
  - `build_video_dest(media_id, base_dir)` → `(Path, "global/resources/web/qishui/<id>/video.mp4")`.
  - `build_ugc_resource_fields(file_path, size_bytes, title)` → `{"file_type":"video","mime_type":"video/mp4","filename":f"{title}.mp4","file_path":...,"file_size_bytes":...}` (only real resource columns — NO music_download_status).
  - `already_downloaded(row, base_dir)` for video → checks `row["download_path"]`.
- [ ] Step 2 — run, FAIL.
- [ ] Step 3 — implement:
  - `async def download_video_file(url, dest_path, cookie) -> int` (in `soda_downloader.py` or inline): stream via `safe_async_client`: `async with client.stream("GET", url, headers=_share_page_headers(cookie)) as resp: resp.raise_for_status(); async with aiofiles.open(dest,'wb') as f: async for chunk in resp.aiter_bytes(): await f.write(chunk)`; return total bytes. (If `client.stream` isn't supported by the SSRF client, fall back to `resp = await client.get(url,...); resp.raise_for_status(); write resp.content` — read `soda_downloader.py` to match the available client API.)
  - `@DBOS.workflow async def soda_ugc_download_workflow(platform_id, user_id, *, media_id=None, title="untitled", resource_id=None, user_agent=None, flow_id=None)`: mirror `soda_download_workflow` — `manager.start`; fetch row; resolve media_id; skip-guard `already_downloaded(row, base_dir)` on `download_path`; fresh cookie; **re-resolve**: `vo = await SodaApiClient(cookie=cookie).get_ugc_video(platform_id)` (platform_id == ugc_video_id); `url = vo["url"]` (raise if missing); stream to `build_video_dest`; `MediaRepository().update(platform_id, {"video_download_status":"completed","download_path":rel})`; update/create resource with `build_ugc_resource_fields`; best-effort cover (vo["coverURL"] → reuse a cover download; the cover here is a normal URL, may or may not hot-link — best-effort, never fail); `manager.complete`. Failures `raise`. Return `{platform_id, media_id, size}`.
- [ ] Step 4 — run + no regression + `uv run python -c "import app.workflows.soda_ugc_download"`.
- [ ] Step 5 — commit `feat(soda): soda_ugc_download_workflow (stream MP4, no decrypt)`.

---

## Task 5: route qishui video in `parse.py`

**Files:** `parse.py`; test extend the parse tests if a soda-routing test exists.

- [ ] Step 1 — Add `dispatch_soda_ugc_download_step(...)` mirroring `dispatch_soda_download_step` but dispatching `soda_ugc_download_workflow` (task_type="download", flow_id threaded, NO `int(media_type)`). 
- [ ] Step 2 — In `parse_workflow` step 5, change the qishui branch: `if is_soda_platform(platform): if str(media_type) == "video": download_dispatch = dispatch_soda_ugc_download_step(...) else: download_dispatch = dispatch_soda_download_step(...)`. (media_type comes from parsed_data; UGC formatter sets `"video"`, track sets `"audio"`.)
- [ ] Step 3 — verify both `int(media_type)` sites still never execute for qishui (video path also avoids them).
- [ ] Step 4 — `uv run pytest tests/ -k "parse or soda" -q` no regression; `uv run python -c "import app.workflows.parse"`.
- [ ] Step 5 — commit `feat(soda): route qishui ugc_video to soda_ugc_download_workflow`.

> The qishui single-fetch already works end-to-end for audio via `POST /media/fetch`; a `share/ugc_video` link now flows through the same entrypoint → parse → ugc download. No new single-video endpoint needed.

---

## Task 6: Playlist UGC videos (Phase 4 extension)

**Files:** `soda_api.py` (`get_playlist_tracks` → include videos), `media_soda_router.py` (batch URL per kind), `SodaPlaylistPanel.tsx` (video badge), `parserService.ts` (type). Tests extend `test_soda_playlist.py` + `test_soda_batch_dispatch.py`.

- [ ] Step 1 — failing tests:
  - `get_playlist_tracks` (rename concept to "items" but keep the method name for compat, OR add `include_videos=True` param) now also emits `mr.type=="video"` entries from `entity.video` as `{track_id: <video.id>, title, artist, cover_url, duration_ms, kind: "video"}`; track entries get `kind: "track"`. Test: a mixed page → both kinds returned, kind set correctly, video not skipped.
  - `build_track_url` → keep for tracks; add `build_item_url(item_id, kind)` → `share/track?track_id=` for track, `share/ugc_video?ugc_video_id=` for video. `batch_plan` accepts items with kind and builds the right URL.
- [ ] Step 2 — run, FAIL.
- [ ] Step 3 — implement: extend `get_playlist_tracks` to include video entries with a `kind` field (default both; tracks keep working). In `media_soda_router.py`: the `/soda/playlist` response items carry `kind`; the `/soda/playlist/download` request accepts `items: [{track_id, kind}]` (back-compat: if only `track_ids` given, treat all as kind="track"); `batch_plan` uses `build_item_url(id, kind)`. Frontend: `SodaTrackSummary` gains `kind`; `SodaPlaylistPanel` shows a small video icon + "Video" pill on `kind==="video"` rows; `downloadSodaTracks` sends items with kind (or keep sending track_ids + a parallel kinds map — pick the cleaner contract and keep the endpoint back-compatible).
- [ ] Step 4 — `uv run pytest tests/soda/ -q` all green; frontend `npx tsc --noEmit` 0 new errors + `npm run build`.
- [ ] Step 5 — commit `feat(soda): include UGC videos in playlist picker + batch download`.

> Keep the endpoint back-compatible: a body with only `track_ids` (no kinds) still works (all treated as tracks). The picker now shows videos with a distinct badge so the user knows what they're getting.

---

## Final
- [ ] `cd backend && uv run pytest tests/soda/ -q` all green; `-k "parse or media or batch"` no regression; collect-only clean.
- [ ] ruff/black/isort clean; frontend tsc 0 new errors; `npm run build` ok.
- [ ] `/ship` → PR to master → CI → merge → deploy.
- [ ] Post-deploy smoke (needs real cookie): (a) paste a `share/ugc_video` link → confirm it downloads + plays as a video resource; (b) paste a 我喜欢 link with mixed content → confirm video rows show a Video badge → select a couple → confirm they land as playable video resources grouped under the flow.

## Self-Review / Risk flags
- **#1 (highest):** `window._ROUTER_DATA` extraction is blueprint-stated, not live-probed this session. Defensive extractor + smoke verification mandatory before trusting. If the share page is JS-rendered (data not in initial HTML), this scrape fails and we'd need a headless fetch — verify the raw `curl` HTML contains `_ROUTER_DATA` during smoke; if not, fall back to the project's DrissionPage browser fetch.
- **#2:** `entity.video.id` assumed to be the `ugc_video_id` accepted by `share/ugc_video?ugc_video_id=`. Verify against a real playlist response during smoke.
- **#3:** douyinvod MP4 URL expiry → handled by re-resolve at download time (mirrors audio PlayAuth re-resolve).
- **#4:** points/billing — the playlist batch already charges `video_parse_batch` per item (Phase 4); video items ride the same charge. Single `share/ugc_video` via `/media/fetch` rides the single-fetch points path. No new billing gap.
- Consistency: UGC video reuses the qishui parse routing, flow grouping, resource two-layer write, skip-guard, and route-C discipline from Phases 2-5. Only genuinely new code: HTML scrape + no-decrypt stream + the video/track kind split in the playlist picker.
