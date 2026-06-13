# Island Redesign v2 — P4: Audio Detail Page (cover-tint stage + cover/lyrics two-column + playlist island)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Prerequisite:** P3 (#700) merged — `DownloadDetailPage` is island-aware (`islandDesktop = islandUI() && !isMobile`), the island info-island infra (`IslandWorkContext` callback-ref `infoIslandEl`, `IslandShell` splitter/info-island/reopen, 54px `iconRail`) and `CometBack` are on master and reused. Flag `VITE_FEATURE_ISLAND_UI` stays **OFF in prod** through P4.

**Goal:** Redesign the **audio** detail page (the `isAudio` branch of `DownloadDetailPage`) into the spec's immersive cover-tinted island layout — a cover-color-tinted **stage** with a two-column left page (cover-side Overview | synced lyrics column) and a bottom play capsule, plus a **Playlist island (COMING SOON)** on the right — matching `docs/design/mockups/mediahub-audio-redesign.html`, with zero feature change (D12). Scope chosen by the user: **full faithful mockup redesign.**

**Architecture (audio-only island branch; video path from P3 unchanged):** In island desktop mode, when `isAudio`, `DownloadDetailPage` renders a new **`AudioStageIsland`** filling the work island instead of the P3 video stage, and portals a new **`PlaylistIsland`** (COMING SOON stub) into `infoIslandEl` instead of `VideoDetailPanel`. `AudioStageIsland` is **composition over existing parts** (no playback/lyrics reimplementation): cover-side column = the existing `MediaCard` (audio Overview — cover/title/author/3-stat/rating/notes/tags/platform-tags/description, already D12-complete); lyrics column = the existing `LyricsView` (synced); bottom capsule = the existing `AudioWaveformPlayer` (play/seek/volume/speed/chorus engine) wrapped in the tinted capsule chrome. A new **`coverTint`** util canvas-samples the cover's dominant color → `--tint`/`--tint-deep` CSS vars (fallback: existing `sodaTheme` colors, else indigo). The whole audio branch is `islandDesktop && isAudio`-gated; classic desktop + the entire mobile path (`MobileAudioScreen`) are byte-identical (D12).

**D12 field mapping (audio Overview has NO Transcript/Analysis tabs — only Overview + Lyrics today):**
- Overview (`MediaCard` audio) → **cover-side column** (scrollable; keeps every field: cover, type/badge, ID, title, author, release/duration, 3-stat grid + bitrate tag, share-card copy, rating, notes blur-save, tags add/remove colored, platform hashtags, description, AI intent badges + Copy/Transcript/Summary/Analyze actions + AI results, collections, more menu).
- Lyrics (`SodaLyricsTab`/`LyricsView`) → **lyrics column** (synced highlight, Fetch Lyrics, copy, auto-scroll).
- Right info island → **Playlist (COMING SOON)** stub.
- Nothing dropped.

**Tech Stack:** React 19 + TS, Canvas 2D (`getImageData`) for tint, `react-dom` `createPortal`, Tailwind v4 island tokens, P2/P3 island infra, vitest + @testing-library/react.

**Hard constraints (spec):**
- **D12 zero feature change** — every audio interaction in the checklist works identically; flag-OFF AND the entire mobile audio path (`MobileAudioScreen`) render today's DOM.
- **D8** cover-tint immersion (`--tint`/`--tint-deep`), **D10** comet back (tinted variant), **D7** info panel collapsible (shell-owned), **D4** no emoji, density not reduced.
- tsc baseline (currently **126**) must not increase; vitest stays green (currently **957**); new code uses `ink-*`/semantic/`--tint` only (no `*-zinc-*`, CI guard).
- Commit each task; flag OFF in prod.

**Out of scope:** P5 pages; a *functional* playlist (the island is an explicit COMING SOON stub — no queue data/behavior); changing playback/lyrics/download logic (`AudioWaveformPlayer`/`SodaLyricsTab`/`lyricsService` internals untouched — reused as-is); mobile redesign (`MobileAudioScreen` stays — island is `!isMobile`/`hidden sm:flex`); video detail (P3 unchanged).

---

## D12 Feature-Parity Checklist (acceptance baseline — re-verify with flag ON)

