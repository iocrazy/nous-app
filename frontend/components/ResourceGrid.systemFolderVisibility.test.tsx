/**
 * ResourceGrid.systemFolderVisibility.test.tsx
 *
 * The top-level My Uploads folders grid used to drop one folder by NAME:
 *
 *   filteredFolders.filter((f) => f.name !== 'temp')
 *
 * That was the chat-attachment folder, hidden because it was an internal
 * bucket with a machine name. P6 gave it a real identity instead — migration
 * `..._chat_uploads_system_folder.sql` adopts it as
 * `system_key='chat_uploads'`, `is_system=true`, `name='Chat Uploads'` — so
 * it is now a system folder exactly like the cover-template library, which
 * the root grid has always shown. Hiding it no longer has a reason, and the
 * lock lives where it belongs: `is_system` makes `useContextMenuItems` drop
 * Rename / Move / Trash, and the folders router refuses those with a typed
 * 409 (`code=system_folder`).
 *
 * Two assertions, and the second is the one that pins the *identity* change
 * rather than just the new name:
 *
 *   1. the adopted Chat Uploads folder is in the root grid;
 *   2. a folder still literally named `temp` is ALSO in the root grid.
 *
 * (2) is not a hypothetical. The migration adopts at most ONE folder per
 * scope, and only one whose `system_key` is still NULL — a scope that had a
 * second `temp` folder, or one the migration has not reached yet, keeps a
 * plain user folder by that name. After P6 such a folder is nothing special:
 * the grid must not go on hiding a user's own folder because of what it is
 * called. A regression that reinstated the name filter would keep test (1)
 * green (the folder is called "Chat Uploads" now) and only this one would
 * catch it.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import React from 'react';
import { ResourceGrid } from './ResourceGrid';
import type { ResourceGridProps } from './ResourceGrid';
import type { Folder } from '../types';

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

vi.mock('../hooks/useGridVirtualizer', () => ({
  useGridVirtualizer: vi.fn(() => ({
    columns: 1,
    rowVirtualizer: {
      getTotalSize: () => 0,
      getVirtualItems: () => [],
      measureElement: vi.fn(),
    },
    containerRef: vi.fn(),
    totalSize: 0,
  })),
}));

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

const mockUseResourcesContext = vi.hoisted(() => vi.fn());

vi.mock('../contexts/ResourcesContext', () => ({
  useResourcesContext: mockUseResourcesContext,
}));

// Lightweight child stubs to avoid deep dependency trees.
vi.mock('./ResourceCard', () => ({
  ResourceCard: () => <div data-testid="resource-card" />,
}));

vi.mock('./FolderCard', () => ({
  FolderCard: ({ folder }: { folder: Folder }) => (
    <div data-testid="folder-card" data-id={String(folder.id)}>
      {folder.name}
    </div>
  ),
}));

vi.mock('./Breadcrumb', () => ({ Breadcrumb: () => null }));
vi.mock('./ToolbarSearch', () => ({ ToolbarSearch: () => null }));
vi.mock('./resources/filter/FilterBar', () => ({ FilterBar: () => null }));
vi.mock('./filters/FilterChipBar', () => ({ FilterChipBar: () => null }));
vi.mock('./filters/FacetPickerSheet', () => ({ FacetPickerSheet: () => null }));
vi.mock('./ResourceFetchUrlModal', () => ({ ResourceFetchUrlModal: () => null }));

// ── Folder rows ────────────────────────────────────────────────────────────────
//
// `fetchFolders` reads `folders` through PostgREST with `select('*')`, so a
// Folder here carries every column of the table. Snowflake ids arrive as JSON
// numbers and `bigIntSafeFetch` (supabaseClient.ts) quotes any bare 16+ digit
// integer before parse, which is why the ids are STRINGS by the time a
// component sees one — this is the normalized service-return shape, not the
// raw wire body (see e2e/helpers/realShapes.ts on that distinction; the raw
// numeric wire shape is exercised in e2e/resources-folders.spec.ts).

const folderRow = (over: Partial<Folder>): Folder => ({
  id: '742318905233408001',
  name: 'Folder',
  parent_id: null,
  library_id: null,
  scope_id: '742318905233407001',
  created_by: '00000000-0000-4000-8000-000000000001',
  sort_order: 0,
  is_system: false,
  system_key: null,
  icon: null,
  color: null,
  visibility: 'inherited',
  is_smart: false,
  smart_rules: null,
  is_trashed: false,
  trashed_at: null,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
  ...over,
});

/** The adopted system folder, exactly as migration + `chat_upload.py` leave it. */
const CHAT_UPLOADS = folderRow({
  id: '742318905233408002',
  name: 'Chat Uploads',
  is_system: true,
  system_key: 'chat_uploads',
});

/** A scope's SECOND legacy folder — the migration deliberately left it alone. */
const LEGACY_TEMP = folderRow({
  id: '742318905233408003',
  name: 'temp',
});

const PLAIN = folderRow({ id: '742318905233408004', name: 'Reference Boards' });

// ── Context value ──────────────────────────────────────────────────────────────

const buildCtx = (overrides: Record<string, unknown> = {}) => ({
  isPersonal: true,
  scopeId: '742318905233407001',
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
  sortedItems: [],
  recycleSubFolders: [],
  trashedFolderPreviews: {},
  allSelectableIds: [],
  filterBarConfig: {} as never,
  allTags: [],
  sortOptions: [],
  currentSortLabel: 'Newest',
  onQueryChange: vi.fn(),
  onAISearch: vi.fn(),
  onSearchClear: vi.fn(),
  isAISearching: false,
  uploading: false,
  overallProgress: 0,
  fileInputRef: { current: null } as never,
  folderInputRef: { current: null } as never,
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
  newFolderInputRef: { current: null } as never,
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

const renderedFolderNames = (): string[] =>
  screen.getAllByTestId('folder-card').map((el) => el.textContent ?? '');

// ── Tests ──────────────────────────────────────────────────────────────────────

describe('ResourceGrid — system folders in the My Uploads root grid', () => {
  beforeEach(() => {
    mockUseResourcesContext.mockReturnValue(buildCtx());
  });

  it('shows the adopted Chat Uploads system folder at the root', () => {
    render(
      <ResourceGrid {...buildProps({ filteredFolders: [PLAIN, CHAT_UPLOADS] })} />,
    );

    expect(screen.getByText('Chat Uploads')).toBeVisible();
    expect(renderedFolderNames()).toEqual(['Reference Boards', 'Chat Uploads']);
  });

  it('shows a folder still literally named temp — after P6 it is a plain user folder', () => {
    render(
      <ResourceGrid
        {...buildProps({ filteredFolders: [PLAIN, CHAT_UPLOADS, LEGACY_TEMP] })}
      />,
    );

    expect(screen.getByText('temp')).toBeVisible();
    expect(renderedFolderNames()).toEqual([
      'Reference Boards',
      'Chat Uploads',
      'temp',
    ]);
  });

  it('still shows every folder inside a folder, where the filter never applied', () => {
    mockUseResourcesContext.mockReturnValue(
      buildCtx({ selectedFolderId: '742318905233408099' }),
    );

    render(
      <ResourceGrid {...buildProps({ filteredFolders: [CHAT_UPLOADS, LEGACY_TEMP] })} />,
    );

    expect(renderedFolderNames()).toEqual(['Chat Uploads', 'temp']);
  });
});
