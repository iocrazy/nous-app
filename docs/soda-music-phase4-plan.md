# Soda Music Phase 4 — Playlist / 「我喜欢的音乐」 Batch Download

> Subagent-driven, TDD. Branch `feature/soda-playlist` off master (Phases 1-5 merged). **Do not execute until the single-track end-to-end smoke is confirmed in prod** (Phase 4 builds on it).

**Goal:** Paste a Soda playlist / 「我喜欢的音乐」 link → backend returns the track list → user multi-selects → batch download (each track reuses the single-track parse→download→decrypt path, grouped under one flow in Task Center).

**Architecture:** A playlist link resolves to N track summaries via the Phase 1 `get_playlist_detail` (paged). The frontend shows a checklist; "Download selected" sends the chosen `track_id`s. The batch endpoint constructs each track's share URL and dispatches the **existing** qishui parse+download path per track, sharing one `flow_id` so the children group in Task Center. No new download/decrypt code — pure orchestration + a list/select UI.

## Integration Map (verified)
- `soda_api.py`: `get_playlist_detail(playlist_id, cursor=0, count=30)` + `get_user_playlists(cursor=0)` + `get_me()` return **raw JSON** (caller extracts). `classify_landing_url` handles track/ugc_video only — **needs a `playlist` branch**. `SodaContent(kind, content_id)`.
- Playlist landing (blueprint §A.2.1/§A.3): `…/qishui/share/playlist?playlist_id=…`; playlist/detail JSON = `media_resources[].entity.track` + `next_cursor`/`has_more`; user/playlist JSON = `playlists[]` (find 我喜欢 by name/type).
- Batch infra: `POST /api/v1/media/fetch/batch` (`media_batch_router.py:36`) loops `start_workflow_routed("parse", parse_workflow, ...)` per URL; frontend `parserService.parseBatchLinks(urls, opts)`.
- Single qishui dispatch: `parse.py::dispatch_soda_download_step(..., flow_id=...)` — already groups by flow_id; `get_task_manager().create_flow(user_id, name, metadata)` makes the parent flow.
- Frontend parse UI: `ParserPage.tsx` (single/batch modes) + `parserService.ts`. **No existing list-then-select-then-download pattern** — Phase 4 adds one.

**Decision (zero migration):** batch reuses the single-track path per track (each track_id → `…/share/track?track_id=…` → existing qishui parse branch → soda_download_workflow), so all of Phase 2/3/5's correctness (incl. the #404 resource fix) applies automatically. No new DB.

---

## Task 1: Classify playlist short links

**Files:** Modify `backend/app/services/media/parsers/soda_music/soda_api.py`; Test extend `backend/tests/soda/test_soda_api.py`.

- [ ] Step 1 — failing test (append):
```python
def test_classify_landing_url_playlist():
    from app.services.media.parsers.soda_music.soda_api import classify_landing_url, SodaContent
    c = classify_landing_url("https://music.douyin.com/qishui/share/playlist?playlist_id=PL123")
    assert c == SodaContent(kind="playlist", content_id="PL123")
```
- [ ] Step 2 — run, FAIL (returns None).
- [ ] Step 3 — add to `classify_landing_url`, after the ugc_video branch:
```python
    if "playlist_id" in query:
        return SodaContent(kind="playlist", content_id=query["playlist_id"][0])
```
- [ ] Step 4 — `uv run pytest tests/soda/test_soda_api.py -q` (incl. existing track/ugc tests) no regression.
- [ ] Step 5 — black/isort/ruff + commit `feat(soda): classify playlist short links`.

---

## Task 2: `get_playlist_tracks` — page + extract track summaries

**Files:** Modify `soda_api.py` (add method); Test `backend/tests/soda/test_soda_playlist.py`.

Pages through `get_playlist_detail` until `has_more` is false (cap at a max), extracting a flat list of track summaries `{track_id, title, artist, cover_url, duration_ms}`.

