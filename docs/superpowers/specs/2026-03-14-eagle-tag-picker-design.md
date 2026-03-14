# Eagle-Style Tag Picker Design

**Date:** 2026-03-14
**Status:** Approved

## Overview

Replace `UnifiedTagPicker` and `ParserTagSelector` with a unified `EagleTagPicker` component inspired by Eagle app's tag management UI. The new picker features a floating panel with category sidebar, starred/recently used sections, display settings, and resizable layout.

## Problems Solved

1. **Tag ✕ button not working** — clicking remove on assigned tags does nothing (bug)
2. **Tag dropdown inside sidebar** — current picker expands within the sidebar, breaking layout
3. **No tag pinning** — users can't pin frequently used tags
4. **Two separate components** — UnifiedTagPicker and ParserTagSelector duplicate logic with different UX
5. **Poor organization for many tags** — no category sidebar for quick filtering

## Component Architecture

### Trigger (inside sidebar / form)

```
┌──────────────────────┐
│ Tags                 │
│ [人工智能] [音乐 ✕]  │  ← assigned tags, ✕ always visible (16px)
│ [转场 ✕] [+ Add Tag] │  ← click + Add Tag opens floating panel
└──────────────────────┘
```

- Assigned tag pills with **always-visible ✕ button** (16px, not hover-only)
- Click ✕ removes tag immediately
- `+ Add Tag` button opens the floating panel

### Floating Panel (portal, flies out to the left of sidebar)

```
┌─────────┬──────────────────────────┐
│ All  39 │ 🔍 Search...      ⚙️ ⊞  │
│ Uncat 0 │                          │
│─────────│ ⭐ STARRED (3)           │
│ GROUPS  │ ● tag1     ● tag2       │
│ Enter 6 │ ● tag3                   │
│ Life  8 │                          │
│ Know  6 │ 🕐 RECENTLY (6)         │
│ Creat 11│ ● tag4 (7) ● tag5 (5)   │
│ Sport 3 │ ● tag6 (5) ● tag7 (3)   │
│ Mood  5 │                          │
│         │ 📁 GROUP_NAME (count)    │
│         │ ● tag8 (1) ● tag9 (7)   │
│         │ ...                      │
└─────────┴──────────────────────────┘
  ↕ resizable by dragging edges/corners
```

**Structure:**
- **Left sidebar** (≈120px): All / Uncategorized / Groups list with counts
- **Right content area**: Search bar + settings gear + sections (Starred, Recently, Recommended, Grouped tags)
- **Rendered as Portal** (`position: fixed`), positioned to the left of the trigger element
- **Resizable** by dragging edges/corners, size persisted to backend

### Interactions

| Action | Behavior |
|--------|----------|
| Click tag in panel | Toggle: add to selected or remove if already selected |
| Right-click tag | Star / Unstar context menu |
| Click group in sidebar | Filter content to show only that group's tags |
| Click "All" | Show all tags (default) |
| Search | Filter by `name` and `name_zh` |
| Click outside panel | Close panel |
| ESC key | Close panel |

### Settings Panel (⚙️ gear icon)

Small popover from the gear icon:

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| Layout | Toggle (list/grid) | list | List view or grid view |
| Column Width | Select (S/M/L) | Medium | Tag column width |
| Starred | Switch | ON | Show/hide starred section |
| Recently | Switch | ON | Show/hide recently used section |
| Recommended | Switch (disabled) | OFF | AI-based recommendations (future) |
| Count | Switch | ON | Show/hide usage count next to tags |

### Selected Tags Display (outside panel, in sidebar/form)

