# Uploaded-Audio Mobile Immersive Parity — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use `- [ ]`.

**Goal:** On mobile, uploaded audio gets the immersive layout (parity with the download `MobileAudioScreen`), by extracting a shared `MobileAudioShell`.

**Architecture:** Extract a presentational `MobileAudioShell` from the LOCKED `MobileAudioScreen`; `MobileAudioScreen` becomes a thin `Video` adapter (output must stay identical, guarded by a characterization test added first); a new upload adapter maps `Resource` → shell and `ResourceDetailPage` renders it on mobile-upload-audio. Decouple `LyricsOverlay` to accept `lines`. Parse artist from filename.

**Tech Stack:** React 19 + TS + Vite + vitest.

**Spec:** `docs/superpowers/specs/2026-06-08-upload-audio-mobile-immersive-design.md`

**Branch:** `feature/upload-audio-mobile-immersive` (already created; spec committed).

---

### Task 1: `parseArtistTitle` util (pure, isolated)

**Files:** Create `frontend/utils/parseArtistTitle.ts`, `frontend/utils/parseArtistTitle.test.ts`

- [ ] **Step 1: Failing test**
```ts
import { describe, it, expect } from 'vitest';
import { parseArtistTitle } from './parseArtistTitle';
describe('parseArtistTitle', () => {
  it('splits "Artist - Title.mp3"', () => {
    expect(parseArtistTitle('Biboulakis - Is That Too Much to Ask (feat. Nina Zeitlin).mp3'))
      .toEqual({ artist: 'Biboulakis', title: 'Is That Too Much to Ask (feat. Nina Zeitlin)' });
  });
  it('no " - " → title only', () => {
    expect(parseArtistTitle('just-a-song.mp3')).toEqual({ title: 'just-a-song' });
  });
  it('splits on the FIRST " - " only', () => {
    expect(parseArtistTitle('A - B - C.wav')).toEqual({ artist: 'A', title: 'B - C' });
  });
  it('does not split a date-like stem with no spaces', () => {
    expect(parseArtistTitle('2024-01-01.m4a')).toEqual({ title: '2024-01-01' });
  });
  it('handles no extension', () => {
    expect(parseArtistTitle('X - Y')).toEqual({ artist: 'X', title: 'Y' });
  });
});
```
- [ ] **Step 2: Run → fail** (`cd frontend && npx vitest run utils/parseArtistTitle.test.ts`)
- [ ] **Step 3: Implement**
```ts
export interface ArtistTitle { artist?: string; title: string; }
const AUDIO_EXT = /\.(mp3|m4a|wav|flac|aac|ogg|opus|wma|aiff?)$/i;
export function parseArtistTitle(filename: string): ArtistTitle {
  const stem = (filename || '').replace(AUDIO_EXT, '').trim();
  const i = stem.indexOf(' - ');
  if (i < 0) return { title: stem };
  const artist = stem.slice(0, i).trim();
  const title = stem.slice(i + 3).trim();
  if (!artist || !title) return { title: stem };
  return { artist, title };
}
```
- [ ] **Step 4: Run → pass**
- [ ] **Step 5: Commit** `feat(audio): parseArtistTitle filename helper`

---

### Task 2: Characterization test for download `MobileAudioScreen` (regression net — BEFORE any extraction)

**Files:** Create `frontend/components/MobileAudioScreen.characterization.test.tsx`

**Context:** No test exists for this LOCKED component. Lock its structure now so the Task 4 extraction is verified non-regressing. Stub the heavy children/services so it mounts: `vi.mock('./AudioWaveformPlayer', ...)→null`, `vi.mock('../services/lyricsService', () => ({ getMediaLyrics: vi.fn().mockResolvedValue({ lines: [] }) }))`, `vi.mock('../services/resourceService')`, `vi.mock('../services/unifiedTagService')`, `vi.mock('./EagleTagPicker', ...)→null`. Stub `ResizeObserver`/`AudioContext` if needed (see `AudioWaveformPlayer.chorus.test.tsx` for the pattern, though the player is mocked here).

