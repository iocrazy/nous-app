# Mobile Resources Module Redesign — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redesign mobile Resources to use folder-based hierarchy navigation with context menus, drag-and-drop, and floating search — removing the tab bar and Downloads integration.

**Architecture:** Extract touch-gesture logic into reusable hooks (`useLongPress`, `useTouchDragDrop`). Modify `ResourcesView.tsx` mobile layout: replace tab bar with breadcrumb, hide toolbar search in favor of floating search button, add Recycle Bin as pinned virtual folder at root, and wire context menus to long-press/right-click. The existing `ContextMenu.tsx` component is reused as-is — it already supports positioning, viewport adjustment, and dismiss behavior.

**Tech Stack:** React 19, TypeScript, TailwindCSS, Lucide React icons, `createPortal` for floating search.

**Spec:** `docs/superpowers/specs/2026-03-15-mobile-resources-redesign-design.md`

---

## Chunk 1: Touch Gesture Hooks

### Task 1: `useLongPress` Hook

Create a hook that distinguishes between long-press (show context menu) and long-press + drag (start dragging). This is the foundation for both context menus and drag-and-drop on touch devices.

**Files:**
- Create: `frontend/hooks/useLongPress.ts`
- Test: `frontend/hooks/__tests__/useLongPress.test.ts`

**Interface:**

```typescript
interface UseLongPressOptions {
  onLongPress: (e: React.TouchEvent) => void;  // Fired after ~500ms hold without movement
  onDragStart?: (e: React.TouchEvent) => void;  // Fired when finger moves during long-press
  threshold?: number;       // ms to wait (default 500)
  moveThreshold?: number;   // px movement to trigger drag instead of menu (default 10)
}

interface UseLongPressReturn {
  onTouchStart: (e: React.TouchEvent) => void;
  onTouchMove: (e: React.TouchEvent) => void;
  onTouchEnd: (e: React.TouchEvent) => void;
  onTouchCancel: (e: React.TouchEvent) => void;
}
```

**Behavior:**
- On `touchstart`: record position, start 500ms timer
- On `touchmove`: if finger moves > `moveThreshold` px before timer fires → cancel timer, call `onDragStart` if provided
- On timer fire (500ms): finger stayed still → call `onLongPress` with original touch event
- On `touchend`/`touchcancel`: clear timer

- [ ] **Step 1: Write the failing test**

```typescript
// frontend/hooks/__tests__/useLongPress.test.ts
import { renderHook, act } from '@testing-library/react';
import { useLongPress } from '../useLongPress';

describe('useLongPress', () => {
  beforeEach(() => { jest.useFakeTimers(); });
  afterEach(() => { jest.useRealTimers(); });

  it('calls onLongPress after threshold when finger stays still', () => {
    const onLongPress = jest.fn();
    const { result } = renderHook(() => useLongPress({ onLongPress }));

    const touchEvent = {
      touches: [{ clientX: 100, clientY: 200 }],
      preventDefault: jest.fn(),
    } as unknown as React.TouchEvent;

    act(() => { result.current.onTouchStart(touchEvent); });
    act(() => { jest.advanceTimersByTime(500); });

    expect(onLongPress).toHaveBeenCalledTimes(1);
  });

  it('does not call onLongPress if touch ends before threshold', () => {
    const onLongPress = jest.fn();
    const { result } = renderHook(() => useLongPress({ onLongPress }));

    const touchEvent = {
      touches: [{ clientX: 100, clientY: 200 }],
      preventDefault: jest.fn(),
    } as unknown as React.TouchEvent;

    act(() => { result.current.onTouchStart(touchEvent); });
    act(() => { jest.advanceTimersByTime(300); });
    act(() => { result.current.onTouchEnd(touchEvent as any); });
    act(() => { jest.advanceTimersByTime(200); });

    expect(onLongPress).not.toHaveBeenCalled();
  });

  it('calls onDragStart when finger moves beyond moveThreshold during long-press', () => {
    const onLongPress = jest.fn();
    const onDragStart = jest.fn();
    const { result } = renderHook(() =>
      useLongPress({ onLongPress, onDragStart, moveThreshold: 10 })
    );

    const startEvent = {
      touches: [{ clientX: 100, clientY: 200 }],
      preventDefault: jest.fn(),
    } as unknown as React.TouchEvent;

    act(() => { result.current.onTouchStart(startEvent); });

    // Move finger 15px (beyond threshold)
    const moveEvent = {
      touches: [{ clientX: 115, clientY: 200 }],
      preventDefault: jest.fn(),
    } as unknown as React.TouchEvent;

    act(() => { result.current.onTouchMove(moveEvent); });

    expect(onDragStart).toHaveBeenCalledTimes(1);
    expect(onLongPress).not.toHaveBeenCalled();

    // Timer should not fire
    act(() => { jest.advanceTimersByTime(500); });
    expect(onLongPress).not.toHaveBeenCalled();
  });

  it('does not call onDragStart for small movements', () => {
    const onLongPress = jest.fn();
    const onDragStart = jest.fn();
    const { result } = renderHook(() =>
      useLongPress({ onLongPress, onDragStart, moveThreshold: 10 })
    );

    const startEvent = {
      touches: [{ clientX: 100, clientY: 200 }],
      preventDefault: jest.fn(),
    } as unknown as React.TouchEvent;

    act(() => { result.current.onTouchStart(startEvent); });

    // Move only 3px
    const moveEvent = {
      touches: [{ clientX: 103, clientY: 200 }],
      preventDefault: jest.fn(),
    } as unknown as React.TouchEvent;

    act(() => { result.current.onTouchMove(moveEvent); });

    expect(onDragStart).not.toHaveBeenCalled();

    // Long press should still fire
    act(() => { jest.advanceTimersByTime(500); });
    expect(onLongPress).toHaveBeenCalledTimes(1);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx jest hooks/__tests__/useLongPress.test.ts --no-cache`
