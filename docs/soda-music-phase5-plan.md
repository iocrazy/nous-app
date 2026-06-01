# Soda Music Phase 5 (Core) — Audio Detail UI + Lyrics + Polish

> Subagent-driven, TDD. Branch `feature/soda-audio-ui` off master (Phases 1-3 merged). Scope = **core slice**: Lyrics tab + audio player + hide video tabs + lyrics persistence + .flac content-type + skip-already-downloaded. Deferred: ffmpeg repackage/transcode, quality dropdown.

**Goal:** A downloaded Soda track is playable inline (waveform player), shows its platform lyrics in a dedicated Lyrics tab, hides video-only tabs (Transcript/Analysis), and persists rich metadata + lyrics at parse time.

**Decision:** Lyrics + rich metadata are **persisted** (migration 248 adds `parsed_media.metadata jsonb`). Lyrics tab reads the stored metadata (no live re-resolve).

## Integration Map (verified)
**Backend**
- `parsed_media` has NO `metadata` column (highest migration 247 → add **248**). `MediaCreate`/`MediaBase` (`app/schemas/media.py`) have no `metadata` field. `MediaCreate(**parsed_data)` drops unknown keys (Pydantic extra=ignore), so the column + schema field are both required to persist.
- ⚠️ **PostgREST schema cache**: after adding a column, REST writes/reads won't see it until reload → migration must end with `NOTIFY pgrst, 'reload schema';` (known gotcha). Include a `_rollback.sql`.
- `soda_api.py get_track_v2` returns full `track` dict incl. `lyric` field (§A.7). `formatter.format_track` builds `metadata` (album/stats/colors/quality) but doesn't capture lyric yet.
- Reference LRC parser: `/Volumes/program/project-code/github-repos/musicdl/musicdl/modules/utils/sodautils.py::SodaTimedLyricsParser` (parsetimedlyrics / tolrclinelevel / toplaintext).
- Audio serving: `app/api/media_download_router.py::download_music_file` (~239-346) maps content-types but **missing `.flac`** (add `".flac": "audio/flac"`).
- `soda_download_workflow` (`app/workflows/soda_download.py`) has **no skip-already-downloaded** guard.
- Download concurrency already capped (lane_queue `download` lane); device_id rotates per request — no work.

**Frontend**
- Detail: `FileDetailDispatcher.tsx` → `DownloadDetailPage.tsx` (player area ~264-317) → `VideoDetailPanel.tsx` (tabs array line 280-284, `TabKey` type line 40, render loop 303-326).
- `AudioWaveformPlayer.tsx` exists (`src`, `filename`, `duration`) — reuse.
- `utils/awemeType.ts` has `isVideoType`/`isAlbumType`, no `isAudioType`.
- Stats card already in `MediaCard.tsx` (Overview tab) — works for audio, no change.
- Tab labels hardcoded English (no i18n) — keep convention.
- `types.ts ParsedMedia` has `media_type`, `source_platform`, `music_download_path`, etc.

---

## Task 1: Migration 248 — `parsed_media.metadata jsonb`

**Files:** Create `supabase/migrations/248_add_parsed_media_metadata.sql` + `supabase/migrations/248_add_parsed_media_metadata_rollback.sql`.

- [ ] `248_add_parsed_media_metadata.sql`:
```sql
-- Add a jsonb metadata column to parsed_media for rich source-specific data
-- (Soda: album / artists / stats / colors / quality / lyrics). Additive, nullable.
ALTER TABLE parsed_media ADD COLUMN IF NOT EXISTS metadata jsonb DEFAULT '{}'::jsonb;

-- PostgREST must reload its schema cache or REST writes/reads won't see the column.
NOTIFY pgrst, 'reload schema';
```
- [ ] `248_add_parsed_media_metadata_rollback.sql`:
```sql
ALTER TABLE parsed_media DROP COLUMN IF EXISTS metadata;
NOTIFY pgrst, 'reload schema';
```
- [ ] Commit `feat(soda): migration 248 — parsed_media.metadata jsonb`. (CI auto-applies on merge; verify PostgREST sees the column post-deploy.)

> No local test. Validate SQL syntax by eye; the migration is additive + idempotent (`IF NOT EXISTS`).

---

## Task 2: `MediaBase` accepts `metadata`

**Files:** Modify `backend/app/schemas/media.py`; Test `backend/tests/soda/test_media_metadata_field.py`.