- [ ] **Step 1: Write the test** — render `MobileAudioScreen` with a representative `video` (`{ id, title:'Song', music_name:null, like_count:1000, comment_count:50, share_count:0, favorite_count:9000, source_platform:'qishui', original_url:'https://x' }`), `src='blob:a'`, `hasAudio`, and the download handlers (`onDownloadAudio`, `onDelete`, `onCopyLink`). Assert the structural invariants:
  - title text `'Song'` present
  - visible social stats render the non-zero ones (like/comment/collect) and NOT the zero share — i.e. `formatCount` outputs `1.0K`, `9.0K` present; assert share stat (value 0) is filtered out
  - speed chip `'1x'` present
  - opening the ⋮ menu (click "More actions") shows "Download Audio" and "Delete"
- [ ] **Step 2: Run → PASS** (against current code) `cd frontend && npx vitest run components/MobileAudioScreen.characterization.test.tsx`
- [ ] **Step 3: Commit** `test(audio): characterization test for MobileAudioScreen (pre-refactor net)`

---

### Task 3: Decouple `LyricsOverlay` to accept `lines`

**Files:** Modify `frontend/components/LyricsOverlay.tsx`, Create `frontend/components/LyricsOverlay.test.tsx`

**Context:** Today `LyricsOverlay` is `mediaId`-only and renders `SodaLyricsTab(mediaId)`. Add optional `lines?: LyricLine[]`. When `lines` is provided, render them directly (reuse the existing `LyricsView` dumb renderer — created in PR #547) and skip the mediaId fetch + qishui "Fetch Lyrics" affordance. When absent, behavior is unchanged (download path). Read the current file first; keep all existing props/behavior intact.

- [ ] **Step 1: Failing test** — `LyricsOverlay` with `lines={[{text:'la', line_start_ms:0}]}` and no `mediaId` renders `'la'` (via `LyricsView`); with `mediaId` and no `lines`, it still renders the `SodaLyricsTab` path (mock `SodaLyricsTab`→ a marker, assert marker present). Mock `getMediaLyrics`.
- [ ] **Step 2: Run → fail**
- [ ] **Step 3: Implement** — add `lines?: LyricLine[]` to `LyricsOverlayProps`; in the body, when `lines?.length` (or `lines` provided and no mediaId), render `<LyricsView lines={lines} currentTime={currentTime} variant=.../>` instead of `<SodaLyricsTab mediaId=.../>`, and hide the Fetch-Lyrics button. Keep `mediaId` optional now (`mediaId?: string`).
- [ ] **Step 4: Run → pass**
- [ ] **Step 5: Commit** `feat(audio): LyricsOverlay accepts passed-in lines (upload lyrics)`

---

### Task 4: Extract `MobileAudioShell`; make `MobileAudioScreen` a thin adapter

**Files:** Create `frontend/components/MobileAudioShell.tsx`, `frontend/components/MobileAudioShell.test.tsx`; Modify `frontend/components/MobileAudioScreen.tsx`

**Context:** Move the immersive render (cover → title → optional artist → lyric couplet → stats+actions row → tags → waveform color-block → rating/notes) out of `MobileAudioScreen` into `MobileAudioShell` with the normalized props from the spec. Generalize the three couplings:
1. **Lyrics:** shell takes `lyricLines: LyricLine[]` + `onReloadLyrics?` (no `getMediaLyrics` inside); the couplet/active-line math (lines 219-232 of MobileAudioScreen) moves into the shell operating on `lyricLines`. The full-screen overlay: shell renders `<LyricsOverlay lines={lyricLines} mediaId={overlayMediaId} .../>` — pass `overlayMediaId` through (download sets it, upload omits → overlay uses `lines`).
2. **Social stats:** shell takes `social?: {likes?;comments?;shares?;collects?;onCopyLink?}`; build the `stats[]`/`visibleStats` filter inside the shell from `social` (empty/absent → left side empty; speed + ⋮ stay).
3. **⋮ menu:** shell takes `menuItems: Array<{key,label,Icon,color?,onClick}>` + optional `extraMenu?: React.ReactNode` (for the download-only Share / Open-Original / Delete block) OR fold those into `menuItems` from the adapter. Prefer: adapter builds the FULL `menuItems` list (incl. Delete) and shell just renders them with a divider rule. Keep the rendered menu DOM equivalent for download.
4. **Artist line:** shell renders an optional artist `<p>` under the title when `artist` present (download omits → unchanged).
5. **Top bar:** NOT in the shell (caller renders it).

`MobileAudioScreen` keeps its `Video` props + lyrics fetch (`getMediaLyrics`) + asset-menu building + tag wiring, and renders `<MobileAudioShell .../>` mapping `video` → the view-model. **The characterization test (Task 2) MUST stay green** — that is the acceptance gate.

- [ ] **Step 1:** Write `MobileAudioShell.test.tsx` first: renders `title`; renders `artist` when given, none when absent; left social empty when `social` absent but speed chip `'1x'` present; renders provided `menuItems` labels when ⋮ opened; renders `noAudioPrompt` when `hasAudio={false}`. (Mock `AudioWaveformPlayer`, `LyricsOverlay`, `EagleTagPicker`, services as in Task 2.)
- [ ] **Step 2:** Run → fail (shell doesn't exist).
- [ ] **Step 3:** Create `MobileAudioShell.tsx` by moving the JSX from `MobileAudioScreen` and parameterizing per above. Then rewrite `MobileAudioScreen` to map `video`→view-model and render the shell.
- [ ] **Step 4:** Run BOTH `MobileAudioShell.test.tsx` AND `MobileAudioScreen.characterization.test.tsx` → all green. If the characterization test breaks, the extraction changed download output — fix until identical.
- [ ] **Step 5:** Commit `refactor(audio): extract MobileAudioShell; MobileAudioScreen becomes adapter (output identical)`

---

### Task 5: Upload adapter + `ResourceDetailPage` mobile gate + i18n

**Files:** Create `frontend/components/MobileAudioUpload.tsx`; Modify `frontend/components/ResourceDetailPage.tsx`, `frontend/public/locales/{en,zh}.json`

**Context:** `MobileAudioUpload` maps a `Resource` → `MobileAudioShell`:
- `coverUrl` = `resource.cover_image_path` ? `getResourceCoverUrl(id, undefined, updated_at)` : undefined (themed placeholder).
- `{ artist, title } = parseArtistTitle(resource.filename)`.
- `social = undefined` (left stats empty).
- `lyricLines = resource.lyrics_json?.lines ?? []`; `onReloadLyrics` re-fetches the resource (reuse existing `setResource`).
- chorus: `chorusStartSec = chorus_start_ms!=null ? /1000 : undefined`, `chorusEditable`, `onChorusChange` → `setResourceChorus` (same as PR #550).
- `menuItems`: Replace cover (hidden `<input type=file accept=image/*>` → `uploadResourceCover` → refresh), Upload .lrc (`<input accept=.lrc>` → `uploadResourceLyrics` → refresh), Copy link (`resource.url` if present), Delete (existing trash handler). Reuse existing `ResourceDetailPage` services/handlers + `addToast`.
- rating/notes/tags: pass `resourceId=resource.id` + existing rating/notes wiring.
- Render a minimal top bar: a back arrow (`navigate(-1)`), no author.

In `ResourceDetailPage`: when `!isDesktop && isAudio && resource.source_type === 'upload'`, render `<MobileAudioUpload resource={resource} ... />` for the whole detail body instead of the current preview+inspector layout. Desktop + non-audio + non-upload unchanged. (Use the existing `isDesktop` state; mobile = `!isDesktop`.)

i18n (`resources.detail`): add `replaceCover`, `uploadLyrics`, `copyLink`, `delete` (reuse existing keys if already present — check first).

- [ ] **Step 1:** Add i18n keys (en+zh).
- [ ] **Step 2:** Create `MobileAudioUpload.tsx` per above.
- [ ] **Step 3:** Wire the `ResourceDetailPage` mobile gate.
- [ ] **Step 4:** `cd frontend && npx tsc --noEmit` (clean on touched files) + `npx vitest run` (all pass).
- [ ] **Step 5:** Commit `feat(resources): immersive mobile layout for uploaded audio`

---

### Task 6: Verify + version bump

- [ ] **Step 1:** `cd frontend && npx vitest run` (all green) + `npx tsc --noEmit` (clean).
- [ ] **Step 2:** Bump `frontend/package.json` version (patch).
- [ ] **Step 3:** Commit `chore: bump version (uploaded-audio mobile immersive)`

---

## Notes for the executor
- **Regression gate:** `MobileAudioScreen.characterization.test.tsx` must stay green through Task 4 — it is the only guard on the LOCKED download layout.
- **Scope guard:** do NOT change `DownloadDetailPage`, desktop layouts, video, or image paths. Mobile + uploaded-audio only.
- **Frontend-only:** ships via Vercel (unaffected by the current GitHub Actions billing block on backend deploys).
