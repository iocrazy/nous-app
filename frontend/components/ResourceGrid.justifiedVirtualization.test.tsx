/**
 * ResourceGrid.justifiedVirtualization.test.tsx
 *
 * The justified (Eagle-style) view previously flex-wrapped ALL items into the
 * DOM — the last un-virtualized browse mode (grid #927, list #933). This
 * pins the virtualized replacement: rows come from the pure layout pre-pass
 * (computeJustifiedRows via useJustifiedVirtualizer, mocked here) and only
 * the windowed rows' cards mount.
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


// Mock useGridVirtualizer to return exactly 3 virtual rows × 2 columns.
// BEFORE implementation: this mock is never imported by ResourceGrid, so
// the old code renders all 500 items and no data-virtualized attr exists → RED.
// AFTER implementation: ResourceGrid calls useGridVirtualizer, the mock
// The grid/list virtualizer is still called unconditionally by the
// component — give it an inert mock so it renders nothing surprising.
vi.mock('../hooks/useGridVirtualizer', () => ({
  useGridVirtualizer: vi.fn(() => ({
    columns: 2,
    rowVirtualizer: {
      getTotalSize: () => 0,
      getVirtualItems: () => [],
      measureElement: vi.fn(),
    },
    containerRef: vi.fn(),
    totalSize: 0,
  })),
}));

// Justified virtualizer mock: two windowed rows covering items 0-4 of 500.
vi.mock('../hooks/useJustifiedVirtualizer', () => ({
  useJustifiedVirtualizer: vi.fn(() => ({
    rows: [
      { start: 0, end: 3, height: 160 },
      { start: 3, end: 5, height: 150 },
    ],
    rowVirtualizer: {
      getTotalSize: () => 42000,
      getVirtualItems: () => [
        { index: 0, key: 'jrow-0', start: 0 },
        { index: 1, key: 'jrow-1', start: 168 },
      ],
      measureElement: vi.fn(),
    },
    containerRef: vi.fn(),
    gap: 8,
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
  viewMode: 'justified' as const,
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

describe('ResourceGrid — justified virtualization', () => {
  beforeEach(() => {
    mockUseResourcesContext.mockReturnValue(buildCtx());
  });

  it('renders the virtualized justified container instead of a flat flex-wrap', () => {
    const { container } = render(<ResourceGrid {...buildProps()} />);
    expect(container.querySelector('[data-virtualized="justified"]')).not.toBeNull();
    // The legacy all-items flex-wrap markup must be gone.
    expect(container.querySelector('.justified-grid')).toBeNull();
  });

  it('bounds the DOM to the windowed rows — 5 cards, not 500', () => {
    render(<ResourceGrid {...buildProps()} />);
    const cards = screen.getAllByTestId('resource-card');
    expect(cards.length).toBe(5); // rows cover items 0-4
    const ids = cards.map((el) => el.getAttribute('data-id'));
    expect(ids).toContain('item-0');
    expect(ids).toContain('item-4');
    expect(ids).not.toContain('item-5');
  });

  it('sets the total-size height on the container (virtualizer sizing wired)', () => {
    const { container } = render(<ResourceGrid {...buildProps()} />);
    const el = container.querySelector('[data-virtualized="justified"]') as HTMLElement;
    expect(el.style.height).toBe('42000px');
  });

  it('sizes each card wrapper from the row height × aspect ratio', () => {
    const { container } = render(<ResourceGrid {...buildProps()} />);
    const rowEls = container.querySelectorAll('[data-virtualized="justified"] > div');
    expect(rowEls.length).toBe(2);
    // Items without resolution/mime fall back to ar=1 → width == row height.
    const firstCardWrap = rowEls[0].firstElementChild as HTMLElement;
    expect(firstCardWrap.style.width).toBe('160px');
    // Rows must NOT hard-code their height: the card renders chrome below
    // the thumbnail (filename bar / TTL strip), so the row self-measures via
    // measureElement and only the thumbnail width comes from the layout pass.
    expect((rowEls[0] as HTMLElement).style.height).toBe('');
  });
});
