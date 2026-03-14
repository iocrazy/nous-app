# Mobile App-Style Redesign

## Problem

The current mobile experience is a "shrunk desktop website" rather than a native app experience. Key issues:

1. **TopBar** — Too web-like: globe icon, language dropdown, search, filter, notifications, avatar all crammed into one row
2. **Resources sidebar** — Was fully visible on mobile (fixed in 9f41200, now a drawer)
3. **Bottom navigation** — Only 4 items (Parser, Library, Dashboard, User), missing Resources
4. **Content spacing** — Inconsistent padding, content sometimes behind TopBar
5. **No app-level interactions** — No pull-to-refresh, no swipe gestures, no native feel
6. **Dashboard** — Stats cards are fine but could be more compact

## Design Principles

Design as a **native mobile app**, not a responsive website:

- **Bottom Tab Bar** = primary navigation (like iOS/Android apps)
- **Top area** = page title + contextual actions only (not global nav)
- **Full-screen content** = edge-to-edge, no wasted space
- **Gesture-first** = swipe, pull-to-refresh, long-press
- **Progressive disclosure** = show essentials, reveal details on demand

## Proposed Changes

### 1. TopBar Redesign (Mobile)

**Current**: `[Globe] [中 ▾] [🔍] [☰] [🔔] [8]`

**Proposed**:
```
┌─────────────────────────────────┐
│  Page Title          [🔍] [🔔8] │
└─────────────────────────────────┘
```

- Show **page title** (e.g., "Parser", "Library", "Resources", "Dashboard") on the left
- Only 2 contextual action buttons on the right: search + notifications
- Language/globe → move to Settings/Profile page
- Filter icon → contextual per-page (only show when relevant)
- User avatar → accessible via bottom tab "Profile" or long-press on tab

### 2. Bottom Tab Bar Enhancement

**Current**: `[🔍 Parser] [📚 Library] [📊 Dashboard] [👤 User]`

**Proposed**:
```
┌─────────────────────────────────────────┐
│  🔍        📚        📁        📊    👤  │
│ Parser   Library  Resources Dashboard Me │
└─────────────────────────────────────────┘
```

- Add **Resources** tab (currently inaccessible from mobile nav)
- Add text labels below icons (app convention)
- Active tab: indigo color + slight scale up
- Haptic feedback on tap (if PWA supports)
- Long-press on Library → view mode popup (existing behavior, keep)

### 3. Content Area Spacing

```
Mobile padding rules:
- Top:    pt-14 (below TopBar, 56px)
- Bottom: pb-20 (above Tab Bar, 80px)
- Left/Right: px-4 (16px, consistent)
- Safe area: env(safe-area-inset-*) for notch devices
```

Ensure all pages follow this consistently:
- Parser: ✅ mostly OK
- Library: ⚠️ `p-0` on mobile — needs `px-4 pt-14 pb-20`
- Resources: ⚠️ inconsistent padding
- Dashboard: ⚠️ needs bottom padding for tab bar

### 4. Page-Specific Mobile Optimizations

#### Parser Page
- Input area should be sticky at top
- Tags section compact
- Worker/Parse Mode/Storage cards → horizontal scroll instead of vertical stack
- Pull-to-refresh to reload parsed media list

#### Library Page
- Grid: 2 columns (current ✅)
- Search: floating search button → full-width search bar on tap (current ✅)
- Pull-to-refresh
- Swipe left on card → quick actions (delete, share)

#### Resources Page
- Sidebar → drawer (done ✅)
- Grid view with proper spacing
- Breadcrumb → compact with overflow menu for deep paths
- Upload button → FAB (Floating Action Button) in bottom-right

#### Dashboard Page
- Stats cards → 2-column grid instead of single column
- Weekly Activity chart → full-width, swipeable
- Task list → compact with swipe actions

### 5. Gesture Support

| Gesture | Action |
|---------|--------|
| Pull down | Refresh current view |
| Swipe left on item | Quick actions (delete, share) |
| Swipe right on item | Star/favorite |
| Long press | Context menu |
| Pinch (media) | Zoom in/out on thumbnails |

### 6. EagleTagPicker Mobile

- Floating panel → full-screen bottom sheet on mobile
- Category sidebar → horizontal scroll tabs at top
- Search always visible at top
- Tags in a scrollable grid

## Implementation Priority

### Phase 1: Critical Fixes (immediate)
- [x] Resources sidebar hidden on mobile (drawer)
- [ ] Consistent content spacing (pt-14 pb-20 px-4)
- [ ] Bottom tab: add Resources entry + text labels

### Phase 2: App-Like TopBar
- [ ] Redesign TopBar for mobile (title + 2 actions)
- [ ] Move language/globe to settings

### Phase 3: Enhanced Interactions
- [ ] Pull-to-refresh on all list views
- [ ] EagleTagPicker bottom sheet on mobile
- [ ] Dashboard 2-column stats grid

### Phase 4: Gestures & Polish
- [ ] Swipe actions on list items
- [ ] Smooth transitions between views
- [ ] Loading skeletons optimized for mobile

## Technical Notes

- Use Tailwind responsive prefixes: `md:` (768px) as the mobile/desktop breakpoint
- Keep `sm:` (640px) for the main Sidebar visibility (existing pattern)
- Test on: iPhone 14 Pro Max (430px), iPhone SE (375px), Android (360px)
- PWA manifest already configured — leverage for app-like install experience