Audio inventory (route `/team/:teamId/player/:displayId`, `isAudio` branch of `DownloadDetailPage`): stage today = `AudioHero`(242L) wrapping `AudioWaveformPlayer`(583L); panel = `VideoDetailPanel`→`MediaCard`(1000L+, audio mode) Overview + `SodaLyricsTab`(218L)/`LyricsView`(110L) Lyrics tab; mobile = `MobileAudioScreen`(269L)→`MobileAudioShell`; lyrics via `lyricsService` (`GET/POST /api/v1/media/{id}/lyrics`); colors via `sodaTheme.ts` (`buildSodaTheme` metadata / `randomThemeFromSeed` hash). **No canvas color extraction today.**

- [ ] **Playback (`AudioWaveformPlayer`)**: play/pause, seek/drag, volume, duration + current time, playback rate, waveform render, chorus marker (`metadata.chorus.start`), `onTimeUpdate`. (Reused verbatim — must keep working inside the capsule.)
- [ ] **Cover**: `getCoverUrl(video, mediaToken)` image + Music-icon placeholder; load-error hide.
- [ ] **Lyrics**: synced active-line highlight (`line_start_ms/1000 <= currentTime`), auto-scroll, Fetch Lyrics (qishui only, POST), copy, loading/error/empty states.
- [ ] **Overview (`MediaCard` audio)**: type badge + "Audio" label, ID, title, author + platform icon, release date + duration, 3-stat grid (Comments/Shares/Collects) + bitrate tag, share-card copy-link, 5-star rating, notes (blur-save), tags (add/remove colored chips + create via EagleTagPicker), platform hashtags, description, AI intent badges, Copy/Transcript/Summary/Analyze actions, AI results, collections toggle, more menu.
- [ ] **Mobile audio (`MobileAudioScreen`, `isMobile && isAudio`)**: locked gradient layout, cover, title, lyric couplet→overlay, social stats + speed + ⋮ asset menu, tags, waveform, rating/notes, no-audio prompt — **byte-identical (untouched)**.
- [ ] **Download/actions**: Download/Extract/Fetch/Retry Audio (incl. qishui soda re-download), Download/Fetch/Retry Cover, per-asset menu, status spinners.
- [ ] **Header**: comet back (`navigate(-1)`), title, author, Share/Download/More (Open Link/Copy Link/Delete).
- [ ] **Theme/data**: Soda palette (`metadata.colors`) / hash fallback still drive waveform+lyric colors; `parsed_media` realtime; rating/notes/tags persistence; Library delete cascade.

---

## File Structure

- **Create** `frontend/utils/coverTint.ts` — `extractCoverTint(url, fallback)`: canvas-sample the cover's average/dominant color → `{ tint: "r,g,b", tintDeep: "r,g,b" }` (deep = darkened ~0.18×). CORS-guarded; on any failure returns `fallback`. `tintFromTheme(theme?)`: derive a fallback from `sodaTheme` accent (or indigo `99,102,241` → deep `24,28,46`).
- **Create** `frontend/utils/coverTint.test.ts` — test `tintFromTheme` fallback + the rgb string formatting + that `extractCoverTint` resolves to the fallback when given an empty/invalid url (jsdom canvas is unavailable, so it must catch + fall back).
- **Create** `frontend/components/PlaylistIsland.tsx` — COMING SOON playlist stub: current-track row (tint highlight + a small CSS equalizer) + a few disabled queue rows (thumb/title/author/duration), a "COMING SOON" badge. Props: `{ title, author, coverUrl }` for the current row. No data fetching.
- **Create** `frontend/components/PlaylistIsland.test.tsx` — renders, shows the current title + COMING SOON.
- **Create** `frontend/components/AudioStageIsland.tsx` — the cover-tinted two-column stage. Header (`CometBack` + title/author + actions slot) → body grid `[cover-side | lyrics column]` → bottom play capsule. Composes `MediaCard` (cover-side), `LyricsView` (lyrics), `AudioWaveformPlayer` (capsule). Receives the data + handlers it needs as props (see Task 3) — it does NOT own playback/lyrics/overview logic.
- **Modify** `frontend/index.css` — add `.audio-stage` (tint backdrop: `radial(rgba(var(--tint),.16)) + linear(rgb(var(--tint-deep))→near-black)`), `.audio-capsule`, `.lyrics-col` (mask-image top/bottom fade), `.eq`/`@keyframes eq`, and a tinted `.comet-back--tint` variant. Append after the P3 `.comet-*` block.
- **Modify** `frontend/pages/DownloadDetailPage.tsx` — in the `islandDesktop` branch, split on `isAudio`: audio → `<AudioStageIsland .../>` + portal `<PlaylistIsland .../>`; video → the P3 stage + `VideoDetailPanel` portal (unchanged). Compute `--tint` via `coverTint` in an effect and set it on the stage wrapper. Classic + mobile branches unchanged.