- [ ] Step 1 — failing test:
```python
def test_media_create_accepts_metadata():
    from app.schemas.media import MediaCreate
    m = MediaCreate(platform_id="1", original_url="u", metadata={"k": "v"})
    assert m.metadata == {"k": "v"}


def test_media_create_metadata_defaults_none():
    from app.schemas.media import MediaCreate
    m = MediaCreate(platform_id="1", original_url="u")
    assert m.metadata is None
```
- [ ] Step 2 — run, confirm FAIL (no field).
- [ ] Step 3 — add to `MediaBase` (read the file for the exact field style):
```python
    metadata: Optional[dict] = Field(None, description="Rich source-specific metadata (jsonb)")
```
- [ ] Step 4 — `uv run pytest tests/soda/test_media_metadata_field.py -v` + `uv run pytest -k media -q` no regression.
- [ ] Step 5 — black/isort/ruff + commit `feat(soda): MediaCreate accepts metadata jsonb`.

---

## Task 3: Port the Soda LRC lyrics parser

**Files:** Create `backend/app/services/media/parsers/soda_music/lyrics.py`; Test `backend/tests/soda/test_soda_lyrics.py`.

Port `SodaTimedLyricsParser` (cleaned up). Soda timed format per line: `[start_ms,dur]<offset,dur,flag>word<...>...`.

- [ ] Step 1 — failing test:
```python
from app.services.media.parsers.soda_music.lyrics import parse_timed_lyrics, to_lrc


SAMPLE = "[1000,2000]<0,500,0>Hello<500,500,0> World\n[3000,1000]<0,1000,0>Bye"


def test_parse_timed_lyrics_lines():
    lines = parse_timed_lyrics(SAMPLE)
    assert len(lines) == 2
    assert lines[0]["text"] == "Hello World"
    assert lines[0]["line_start_ms"] == 1000
    assert lines[1]["text"] == "Bye"


def test_to_lrc_format():
    lrc = to_lrc(parse_timed_lyrics(SAMPLE))
    assert lrc.splitlines()[0].startswith("[00:01.00]Hello World")
    assert lrc.splitlines()[1].startswith("[00:03.00]Bye")


def test_parse_empty_or_null():
    assert parse_timed_lyrics("") == []
    assert parse_timed_lyrics("NULL") == []
    assert to_lrc([]) == ""
```
- [ ] Step 2 — run, confirm FAIL.
- [ ] Step 3 — implement `lyrics.py` (port from `/Volumes/program/project-code/github-repos/musicdl/musicdl/modules/utils/sodautils.py::SodaTimedLyricsParser`, cleaned into module functions `parse_timed_lyrics(text)->list[dict]`, `to_lrc(parsed)->str`, `to_plain_text(parsed)->str`). Key rules: line regex `^\[(\d+),(\d+)\]`, token regex `<(\d+),(\d+),(\d+)>`; line text = concatenated token texts; LRC timestamp `[MM:SS.CS]` from `line_start_ms`. Empty/`NULL`/`null`/`None`/`none` → `[]`. `to_lrc([])` → `""`.
- [ ] Step 4 — run tests (3 pass) + soda suite no regression.
- [ ] Step 5 — black/isort/ruff + commit `feat(soda): port timed lyrics → LRC parser`.

---

## Task 4: Capture lyrics + persist metadata in formatter

**Files:** Modify `backend/app/services/media/parsers/soda_music/formatter.py`; Test extend `backend/tests/soda/test_soda_formatter.py`.

- [ ] Step 1 — failing test (add to existing file): track with a `lyric` field → `metadata.lyrics.lrc` populated.
```python
def test_format_track_captures_lyrics():
    from app.services.media.parsers.soda_music.formatter import format_track
    track = {"id": "7", "name": "S", "artists": [], "album": {},
             "lyric": {"content": "[1000,1000]<0,1000,0>Hi"}}
    chosen = {"Quality": "lossless", "Format": "flac", "Bitrate": 729}
    pd = format_track(track, chosen, original_url="u")
    assert pd["metadata"]["lyrics"]["lrc"].startswith("[00:01.00]Hi")
    assert pd["metadata"]["lyrics"]["lines"][0]["text"] == "Hi"
```
> First inspect the real `track.lyric` shape from `soda_api`/blueprint §A.7: it may be `lyric.content` (string) or `lyric` directly. The implementation must handle both: pull the timed-lyric string from `track.get("lyric", {}).get("content")` OR `track.get("lyric")` if it's a str. Adapt the test's input to match the real shape if §A.7 differs; keep the assertion that LRC is produced.
- [ ] Step 2 — run, confirm FAIL.
- [ ] Step 3 — in `format_track`, after building metadata, add:
```python
    from app.services.media.parsers.soda_music.lyrics import parse_timed_lyrics, to_lrc

    raw_lyric = track.get("lyric")
    lyric_text = raw_lyric.get("content") if isinstance(raw_lyric, dict) else (raw_lyric or "")
    parsed_lines = parse_timed_lyrics(lyric_text or "")
    # ... set metadata["lyrics"] = {"lrc": to_lrc(parsed_lines), "lines": parsed_lines}
```
- [ ] Step 4 — run the new test + full formatter test file + soda suite. (Existing formatter tests must still pass — track dicts without `lyric` should yield `metadata.lyrics` = `{"lrc": "", "lines": []}` or absent; pick one and assert it.)
- [ ] Step 5 — black/isort/ruff + commit `feat(soda): capture track lyrics into metadata`.