Expected: FAIL — module not found

- [ ] **Step 3: Implement useLongPress**

```typescript
// frontend/hooks/useLongPress.ts
import { useRef, useCallback } from 'react';

interface UseLongPressOptions {
  onLongPress: (e: React.TouchEvent) => void;
  onDragStart?: (e: React.TouchEvent) => void;
  threshold?: number;
  moveThreshold?: number;
}

interface UseLongPressReturn {
  onTouchStart: (e: React.TouchEvent) => void;
  onTouchMove: (e: React.TouchEvent) => void;
  onTouchEnd: (e: React.TouchEvent) => void;
  onTouchCancel: (e: React.TouchEvent) => void;
}

export function useLongPress({
  onLongPress,
  onDragStart,
  threshold = 500,
  moveThreshold = 10,
}: UseLongPressOptions): UseLongPressReturn {
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const startPosRef = useRef<{ x: number; y: number } | null>(null);
  const firedRef = useRef(false);

  const clear = useCallback(() => {
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    startPosRef.current = null;
    firedRef.current = false;
  }, []);

  const onTouchStart = useCallback(
    (e: React.TouchEvent) => {
      const touch = e.touches[0];
      startPosRef.current = { x: touch.clientX, y: touch.clientY };
      firedRef.current = false;

      timerRef.current = setTimeout(() => {
        firedRef.current = true;
        onLongPress(e);
      }, threshold);
    },
    [onLongPress, threshold]
  );

  const onTouchMove = useCallback(
    (e: React.TouchEvent) => {
      if (!startPosRef.current || firedRef.current) return;

      const touch = e.touches[0];
      const dx = touch.clientX - startPosRef.current.x;
      const dy = touch.clientY - startPosRef.current.y;
      const distance = Math.sqrt(dx * dx + dy * dy);

      if (distance > moveThreshold) {
        if (timerRef.current) {
          clearTimeout(timerRef.current);
          timerRef.current = null;
        }
        firedRef.current = true;
        onDragStart?.(e);
      }
    },
    [onDragStart, moveThreshold]
  );

  const onTouchEnd = useCallback(() => { clear(); }, [clear]);
  const onTouchCancel = useCallback(() => { clear(); }, [clear]);

  return { onTouchStart, onTouchMove, onTouchEnd, onTouchCancel };
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx jest hooks/__tests__/useLongPress.test.ts --no-cache`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add frontend/hooks/useLongPress.ts frontend/hooks/__tests__/useLongPress.test.ts
git commit -m "feat: add useLongPress hook for touch long-press and drag detection"
```

---

### Task 2: `useTouchDragDrop` Hook

Hook for managing touch-based drag-and-drop state: tracking the dragged item, detecting drop targets (folders), and executing the move.

**Files:**
- Create: `frontend/hooks/useTouchDragDrop.ts`

**Interface:**

```typescript
interface DragState {
  isDragging: boolean;
  dragIds: string[];           // IDs being dragged (supports multi-select)
  dragPosition: { x: number; y: number } | null;  // Current finger position
  dropTargetId: string | null; // Folder ID currently being hovered
}

interface UseTouchDragDropOptions {
  onDrop: (dragIds: string[], targetFolderId: string) => void;
  selectedIds: Set<string>;    // For multi-select drag
}

interface UseTouchDragDropReturn {
  dragState: DragState;
  startDrag: (itemId: string, e: React.TouchEvent) => void;
  handleTouchMove: (e: React.TouchEvent) => void;
  handleTouchEnd: () => void;
  cancelDrag: () => void;
  isDropTarget: (folderId: string) => boolean;
}
```

**Behavior:**
- `startDrag`: Records item ID (or all selected IDs if item is in selection), sets `isDragging = true`
- `handleTouchMove`: Updates `dragPosition`, uses `document.elementFromPoint` to detect folder drop targets via `[data-folder-id]` attribute
- `handleTouchEnd`: If over a valid folder target → call `onDrop(dragIds, targetFolderId)`, then reset
- `cancelDrag`: Resets all state
- Drag preview: Component renders a floating element at `dragPosition` (handled in ResourcesView)

- [ ] **Step 1: Write the hook**

```typescript
// frontend/hooks/useTouchDragDrop.ts
import { useState, useCallback, useRef } from 'react';

