# Island Redesign v2 — P3: Video Detail Page (stage island + portaled info island + comet back)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Prerequisite:** P2 (#699) merged — the island info-island infra (`IslandWorkContext` callback-ref portal target `infoIslandEl`/`setInfoIslandEl`, `IslandShell` splitter + info-island + `.island-reopen`, `iconRail` on detail routes) is on master and reused **unchanged**. Flag `VITE_FEATURE_ISLAND_UI` stays **OFF in prod** through P3 (enabled only after P2–P5 per spec §6).

**Goal:** Render the video detail page (`DownloadDetailPage`) inside the island work area — stage (comet-back header → black viewport → controls) fills the work island, and `VideoDetailPanel` (Overview/Transcript/Analysis/Lyrics tabs) is **React-portaled** into the shell's separate info island — matching `docs/design/mockups/mediahub-player-redesign.html`, with zero feature change (D12).

**Architecture (reuses P2's island infra — no shell/context changes):** In island mode, `DownloadDetailPage` drops its own desktop split (stage | resize-divider | panel) and instead (a) renders ONLY the stage to fill the work island, (b) `createPortal`s `<VideoDetailPanel island />` into `useIslandWork().infoIslandEl`, (c) drives `setInfoAvailable(true)` + `setInfoVisible(panelOpen)` so the shell renders the floating info-island chrome (width/splitter/reopen). The nav is already a 54px icon rail on `/player/` routes (P1b `iconRail`, `isDetailPage` covers `/player/` — `AppLayout.tsx:100`). A new reusable **`CometBack`** component implements the D10 signature back button. Everything is `islandUI()`-gated; the classic path (and the entire mobile path, since the island frame is `hidden sm:flex`) is byte-identical (D12).

**Tech Stack:** React 19 + TS, `react-dom` `createPortal`, Tailwind v4 (island semantic tokens in `index.css`), `IslandShell`/`IslandWorkContext` from P1b/P2, vitest + @testing-library/react.

**Hard constraints (spec):**
- **D12 zero feature change** — every interaction in the checklist below works identically; flag-OFF (and ALL mobile) renders today's DOM.
- **D5** nav never disappears (already a 54px icon rail on detail routes — verify, don't rebuild).
- **D10** comet back button (circular violet ring + 2px fading light trail under the title on `stage-head:hover`, hover glow).
- **D7** info panel draggable-width + collapsible — owned by the shell (reused from P2); **density not reduced** (all tabs/fields/AI actions/colored tag chips stay).
- **D4** no emoji (lucide icons / type badges only).
- tsc baseline (currently **126** after P2 merge) must not increase; vitest stays green (currently **956**); new code uses `ink-*`/semantic tokens only (no `*-zinc-*`, CI guard `scripts/check-no-zinc.sh`).
- Commit each task; flag stays OFF in prod.

**Out of scope:** P4 audio detail (cover-tint, lyrics column, playlist island — `MobileAudioScreen`/`AudioHero`/`SodaLyricsTab` stay as-is; audio in island mode keeps its current stage rendering, only gaining the shell info-island like video); P5 pages; Cmd+K; any backend/player-logic/AI-task change; mobile redesign (mobile detail page keeps today's layout — island frame is `hidden sm:flex`). No new player features — `VideoPlayer`/`SlidePlayer`/`AudioHero` internals are untouched; we only relocate where the stage and panel render.

---

## D12 Feature-Parity Checklist (acceptance baseline — re-verify with flag ON)

Video detail inventory (entry: route `/team/:teamId/player/:displayId` → `pages/DownloadDetailPage.tsx` (630L) → `VideoPlayer`(816L) / `SlidePlayer`(299L) / `AudioHero`(242L) + `VideoDetailPanel`(930L) → `MediaCard`(1158L); state in `useDownloadDetail.ts`(322L) + Supabase realtime + `ResourcesContext`-adjacent direct queries).

- [ ] **Header (desktop `hidden sm:flex`)**: back navigation (`navigate(-1)`), title (`video.title || description`), author + platform icon, action buttons — Share (→ ShareModal), Download (→ DownloadMenuDropdown), More ⋯ (Open Link / Copy Link / Delete → DeleteDialog).
- [ ] **Mobile header/nav**: floating back button (safe-area-inset aware), back scrim, mobile-audio author bar, iOS safe-area reflow kicks, MobileTabBar hidden on detail — **all unchanged** (mobile path untouched).
- [ ] **Stage — video**: `VideoPlayer` HLS multi-bitrate, play/pause, seek, volume, fullscreen, speed 0.5–2x, quality (HLS levels + Original), auto quality, frame stepping, full keyboard map (Space/arrows/j/l/k/m/f/>/</0-9/c), auth-token header, 401/403 states, resolution display.
- [ ] **Stage — slides**: `SlidePlayer` load `/api/v1/media/{id}/slides`, prev/next/swipe/dots/counter, bg-music mute, keyboard, loading/error/empty.
- [ ] **Stage — audio**: `AudioHero` cover/placeholder, gradient backdrop, waveform, play/seek/volume/duration/speed, chorus marker, lyrics preview→overlay, Fetch Lyrics (soda/qishui). Mobile audio = `MobileAudioScreen` (locked) — untouched.
- [ ] **Info panel — Overview (`MediaCard`)**: preview, type+resolution badges, ID, author, release date + duration, stats grid (Likes/Comments/Shares/Collects; audio=3), share-card copy-link, 5-star rating, notes (blur-to-save), tags (add/remove, colored chips, create), platform hashtags, AI intent badges (✓/…/✕/·), AI action buttons (Copy/Transcript/Summary/Analyze), description, AI results (Extracted Data / Rewrite / Analysis), collections toggle, more menu, `compact` mode (mobile-audio).
- [ ] **Info panel — Transcript**: processing/not-started/loading/error+retry, metadata (duration/lang/segments), Segments↔Full-Text toggle, copy (w/ timestamps), export SRT/TXT.
- [ ] **Info panel — Analysis**: summary (text/key-points/topics chips/status), visual analysis (description/object-scene-people chips/OCR/model/status), task-driven state (polls `task_tracking`, NOT prop), processing spinner, trigger, retry.
- [ ] **Info panel — Lyrics (audio)**: `SodaLyricsTab` synced highlight + chorus, Fetch Lyrics.
- [ ] **Modals/menus**: ShareModal (type/name/password/expiry/download/watermark/copy), DownloadMenuDropdown (video/gallery/cover/audio variants incl. fetch/retry/progress), DeleteDialog (move-to-trash + cascade), More menu, MobileDownloadMenu.
- [ ] **Realtime/state**: `parsed_media` realtime, rating/notes persistence, `resource_tags`, AI `task_tracking` polling, HLS transcoding status, auth token, Library delete cascade, toasts.

---

## File Structure

- **Create** `frontend/components/CometBack.tsx` — D10 comet back button (circular violet ring + hover trail). Reusable (P4 audio reuses it). Props: `onClick`, optional `title?` for aria.
- **Create** `frontend/components/CometBack.test.tsx` — renders a button, fires onClick.
- **Modify** `frontend/index.css` — add `.comet-back` / `.comet-trail` helpers (from the mockup `.back`/`.trail`), and `.stage-island` helpers if needed (reuse `.island-card`). Append after the `.island-reopen` block.
- **Modify** `frontend/components/VideoDetailPanel.tsx` — add `island?: boolean` + `onCollapse?: () => void`. In island mode render the tabs + content WITHOUT the panel's own outer width/positioning chrome (the shell owns width); the tabs row's collapse button calls `onCollapse`. Classic path byte-identical.
- **Modify** `frontend/pages/DownloadDetailPage.tsx` — `islandUI()`-gated desktop branch: stage fills the work island (comet-back header + viewport + controls, NO own split/divider/panelWidth); `createPortal(<VideoDetailPanel island onCollapse=… />, infoIslandEl)`; effects sync `infoAvailable`/`infoVisible`. Classic + mobile paths unchanged.

---

### Task 1: `CometBack` component + CSS (D10, TDD)

**Files:** Create `frontend/components/CometBack.tsx` + `.test.tsx`; modify `frontend/index.css`.

- [ ] **Step 1: failing test** `frontend/components/CometBack.test.tsx`:

```tsx
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import React from 'react';
import { CometBack } from './CometBack';

describe('CometBack', () => {
  it('renders a button and fires onClick', () => {
    const onClick = vi.fn();
    render(<CometBack onClick={onClick} title="Back" />);
    const btn = screen.getByRole('button', { name: 'Back' });
    fireEvent.click(btn);
    expect(onClick).toHaveBeenCalledTimes(1);
  });
});
```

- [ ] **Step 2: run → FAIL** (`npx vitest run components/CometBack.test.tsx`).

- [ ] **Step 3: implement** `frontend/components/CometBack.tsx` (lucide `ArrowLeft`, no emoji; the trail lives on the parent `.stage-head` via CSS so the hover sweep tracks the header — the component renders the ring button + a trail span sibling the header places):

```tsx
import React from 'react';
import { ArrowLeft } from 'lucide-react';

interface CometBackProps {
  onClick: () => void;
  /** Accessible label (defaults to "Back"). */
  title?: string;
}

/**
 * D10 "comet" back button — a circular violet ring; the 2px fading light trail
 * that sweeps under the title on hover is drawn by the parent `.stage-head`
 * (see `.comet-trail` in index.css), so render this as the first child of a
 * `.stage-head` and put a <span className="comet-trail" /> right after the title.
 */
export const CometBack: React.FC<CometBackProps> = ({ onClick, title = 'Back' }) => (
  <button type="button" onClick={onClick} aria-label={title} title={title} className="comet-back">
    <ArrowLeft size={16} />
  </button>
);
```

- [ ] **Step 4: index.css** — append after the `.island-reopen` block (values from the mockup `.back`/`.trail`; uses violet literals, not zinc):

```css
/* D10 comet back button — circular violet ring + a 2px light trail that sweeps
   under the title on stage-head hover. Place <CometBack/> as the first child of
   a `.stage-head` and a <span class="comet-trail"/> after the title. */
.comet-back {
  width: 32px; height: 32px; border-radius: 50%; flex-shrink: 0;
  border: 1px solid rgba(139,92,246,.35);
  background: radial-gradient(circle at 35% 30%, #2a2438, #1a1722);
  display: grid; place-items: center; color: #c4b5fd; cursor: pointer;
  transition: .2s;
}
.comet-back:hover { border-color: rgba(139,92,246,.7); box-shadow: 0 0 18px rgba(139,92,246,.35); transform: translateX(-1px); }
.comet-trail {
  position: absolute; left: 52px; bottom: 8px; height: 2px; width: 0; border-radius: 2px; pointer-events: none;
  background: linear-gradient(90deg,#8b5cf6,rgba(139,92,246,.45) 35%,rgba(139,92,246,.12) 70%,transparent);
  transition: width .35s ease;
}
.stage-head:hover .comet-trail { width: 340px; }
```

- [ ] **Step 5: run → PASS**; tsc 126; vitest +1.
- [ ] **Step 6: commit** `feat(player): CometBack D10 button + comet-trail CSS (v2 P3 Task 1)`

---

### Task 2: `VideoDetailPanel` island mode (content-only + collapse callback)

**Files:** Modify `frontend/components/VideoDetailPanel.tsx`.

- [ ] **Step 1:** Add to its props: `island?: boolean;` and `onCollapse?: () => void;`. Destructure with defaults (`island = false`, `onCollapse`). Keep ALL existing hooks/state/effects (transcript polling, analysis task-driven state, tab switching) at the top unconditionally — do not reorder.
- [ ] **Step 2:** Find the panel's outermost wrapper (the element that today sets the column width / fills the sibling column). In **island** mode, render the SAME inner content (tabs row + active tab body: Overview `MediaCard` / Transcript / Analysis / Lyrics) inside `<div className="h-full flex flex-col min-h-0">` with NO own width style and NO fixed/absolute positioning (the shell's `<aside className="island-card">` owns width + scroll). The classic wrapper (with `panelWidth`/positioning) must stay **byte-identical** for `island === false`.
- [ ] **Step 3:** In the tabs row, add a collapse button at the end (mockup `.tabs .collapse`, lucide `ChevronRight`/`PanelRightClose`, no emoji) rendered ONLY when `island` — `onClick={onCollapse}`. The Overview/Transcript/Analysis(/Lyrics) tab buttons + their bodies are unchanged (same components, same props → no feature loss; D12).
- [ ] **Step 4:** tsc 126, vitest green (existing VideoDetailPanel tests, if any, still pass), no-zinc OK.
- [ ] **Step 5:** commit `feat(player): VideoDetailPanel island content-only mode + collapse (v2 P3 Task 2)`

---

### Task 3: `DownloadDetailPage` island-mode layout + panel portal

**Files:** Modify `frontend/pages/DownloadDetailPage.tsx`.

- [ ] **Step 1:** Imports: `import { createPortal } from 'react-dom';`, `import { islandUI } from '../utils/featureFlags';`, `import { useIslandWork } from '../contexts/IslandWorkContext';`, `import { CometBack } from '../components/CometBack';`. Call `const island = islandUI();` and `const { infoIslandEl, infoVisible, setInfoVisible, setInfoAvailable } = useIslandWork();` near the top with the other hooks (unconditional — `useIslandWork` returns the inert shape if somehow outside a shell, so it's safe; classic mode still calls it but the effects below are gated on `island`).
- [ ] **Step 2:** Add island visibility effects (after existing effects):

```tsx
// In the island shell the video detail panel lives in the shell's info island.
// It's always available on this page; default open. Collapse (panel »)/reopen
// (shell ‹ Info) flip infoVisible. Effects no-op in classic mode (inert context).
useEffect(() => {
  if (!island) return;
  setInfoAvailable(true);
  setInfoVisible(true);
  return () => { setInfoAvailable(false); setInfoVisible(false); };
}, [island, setInfoAvailable, setInfoVisible]);
```

- [ ] **Step 3:** Branch the DESKTOP layout on `island`. Keep the mobile path and the classic desktop path byte-identical. Today's desktop content row (`DownloadDetailPage.tsx:387` `flex-1 min-h-0 flex flex-col md:flex-row …` containing stage `md:flex-1` + divider `:515` + `VideoDetailPanel` column `:522/:528`) becomes, in island mode:
  - **Stage only**, filling the work island: a `<div className="hidden sm:flex flex-col h-full min-h-0">` containing:
    - **stage-head** (`<div className="stage-head flex items-center gap-3 px-4 py-3 border-b border-line relative">`): `<CometBack onClick={() => navigate(-1)} />` + title/author block + `<span className="comet-trail" />` (after the title) + the SAME action buttons (Share/Download/More) on `margin-left:auto`. Reuse the exact existing handlers (`onShare`, download menu, more menu) — do not change their behavior.
    - **viewport** (`<div className="flex-1 min-h-0 grid place-items-center bg-[#050507] overflow-hidden">`): the SAME `VideoPlayer`/`SlidePlayer`/`AudioHero` switch the page already renders (move it here verbatim; do not touch the player components or their props).
    - controls: the player components render their own controls — keep as today (no separate control bar unless the page already has one).
  - **Portal the panel** out to the info island, OUTSIDE the stage div:

```tsx
{island && infoIslandEl && createPortal(
  <VideoDetailPanel island onCollapse={() => setInfoVisible(false)} {/* ...the SAME props passed today */} />,
  infoIslandEl,
)}
```
  Copy the `VideoDetailPanel` props **verbatim** from the current classic render (`:528`) — same `video`, handlers, statuses — so the panel behaves identically.
  - The shell already shows `‹ Info` reopen when `!infoVisible && infoAvailable` (P2) → clicking it sets `infoVisible` true → panel reappears. No extra wiring needed.
- [ ] **Step 4:** Verify there's NO double layout: in island mode the page must NOT also render the classic `flex md:flex-row` split or the `panelWidth` divider (those are classic-only). Gate them with `!island`. The mobile branch (`sm:hidden` floating back, `MobileAudioScreen`, etc.) is shared/unchanged (island is `sm:`+).
- [ ] **Step 5:** tsc 126, vitest green, `npm run build` (OFF & `VITE_FEATURE_ISLAND_UI=true`), `scripts/check-no-zinc.sh`.
- [ ] **Step 6:** commit `feat(player): island layout — stage in work island, VideoDetailPanel portaled to info island + comet back (v2 P3 Task 3)`

---

### Task 4: Visual polish to mockup + iconRail verification

**Files:** Possibly `frontend/index.css`, `frontend/components/VideoDetailPanel.tsx`, `frontend/pages/DownloadDetailPage.tsx` (minimal).

- [ ] **Step 1: Verify** the nav is the 54px icon rail on `/player/` (P1b: `AppLayout.tsx:100` `isDetailPage` includes `/player/` → `IslandShell isDetailPage` → `Sidebar iconRail`). Confirm with flag ON; do NOT rebuild — just confirm in the dogfood step.
- [ ] **Step 2:** Stage-head action buttons → tinted (D6): Share uses `.btn-tint-indigo` (already in `index.css`); Download/More use the neutral island button look (`bg-island-2 border-line`). Only restyle if the current buttons don't already read as tinted/neutral; verify first, change minimally (like P2 Task 4). Type badge in the panel uses the §4 semantic-color badge (`.typebadge` video=indigo / audio=green / image=amber) — reuse existing badge if present.
- [ ] **Step 3:** Ensure the viewport black area is `#050507` (mockup) and the stage scrolls correctly inside the work island (no double scrollbar — the work island is `overflow-auto`; the stage is `h-full flex-col`, viewport `flex-1 min-h-0`).
- [ ] **Step 4:** All polish uses semantic tokens / existing `.btn-tint-*`; no zinc. tsc 126 / vitest green / build / no-zinc.
- [ ] **Step 5:** commit `feat(player): stage tinted actions + viewport polish to mockup (v2 P3 Task 4)`

---

### Task 5: Verify + ship (flag still OFF in prod)

- [ ] **Step 1:** Full verification — tsc 126, vitest green, `npm run build` (OFF & ON), `scripts/check-no-zinc.sh`.
- [ ] **Step 2:** Visual dogfood (REQUIRES an authenticated session). The reviewer cannot reach `/player/:id` headless without credentials; verify via a throwaway local account against local Supabase (`http://127.0.0.1:54321`, sign up → log in → open a video) OR hand to the user. Run the entire D12 checklist; verify: nav = 54px icon rail (hover tooltips), comet back ring + trail sweep on stage-head hover, stage fills the work island, info island shows the panel with all tabs, splitter drags 250–480, panel `»` collapses + shell `‹ Info` reopens, audio + slide media types render in the stage, light theme readable, classic mode (flag OFF) + mobile pixel-identical.
- [ ] **Step 3:** ⚠️ **Before opening the PR, check for parallel-session collision** (P2 burned us here): `git fetch origin master && git diff <branch-base> origin/master -- frontend/components/VideoDetailPanel.tsx frontend/pages/DownloadDetailPage.tsx frontend/components/CometBack.tsx frontend/index.css`. If master already moved these, reconcile (reset onto master + re-apply, take the superseding island versions) as in P2.
- [ ] **Step 4:** Version bump (read `origin/master` first to avoid collision), PR with D12 statement + standard ship chain (public → CI green → merge → confirm Vercel Production row + `curl` live version → inflight=0 → private). `gh pr merge` then `gh pr view` MERGED. Flag stays OFF in prod.
- [ ] **Step 5:** Update memory `project_island_redesign.md` item 6 → P3 shipped (flag still OFF; enable after P5).

---

## Self-Review

**1. Spec coverage (§5.2):** nav 54px rail (P1b, verified Task 4) ✅; stage island = comet-back (Task 1) + header/title/author + tinted actions (Task 3/4) → black viewport → controls ✅; info island Overview/Transcript/Analysis tabs + collapse (Task 2/3) ✅; Overview content order = `MediaCard` (unchanged — D12) ✅; splitter + draggable width + reopen (reused from P2 shell) ✅; D10 comet (Task 1) ✅; D4 no-emoji (lucide) ✅; D12 (flag-OFF + mobile byte-identical + full checklist) ✅.

**2. Placeholder scan:** Tasks 2 & 4 say "verify current state, copy props verbatim, change minimally" rather than dictating exact diffs because `VideoDetailPanel`(930L) / `DownloadDetailPage`(630L) are large existing files whose exact wrapper lines must be read at execution — the verify step is explicit, not a TODO. The new code (CometBack, CSS, portal wiring, effects) is concrete. No TBD.

**3. Type consistency:** `CometBack` props (`onClick`/`title`) defined Task 1, used Task 3. `VideoDetailPanel` `island?`/`onCollapse?` defined Task 2, used Task 3. `useIslandWork()` shape (`infoIslandEl`/`infoVisible`/`setInfoVisible`/`setInfoAvailable`) is the P2 master contract — consumed in Task 3. Portal target `infoIslandEl` is the callback-ref state node from P2 (re-renders on attach — no timing race).

**4. Risks:**
- *Player not unmounting / re-mounting on portal*: the stage (VideoPlayer) stays in the work island (NOT portaled) — only the panel is portaled — so HLS/auth/playback are unaffected. Verify the player doesn't reinit on info-island show/hide (it shouldn't — infoVisible only toggles the panel subtree).
- *VideoDetailPanel task-driven analysis state* (polls `task_tracking`, ignores prop): portaling the panel doesn't change its data subscriptions (same component instance, same props) — verify analysis/transcript still update live.
- *Mobile untouched*: every island branch is `island && sm:`-scoped; mobile (`sm:hidden` floating back, `MobileAudioScreen`) and classic desktop are byte-identical — diff-review each.
- *Double scrollbar*: work island `overflow-auto` + stage `h-full` + viewport `flex-1 min-h-0` — dogfood-verify.
- *Parallel-session collision*: Task 5 Step 3 makes the master-diff check mandatory before the PR.