- Tag pills with colored background (`{color}20` opacity)
- ✕ button: **16px, always visible** (fixes current bug where it's 10px hover-only)
- Click ✕ calls `onRemove(tagId)` — must actually work (fix current bug)

## Data Model

### New Table: `user_tag_preferences`

```sql
CREATE TABLE user_tag_preferences (
  user_id UUID REFERENCES auth.users(id) ON DELETE CASCADE,
  starred_tag_ids BIGINT[] DEFAULT '{}',
  picker_settings JSONB DEFAULT '{
    "layout": "list",
    "columnWidth": "medium",
    "showStarred": true,
    "showRecently": true,
    "showRecommended": false,
    "showCount": true
  }',
  panel_size JSONB DEFAULT '{"width": 480, "height": 400}',
  PRIMARY KEY (user_id)
);

-- RLS: users can only access their own preferences
ALTER TABLE user_tag_preferences ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can manage own tag preferences"
  ON user_tag_preferences FOR ALL
  USING (auth.uid() = user_id)
  WITH CHECK (auth.uid() = user_id);
```

### No Changes to Existing Tables

- `tags` table: unchanged
- `tag_groups` table: unchanged
- `resource_tags` table: unchanged

## API Endpoints

### New Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/tags/preferences` | GET | Get user's tag picker preferences (starred, settings, panel size) |
| `/api/v1/tags/preferences` | PUT | Update preferences (partial update via JSONB merge) |

### Request/Response

**GET /api/v1/tags/preferences**
```json
{
  "starred_tag_ids": ["123", "456", "789"],
  "picker_settings": {
    "layout": "list",
    "columnWidth": "medium",
    "showStarred": true,
    "showRecently": true,
    "showRecommended": false,
    "showCount": true
  },
  "panel_size": { "width": 480, "height": 400 }
}
```

**PUT /api/v1/tags/preferences**
```json
{
  "starred_tag_ids": ["123", "456"],
  "picker_settings": { "showCount": false },
  "panel_size": { "width": 600, "height": 500 }
}
```

All fields optional — only provided fields are updated.

## Component Props

```typescript
interface EagleTagPickerProps {
  // Mode 1: Resource detail (add/remove tags via API)
  resourceId?: string;
  mediaId?: string;

  // Mode 2: Form selection (controlled component)
  selectedTagIds?: string[];
  onTagsChange?: (tagIds: string[]) => void;

  // Shared
  allTags: Tag[];
  assignedTags?: Tag[];
  readOnly?: boolean;
  onCreate?: (name: string, color: string) => Promise<Tag | null>;
}
```

**Mode 1** (resource detail): Uses `resourceId` to call add/remove tag APIs directly.
**Mode 2** (parser form): Uses `selectedTagIds` + `onTagsChange` as controlled component.

## Component File Structure

```
frontend/components/
├── EagleTagPicker/
│   ├── index.tsx              # Main component + trigger
│   ├── FloatingPanel.tsx      # Portal-based floating panel
│   ├── CategorySidebar.tsx    # Left sidebar with groups
│   ├── TagContent.tsx         # Right content area (starred, recent, grouped)
│   ├── SettingsPopover.tsx    # Settings gear popover
│   ├── TagPill.tsx            # Single tag pill (selected state)
│   ├── TagRow.tsx             # Single tag row in panel
│   ├── useTagPreferences.ts   # Hook for preferences API
│   └── types.ts               # Local types
```

## Sections in Content Area

### Starred
- Tags the user has starred (from `user_tag_preferences.starred_tag_ids`)
- Star/unstar via right-click context menu
- Hidden when `showStarred` setting is OFF or no starred tags

### Recently Used
- Top 6 tags by `media_count` (from available/unselected tags)
- Hidden when `showRecently` setting is OFF

### Recommended
- AI-based recommendations (future feature)
- Setting toggle present but disabled/grayed out
- Hidden by default (`showRecommended: false`)

### Grouped Tags
- All tags grouped by `group_name`
- Filtered when a specific group is selected in sidebar
- Two-column layout (list mode) or flex-wrap (grid mode)

## Bug Fixes Included

1. **✕ button not removing tags**: Ensure `onRemove` is called correctly, button is 16px and always visible
2. **Panel inside sidebar**: Use Portal rendering with `position: fixed`, positioned relative to trigger

## Scope Exclusions

- **TagsSettings component**: Not modified (separate management UI)
- **Admin panel tag management**: Not modified
- **ShortcutsTagsPage**: Not modified (standalone page for iOS Shortcuts)
- **AI Recommended tags**: Setting toggle present but feature deferred
- **Tag creation in picker**: Preserved from current UnifiedTagPicker (inline create with color picker)