interface DragState {
  isDragging: boolean;
  dragIds: string[];
  dragPosition: { x: number; y: number } | null;
  dropTargetId: string | null;
}

interface UseTouchDragDropOptions {
  onDrop: (dragIds: string[], targetFolderId: string) => void;
  selectedIds: Set<string>;
}

const INITIAL_STATE: DragState = {
  isDragging: false,
  dragIds: [],
  dragPosition: null,
  dropTargetId: null,
};

export function useTouchDragDrop({ onDrop, selectedIds }: UseTouchDragDropOptions) {
  const [dragState, setDragState] = useState<DragState>(INITIAL_STATE);
  const dragStateRef = useRef(INITIAL_STATE);

  const startDrag = useCallback(
    (itemId: string, e: React.TouchEvent) => {
      const touch = e.touches[0];
      const ids = selectedIds.has(itemId)
        ? Array.from(selectedIds)
        : [itemId];

      const newState: DragState = {
        isDragging: true,
        dragIds: ids,
        dragPosition: { x: touch.clientX, y: touch.clientY },
        dropTargetId: null,
      };
      dragStateRef.current = newState;
      setDragState(newState);
    },
    [selectedIds]
  );

  const handleTouchMove = useCallback((e: React.TouchEvent) => {
    if (!dragStateRef.current.isDragging) return;
    e.preventDefault();

    const touch = e.touches[0];
    const { clientX: x, clientY: y } = touch;

    // Detect drop target via data attribute
    // Temporarily hide drag preview to find element underneath
    const elements = document.elementsFromPoint(x, y);
    let targetId: string | null = null;
    for (const el of elements) {
      const folderId = (el as HTMLElement).closest('[data-folder-id]')
        ?.getAttribute('data-folder-id');
      if (folderId && !dragStateRef.current.dragIds.includes(folderId)) {
        targetId = folderId;
        break;
      }
    }

    // Also check breadcrumb segments
    if (!targetId) {
      for (const el of elements) {
        const breadcrumbFolderId = (el as HTMLElement).closest('[data-breadcrumb-folder-id]')
          ?.getAttribute('data-breadcrumb-folder-id');
        if (breadcrumbFolderId) {
          targetId = breadcrumbFolderId;
          break;
        }
      }
    }

    const newState: DragState = {
      ...dragStateRef.current,
      dragPosition: { x, y },
      dropTargetId: targetId,
    };
    dragStateRef.current = newState;
    setDragState(newState);
  }, []);

  const handleTouchEnd = useCallback(() => {
    const { isDragging, dragIds, dropTargetId } = dragStateRef.current;
    if (isDragging && dropTargetId && dragIds.length > 0) {
      onDrop(dragIds, dropTargetId);
    }
    dragStateRef.current = INITIAL_STATE;
    setDragState(INITIAL_STATE);
  }, [onDrop]);

  const cancelDrag = useCallback(() => {
    dragStateRef.current = INITIAL_STATE;
    setDragState(INITIAL_STATE);
  }, []);

  const isDropTarget = useCallback(
    (folderId: string) => dragState.dropTargetId === folderId,
    [dragState.dropTargetId]
  );

  return {
    dragState,
    startDrag,
    handleTouchMove,
    handleTouchEnd,
    cancelDrag,
    isDropTarget,
  };
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/hooks/useTouchDragDrop.ts
git commit -m "feat: add useTouchDragDrop hook for mobile drag-and-drop"
```

---

## Chunk 2: Mobile ResourcesView Layout Changes

### Task 3: Remove Mobile Tab Bar & Downloads Route

Remove the Downloads tab from mobile Resources. The `isDownloadsView` / `DownloadsView` embedding stays for desktop sidebar navigation but the mobile horizontal tab bar is replaced entirely.

**Files:**
- Modify: `frontend/components/ResourcesView.tsx` (lines 2274-2321)

**Current code** (mobile tab bar, lines 2274-2321):
```tsx
{/* Mobile section tabs */}
<div className="md:hidden border-b border-zinc-800/60">
  <div className="flex items-center gap-1 px-3 py-2 overflow-x-auto no-scrollbar">
    {/* Downloads, My Resources, Recycle Bin buttons */}
  </div>
</div>
```

**New code** — replace with mobile breadcrumb:
```tsx
{/* Mobile breadcrumb navigation */}
<div className="md:hidden border-b border-zinc-800/60 px-3 py-2">
  <Breadcrumb segments={breadcrumbSegments} />
</div>
```

- [ ] **Step 1: Replace mobile tab bar with mobile breadcrumb**

In `ResourcesView.tsx`, find the `{/* Mobile section tabs */}` block (lines 2274-2321). Replace the entire `<div className="md:hidden border-b border-zinc-800/60">` block with:

```tsx
{/* Mobile breadcrumb navigation — replaces horizontal tabs */}
{!isDownloadsView && (
  <div className="md:hidden border-b border-zinc-800/60 px-3 py-2.5 min-h-[40px]">
    <Breadcrumb segments={breadcrumbSegments} />
  </div>
)}
```

- [ ] **Step 2: Hide desktop toolbar on mobile**

The toolbar (lines 2327-2450+) contains search, filter, sort, view toggle, upload — these should be hidden on mobile (search moves to floating button, other controls removed per spec).

Wrap the toolbar `<div>` with `hidden md:block`:

```tsx
{/* Toolbar — desktop only, mobile uses floating search */}
<div
  className="hidden md:block px-3 md:px-6 py-3 border-b border-zinc-800/80"
  style={{ paddingRight: ... }}
>
```

- [ ] **Step 3: Verify desktop is unchanged**

Open Resources page on a desktop-width browser. The sidebar, toolbar, and grid should all render exactly as before.

- [ ] **Step 4: Commit**

```bash
git add frontend/components/ResourcesView.tsx
git commit -m "feat(mobile): replace Resources tab bar with breadcrumb, hide toolbar"
```

---

### Task 4: Add Recycle Bin as Pinned Virtual Folder at Root

On mobile, show a "Recycle Bin" row pinned at the top of the root folder view. Tapping it navigates to `/resources/section/recycle`. Only visible when at root level (no `selectedFolderId`).

**Files:**
- Modify: `frontend/components/ResourcesView.tsx` (content area, around line 2680)

- [ ] **Step 1: Add Recycle Bin pinned row**

In the content area, just before the `{creatingFolder && ...}` inline new-folder input (around line 2680), add the recycle bin entry. It should only show on mobile at root level:

```tsx
{/* Recycle Bin — pinned at top of root on mobile */}
{!selectedFolderId && !selectedSmartFolderId && !selectedLibraryId && isResourcesView && !isRecycleView && (
  <div className="md:hidden mb-3">
    <button
      onClick={() => navigate(resPath('/resources/recycle'))}
      className="w-full flex items-center gap-3 px-4 py-3 bg-zinc-800/30 hover:bg-zinc-800/60 rounded-xl border border-zinc-700/30 transition-colors"
    >
      <div className="w-10 h-10 rounded-lg bg-zinc-700/40 flex items-center justify-center">
        <Trash2 size={18} className="text-zinc-400" />
      </div>
      <div className="flex-1 text-left">
        <span className="text-sm text-zinc-300 font-medium">Recycle Bin</span>
        {(trashedResources.length > 0 || trashedFolders.length > 0) && (
          <span className="ml-2 text-xs text-zinc-500">
            {trashedResources.length + trashedFolders.length} items
          </span>
        )}
      </div>
      <ChevronRight size={16} className="text-zinc-600" />
    </button>
  </div>
)}
```

**Note:** `ChevronRight` should already be imported from Lucide. If not, add to the import.

- [ ] **Step 2: Load trashed counts at root level**

The `trashedResources` and `trashedFolders` state already exists in ResourcesView (used by recycle view). Check if they're loaded when at root. If only loaded when `isRecycleView`, add a light count fetch on mount. If they're already populated, skip this step.

Look at the existing `useEffect` that fetches trashed data — if it only runs when `isRecycleView`, add a separate lightweight count query:

```typescript
// Fetch trashed item count for Recycle Bin badge (root view only)
useEffect(() => {
  if (!isResourcesView || selectedFolderId || isRecycleView) return;
  // Reuse existing fetch functions — they already set trashedResources/trashedFolders
  fetchTrashedResources();
  fetchTrashedFolders();
}, [isResourcesView, selectedFolderId, isRecycleView]);
```

- [ ] **Step 3: Commit**

```bash
git add frontend/components/ResourcesView.tsx
git commit -m "feat(mobile): add Recycle Bin as pinned virtual folder at root"
```

---

### Task 5: Floating Search Button (Mobile)

Add a floating search button on mobile that expands to full-width search input, matching the Downloads/LibraryPage pattern. Uses `createPortal` to body.

**Files:**
- Modify: `frontend/components/ResourcesView.tsx`

**Reference pattern** from `LibraryPage.tsx`:
```tsx
createPortal(
  <div className="md:hidden fixed top-14 left-0 right-0 z-40 p-3 flex justify-end ...">
    {isMobileSearchOpen ? (
      // Full-width search input with backdrop blur
    ) : (
      // Small circular search icon button
    )}
  </div>,
  document.body
)
```

- [ ] **Step 1: Add mobile search state**

In ResourcesView, add state:

```typescript
const [isMobileSearchOpen, setIsMobileSearchOpen] = useState(false);
const mobileSearchInputRef = useRef<HTMLInputElement>(null);
```

- [ ] **Step 2: Add floating search button portal**

At the end of ResourcesView's return, before the closing `</div>`, add:

```tsx
{/* Mobile floating search button — portal to body */}
{isResourcesView && !isRecycleView && !isSharedView && createPortal(
  <div className="md:hidden fixed top-14 left-0 right-0 z-40 p-3 flex justify-end items-start pointer-events-none">
    <div className="pointer-events-auto flex items-center justify-end w-full">
      {isMobileSearchOpen ? (
        <div className="flex items-center bg-black/50 backdrop-blur-md rounded-full px-4 py-2.5 w-full animate-in slide-in-from-right-10 duration-200">
          <Search size={16} className="text-zinc-300 mr-2 flex-shrink-0" />
          <input
            ref={mobileSearchInputRef}
            autoFocus
            type="text"
            value={searchQuery}
            onChange={(e) => handleResourceQueryChange(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && searchQuery.trim()) {
                handleResourceAISearch(searchQuery, 'hybrid');
              }
              if (e.key === 'Escape') {
                setIsMobileSearchOpen(false);
                handleResourceSearchClear();
              }
            }}
            placeholder={t('resources.searchFiles')}
            className="bg-transparent text-sm text-zinc-100 placeholder-zinc-500 outline-none w-full"
          />
          {searchQuery && (
            <button
              onClick={() => handleResourceSearchClear()}
              className="ml-1 text-zinc-400 hover:text-zinc-200"
            >
              <X size={14} />
            </button>
          )}
          <button
            onClick={() => {
              setIsMobileSearchOpen(false);
              if (!searchQuery) handleResourceSearchClear();
            }}
            className="ml-2 text-zinc-400 hover:text-zinc-200"
          >
            <X size={16} />
          </button>
        </div>
      ) : (
        <button
          onClick={() => setIsMobileSearchOpen(true)}
          className="p-3 bg-black/20 backdrop-blur-md rounded-full hover:bg-black/40 transition-colors"
        >
          <Search size={22} className="text-zinc-300" />
        </button>
      )}
    </div>
  </div>,
  document.body
)}
```

- [ ] **Step 3: Import createPortal**

Ensure `createPortal` is imported from `react-dom`:

```typescript
import { createPortal } from 'react-dom';
```

Also ensure `X` is imported from `lucide-react` (likely already imported).

- [ ] **Step 4: Commit**

```bash
git add frontend/components/ResourcesView.tsx
git commit -m "feat(mobile): add floating search button for Resources"
```

---

## Chunk 3: Context Menus & Touch Interactions

### Task 6: Wire Long-Press Context Menu on Mobile

Add long-press handlers to file/folder cards and empty areas for mobile context menu triggering. The existing `ContextMenu` component is reused — we only need to trigger it from touch events in addition to right-click.

**Files:**
- Modify: `frontend/components/ResourcesView.tsx`

**Current state:** Context menus already work via `onContextMenu` (right-click) on desktop. Functions `handleEmptyAreaContextMenu`, `handleFileContextMenu`, `handleFolderContextMenu` already exist and set the `contextMenu` state that renders `<ContextMenu>`.

**Goal:** Add `useLongPress` to the content area and individual cards so long-press on mobile triggers the same context menu functions.

- [ ] **Step 1: Import useLongPress**

```typescript
import { useLongPress } from '../hooks/useLongPress';
```

- [ ] **Step 2: Add long-press for empty area**

Create a long-press handler for the content area div:

```typescript
const emptyAreaLongPress = useLongPress({
  onLongPress: (e) => {
    if (!isResourcesView) return;
    const touch = e.touches[0];
    // Check if touch was on empty area (not on a card)
    const target = e.target as HTMLElement;
    if (target.closest('[data-context-item]')) return;

    handleEmptyAreaContextMenu({
      preventDefault: () => {},
      clientX: touch.clientX,
      clientY: touch.clientY,
    } as unknown as React.MouseEvent);
  },
});
```

Spread `emptyAreaLongPress` onto the content area `<div>`:

```tsx
<div
  className="flex-1 overflow-y-auto p-6 relative"
  {...emptyAreaLongPress}
  onContextMenu={isResourcesView ? handleEmptyAreaContextMenu : undefined}
  // ... existing onClick, onDragEnter, etc.
