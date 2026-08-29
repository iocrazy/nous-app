/**
 * ResourceGrid.folderDoubleClick.test.tsx
 *
 * Double-clicking a folder while the info panel is closed must navigate INTO
 * the folder. The single-click handler used to open the info panel
 * synchronously; that shrinks the grid and reflows the auto-fill columns, so
 * the card moves out from under the mouse before the second click lands —
 * the dblclick never fires (or fires on the wrong card). Files were fixed by
 * deferring single-click selection 250ms (ResourcesViewInner.handleResourceClick);
 * folders must follow the same pattern: the single click is deferred and a
 * double-click cancels it, so the panel never opens mid-gesture.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';
import React from 'react';
import { ResourceGrid } from './ResourceGrid';
import type { ResourceGridProps } from './ResourceGrid';
import type { ResourceItem } from '../types';

// ── Browser API stubs (jsdom doesn't implement these) ─────────────────────────
beforeEach(() => {
  global.IntersectionObserver = vi.fn().mockImplementation(() => ({
    observe: vi.fn(),
    unobserve: vi.fn(),
    disconnect: vi.fn(),
  }));

  global.ResizeObserver = vi.fn().mockImplementation(() => ({
    observe: vi.fn(),
    unobserve: vi.fn(),
    disconnect: vi.fn(),
  }));
});

// ── Module mocks ───────────────────────────────────────────────────────────────

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
  Trans: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

vi.mock('../hooks/useFilterBarVisibility', () => ({
  useFilterBarVisibility: () => ({ visible: false, toggle: vi.fn() }),
}));


// Mock useGridVirtualizer to return exactly 5 virtual rows × 1 column (list mode).
// BEFORE implementation: the list branch doesn't call useGridVirtualizer at all,
// so all 500 items are rendered and no data-virtualized="list" exists → RED.
// AFTER implementation: ResourceGrid uses the virtualizer in the list branch,
// and only 5 cards are rendered inside data-virtualized="list" → GREEN.
vi.mock('../hooks/useGridVirtualizer', () => ({
  useGridVirtualizer: vi.fn(() => ({
    columns: 1,
    rowVirtualizer: {
      getTotalSize: () => 32000,
      getVirtualItems: () => [
        { index: 0, key: 'row-0', start: 0 },
        { index: 1, key: 'row-1', start: 64 },
        { index: 2, key: 'row-2', start: 128 },
        { index: 3, key: 'row-3', start: 192 },
        { index: 4, key: 'row-4', start: 256 },
      ],
      // measureElement is passed as a ref callback to each row div
      measureElement: vi.fn(),
    },
    containerRef: vi.fn(),
    totalSize: 32000,
  })),
}));

// Context mock — replaces the entire module so no real Provider is needed.
const mockUseResourcesContext = vi.hoisted(() => vi.fn());

// The justified virtualizer is now called unconditionally too — inert mock
// so its real @tanstack/react-virtual instance never runs in this suite.
vi.mock('../hooks/useJustifiedVirtualizer', () => ({
  useJustifiedVirtualizer: vi.fn(() => ({
    rows: [],
    rowVirtualizer: {
      getTotalSize: () => 0,
      getVirtualItems: () => [],
      measureElement: vi.fn(),
    },
    containerRef: vi.fn(),
    gap: 8,
  })),
}));

vi.mock('../contexts/ResourcesContext', () => ({
  useResourcesContext: mockUseResourcesContext,
}));

// Lightweight child stubs to avoid deep dependency trees.
vi.mock('./ResourceCard', () => ({
  ResourceCard: ({ item }: { item: ResourceItem }) => (
    <div data-testid="resource-card" data-id={item.id} />
  ),
}));

vi.mock('./FolderCard', () => ({
  FolderCard: ({ folder, onClick, onDoubleClick }: any) => (
    <div
      data-context-item
      data-testid="folder-card"
      data-id={String(folder.id)}
      onClick={(e) => onClick(e)}
      onDoubleClick={(e) => onDoubleClick?.(e)}
    />
  ),
}));

vi.mock('./Breadcrumb', () => ({
  Breadcrumb: () => null,
}));

vi.mock('./ToolbarSearch', () => ({
  ToolbarSearch: () => null,
}));

vi.mock('./resources/filter/FilterBar', () => ({
  FilterBar: () => null,
}));

vi.mock('./filters/FilterChipBar', () => ({
  FilterChipBar: () => null,
}));

vi.mock('./filters/FacetPickerSheet', () => ({
  FacetPickerSheet: () => null,
}));

vi.mock('./ResourceFetchUrlModal', () => ({
  ResourceFetchUrlModal: () => null,
}));


// ── Test data ──────────────────────────────────────────────────────────────────

const makeItem = (i: number): ResourceItem => ({
  id: `item-${i}`,
  resource_id: `resource-${i}`,
  scope_id: 'scope-1',
  folder_id: null,
  library_id: null,
  added_by: null,
  created_at: '2024-01-01T00:00:00Z',
});

const ITEMS_500 = Array.from({ length: 500 }, (_, i) => makeItem(i));

// ── Context value ──────────────────────────────────────────────────────────────

const buildCtx = (overrides: Record<string, unknown> = {}) => ({
  isPersonal: true,
  scopeId: 'scope-1',
  selectedFolderId: null,
  selectedLibraryId: null,
  selectedSmartFolderId: null,
  isResourcesView: true,
  isRecycleView: false,
  isSharedView: false,
  loading: false,
  viewMode: 'grid' as const,
  setViewMode: vi.fn(),
  flattenFolders: false,
  setFlattenFolders: vi.fn(),
  sortBy: 'newest',
  setSortBy: vi.fn(),
  searchQuery: '',
  folderChain: [],
  folderPreviews: {},
  selectedResource: null,
  setSelectedResource: vi.fn(),
  selectedFolder: null,
  setSelectedFolder: vi.fn(),
  selectedIds: new Set<string>(),
  setSelectedIds: vi.fn(),
  multiSelectMode: false,
  setMultiSelectMode: vi.fn(),
  showInfoPanel: false,
  infoPanelWidth: 320,
  setShowInfoPanel: vi.fn(),
  recycleFolderId: null,
  setRecycleFolderId: vi.fn(),
  navigate: vi.fn(),
  resPath: (p: string) => p,
  canUpload: false,
  addToast: vi.fn(),
  transcodingResourceIds: new Set<string>(),
  handleTrashResource: vi.fn(),
  handleRestoreResource: vi.fn(),
  handlePermanentDelete: vi.fn(),
  reloadResources: vi.fn(),
  ...overrides,
});

// ── Props factory ──────────────────────────────────────────────────────────────

const buildProps = (overrides: Partial<ResourceGridProps> = {}): ResourceGridProps => ({
  breadcrumbSegments: [{ label: 'My Library' }],
  filteredFolders: [],
  sortedItems: ITEMS_500,
  recycleSubFolders: [],
  trashedFolderPreviews: {},
  allSelectableIds: [],
  filterBarConfig: {} as any,
  allTags: [],
  sortOptions: [],
  currentSortLabel: 'Newest',
  onQueryChange: vi.fn(),
  onAISearch: vi.fn(),
  onSearchClear: vi.fn(),
  isAISearching: false,
  uploading: false,
  overallProgress: 0,
  fileInputRef: { current: null } as any,
  folderInputRef: { current: null } as any,
  canUploadDrop: false,
  dragOver: false,
  onDragEnter: vi.fn(),
  onDragOver: vi.fn(),
  onDragLeave: vi.fn(),
  onDrop: vi.fn(),
  onResourceClick: vi.fn(),
  onResourceDoubleClick: vi.fn(),
  onFileContextMenu: vi.fn(),
  onFolderContextMenu: vi.fn(),
  onEmptyAreaContextMenu: vi.fn(),
  onCardClick: vi.fn(),
  onToggleSelect: vi.fn(),
  onDropOnFolder: vi.fn(),
  renamingResourceId: null,
  renameValue: '',
  onRenameChange: vi.fn(),
  onRenameResourceConfirm: vi.fn(),
  onRenameResourceCancel: vi.fn(),
  onStartRenameResource: vi.fn(),
  renamingFolderId: null,
  renameFolderValue: '',
  onRenameFolderChange: vi.fn(),
  onRenameFolderConfirm: vi.fn(),
  onRenameFolderCancel: vi.fn(),
  onStartRenameFolder: vi.fn(),
  creatingFolder: false,
  newFolderName: '',
  savingFolder: false,
  newFolderInputRef: { current: null } as any,
  onNewFolderNameChange: vi.fn(),
  onCreateFolder: vi.fn(),
  onCancelCreateFolder: vi.fn(),
  onStartCreateFolder: vi.fn(),
  onShowSmartFolderEditor: vi.fn(),
  getItemTouchHandlers: () => ({
    onTouchStart: vi.fn(),
    onTouchMove: vi.fn(),
    onTouchEnd: vi.fn(),
    onTouchCancel: vi.fn(),
  }),
  isTouchDropTarget: () => false,
  touchDragState: { isDragging: false },
  onEmptyAreaTouchStart: vi.fn(),
  onEmptyAreaTouchMove: vi.fn(),
  onEmptyAreaTouchEnd: vi.fn(),
  onTouchDragMove: vi.fn(),
  onTouchDragEnd: vi.fn(),
  loadMore: vi.fn(),
  hasMore: false,
  isLoadingMore: false,
  ...overrides,
});

// ── Tests ──────────────────────────────────────────────────────────────────────

const FOLDER = { id: 'folder-1', name: 'Covers', created_at: '2024-01-01T00:00:00Z' } as any;

describe('ResourceGrid — folder double-click vs info panel reflow', () => {
  let ctx: ReturnType<typeof buildCtx>;

  beforeEach(() => {
    vi.useFakeTimers();
    ctx = buildCtx();
    mockUseResourcesContext.mockReturnValue(ctx);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('double-click navigates into the folder WITHOUT opening the info panel first', () => {
    render(<ResourceGrid {...buildProps({ filteredFolders: [FOLDER] })} />);
    const card = screen.getByTestId('folder-card');

    // Real browser sequence: click, click, dblclick — all within the dblclick window.
    fireEvent.click(card);
    fireEvent.click(card);
    fireEvent.dblClick(card);

    expect(ctx.navigate).toHaveBeenCalledWith('/resources/folder/folder-1');
    // The single-click selection must not have fired synchronously (that is the reflow).
    expect(ctx.setShowInfoPanel).not.toHaveBeenCalled();
    expect(ctx.setSelectedFolder).not.toHaveBeenCalled();

    // …and the cancelled single-click must not fire later either.
    act(() => { vi.advanceTimersByTime(1000); });
    expect(ctx.setShowInfoPanel).not.toHaveBeenCalled();
    expect(ctx.setSelectedFolder).not.toHaveBeenCalled();
  });

  it('a lone single click still selects the folder and opens the info panel (deferred)', () => {
    render(<ResourceGrid {...buildProps({ filteredFolders: [FOLDER] })} />);
    fireEvent.click(screen.getByTestId('folder-card'));

    act(() => { vi.advanceTimersByTime(300); });

    expect(ctx.setSelectedFolder).toHaveBeenCalledWith(FOLDER);
    expect(ctx.setShowInfoPanel).toHaveBeenCalledWith(true);
    expect(ctx.navigate).not.toHaveBeenCalled();
  });
});