---

### Task 1: `coverTint` util (TDD)

**Files:** Create `frontend/utils/coverTint.ts` + `coverTint.test.ts`.

- [ ] **Step 1: failing test** `coverTint.test.ts`:
```ts
import { describe, it, expect } from 'vitest';
import { tintFromTheme, extractCoverTint } from './coverTint';

describe('coverTint', () => {
  it('tintFromTheme falls back to indigo when no theme', () => {
    expect(tintFromTheme()).toEqual({ tint: '99,102,241', tintDeep: '24,28,46' });
  });
  it('extractCoverTint resolves to fallback on invalid url (no canvas in jsdom)', async () => {
    const fb = { tint: '1,2,3', tintDeep: '4,5,6' };
    await expect(extractCoverTint('', fb)).resolves.toEqual(fb);
  });
});
```
- [ ] **Step 2: run → FAIL** (`npx vitest run utils/coverTint.test.ts`).
- [ ] **Step 3: implement** `coverTint.ts`:
```ts
export interface CoverTint { tint: string; tintDeep: string }

const INDIGO: CoverTint = { tint: '99,102,241', tintDeep: '24,28,46' };

/** Fallback tint from a sodaTheme accent (hex "#rrggbb") or indigo. */
export function tintFromTheme(theme?: { accent?: string } | null): CoverTint {
  const hex = theme?.accent;
  if (!hex || !/^#?[0-9a-fA-F]{6}$/.test(hex)) return INDIGO;
  const h = hex.replace('#', '');
  const r = parseInt(h.slice(0, 2), 16), g = parseInt(h.slice(2, 4), 16), b = parseInt(h.slice(4, 6), 16);
  const deep = (n: number) => Math.round(n * 0.18);
  return { tint: `${r},${g},${b}`, tintDeep: `${deep(r)},${deep(g)},${deep(b)}` };
}

/** Canvas-sample the cover's average color → tint. Any failure → fallback. */
export function extractCoverTint(url: string, fallback: CoverTint): Promise<CoverTint> {
  return new Promise((resolve) => {
    if (!url || typeof document === 'undefined') return resolve(fallback);
    try {
      const img = new Image();
      img.crossOrigin = 'anonymous';
      img.onload = () => {
        try {
          const c = document.createElement('canvas');
          c.width = 16; c.height = 16;
          const ctx = c.getContext('2d');
          if (!ctx) return resolve(fallback);
          ctx.drawImage(img, 0, 0, 16, 16);
          const d = ctx.getImageData(0, 0, 16, 16).data;
          let r = 0, g = 0, b = 0, n = 0;
          for (let i = 0; i < d.length; i += 4) { r += d[i]; g += d[i + 1]; b += d[i + 2]; n++; }
          r = Math.round(r / n); g = Math.round(g / n); b = Math.round(b / n);
          const deep = (x: number) => Math.round(x * 0.18);
          resolve({ tint: `${r},${g},${b}`, tintDeep: `${deep(r)},${deep(g)},${deep(b)}` });
        } catch { resolve(fallback); }
      };
      img.onerror = () => resolve(fallback);
      img.src = url;
    } catch { resolve(fallback); }
  });
}
```
- [ ] **Step 4: run → PASS**; tsc 126.
- [ ] **Step 5: commit** `feat(audio): coverTint util — canvas dominant-color → --tint (v2 P4 Task 1)`

---

### Task 2: `PlaylistIsland` stub (TDD)

**Files:** Create `frontend/components/PlaylistIsland.tsx` + `.test.tsx`; CSS `.eq` added in Task 5 (or inline here).

