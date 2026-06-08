# Uploaded-Audio Mobile Immersive Parity — Design Spec

Date: 2026-06-08
Status: Awaiting user review

## Problem

On **mobile**, a downloaded audio detail page shows an immersive "music app" layout
(`MobileAudioScreen`: one continuous cover-derived gradient surface — big album
cover, title, synced lyric couplet, social-stats row, speed chip + ⋮ menu, tags,
waveform color-block, rating/notes). An **uploaded** audio detail page shows the
plain `AudioHero` (a small dark waveform + control bar) followed by separate dark
metadata cards. They look like two different apps.

Routing today (`pages/FileDetailDispatcher.tsx`):
- `source_type === 'web'` + `media_id` → `DownloadDetailPage` → (mobile) `MobileAudioScreen` immersive ✅
- else (uploaded/imported) → `ResourceDetailPage` → `AudioHero` plain ✗

So **downloaded-into-library audio already gets the immersive view**; only **pure
uploaded audio** is plain. That is the entire gap.

Desktop needs nothing: `DownloadDetailPage` on desktop also uses `AudioHero` (the
immersive `MobileAudioScreen` is a mobile-only short-circuit), so download and
resource desktop views are already consistent.

## Scope

- **Mobile only.**
- **Uploaded audio only** (`source_type === 'upload'`, audio mime). Downloads,
  desktop, video, and image are untouched.

## Decisions (confirmed with user)