>
```

- [ ] **Step 3: Add long-press handlers for cards**

For ResourceCard and FolderCard containers, we need to pass touch event handlers. Since cards are rendered inside `.map()`, we'll create a wrapper component or add inline handlers.

**Approach:** Create helper functions that return long-press handlers for a specific item:

```typescript
const getItemLongPressHandlers = useCallback(
  (itemType: 'file' | 'folder', item: any) => {
    let timer: ReturnType<typeof setTimeout> | null = null;
    let startPos: { x: number; y: number } | null = null;

    return {
      onTouchStart: (e: React.TouchEvent) => {
        const touch = e.touches[0];
        startPos = { x: touch.clientX, y: touch.clientY };
        timer = setTimeout(() => {
          const mockEvent = {
            preventDefault: () => {},
            clientX: touch.clientX,
            clientY: touch.clientY,
          } as unknown as React.MouseEvent;

          if (itemType === 'file') {
            handleFileContextMenu(mockEvent, item);
          } else {
            handleFolderContextMenu(mockEvent, item);
          }
        }, 500);
      },
      onTouchMove: (e: React.TouchEvent) => {
        if (!startPos || !timer) return;
        const touch = e.touches[0];
        const dx = touch.clientX - startPos.x;
        const dy = touch.clientY - startPos.y;
        if (Math.sqrt(dx * dx + dy * dy) > 10) {
          clearTimeout(timer);
          timer = null;
        }
      },
      onTouchEnd: () => {
        if (timer) { clearTimeout(timer); timer = null; }
        startPos = null;
      },
      onTouchCancel: () => {
        if (timer) { clearTimeout(timer); timer = null; }
        startPos = null;
      },
    };
  },
  [handleFileContextMenu, handleFolderContextMenu]
);
```

Then on each card's container `<div>`:

```tsx
{/* For folder cards */}
<div
  key={folder.id}
  data-folder-id={String(folder.id)}
  {...getItemLongPressHandlers('folder', folder)}
  onContextMenu={(e) => handleFolderContextMenu(e, folder)}
  onDoubleClick={() => navigate(resPath(`/resources/folder/${folder.id}`))}