- [ ] **Step 1: failing test** `PlaylistIsland.test.tsx`:
```tsx
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import React from 'react';
import { PlaylistIsland } from './PlaylistIsland';

describe('PlaylistIsland', () => {
  it('renders the current track + COMING SOON', () => {
    render(<PlaylistIsland title="My Song" author="Artist" coverUrl={null} />);
    expect(screen.getByText('My Song')).toBeTruthy();
    expect(screen.getByText(/COMING SOON/i)).toBeTruthy();
  });
});
```
- [ ] **Step 2: run → FAIL**.
- [ ] **Step 3: implement** `PlaylistIsland.tsx` — `h-full flex flex-col` content-only (shell owns width): a header "Playlist" + a `COMING SOON` badge; the current track row (cover thumb or Music icon + title + author + a small `.eq` equalizer, tinted via `rgb(var(--tint))`); then 3–4 disabled placeholder queue rows (skeleton thumb/title/author/duration at low opacity). No emoji (lucide `Music`/`ListMusic`). Props `{ title?: string; author?: string; coverUrl?: string | null }`. No data/effects.
- [ ] **Step 4: run → PASS**; tsc 126; no-zinc OK.
- [ ] **Step 5: commit** `feat(audio): PlaylistIsland COMING SOON stub (v2 P4 Task 2)`

---

### Task 3: `AudioStageIsland` component

**Files:** Create `frontend/components/AudioStageIsland.tsx`; modify `frontend/index.css`.

This composes existing parts — do NOT reimplement playback/lyrics/overview. Read `AudioHero.tsx`, `AudioWaveformPlayer.tsx`, `MediaCard.tsx` (audio mode + its props), `LyricsView.tsx` (props), and the current desktop-audio render in `DownloadDetailPage.tsx` (lines ~336-352 AudioHero props; ~528 VideoDetailPanel/MediaCard props) FIRST to learn the exact props each needs.

