# Mobile Resources Module Redesign

## Goal

Redesign the mobile Resources module to use folder-based hierarchical navigation instead of tab-based navigation. Remove Downloads from Resources (already has its own bottom nav tab). Add context menus for file operations.

## Current State

- Mobile Resources uses horizontal tab bar: Downloads / My Resources / Recycle Bin
- Search bar, filter, sort, and upload buttons occupy toolbar space
- Downloads content shown inside Resources via DownloadsView component
- No context menu support (long-press / right-click)

## Design

### Navigation Structure

Replace tab bar with pure folder hierarchy navigation:

- **Root directory**: Recycle Bin (pinned top, special icon) + user folders + files
- **Folder navigation**: double-tap folder to enter, breadcrumb shows current path
- **Back**: swipe-left or tap breadcrumb to navigate up
- **No Downloads**: Resources module only shows `resources` table data

### Top Area (Mobile Only)

Clean top with breadcrumb path only:

```
My Resources > Project Assets > Icons
```

- Breadcrumb segments are tappable for quick navigation to any parent
- No search bar, no filter/sort buttons, no upload button in toolbar
- Desktop layout unchanged (sidebar + toolbar + grid)

### Floating Search Button

Right-side floating search button (matches Downloads mobile search pattern):

- Idle: small circular search icon button, positioned top-right (portal to body)
- Active: expands to full-width search input with backdrop blur
- Auto-close on blur
- Supports existing search modes (keyword / hybrid / semantic)

### Interactions

| Action | Target | Behavior |
|--------|--------|----------|
| Single tap | File or folder | Select item, show info panel (slide from right) |
| Double tap | Folder | Navigate into folder |
| Double tap | File | Navigate to detail page (`/resources/file/{id}`) |
| Long-press | Empty area | Show context menu (create/upload) |
| Right-click | Empty area | Show context menu (create/upload) |
| Long-press | File or folder | Show context menu OR start drag |
| Right-click | File or folder | Show context menu (file operations) |
| Drag (after long-press) | File or folder | Drag item over a folder to move it there |
| Swipe left | Anywhere | Navigate back to parent folder |

### Context Menus

**Empty area context menu** (long-press or right-click on blank space):

| Item | Icon | Action |
|------|------|--------|
| New Folder | FolderPlus | Create new folder in current directory |
| Upload File | Upload | Open file picker |
| Upload Folder | FolderUp | Open folder picker |
| Paste | Clipboard | Paste copied/cut items (shown only when clipboard has content) |

**File/folder context menu** (long-press or right-click on item):

| Item | Icon | Action |
|------|------|--------|
| Open | FolderOpen / Eye | Enter folder or open file detail |
| Rename | Pencil | Inline rename |
| Move to... | Move | Open folder picker for move destination |
| Copy | Copy | Copy to clipboard |
| Move to Trash | Trash2 | Soft-delete (set is_trashed=true) |

### Recycle Bin

- **Position**: Fixed at top of root directory file list, always visible
- **Visual**: Trash2 icon, "Recycle Bin" label, semi-transparent/gray style to distinguish from regular folders
- **Inside Recycle Bin**: Shows trashed files/folders with restore and permanent delete actions
- **Item actions**: Restore (move back to original location) and Delete Permanently
- **Not nested**: Recycle Bin only appears at root level, not inside subfolders

### Drag and Drop (Long-Press to Drag)

Long-press on a file or folder initiates either a context menu or a drag gesture:

- **Long-press without movement (~500ms)**: show context menu
- **Long-press + move finger**: start dragging the item

**Drag behavior:**
- Dragged item follows finger with a semi-transparent preview
- Folder targets highlight (border glow) when dragged item hovers over them
- Drop onto a folder: move the item into that folder (API call to update `folder_id`)
- Drop onto empty area: cancel drag (item stays in place)
- Drop onto breadcrumb segment: move item to that parent folder
- Visual feedback: source item dims during drag, target folder highlights

**Multi-select drag:**
- If multiple items are selected, dragging one drags all selected items
- Show count badge on drag preview (e.g., "3 items")

### Context Menu Component

Shared `ContextMenu` component for both menu types:

```typescript
interface ContextMenuProps {
  x: number;          // Position from touch/click event
  y: number;
  items: ContextMenuItem[];
  onClose: () => void;
}

interface ContextMenuItem {
  label: string;
  icon: ReactNode;
  onClick: () => void;
  variant?: 'default' | 'danger';  // danger = red text (delete)
  disabled?: boolean;
  hidden?: boolean;                // conditionally hide (e.g., Paste when no clipboard)
}
```

- Triggered by `onContextMenu` (right-click) and `onTouchStart`/`onTouchEnd` (long-press ~500ms)
- Positioned near touch/click point, adjusted to stay within viewport
- Backdrop overlay to dismiss on tap outside
- Renders via portal to body

### Unchanged

- Desktop layout (sidebar + toolbar + grid) — no changes
- Grid/list view toggle logic
- Multi-select mode (Cmd+Click / checkbox)
- Right-side info panel slide-out behavior
- ResourceCard and FolderCard components (reused as-is)
- File detail page (`/resources/file/{id}`)

## File Changes

| File | Change |
|------|--------|
| `components/ResourcesView.tsx` | Remove mobile tab bar, add breadcrumb, context menu handlers, long-press detection |
| `components/ContextMenu.tsx` | New shared context menu component |
| `hooks/useContextMenu.ts` | New hook for long-press and right-click detection |
| `hooks/useLongPress.ts` | New hook for touch long-press gesture (distinguishes press vs drag) |
| `hooks/useDragDrop.ts` | New hook for drag-and-drop on touch devices |
| `pages/LibraryPage.tsx` | No change (Downloads stays independent) |
| `components/Sidebar.tsx` | No change (desktop only) |

## Out of Scope

- Desktop layout changes
- Downloads integration into Resources
- Keyboard shortcuts for mobile
- Drag-and-drop between different views (e.g., Downloads → Resources)