>
  <FolderCard ... />
</div>

{/* For resource cards */}
<div
  key={item.resource.id}
  {...getItemLongPressHandlers('file', item)}
  onContextMenu={(e) => handleFileContextMenu(e, item)}
  onDoubleClick={() => handleResourceDoubleClick(item)}
>
  <ResourceCard ... />
</div>
```

- [ ] **Step 4: Test on mobile (manual)**

1. Open Resources on mobile viewport
2. Long-press empty area → should show "New Folder / Upload" context menu
3. Long-press a file → should show file context menu
4. Long-press a folder → should show folder context menu
5. Right-click should still work on desktop

- [ ] **Step 5: Commit**

```bash
git add frontend/components/ResourcesView.tsx
git commit -m "feat(mobile): add long-press context menus for files, folders, and empty area"
```

---

### Task 7: Wire Touch Drag-and-Drop

Integrate `useTouchDragDrop` into ResourcesView for mobile file/folder moving via drag gesture.

**Files:**
- Modify: `frontend/components/ResourcesView.tsx`

- [ ] **Step 1: Import and initialize hook**

```typescript
import { useTouchDragDrop } from '../hooks/useTouchDragDrop';
```

Initialize the hook in ResourcesView:

```typescript
const { dragState, startDrag, handleTouchMove, handleTouchEnd, cancelDrag, isDropTarget } =
  useTouchDragDrop({
    onDrop: async (dragIds, targetFolderId) => {
      // Move items to target folder
      try {
        for (const id of dragIds) {
          if (id.startsWith('folder:')) {
            const folderId = id.replace('folder:', '');
            await resourceService.moveFolder(folderId, targetFolderId);
          } else {
            const resourceId = id.replace('item:', '');
            await resourceService.moveResource(resourceId, targetFolderId);
          }
        }
        addToast(`Moved ${dragIds.length} item(s)`, 'success');
        refreshCurrentView();
      } catch (err) {
        console.error('Failed to move items:', err);
        addToast('Failed to move items', 'error');
      }
    },
    selectedIds,
  });
