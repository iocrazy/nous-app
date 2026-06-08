# Uploaded-Audio Detail Parity — Design Spec

Date: 2026-06-07
Branch: `feature/audio-detail-parity`
Status: Awaiting user review

## Problem

The **uploaded-audio** detail page (`ResourceDetailPage.tsx`) is wrong/incomplete compared to the **downloaded-audio** detail page (`VideoDetailPanel` + `MediaCard`):

| | Uploaded (now) | Downloaded (target) |
|--|--|--|
| Type badge | `AUDIO 500×500` (resolution — meaningless for audio) | `AUDIO 324kbps` (bitrate) |
| Cover | placeholder only, no way to set one | cover art + placeholder |
| Lyrics | none | Lyrics tab (synced) |
| Tabs | Overview / Review / Transcript / Analyze | Overview / Lyrics |

Goal: bring the uploaded-audio detail page to parity — correct bitrate badge, a user-settable cover, a Lyrics tab fed by an uploaded `.lrc` file, and a trimmed tab set.

Discriminator: `resource.source_type` = `'upload' | 'web'`. This feature targets `source_type === 'upload'` AND audio mime type.

## Decisions (confirmed with user)

| # | Decision |
|---|----------|
| Bitrate | Extract via `ffprobe` on audio upload, store in a new `resources.audio_bitrate_kbps` column. New uploads only (no backfill); badge shows bitrate when present, else just `AUDIO`. |
| Lyrics input | User uploads a `.lrc` file → backend parses timestamps → stored; display is timeline-synced (reuse the synced renderer). |
| Lyrics storage | New `resources.lyrics_json` jsonb, shape `{ lrc: string, lines: [{ text, line_start_ms }] }` (same shape SodaLyricsTab consumes). |
| Cover | Reuse existing resource upload storage; set the existing-but-unused `resources.cover_image_path`. |
| Tabs | For uploaded audio: show only Overview + Lyrics. |

## Non-goals (YAGNI)

- No bitrate backfill for existing uploads (only new uploads get probed; optional re-probe deferred).
- No in-browser LRC text editor — input is `.lrc` file upload only.
- No lyrics auto-generation / transcription-to-lyrics.
- No change to the downloaded-audio path (it already works); we only make the lyrics renderer source-agnostic so uploaded audio can reuse it.
- No cover for video/image uploads in this pass (audio detail page only; the cover endpoint is generic but the UI affordance is on the audio detail view).

## Data model

New columns on `resources` (one migration):
- `audio_bitrate_kbps integer NULL` — kbps, set on audio upload.
- `lyrics_json jsonb NULL` — `{ lrc, lines: [{ text, line_start_ms }] }`.
(`cover_image_path text` already exists — reused, no migration.)

Add both to `frontend/types.ts` `Resource`.

## Backend

### Upload pipeline — bitrate
On resource upload, when the file is audio (mime `audio/*`), run `ffprobe` to read the audio stream bitrate (fallback to format bitrate), convert to kbps, persist to `audio_bitrate_kbps`. ffprobe is already available in the backend media toolchain. Failure to probe is non-fatal — leave the column NULL and log.

### Cover upload
`POST /api/v1/resources/{id}/cover` — multipart image. Validates ownership + image mime, stores the file via the same storage path resources already use, sets `resources.cover_image_path`, returns the updated resource. Ownership/permission consistent with existing resource mutation endpoints.

### Lyrics upload + read
- `POST /api/v1/resources/{id}/lyrics` — multipart `.lrc` (or text/plain LRC body). Backend parses LRC (`[mm:ss.xx] line` → `{ text, line_start_ms }`, sorted; tolerate metadata tags `[ar:]/[ti:]` etc. and multi-timestamp lines), stores `{ lrc, lines }` into `lyrics_json`, returns it.
- `GET /api/v1/resources/{id}/lyrics` — returns `lyrics_json` (or 404/empty).
- LRC parser lives in a small focused module (`backend/app/services/lrc_parser.py`) with unit tests (the parsing is the correctness-sensitive bit).

## Frontend

### Badge (`ResourceDetailPage.tsx` ~1257)
For audio: render `{audio_bitrate_kbps}kbps` when present, else just the `AUDIO` type badge. Never render `resolution` for audio. Non-audio unchanged.

### Tabs (`ResourceDetailPage.tsx` ~1144-1195)
When `source_type === 'upload'` and audio: tab set = `[Overview, Lyrics]`. Otherwise unchanged (the existing Review/Transcript/Analyze gating stays for non-audio / web).

### Lyrics renderer (shared display, separate storage)
**Storage stays separate** — the downloaded path keeps reading `parsed_media.metadata.lyrics` via the existing soda fetch; only the uploaded path uses `resources.lyrics_json`. We do NOT migrate or touch the working download lyrics store.

**Display is shared** — extract the synced-highlight rendering (takes `{ lrc, lines }` + `currentTime` → highlights/scrolls the active line) into a new dumb `LyricsView` component. `SodaLyricsTab` keeps its soda-fetch responsibility and feeds the fetched data to `LyricsView`; the uploaded-audio Lyrics tab reads `resource.lyrics_json` and feeds it to `LyricsView`. Fetch and render are cleanly separated; no duplicated rendering code.

### Cover + LRC upload affordances
On the uploaded-audio detail view: an "Upload cover" action (image picker → `POST .../cover` → refresh) and, in the Lyrics tab when empty, an "Upload .lrc" action (file picker → `POST .../lyrics` → refresh). i18n keys (en/zh).

### Service layer
`resourceService.ts`: `uploadResourceCover(id, file)`, `uploadResourceLyrics(id, file)`, `getResourceLyrics(id)`.

## Error handling
- Bad/empty LRC → 400 with a clear message; UI toasts it, tab stays in empty state.
- Non-image cover / non-audio cover target → 400.
- ffprobe failure on upload → non-fatal, column NULL, badge falls back to `AUDIO`.
- All mutations ownership-gated like sibling resource endpoints.

## Testing
- Backend: `lrc_parser` unit tests (timestamps, multi-timestamp lines, metadata tags, malformed input); cover/lyrics endpoint tests (ownership, mime validation, persisted shape) with mocked storage.
- Frontend: `SodaLyricsTab`/`LyricsView` renders + syncs from passed data; badge picks bitrate-vs-nothing for audio; tab set for uploaded audio = Overview+Lyrics. Service tests mock fetch.

## Resolved decisions (confirmed during review)
1. Bitrate for existing uploads: leave NULL, no backfill. ✓
2. Lyrics storage: `resources.lyrics_json` column for uploads only. Downloads keep `parsed_media.metadata.lyrics` — storage is NOT unified, only the display component is shared. ✓
3. Extract a shared `LyricsView` dumb renderer; `SodaLyricsTab` keeps soda-fetch and feeds it. ✓