- [ ] **Step 1:** Define `AudioStageIslandProps` carrying everything the composed children need, sourced from `DownloadDetailPage` (so the page passes the SAME values it passes today): the `video`/media object, `mediaToken`, `coverUrl`, `title`, `author`, `duration`, `currentTime`, `chorusStartSec`, `theme` (sodaTheme), `sourcePlatform`, `mediaId`, `onTimeUpdate`, the audio `src`, the lyric `lines`, the `MediaCard`/Overview props (resourceId, rating/notes/tags handlers — exactly what `VideoDetailPanel`→`MediaCard` receives today), and an `actions` ReactNode (the Share/Download/More buttons built in `DownloadDetailPage`, passed in so behavior is identical).
- [ ] **Step 2:** Layout (mockup `.stage`): root `<div className="audio-stage hidden sm:flex flex-col h-full min-h-0">` (the `.audio-stage` tint backdrop is set by CSS reading `--tint`/`--tint-deep` which the page sets on this element's style or a parent):
  - **stage-head** `<div className="stage-head flex items-center gap-3 px-4 py-3 relative z-[2]">`: `<CometBack onClick={onBack} />` (tinted via the `.comet-back--tint` class — pass a `tinted` prop or wrap), title/author, `<span className="comet-trail" />`, `<div className="ml-auto flex gap-2">{actions}</div>`.
  - **body** `<div className="flex-1 min-h-0 flex items-start justify-center gap-8 px-6 overflow-auto z-[2]">`:
    - **cover-side column** `<div className="w-[min(440px,40%)] shrink-0 overflow-auto">`: render the existing `<MediaCard {...overviewProps} />` (audio mode — it already shows cover/title/author/3-stat/rating/notes/tags/etc.). This preserves ALL Overview features (D12). Do NOT strip fields.
    - **lyrics column** `<div className="lyrics-col flex-1 max-w-[440px] min-h-0 overflow-hidden">`: render the existing `<LyricsView lines={lines} currentTime={currentTime} theme={theme} variant="bare" />`. The `.lyrics-col` CSS applies the top/bottom mask fade.
  - **bottom capsule** `<div className="audio-capsule shrink-0 z-[2]">`: render the existing `<AudioWaveformPlayer {...playerProps} />` (it owns round play / waveform / time / speed / chorus). The `.audio-capsule` wraps it with the tinted pill chrome; the waveform's played-portion color comes from `theme` (Soda accent) as today — the tint is the backdrop, not a player-internal change.
- [ ] **Step 3:** `index.css` (append after `.comet-*`):
```css
/* Audio detail island — cover-tinted immersive stage (spec §5.3 / D8). --tint
   and --tint-deep are set per-track by DownloadDetailPage (coverTint util). */
.audio-stage {
  position: relative;
  background:
    radial-gradient(900px 600px at 38% 30%, rgba(var(--tint, 99,102,241), .16), transparent 65%),
    linear-gradient(165deg, rgb(var(--tint-deep, 24,28,46)), #0d1210 55%, #0a0d0c);
}
.lyrics-col { -webkit-mask-image: linear-gradient(transparent, #000 18%, #000 82%, transparent); mask-image: linear-gradient(transparent, #000 18%, #000 82%, transparent); }
.audio-capsule { margin: 10px 16px 14px; padding: 8px 14px; border-radius: 16px; background: rgba(0,0,0,.28); border: 1px solid var(--line); backdrop-filter: blur(8px); }
.comet-back--tint { border-color: rgba(var(--tint,99,102,241),.4); background: rgba(var(--tint,99,102,241),.08); color: rgb(var(--tint,99,102,241)); }
.comet-back--tint:hover { box-shadow: 0 0 18px rgba(var(--tint,99,102,241),.35); }
.eq { display: inline-flex; align-items: flex-end; gap: 2px; height: 12px; }
.eq i { width: 2.5px; background: rgb(var(--tint,99,102,241)); border-radius: 1px; animation: eq 1s ease-in-out infinite; }
@keyframes eq { 0%,100% { height: 30%; } 50% { height: 100%; } }
```
  (If `CometBack` needs a `tinted` prop to add `.comet-back--tint`, add an optional `tinted?: boolean` to `CometBack` — additive, default false, classic/video callers unaffected.)
- [ ] **Step 4:** tsc 126, vitest green, no-zinc OK, build OK. (No new unit test for the composed layout — it's verified in the dogfood; the composed children keep their own tests.)
- [ ] **Step 5:** commit `feat(audio): AudioStageIsland — cover-tint two-column stage (cover-side Overview | lyrics) + capsule (v2 P4 Task 3)`

---

### Task 4: `DownloadDetailPage` audio-island wiring

**Files:** Modify `frontend/pages/DownloadDetailPage.tsx`.

- [ ] **Step 1:** Import `AudioStageIsland`, `PlaylistIsland`, `extractCoverTint`, `tintFromTheme`. Add tint state: `const [tint, setTint] = useState<CoverTint>(() => tintFromTheme(sodaTheme));` and an effect (gated `islandDesktop && isAudio`) that calls `extractCoverTint(audioCoverUrl, tintFromTheme(sodaTheme)).then(setTint)` when the cover url changes.
- [ ] **Step 2:** In the existing `islandDesktop` branch (from P3), split on `isAudio`:
  - `isAudio` → render `<AudioStageIsland ... style={{ ['--tint' as any]: tint.tint, ['--tint-deep' as any]: tint.tintDeep }} actions={actionButtons} onBack={handleBack} {...the same data/handlers MediaCard + AudioWaveformPlayer + LyricsView get today} />` as the work-island content; and portal `<PlaylistIsland title={...} author={...} coverUrl={audioCoverUrl} />` into `infoIslandEl` (instead of `VideoDetailPanel`). Set `--tint`/`--tint-deep` on the `.audio-stage` root (or on the work-island via the style prop above).
  - `!isAudio` (video) → the P3 stage + `VideoDetailPanel` portal, UNCHANGED.
- [ ] **Step 3:** The `infoAvailable`/`infoVisible` effect from P3 stays (the info island shows the Playlist for audio, the panel for video — both "available"). Confirm the audio Overview no longer ALSO renders in the info island (it's now in the cover-side column) — i.e. for audio, `VideoDetailPanel` is NOT portaled (only `PlaylistIsland`).
- [ ] **Step 4:** D12: classic desktop (`!island`) and mobile (`isMobile`, incl. `MobileAudioScreen`) branches byte-identical. The players mount once. Verify no `VideoDetailPanel` + `AudioStageIsland` double-render of `MediaCard` for audio (only `AudioStageIsland`'s `MediaCard` renders in island-audio).
- [ ] **Step 5:** tsc 126, vitest green, `npm run build` OFF & ON, no-zinc.
- [ ] **Step 6:** commit `feat(audio): island audio branch — AudioStageIsland + PlaylistIsland + cover tint wiring (v2 P4 Task 4)`

---

### Task 5: Verify + ship (flag OFF; check collision)

- [ ] **Step 1:** Full verification — tsc 126, vitest green, `npm run build` (OFF & ON), `scripts/check-no-zinc.sh`. Move any `.eq` CSS not already added into `index.css`.
- [ ] **Step 2:** Visual dogfood (REQUIRES auth + a real audio track). Headless can't reach `/player` of an audio item without real media — verify via a throwaway local account with a seeded audio row OR hand to the user. Run the D12 checklist; verify: cover-tint backdrop reflects the cover color (fallback indigo when no cover), comet-back tinted, cover-side Overview shows ALL fields (rating/notes/tags/stats/etc.), lyrics column synced + masked-fade, bottom capsule plays/seeks/volume/speed + chorus, Playlist island shows COMING SOON, splitter/reopen work, light theme readable, **classic + mobile (`MobileAudioScreen`) pixel-identical**.
- [ ] **Step 3:** ⚠️ **Mandatory parallel-collision check before PR** (P2 lesson): `git fetch origin master && git diff <base> origin/master -- frontend/pages/DownloadDetailPage.tsx frontend/components/AudioStageIsland.tsx frontend/components/PlaylistIsland.tsx frontend/utils/coverTint.ts frontend/index.css`. Reconcile if master moved them.
- [ ] **Step 4:** Version bump (read `origin/master` first), PR with D12 statement + ship chain (public → CI → merge → Vercel Production row + `curl` live version → inflight=0 → private). `gh pr merge` then `gh pr view` MERGED. Flag OFF in prod.
- [ ] **Step 5:** Update memory `project_island_redesign.md` item 7 → P4 shipped; **after P4, consider enabling the flag (P5 is "其余页" polish; per spec §6 the flag turns on once the core detail pages are migrated — confirm with the user whether to flip it now or after P5).**

---

## Self-Review

**1. Spec coverage (§5.3 / D8):** cover-tint stage (`coverTint` Task 1 + `.audio-stage` Task 3/4) ✅; left two-column cover-side Overview | lyrics (Task 3, reusing `MediaCard` + `LyricsView`) ✅; bottom play capsule (Task 3, reusing `AudioWaveformPlayer` + chorus) ✅; right Playlist COMING SOON island (Task 2, portaled Task 4) ✅; cover-color sampling → `--tint`/`--tint-deep` with fallback (Task 1) ✅; comet back tinted (D10, Task 3 `.comet-back--tint`) ✅; D4 no-emoji (lucide) ✅; D12 (flag-OFF + mobile byte-identical + full Overview/lyrics/playback/download preserved by composition) ✅.

**2. Placeholder scan:** Tasks 3 & 4 say "read the existing components first, pass the SAME props" rather than inventing prop lists, because `MediaCard`(1000L+)/`AudioWaveformPlayer`(583L) prop shapes must be read at execution from the live `DownloadDetailPage` call sites — the read step is explicit, not a TODO. New code (coverTint, PlaylistIsland, the stage shell + CSS) is concrete. No TBD.

**3. Type consistency:** `CoverTint` (Task 1) used in Task 4 state. `AudioStageIslandProps` defined Task 3, satisfied by Task 4's call site (same values today's AudioHero/MediaCard/LyricsView/AudioWaveformPlayer receive). `PlaylistIsland` props (`title`/`author`/`coverUrl`) Task 2 ↔ Task 4. Optional `CometBack` `tinted?` is additive (P3 video caller unaffected). `infoIslandEl`/`infoVisible` = P2/P3 contract.

**4. Risks:**
- *Re-hosting MediaCard in the cover-side column*: MediaCard is large + has its own preview/layout assumptions; verify it renders cleanly in a narrow scrollable column (no fixed widths fighting the column). If MediaCard's preview block is redundant with the cover-side cover, that's a visual-dedup decision — keep all FIELDS (D12), only adjust layout. Verify in dogfood.
- *AudioWaveformPlayer inside the capsule*: it owns its own controls/layout; the `.audio-capsule` is a chrome wrapper only — do not alter the player's props/behavior. Verify play/seek/volume/speed/chorus all still work.
- *Canvas CORS*: cover images served with auth/CORS may taint the canvas → `getImageData` throws → util falls back (handled). Verify the fallback (sodaTheme/indigo) looks right when sampling fails.
- *Double MediaCard*: for audio, `VideoDetailPanel` must NOT be portaled (only `PlaylistIsland`); else MediaCard renders twice. Gate explicitly (Task 4 Step 4).
- *Mobile/classic untouched*: every change is `islandDesktop && isAudio`-gated; `MobileAudioScreen` + classic desktop byte-identical — diff-review.
- *Parallel collision*: Task 5 Step 3 mandatory.