```

- [ ] **Step 2: Update long-press handlers to support drag initiation**

Modify the `getItemLongPressHandlers` from Task 6 to also initiate drag when finger moves during long-press:

In the `onTouchMove` handler, when movement exceeds threshold, instead of just canceling the timer, also call `startDrag`:

```typescript
onTouchMove: (e: React.TouchEvent) => {
  if (!startPos || !timer) return;
  const touch = e.touches[0];
  const dx = touch.clientX - startPos.x;
  const dy = touch.clientY - startPos.y;
  if (Math.sqrt(dx * dx + dy * dy) > 10) {
    clearTimeout(timer);
    timer = null;
    // Start drag instead
    const compositeId = itemType === 'folder'
      ? `folder:${item.id}`
      : `item:${item.resource?.id || item.id}`;
    startDrag(compositeId, e);
  }
},
```

- [ ] **Step 3: Add touch move/end handlers to content area**

On the content area `<div>`, add:

```tsx
<div
  className="flex-1 overflow-y-auto p-6 relative"
  onTouchMove={dragState.isDragging ? handleTouchMove : undefined}
  onTouchEnd={dragState.isDragging ? handleTouchEnd : undefined}
  // ... existing handlers
>
```

- [ ] **Step 4: Add `data-folder-id` attribute to FolderCard wrappers**

Ensure every folder card wrapper has `data-folder-id` so the drag-drop hook can detect drop targets:

```tsx
<div key={folder.id} data-folder-id={String(folder.id)} data-context-item>
```

Also add `data-breadcrumb-folder-id` to breadcrumb segments for drop-on-breadcrumb support. This requires modifying `Breadcrumb.tsx`:

```tsx
// In Breadcrumb.tsx, for each clickable segment:
<button
  data-breadcrumb-folder-id={segment.id || 'root'}
  onClick={() => segment.onClick?.()}