| # | Decision |
|---|----------|
| Social stats / artist | The stats+actions row **stays** (it also holds the speed chip + ⋮ menu on the right); only the **left social stats are absent** for uploads. This needs no special hiding — the component already does `visibleStats = stats.filter(v > 0)`, so feeding empty/zero social values leaves the left side empty while keeping speed + menu. Artist: parse it from the **filename** — when the filename is `Artist - Title`, split into artist + title for display (display-only, no persistence). |
| ⋮ menu | **Reuse the existing uploaded-audio capabilities**: Replace cover, Upload .lrc lyrics, Copy link, Delete. (Download's Fetch/Retry asset actions do not apply to uploads.) |
| Approach | **Extract a shared presentational `MobileAudioShell`** from `MobileAudioScreen`; both the download path and the new uploaded-audio path compose it. The download view must stay **pixel-identical** (it is the LOCKED layout). |

## Non-goals (YAGNI)

- No desktop change.
- No social-stat placeholders/zeros for uploads — the row is simply absent.
- No editable artist field — artist is a display-only parse of the filename.
- No change to how downloaded audio sources its data (still `Video` + `getMediaLyrics`).
- No new persistence (cover/lyrics/chorus already have storage + endpoints from prior PRs).

## Architecture

### 1. Extract `MobileAudioShell` (pure presentational)
Pull the immersive layout out of `MobileAudioScreen` into a new dumb component
`frontend/components/MobileAudioShell.tsx` that renders the gradient surface +
cover + title + optional artist + lyric couplet + (optional) social-stats row +
speed chip + ⋮ menu + tags + waveform color-block + rating/notes. It takes a
**normalized view-model**, NOT a `Video`:

```
interface MobileAudioShellProps {
  src: string; hasAudio: boolean; coverUrl?: string; theme?: SodaTheme;
  title: string; artist?: string;            // artist hidden when absent
  social?: { likes?; comments?; shares?; collects?; onCopyLink?: () => void } | null; // null/undefined → row hidden
  lyricLines: LyricLine[]; onReloadLyrics?: () => void;   // source-agnostic (NOT getMediaLyrics inside)
  currentTime?: number; onTimeUpdate?: (s:number)=>void;
  chorusStartSec?: number; chorusEditable?: boolean; onChorusChange?: (s:number|null)=>void; // reuse PR #550 props
  menuItems: Array<{ key; label; Icon; color?; onClick: () => void }>;   // generic ⋮ items
  resourceId?: string; rating?; notes?; onRatingChange?; onNotesChange?; onNotesBlur?;
  noAudioPrompt?: React.ReactNode;          // download's "not downloaded — Fetch" slot
}
```

Key generalizations vs today:
- **Lyrics become an input** (`lyricLines` + `onReloadLyrics`) instead of the
  component calling `getMediaLyrics(mediaId)`. Download feeds it
  `getMediaLyrics`; upload feeds `resource.lyrics_json.lines`.
- **`LyricsOverlay` must also be decoupled.** Today it is `mediaId`-only and
  re-fetches via `SodaLyricsTab(mediaId)`. Add an optional `lines: LyricLine[]`
  input so the full-screen lyric sub-page renders passed-in lines when no
  `mediaId` (uploads). When `lines` is provided it renders them directly and
  skips the mediaId fetch + the qishui "Fetch Lyrics" affordance. Download
  keeps passing `mediaId` (unchanged behavior).
- **Social stats stay optional via the existing `visibleStats` filter** — feed
  uploads no social values → left side empty; the row (speed + ⋮) remains.
- **⋮ menu is a generic `menuItems[]`** — download builds Fetch/Retry asset
  items; upload builds Replace-cover / Upload-.lrc / Copy-link / Delete.
- **Top bar is the caller's job.** The immersive top bar (back arrow + `@author`)
  lives in `DownloadDetailPage`, NOT in the component. The uploaded path
  (`ResourceDetailPage` mobile) must render its own minimal top bar: a back
  arrow, no author.

### 2. `MobileAudioScreen` becomes a thin download adapter
It keeps its `Video`-based props, does its existing lyrics fetch + asset-menu
building, and renders `<MobileAudioShell .../>` with the mapped view-model. The
rendered output must be **identical** to today (same DOM/classes) — this is the
LOCKED layout.

**Regression guard — there is NO existing `MobileAudioScreen` test.** The FIRST
task is to add a **characterization test** for the current download
`MobileAudioScreen` (render with a representative `Video` + handlers; assert the
key structural pieces: cover, title, the visible social stats, speed chip, ⋮ menu
items, waveform player, rating/notes/tags) and lock it in BEFORE extracting the
shell. The extraction is done only once this test stays green.

### 3. New uploaded-audio mobile path
A small adapter `MobileAudioUpload.tsx` (or an inline branch in
`ResourceDetailPage`) maps a `Resource` → the shell view-model:
- `coverUrl` = uploaded cover (`getResourceCoverUrl`) or themed placeholder.
- `title`/`artist` = `parseArtistTitle(resource.filename)` (see helper).
- `social = null` → row hidden.
- `lyricLines` = `resource.lyrics_json?.lines ?? []`; `onReloadLyrics` re-pulls the resource.
- chorus props = same wiring as PR #550 (`chorus_start_ms`/`setResourceChorus`).
- `menuItems` = Replace cover (file picker → `uploadResourceCover`), Upload .lrc
  (file picker → `uploadResourceLyrics`), Copy link, Delete — reusing the
  existing `ResourceDetailPage` handlers/services.
- rating/notes/tags = existing resource wiring.

### 4. `ResourceDetailPage` mobile gate
When `isMobile && isAudio && source_type === 'upload'`, render the immersive
upload path instead of the current `AudioHero` + cards. Desktop and non-audio
unchanged.

### 5. Filename → artist/title helper
`frontend/utils/parseArtistTitle.ts`:
```
parseArtistTitle("Biboulakis - Is That Too Much to Ask (feat. Nina Zeitlin).mp3")
  → { artist: "Biboulakis", title: "Is That Too Much to Ask (feat. Nina Zeitlin)" }
parseArtistTitle("just-a-song.mp3") → { title: "just-a-song" }   // no " - " → no artist
```
Rules: strip a trailing audio extension; split on the **first** ` - ` (space-hyphen-space);
left = artist, right = title; if no ` - `, artist undefined and title = whole stem.
Pure, unit-tested.

## Error handling
- Cover/lyrics upload failures → toast error, view unchanged (existing behavior).
- No lyrics → couplet collapses (shell already handles empty `lyricLines`).
- Missing cover → themed `Music` placeholder + gradient (existing fallback).

## Testing
- **Characterization test for download `MobileAudioScreen` FIRST** (added before any extraction; locks the LOCKED layout's structure — none exists today).
- `parseArtistTitle` unit tests (with/without ` - `, extension strip, feat. parens, multiple hyphens, date-like `2024-01-01` not split).
- `MobileAudioShell`: renders title; left social stats empty when no social values (speed + ⋮ still present); hides artist when absent; renders provided `menuItems`; shows `noAudioPrompt` when `!hasAudio`.
- `LyricsOverlay`: renders passed-in `lines` when no `mediaId`; still fetches by `mediaId` when given (download path unchanged).
- Uploaded path: left social stats empty; artist parsed from filename; menu has the 4 upload items; back-arrow top bar present.

## Files
- Create: `frontend/components/MobileAudioScreen.characterization.test.tsx` (regression guard, added FIRST)
- Create: `frontend/components/MobileAudioShell.tsx`
- Create: `frontend/components/MobileAudioShell.test.tsx`
- Create: `frontend/utils/parseArtistTitle.ts` + `frontend/utils/parseArtistTitle.test.ts`
- Create: `frontend/components/MobileAudioUpload.tsx` (or inline in ResourceDetailPage)
- Modify: `frontend/components/MobileAudioScreen.tsx` (becomes shell adapter — output identical)
- Modify: `frontend/components/LyricsOverlay.tsx` (optional `lines` input for the upload full-screen sub-page)
- Modify: `frontend/components/ResourceDetailPage.tsx` (mobile upload-audio gate + back-arrow top bar)
- Modify: `frontend/public/locales/{en,zh}.json` (menu labels: replaceCover, uploadLyrics, copyLink, delete — reuse existing keys where present)
- Modify: `frontend/package.json` (version bump on ship)
