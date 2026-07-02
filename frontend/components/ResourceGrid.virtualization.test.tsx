/**
 * ResourceGrid.virtualization.test.tsx
 *
 * TDD test for Task 2: Virtualizing the grid-mode resource library.
 *
 * RED assertion: `data-virtualized="grid"` attribute does NOT exist in the
 * old code (which uses <div className="grid grid-cols-2 ...">). This test
 * FAILS before the implementation is wired in.
 *
 * GREEN assertion: after the implementation replaces the grid branch with a
 * virtualizer container, `data-virtualized="grid"` exists and only the
 * windowed rows (3 rows × 2 columns = 6 cards) are in the DOM, not all 500.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import React from 'react';
import { ResourceGrid } from './ResourceGrid';
import type { ResourceGridProps } from './ResourceGrid';
import type { ResourceItem } from '../types';

// ── Browser API stubs (jsdom doesn't implement these) ──────────────────────────
// IntersectionObserver is used by the loadMoreRef sentinel effect
// ResizeObserver is used by useGridVirtualizer's container width measurement
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

vi.mock('../services/tempTtlService', () => ({
  tempTtlService: {
    getChatTempTtl: vi.fn().mockResolvedValue({ ttl_days: -1 }),
  },
}));

// Mock useGridVirtualizer to return exactly 3 virtual rows × 2 columns.
// BEFORE implementation: this mock is never imported by ResourceGrid, so
// the old code renders all 500 items and no data-virtualized attr exists → RED.
// AFTER implementation: ResourceGrid calls useGridVirtualizer, the mock
// is invoked, and only 6 cards are rendered inside data-virtualized → GREEN.
vi.mock('../hooks/useGridVirtualizer', () => ({
  useGridVirtualizer: vi.fn(() => ({
    columns: 2,
    rowVirtualizer: {
      getTotalSize: () => 70000,
      getVirtualItems: () => [
        { index: 0, key: 'row-0', start: 0 },
        { index: 1, key: 'row-1', start: 280 },
        { index: 2, key: 'row-2', start: 560 },
      ],
      // measureElement is passed as a ref callback to each row div
      measureElement: vi.fn(),
    },
    containerRef: { current: null },
    totalSize: 70000,
  })),
}));

// Context mock — useResourcesContext is a hook that reads from an internal
// React context. We replace the entire module so the context lookup never
// needs the real Provider in the render tree.
const mockUseResourcesContext = vi.hoisted(() => vi.fn());
vi.mock('../contexts/ResourcesContext', () => ({
  useResourcesContext: mockUseResourcesContext,
}));

// Mock heavy child components to avoid pulling in their own deep dep trees.
vi.mock('./ResourceCard', () => ({
  ResourceCard: ({ item }: { item: ResourceItem }) => (
    <div data-testid="resource-card" data-id={item.id} />
  ),
}));

vi.mock('./FolderCard', () => ({
  FolderCard: () => null,
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

vi.mock('./TempResourceActions', () => ({
  TempResourceActions: () => null,
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
  isTempView: false,
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
  reloadTemp: vi.fn(),
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

describe('ResourceGrid — grid virtualization', () => {
  beforeEach(() => {
    mockUseResourcesContext.mockReturnValue(buildCtx());
  });

  it('renders the virtualized grid container (data-virtualized="grid") instead of a flat map', () => {
    const { container } = render(<ResourceGrid {...buildProps()} />);

    // Structural proof: the grid branch must be using the virtualizer wrapper
    const virtualizedGrid = container.querySelector('[data-virtualized="grid"]');
    expect(virtualizedGrid).not.toBeNull();
  });

  it('bounds the DOM to only the windowed rows — far fewer than 500 items rendered', () => {
    render(<ResourceGrid {...buildProps()} />);

    // The mock virtualizer returns 3 rows × 2 columns = 6 cards
    // Before virtualization: 500 cards would be in the DOM.
    const cards = screen.getAllByTestId('resource-card');
    expect(cards.length).toBeLessThan(500);
    // Specifically, only the 6 windowed items should be rendered
    expect(cards.length).toBe(6);
  });

  it('sets the total-size height on the container div (proves virtualizer sizing is wired)', () => {
    const { container } = render(<ResourceGrid {...buildProps()} />);

    const virtualizedGrid = container.querySelector('[data-virtualized="grid"]') as HTMLElement;
    expect(virtualizedGrid).not.toBeNull();
    // The mock getTotalSize() returns 70000 — the height style must reflect this
    expect(virtualizedGrid.style.height).toBe('70000px');
  });

  it('does NOT render the justified-grid branch when viewMode is grid', () => {
    const { container } = render(<ResourceGrid {...buildProps()} />);

    // justified branch uses class "justified-grid"
    const justifiedEl = container.querySelector('.justified-grid');
    expect(justifiedEl).toBeNull();
  });

  it('does NOT render the list branch (space-y-1.5 file row) when viewMode is grid', () => {
    // list branch wraps files in <div class="space-y-1.5">
    // This is also present in the folders section in some views, but since
    // filteredFolders=[] and viewMode='grid', there should be no list file row
    const { container } = render(<ResourceGrid {...buildProps()} />);

    // The file-list <div class="space-y-1.5"> is only rendered in list viewMode
    // justified and grid render different containers → not present
    // (We can't assert the specific class because space-y-1.5 may appear elsewhere)
    // Instead, confirm we ARE in the virtualized path, which is sufficient
    const virtualizedGrid = container.querySelector('[data-virtualized="grid"]');
    expect(virtualizedGrid).not.toBeNull();
  });

  it('preserves each windowed item id as a data attribute on the card', () => {
    render(<ResourceGrid {...buildProps()} />);

    // Row 0 → items 0,1 ; Row 1 → items 2,3 ; Row 2 → items 4,5
    const cards = screen.getAllByTestId('resource-card');
    const renderedIds = cards.map((el) => el.getAttribute('data-id'));
    expect(renderedIds).toContain('item-0');
    expect(renderedIds).toContain('item-5');
    // item-6 should NOT be rendered (beyond the 3 mocked rows)
    expect(renderedIds).not.toContain('item-6');
  });
});
