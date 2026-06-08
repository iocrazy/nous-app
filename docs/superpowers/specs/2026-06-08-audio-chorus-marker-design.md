# Uploaded-Audio Chorus Marker — Design Spec

Date: 2026-06-08
Branch: `feature/audio-chorus-marker`
Status: Awaiting user review

## Problem

The audio detail views already **render** a chorus (highlight) marker — `AudioWaveformPlayer` draws a clickable amber line at `chorusStartSec` (click-to-seek). For **downloaded** audio the position comes from platform metadata (`parsed_media.metadata.chorus.start`, ms). **Uploaded** audio has no platform source, no way to set the point, and `ResourceDetailPage` doesn't even pass `chorusStartSec` to `AudioHero` — so uploaded audio shows no marker at all.

Goal: let the user **set / clear** a chorus point on uploaded audio, persist it, and display it with the **same** amber marker the download view uses.

Discriminator: `resource.source_type === 'upload'` AND audio mime type. Downloads are out of scope.

## Decisions (confirmed with user)

| # | Decision |
|---|----------|
| Scope | Uploaded audio only. Downloads keep platform-derived chorus; `DownloadDetailPage` + `MobileAudioScreen` are untouched. |
| Interaction | Button-based, located in the playback (waveform) area. **No chorus set → "Set chorus" button** (stamps the current playhead). **Chorus set → marker + "Clear" button**. No waveform click/drag editing (keeps the marker's look identical to download). |
| Display | The marker renders exactly like the download view — the existing `AudioWaveformPlayer` amber line, click-to-seek preserved. "Set then it shows, unset then it's gone." |
| Mobile | Covered automatically. Uploaded audio uses one `AudioHero` on both desktop and mobile, and `AudioWaveformPlayer` renders on mobile (no `sm:hidden`). Putting the buttons in `AudioWaveformPlayer` covers mobile with no separate mobile work. |
| Storage | New discrete column `resources.chorus_start_ms` (consistent with PR #547's `audio_bitrate_kbps` / `lyrics_json`), not a jsonb blob. |
| Backend | A dedicated `PUT /api/v1/resources/{id}/chorus` endpoint (clean set+clear semantics + `check_media_access` gating, matching the cover/lyrics endpoints), not an extension of the shared `PATCH /resources/{id}` (whose `exclude_none` dump can't express "clear to null"). |

## Non-goals (YAGNI)

- No editing of downloaded-audio chorus (out of scope).
- No waveform click/drag to position the marker — button at playhead + Clear only.
- No `resource_versions` mirror of `chorus_start_ms` (it is set post-upload via the endpoint, never through the upload `**metadata` spread, so no 42703 risk).
- No chorus *duration* / region — only a single start point (mirrors what the marker renders).
- No auto-detection of the chorus.

## Data model

New column on `resources` (migration `273_resource_chorus_start.sql`):
- `chorus_start_ms integer NULL` — chorus start in milliseconds (same unit as `metadata.chorus.start`). `NULL` = no chorus.
- `NOTIFY pgrst, 'reload schema';` at the end (PostgREST visibility).

Add `chorus_start_ms?: number | null` to the three Resource-shaped interfaces in `frontend/types.ts` (the `Resource` type plus its two siblings — the same three that carry `audio_bitrate_kbps`).

## Backend

### Endpoint
`PUT /api/v1/resources/{id}/chorus` — JSON body `{ "chorus_start_ms": number | null }`.

Flow (mirrors `POST /{id}/cover` / `/{id}/lyrics`):
1. `check_media_access(resource_id, auth.user_id, None)` → 403 on failure (the Supabase REST repo runs as service_role and does not self-gate).
2. Load resource; 404 if missing.
3. Validate it is an **audio** resource (`mime_type` starts with `audio/`) **and** `source_type == 'upload'` → else 400.
4. Validate `chorus_start_ms` is `None` or an `int >= 0` → else 400.
5. `repo.update_resource(resource_id, {"chorus_start_ms": value})` (value may be `None` to clear).
6. Return `{ "success": True, "data": <updated resource> }`.

Request schema: a small Pydantic model `ChorusUpdate(BaseModel)` with `chorus_start_ms: Optional[int] = Field(None, ge=0)`. Because "clear" must send an explicit `null`, the handler reads `data.chorus_start_ms` directly (do **not** `exclude_none`).

## Frontend

### `AudioWaveformPlayer` (shared — additive, default-off)
New optional props:
- `chorusEditable?: boolean`
- `onChorusChange?: (sec: number | null) => void`

`AudioWaveformPlayer` has two layouts: `full` (default) and `compact`. `AudioHero`
passes no `layout`, so uploaded audio renders the **`full`** layout on **both
desktop and mobile**; the `compact` layout is used only by `MobileAudioScreen`
(download mobile player, out of scope). Therefore the chorus controls are added to
the **`full` layout's bottom control bar only** — the compact layout is left
unchanged.

Behavior when `chorusEditable` is true (else: today's read-only marker, unchanged):
- **No chorus** (`chorusStartSec` undefined): the bottom control bar shows a small "Set chorus" button → calls `onChorusChange(currentTime)` (the player owns `currentTime`; if playback hasn't started, `currentTime` is `0`, which stamps the very start — acceptable, no special-casing).
- **Chorus set**: the existing amber marker renders **plus** a "Clear" button in the bottom control bar → calls `onChorusChange(null)`.

`DownloadDetailPage` / `MobileAudioScreen` pass neither prop → no change.

### `AudioHero` (pass-through)
Forward `chorusEditable` and `onChorusChange` to `AudioWaveformPlayer` (it already forwards `chorusStartSec` and `onTimeUpdate`).

### `ResourceDetailPage` (uploaded audio only)
- Pass `chorusStartSec={resource.chorus_start_ms != null ? resource.chorus_start_ms / 1000 : undefined}` to `AudioHero` (currently passes nothing → also fixes "uploaded audio shows no marker").
- Pass `chorusEditable={isUploadedAudio}` and an `onChorusChange` handler:
  - set: `setResourceChorus(resource.id, Math.round(sec * 1000))` → `setResource(updated)` + success toast.
  - clear (`sec === null`): `setResourceChorus(resource.id, null)` → `setResource(updated)` + toast.
  - on failure: error toast, keep previous value (pessimistic update — await endpoint, then `setResource`).
- i18n keys (en/zh): `resources.detail.{setChorus, clearChorus, chorusSet, chorusCleared}`.

### Service layer
`resourceService.ts`: `setResourceChorus(resourceId: string, chorusMs: number | null): Promise<Resource>` — `PUT .../chorus` with JSON body, auth headers, returns the updated resource (unwrap `data`).

## Error handling
- Non-audio / non-upload target → 400; UI toasts and leaves state unchanged.
- Negative / non-integer ms → 400.
- Endpoint failure (network/500) → error toast, marker keeps its previous value.
- All mutations ownership-gated via `check_media_access` (403).

## Testing
- **Backend** (`backend/tests/test_resource_chorus_endpoint.py`, mirrors `test_resource_media_endpoints.py`): ownership 403; non-upload or non-audio 400; negative ms 400; successful set persists `chorus_start_ms`; clear with `null` nulls the column.
- **Frontend**:
  - `AudioWaveformPlayer`: with `chorusEditable` and no chorus, the "Set chorus" button calls `onChorusChange` with the current time; with a chorus, "Clear" calls `onChorusChange(null)`; without `chorusEditable`, neither button renders (read-only parity with download).
  - `ResourceDetailPage` (or a focused harness): the set handler calls `setResourceChorus` with `round(sec*1000)`; clear calls it with `null`.
  - `resourceService.setResourceChorus`: mocks fetch, asserts method/URL/body and returns unwrapped resource.

## Files

- Create: `supabase/migrations/273_resource_chorus_start.sql`
- Modify: `frontend/types.ts` (3 Resource-shaped interfaces)
- Modify: `backend/app/schemas/resources.py` (add `ChorusUpdate`)
- Modify: `backend/app/api/resources_crud_router.py` (add `PUT /{id}/chorus`)
- Create: `backend/tests/test_resource_chorus_endpoint.py`
- Modify: `frontend/components/AudioWaveformPlayer.tsx` (editable buttons)
- Modify: `frontend/components/AudioHero.tsx` (pass-through props)
- Modify: `frontend/components/ResourceDetailPage.tsx` (wire chorus display + edit)
- Modify: `frontend/services/resourceService.ts` (`setResourceChorus`)
- Create: `frontend/components/AudioWaveformPlayer.chorus.test.tsx` (or extend an existing test)
- Modify: `frontend/public/locales/{en,zh}.json` (4 keys)
- Modify: `frontend/package.json` (version bump on ship)