>
  {segment.label}
</button>
```

- [ ] **Step 5: Add drop target highlight styling**

On FolderCard wrapper, add conditional styling when it's a drop target:

```tsx
<div
  key={folder.id}
  data-folder-id={String(folder.id)}
  className={isDropTarget(String(folder.id)) ? 'ring-2 ring-indigo-500 rounded-xl' : ''}
>
```

- [ ] **Step 6: Add drag preview overlay**

Render a floating drag preview at the current drag position:

```tsx
{/* Touch drag preview */}
{dragState.isDragging && dragState.dragPosition && createPortal(
  <div
    className="fixed z-[100] pointer-events-none flex items-center gap-2 bg-zinc-800/90 backdrop-blur-sm border border-zinc-600 rounded-lg px-3 py-2 shadow-2xl"
    style={{
      left: dragState.dragPosition.x - 40,
      top: dragState.dragPosition.y - 20,
    }}
  >
    <Move size={14} className="text-indigo-400" />
    <span className="text-sm text-zinc-200">
      {dragState.dragIds.length === 1 ? 'Moving item' : `${dragState.dragIds.length} items`}
    </span>
  </div>,
  document.body
)}
```

- [ ] **Step 7: Test drag-and-drop (manual)**

1. Long-press a file and move finger → drag preview appears
2. Drag over a folder → folder highlights with ring
3. Drop on folder → item moves, toast shows success
4. Long-press without moving → context menu appears (not drag)

- [ ] **Step 8: Commit**

```bash
git add frontend/components/ResourcesView.tsx frontend/hooks/useTouchDragDrop.ts frontend/components/Breadcrumb.tsx
git commit -m "feat(mobile): add touch drag-and-drop for moving files/folders"
```

---

## Chunk 4: Double-Tap Navigation & Polish

### Task 8: Double-Tap Folder Enter & File Open

Ensure double-tap on folders navigates into them and double-tap on files opens the detail page. On mobile, single tap selects (shows info panel), double-tap navigates.

**Files:**
- Modify: `frontend/components/ResourcesView.tsx`

**Current state:** `onDoubleClick` handlers already exist on card wrappers:
- Folder: `onDoubleClick={() => navigate(resPath('/resources/folder/${folder.id}'))}`
- File: `onDoubleClick={() => handleResourceDoubleClick(item)}`

These work on desktop with mouse double-click. On mobile, `onDoubleClick` events are not reliably fired on touch. We need to implement double-tap detection.

- [ ] **Step 1: Add double-tap detection state**

```typescript
const lastTapRef = useRef<{ id: string; time: number } | null>(null);
const DOUBLE_TAP_DELAY = 300; // ms