- [ ] Step 1 — failing test (fake api returns two pages):
```python
import asyncio
from app.services.media.parsers.soda_music.soda_api import SodaApiClient


class _FakeApi(SodaApiClient):
    def __init__(self, pages):
        super().__init__(cookie="c")
        self._pages = pages
        self.calls = []

    async def get_playlist_detail(self, playlist_id, cursor=0, count=30):
        self.calls.append(cursor)
        return self._pages[len(self.calls) - 1]


def _track(tid, name):
    return {"entity": {"track": {"id": tid, "name": name, "artists": [{"name": "A"}],
            "album": {}, "duration": 1000}}}


def test_get_playlist_tracks_paginates_and_flattens():
    pages = [
        {"media_resources": [_track("1", "S1"), _track("2", "S2")], "has_more": True, "next_cursor": 2},
        {"media_resources": [_track("3", "S3")], "has_more": False, "next_cursor": 3},
    ]
    api = _FakeApi(pages)
    tracks = asyncio.run(api.get_playlist_tracks("PL", max_tracks=100))
    assert [t["track_id"] for t in tracks] == ["1", "2", "3"]
    assert tracks[0]["title"] == "S1"
    assert tracks[0]["artist"] == "A"
    assert len(api.calls) == 2  # paged twice


def test_get_playlist_tracks_respects_max():
    pages = [{"media_resources": [_track(str(i), f"S{i}") for i in range(30)],
              "has_more": True, "next_cursor": 30}]
    api = _FakeApi(pages)
    tracks = asyncio.run(api.get_playlist_tracks("PL", max_tracks=10))
    assert len(tracks) == 10
```
- [ ] Step 2 — run, FAIL (method missing).
- [ ] Step 3 — implement `async def get_playlist_tracks(self, playlist_id, *, max_tracks=500, count=30) -> list[dict]`: loop cursor 0,count,2*count… reading `get_playlist_detail`; for each `media_resources[].entity.track` build `{track_id:str(track["id"]), title:track.get("name"), artist:(artists[0].name if artists else None), cover_url: cover_url(album.url_cover) if present else None, duration_ms: track.get("duration")}`; stop when `has_more` false OR no media_resources OR len >= max_tracks. Use the next_cursor or cursor+=count. Defensive `or {}`/`or []`.
- [ ] Step 4 — run (2 pass) + soda suite no regression.
- [ ] Step 5 — commit `feat(soda): get_playlist_tracks (paged track summaries)`.

> Note: the real `media_resources[].entity` may nest `track_wrapper.track` rather than `track` (the Go ref uses `entity.track_wrapper.track`). The implementer MUST verify against §A.3 / a real response and handle both `entity.track` and `entity.track_wrapper.track`. Adapt the test fixture to the real shape and report.

---

## Task 3: Favorites playlist resolution

**Files:** Modify `soda_api.py`; Test extend `test_soda_playlist.py`.

`get_user_playlists` → find the 「我喜欢的音乐」 playlist id (the favorites list). Lets a user paste their library / favorites link or just trigger "download my favorites".

- [ ] Step 1 — failing test: fake `get_user_playlists` returns `{"playlists":[{"id":"fav","name":"我喜欢的音乐","type":"favorite"},{"id":"x","name":"Other"}]}` → `find_favorites_playlist_id(...)` returns `"fav"`.
- [ ] Step 2 — run, FAIL.
- [ ] Step 3 — implement `async def find_favorites_playlist_id(self) -> str | None`: read `get_user_playlists()`, scan `playlists[]` for the favorites one. **Verify the real marker** (a `type`/`playlist_type` == favorite, or name match) against §A.3 / a real response; prefer a stable field over the Chinese name. Report what you used.
- [ ] Step 4 — run + no regression.
- [ ] Step 5 — commit `feat(soda): resolve 我喜欢 favorites playlist id`.

---

## Task 4: `POST /api/v1/media/soda/playlist` — return track list

**Files:** Read the media router (where `/media/fetch` lives, likely `media_fetch_router.py`); add the route; schema in `app/schemas/`; Test `backend/tests/soda/test_soda_playlist_endpoint.py`.

Takes `{url}` (a qishui playlist/short link) → resolves to `playlist_id` (classify or direct) → `get_playlist_tracks` → returns `{playlist_id, tracks:[{track_id,title,artist,cover_url,duration_ms}]}`. Auth-gated; cookie via `get_soda_cookie(user_id)`.

- [ ] Step 1 — READ the fetch router for style/auth/cookie pattern. Failing test for a pure helper `resolve_playlist_id(url, client)` (track_id-in-query vs short-link redirect → playlist) with a fake client — assert it returns the playlist_id, and raises/None for non-playlist links.
- [ ] Step 2 — run, FAIL.
- [ ] Step 3 — implement the helper + the endpoint (auth dep; `SodaApiClient(cookie=await get_soda_cookie(auth.user_id))`; resolve playlist_id; `get_playlist_tracks`; return the list). Pydantic request/response models. Register the route.
- [ ] Step 4 — `uv run pytest tests/soda/test_soda_playlist_endpoint.py -q` + `uv run python -c "import app.main"` + collect-only no breakage.
- [ ] Step 5 — commit `feat(soda): POST /media/soda/playlist returns track list`.