---

## Task 5: Lyrics endpoint `GET /media/{id}/lyrics`

**Files:** Modify the media router that serves `/media/{id}/*` (find it — likely `app/api/media_router.py` or `media_download_router.py`); Test `backend/tests/soda/test_lyrics_endpoint.py`.

Reads `parsed_media.metadata.lyrics` for the media id, returns `{lrc, lines}`. 404 if no media; empty `{lrc:"", lines:[]}` if no lyrics.

- [ ] Step 1 — READ the existing `/media/{id}/audio` or `/media/{id}` route to match router style, auth dep, and repo access. Then write a failing test for a small pure helper `extract_lyrics(media_row) -> dict` (so logic is testable without HTTP):
```python
from app.api.<router> import extract_lyrics  # adjust import to where you put it


def test_extract_lyrics_from_metadata():
    row = {"metadata": {"lyrics": {"lrc": "[00:01.00]Hi", "lines": [{"text": "Hi"}]}}}
    assert extract_lyrics(row) == {"lrc": "[00:01.00]Hi", "lines": [{"text": "Hi"}]}


def test_extract_lyrics_missing():
    assert extract_lyrics({"metadata": {}}) == {"lrc": "", "lines": []}
    assert extract_lyrics({}) == {"lrc": "", "lines": []}
```
- [ ] Step 2 — run, FAIL.
- [ ] Step 3 — implement `extract_lyrics` + the `GET /media/{id}/lyrics` endpoint (auth-gated like siblings; fetch media by id via the media repo; return `extract_lyrics(row)`; 404 if row is None).
- [ ] Step 4 — run the test + `uv run python -c "import app.main"` (router still imports) + `uv run pytest --collect-only -q | tail -1`.
- [ ] Step 5 — black/isort/ruff + commit `feat(soda): GET /media/{id}/lyrics endpoint`.

---

## Task 6: `.flac` content-type in audio serving

**Files:** Modify `backend/app/api/media_download_router.py` (~line 328-335 content-type map); Test `backend/tests/soda/test_flac_content_type.py` (if the map is extractable) or verify inline.

- [ ] Step 1 — if the content-type map is a module-level dict or extractable helper, test it: `assert content_type_for(".flac") == "audio/flac"`. If it's inline in the handler, extract a tiny pure helper `_audio_content_type(suffix)` first (refactor), test that.
- [ ] Step 2 — run, FAIL.
- [ ] Step 3 — add `".flac": "audio/flac"` (and ensure the handler uses the helper).
- [ ] Step 4 — run test + `uv run python -c "import app.api.media_download_router"`.
- [ ] Step 5 — commit `fix(soda): serve .flac with audio/flac content-type`.

---

## Task 7: Skip-already-downloaded guard in `soda_download_workflow`

**Files:** Modify `backend/app/workflows/soda_download.py`; Test extend `backend/tests/soda/test_soda_download_workflow.py`.

Add a pure helper `already_downloaded(media_row, base_dir) -> bool` (true if `music_download_path` set AND the file exists on disk) and short-circuit the workflow (still mark task complete) when true.