const handleTap = useCallback(
  (id: string, onDoubleTap: () => void) => {
    const now = Date.now();
    if (lastTapRef.current && lastTapRef.current.id === id && now - lastTapRef.current.time < DOUBLE_TAP_DELAY) {
      // Double tap
      lastTapRef.current = null;
      onDoubleTap();
    } else {
      lastTapRef.current = { id, time: now };
    }
  },
  []
);
```

- [ ] **Step 2: Wire handleTap into card onClick handlers**

For folder cards, modify the `onClick` to detect double-tap on mobile:

```tsx
onClick={(e) => {
  // Existing single-click selection logic
  handleItemSelect(compositeId, e);
  // Double-tap detection for mobile
  handleTap(`folder:${folder.id}`, () => {
    navigate(resPath(`/resources/folder/${folder.id}`));
  });
}}
```

For resource cards:

```tsx
onClick={(e) => {
  handleItemSelect(compositeId, e);
  handleTap(`item:${item.resource.id}`, () => {
    handleResourceDoubleClick(item);
  });
}}
```

**Note:** Keep the existing `onDoubleClick` for desktop — they don't conflict with the tap detection.

- [ ] **Step 3: Navigate to file detail on double-tap**

Verify `handleResourceDoubleClick` navigates to `/resources/file/{id}`. Check its implementation:

```typescript
// Should already exist, verify it navigates:
const handleResourceDoubleClick = (item: ResourceItem) => {
  navigate(`/resources/file/${item.resource.id}`);
};
```

- [ ] **Step 4: Commit**

```bash
git add frontend/components/ResourcesView.tsx
git commit -m "feat(mobile): add double-tap navigation for folders and files"
```

---

### Task 9: Swipe-Left to Go Back

On mobile, swipe-left gesture navigates to the parent folder. This uses the browser's native back gesture on iOS/Android when the page is at the leftmost scroll position.

**Files:**
- Modify: `frontend/components/ResourcesView.tsx`

**Approach:** Rather than implementing a custom swipe gesture (which conflicts with native browser swipe-back), leverage the existing breadcrumb navigation. The mobile breadcrumb already shows the full path with tappable segments. Users can tap any parent segment to navigate up.

For a more explicit "back" button, add a back arrow to the mobile breadcrumb when inside a subfolder:

- [ ] **Step 1: Add back button to mobile breadcrumb**

Modify the mobile breadcrumb area to include a back button when inside a folder:

```tsx
{/* Mobile breadcrumb navigation */}
{!isDownloadsView && (
  <div className="md:hidden border-b border-zinc-800/60 px-3 py-2.5 min-h-[40px] flex items-center gap-2">
    {(selectedFolderId || isRecycleView) && (
      <button
        onClick={() => {
          if (isRecycleView) {
            if (recycleFolderId) {
              setRecycleFolderId(null);
            } else {
              navigate(resPath('/resources'));
            }
          } else if (folderChain.length > 1) {
            const parentId = folderChain[folderChain.length - 2]?.id;
            navigate(resPath(parentId ? `/resources/folder/${parentId}` : '/resources'));
          } else {
            navigate(resPath('/resources'));
          }
        }}
        className="p-1 -ml-1 text-zinc-400 hover:text-zinc-200"
      >
        <ChevronLeft size={20} />
      </button>
    )}
    <div className="flex-1 min-w-0">
      <Breadcrumb segments={breadcrumbSegments} />
    </div>
  </div>
)}
```

Import `ChevronLeft` from lucide-react if not already imported.

- [ ] **Step 2: Verify native swipe-back works**

On iOS Safari and Android Chrome, the native back swipe gesture should work automatically since we use `navigate()` which pushes to history. No custom implementation needed — the browser handles it.

- [ ] **Step 3: Commit**

```bash
git add frontend/components/ResourcesView.tsx
git commit -m "feat(mobile): add back button to breadcrumb for folder navigation"
```

---

### Task 10: Final Polish & Integration Testing

Ensure all pieces work together. Test the complete mobile flow.

**Files:**
- Modify: `frontend/components/ResourcesView.tsx` (minor fixes if needed)

- [ ] **Step 1: Verify mobile flow end-to-end**

Test checklist (mobile viewport):
1. ✅ Root view shows: Recycle Bin (pinned) + folders + files
2. ✅ No tab bar (Downloads / My Resources / Recycle Bin tabs removed)
3. ✅ Breadcrumb shows at top with current path
4. ✅ Double-tap folder → enters folder, breadcrumb updates
5. ✅ Double-tap file → opens detail page
6. ✅ Back button in breadcrumb → goes up one level
7. ✅ Floating search button → expands to search input
8. ✅ Long-press empty area → context menu (New Folder, Upload)
9. ✅ Long-press file/folder → context menu (Open, Rename, Move, Copy, Trash)
10. ✅ Right-click → same context menus (desktop)
11. ✅ Long-press + move → drag preview, drop on folder to move
12. ✅ Recycle Bin → shows trashed items with restore/delete
13. ✅ Desktop layout → completely unchanged

- [ ] **Step 2: Fix any layout issues**

Common issues to check:
- Content area padding on mobile (reduce `p-6` to `p-3` on mobile: `p-3 md:p-6`)
- Context menu positioning near screen edges
- Floating search z-index conflicts
- Drag preview visibility during scroll

- [ ] **Step 3: Final commit**

```bash
git add -A
git commit -m "fix(mobile): polish Resources layout and interactions"
```

---

## Summary

| Task | Description | New Files | Modified Files |
|------|-------------|-----------|----------------|
| 1 | `useLongPress` hook | `hooks/useLongPress.ts`, `hooks/__tests__/useLongPress.test.ts` | — |
| 2 | `useTouchDragDrop` hook | `hooks/useTouchDragDrop.ts` | — |
| 3 | Remove mobile tab bar, add breadcrumb | — | `ResourcesView.tsx` |
| 4 | Recycle Bin pinned folder | — | `ResourcesView.tsx` |
| 5 | Floating search button | — | `ResourcesView.tsx` |
| 6 | Long-press context menus | — | `ResourcesView.tsx` |
| 7 | Touch drag-and-drop | — | `ResourcesView.tsx`, `Breadcrumb.tsx` |
| 8 | Double-tap navigation | — | `ResourcesView.tsx` |
| 9 | Swipe-left / back button | — | `ResourcesView.tsx` |
| 10 | Polish & integration testing | — | `ResourcesView.tsx` |
