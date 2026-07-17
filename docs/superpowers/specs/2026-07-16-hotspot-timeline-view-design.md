# Hotspot Timeline View — Design Spec

Date: 2026-07-16
Status: approved mockup — https://claude.ai/code/artifact/4e44ba1e-a679-4f52-ad71-d82fa692676b
Scope: frontend-only. Backend untouched (`captured_at` already served by `GET /api/v1/topics`).

## Goal

The Inspiration Library → Hotspots tab gets a second view: a chronological
timeline (date-grouped, time rail, cards), alongside the existing ranked
list + detail two-pane view. Reference: the legacy TopicInspiration timeline
look (screenshot provided by user).

## Decisions (user-confirmed)

1. **Toggle, not replace** — a "Ranked ⇄ Timeline" view switch in the Hotspots
   tab toolbar (same interaction as ActivityPanel's heatmap/calendar toggle),
   preference persisted to localStorage (`inspiration.hotspotsView`).
2. **Detail = inline expand (Variant A)** — clicking a card expands the full
   detail (body, actions) in place under the card; clicking again collapses.
   No side panel in timeline mode.
3. **Date header layout** — date at far left, collapse chevron sits ON the
   rail line (island-bg circular mask), weekday · count to the right of the
   line: `7月16日 (▾ on rail) 星期四 · 5 条`.

## Architecture

### Components

- `Inspiration/HotspotsWorkspace.tsx` — owns the view state
  (`'ranked' | 'timeline'`, localStorage-backed). Renders the existing
  two-pane layout for `ranked` (zero changes to that path) and the new
  timeline for `timeline`. Toolbar gains the view toggle (top-right).
- `Inspiration/HotspotTimeline.tsx` (new) — adapted copy of
  `TopicInspiration/Timeline.tsx` (which stays untouched; the legacy page
  still uses it). Changes vs the legacy component:
  - Date header per decision 3 (weekday added, chevron on the rail).
  - Read items: card at `opacity-60` + rail dot goes `content-4` grey.
  - Inline expand: clicking a card toggles an expanded detail section
    instead of calling the legacy `onSelect` side-panel flow.
- `TopicInspiration/HotspotCard.tsx` — reused as-is for the collapsed card
  (source / rank / multi-platform / pick / score / save / hide / title /
  summary / tags / reason). The expand affordance wraps it.
- Expanded detail content: reuse the content pieces of
  `Inspiration/HotspotDetail.tsx` (summary → original/translated body →
  score dims → actions row: Save as Note / Parse / Open original /
  Not interested). Reuse via an `embedded` boolean prop on `HotspotDetail`
  that drops its island wrapper/padding and renders content-only; extract a
  `HotspotDetailBody.tsx` instead only if that prop turns HotspotDetail
  into branching soup (>3 conditional blocks), in which case both callers
  use the extracted body.

### Data flow

- Same single `useHotspots` instance owned by `InspirationPage` (day-scoped
  by the page's selected date; no date selected = recent feed). Switching
  views does NOT refetch.
- Timeline ordering: `captured_at` desc; group key = local-date of
  `captured_at`; items missing `captured_at` group under "Undated" at the
  bottom.
- Expanding a card lazily fetches the full body via `getHotspot(id)`
  (the same call HotspotDetail makes today) and caches it on the item for
  the session.
- `applyState` (save/hide/read) works identically in both views (same
  optimistic patch through the shared hook).
- Category chips / search / calendar date selection continue to filter both
  views identically (filtering happens above the view split).

### i18n

New keys under `inspiration.*`: `viewRanked` ("Ranked"), `viewTimeline`
("Timeline"), `undated` ("Undated"), weekday formatting via
`toLocaleDateString(i18n.language, { weekday: 'long' })`; count reuses an
`{{count}}` plural key. UI strings English; zh.json provides中文.

## Error handling

- `getHotspot(id)` failure on expand: keep the card expanded with a small
  inline error line + retry button; `console.error` the cause.
- Empty timeline (no hotspots after filters): reuse the existing
  `inspiration.noHotspots` empty state.

## Testing (vitest, colocated)

- Grouping: multi-day input → correct group keys/order; undated bucket last.
- Collapse: toggling a date header hides its rows only.
- Expand: click toggles detail; save/hide icon clicks do NOT toggle expand
  (stopPropagation), matching mockup behavior.
- Read state: `is_read` renders dimmed card + grey dot.
- View toggle: persists to localStorage; ranked path renders unchanged
  (snapshot of existing list still passes).

## Out of scope

- Backend changes, new endpoints, infinite scroll/pagination beyond what the
  current feed returns, and any change to the legacy TopicInspiration page.