---

## Task 5: Batch download dispatch (selected track_ids, flow-grouped)

**Files:** Modify the media router / a batch helper; reuse `dispatch_soda_download_step` + `create_flow`; Test `backend/tests/soda/test_soda_batch_dispatch.py`.

Endpoint `POST /api/v1/media/soda/playlist/download` takes `{track_ids:[...], playlist_title?}` → creates one flow (`create_flow`) → for each track_id, constructs `https://music.douyin.com/qishui/share/track?track_id=<id>` and dispatches the existing qishui parse+download (via `start_workflow_routed("parse", parse_workflow, {url, user_id, platform:"qishui", flow_id, ...})`) so each track lands parsed_media + resource + decrypted audio, grouped under the flow.

- [ ] Step 1 — failing test for a pure helper `build_track_url(track_id) -> str` (→ the share/track URL) and `batch_plan(track_ids, flow_id) -> list[dict]` (the per-track parse kwargs incl. shared flow_id, url, platform="qishui"). Assert N entries, shared flow_id, correct urls.
- [ ] Step 2 — run, FAIL.
- [ ] Step 3 — implement the helpers + the endpoint: `create_flow` once, loop dispatch per track (reuse the parse dispatch with `platform="qishui"` + flow_id). Cap batch size (e.g. 210 max, matching the blueprint's 我喜欢 size) and log if truncated (no silent cap). Rate-limiting is already handled by the lane queue (download lane cap) — note it.
- [ ] Step 4 — run + import + collect-only.
- [ ] Step 5 — commit `feat(soda): batch download selected playlist tracks (flow-grouped)`.

> The parse path already de-dups (L2 owned-resource check) + the Phase 5 skip-already-downloaded guard avoids re-downloading tracks the user already has. Confirm both apply on the batch path; note any gap.

---

## Task 6: Frontend — playlist parse → select → download

**Files:** `frontend/services/` (add `getSodaPlaylist`/`downloadSodaTracks`); a new `SodaPlaylistPanel.tsx`; wire into `ParserPage.tsx` (when the pasted link is a qishui playlist).

- [ ] Add service fns (via `apiClient`): `getSodaPlaylist(url) -> {playlist_id, tracks[]}` and `downloadSodaTracks(trackIds, title?) -> {flow_id, submitted}`.
- [ ] `SodaPlaylistPanel`: given a playlist url, fetch tracks → render a checklist (cover + title + artist + duration), "Select all" + per-row checkbox, "Download selected (N)" button → `downloadSodaTracks(selected)` → toast + the flow appears in Task Center. Loading/empty/error states; English UI; match existing ParserPage styling.
- [ ] Wire detection: in `ParserPage`, when the input looks like a qishui playlist link (or after a probe), show `SodaPlaylistPanel` instead of the single-fetch flow. (Simplest: a "Soda playlist" mode, or detect `playlist` via a lightweight call.)
- [ ] Verify `npx tsc --noEmit` (no new errors) + `npm run build`.
- [ ] Commit `feat(soda): playlist track-picker + batch download UI`.

---

## Final
- [ ] `cd backend && uv run pytest tests/soda/ -q` all green; `-k "parse or media or batch"` no regression; collect-only clean.
- [ ] ruff/black/isort clean; frontend tsc no new errors; `npm run build` ok.
- [ ] `/ship` → PR to master → CI → merge → deploy.
- [ ] Post-deploy smoke: paste 我喜欢 link → see track list → select a few → confirm each lands in the library playable, grouped under one flow in Task Center.

## Self-Review
- Spec (B.10 item 4): `/media/soda/playlist` (Task 4 ✓), 我喜欢 batch (Tasks 3+5 ✓), frontend select + batch enqueue (Task 6 ✓), DBOS `soda_download_workflow` reused per track grouped by flow_id (Task 5 ✓). Zero migration (reuses single-track path).
- Risk flags for the implementer: (1) real `media_resources[].entity` nesting (`track` vs `track_wrapper.track`) — verify against a real response; (2) favorites marker field — prefer a stable field over the Chinese name; (3) playlist landing URL shape — verify `share/playlist?playlist_id=`; (4) batch size cap must log when truncated. All depend on a real cookie'd response — confirm during execution, not from the blueprint alone.
- Consistency: each batch track goes through the SAME parse→soda_download path as a single track, so #404's resource fix + Phase 5's lyrics capture + skip-guard all apply for free.