- [ ] Step 1 — failing test for the helper:
```python
def test_already_downloaded_true(tmp_path):
    from app.workflows.soda_download import already_downloaded
    f = tmp_path / "a.flac"; f.write_bytes(b"x")
    row = {"music_download_path": "a.flac"}
    assert already_downloaded(row, str(tmp_path)) is True


def test_already_downloaded_false_no_path():
    from app.workflows.soda_download import already_downloaded
    assert already_downloaded({"music_download_path": None}, "/tmp") is False


def test_already_downloaded_false_missing_file(tmp_path):
    from app.workflows.soda_download import already_downloaded
    assert already_downloaded({"music_download_path": "nope.flac"}, str(tmp_path)) is False
```
- [ ] Step 2 — run, FAIL.
- [ ] Step 3 — implement `already_downloaded` + in the workflow, after resolving `media_id`/row, if `already_downloaded(row, base_dir)`: `await manager.complete(wf_id, subtitle="Already downloaded")` and return early (don't re-download). Keep route-C discipline.
- [ ] Step 4 — run helper tests + soda suite + import check.
- [ ] Step 5 — commit `feat(soda): skip re-download when file already present`.

---

## Task 8: Frontend `isAudioType` helper

**Files:** Modify `frontend/utils/awemeType.ts`.

- [ ] Add:
```typescript
export function isAudioType(mediaType?: string): boolean {
  return mediaType === 'audio';
}
```
- [ ] Verify `npx tsc --noEmit` clean for the file. Commit `feat(soda): isAudioType helper`.

---

## Task 9: Lyrics tab + hide video tabs for audio

**Files:** Modify `frontend/components/VideoDetailPanel.tsx`.

- [ ] Add `'lyrics'` to `TabKey` (line 40) and a tab entry `{ key: 'lyrics', label: 'Lyrics', icon: <Music size={16} /> }` (import `Music` from lucide-react).
- [ ] Compute `const isAudio = isAudioType(video.media_type)` and a `visibleTabs`: for audio → `['overview','lyrics']`; else → existing tabs (NO lyrics for video). Render `visibleTabs` instead of `tabs`.
- [ ] Add a `lyrics` content branch that renders the `<SodaLyricsTab mediaId={video.id} />` component (Task 11).
- [ ] `npx tsc --noEmit` clean. Commit `feat(soda): Lyrics tab + hide video tabs for audio`.

---

## Task 10: Wire `AudioWaveformPlayer` for audio in the detail page

**Files:** Modify `frontend/pages/DownloadDetailPage.tsx` (player area ~264-317).

- [ ] Add an `isAudio` branch in the player-area conditional: when `isAudioType(video.media_type)` and a downloaded audio path exists, render:
```tsx
<AudioWaveformPlayer
  src={`${API_BASE}/api/v1/download/${video.platform_id}/music`}  // match the real audio-serving route + auth
  filename={video.music_name || video.title || 'Audio'}
  duration={Number(video.duration) || undefined}
/>
```
  (Read the real audio endpoint path + how other media fetch authed file URLs — match the existing pattern, e.g. an authed blob fetch if the endpoint needs a Bearer token. If auth headers are needed, follow how the existing audio download does it.)
- [ ] If no audio file yet, show a small "Download to play" placeholder.
- [ ] `npx tsc --noEmit` clean. Commit `feat(soda): inline audio player on detail page`.

---

## Task 11: Lyrics tab component (fetch + render)

**Files:** Create `frontend/components/SodaLyricsTab.tsx`; Service add to a media service (`frontend/services/`).

- [ ] Add a service fn `getMediaLyrics(mediaId)` → `GET /api/v1/media/{id}/lyrics` (authed) → `{lrc, lines}`.
- [ ] `SodaLyricsTab`: on mount fetch lyrics; render the `lines[].text` in a scrollable list (one line per row). Loading + empty ("No lyrics available") states. (Time-sync-to-playback is deferred polish — just render lines for the core slice.)
- [ ] `npx tsc --noEmit` clean. Commit `feat(soda): Lyrics tab component`.

---

## Final
- [ ] `cd backend && uv run pytest tests/soda/ -q` all green; `uv run pytest -k "media or parse or cookie" -q` no regression; `uv run pytest --collect-only` clean.
- [ ] black + isort + ruff clean; `cd frontend && npx tsc --noEmit` no NEW errors; `npm run build` clean.
- [ ] `/ship` → PR to master. **After merge, verify migration 248 applied + PostgREST reloaded** (the column is visible to REST) before relying on metadata reads/writes.
- [ ] ⚠️ Still pending the real-API smoke (carried from Phase 2/3): the `track.lyric` shape (Task 4) is the highest-risk assumption — confirm against a real response.

## Self-Review
- Scope = core slice per decision: Lyrics tab (Tasks 3,4,5,9,11), audio player (Tasks 8,10), hide video tabs (Task 9), lyrics persistence (Tasks 1,2,4), .flac (Task 6), skip-already-downloaded (Task 7). Deferred (noted): ffmpeg repackage/transcode, quality dropdown, lyric time-sync-to-playback, rich-metadata UI display beyond stats card (stats already shown).
- Migration safety: additive + IF NOT EXISTS + rollback + NOTIFY pgrst reload (the column-visibility gotcha).
- Consistency: `metadata.lyrics = {lrc, lines}` shape identical across formatter (Task 4), endpoint (Task 5), FE service+tab (Task 11). `isAudioType` used in Tasks 9+10. Audio endpoint path must be matched from the real router in Tasks 6/10/5.
- Risk flagged: `track.lyric` real shape (Task 4) + the unverified real-API path — instruct the implementer to confirm §A.7 / real response and adapt.
