/**
 * ResourcesViewInner — the host that owns the "As Asset" dialog (P6 ruling E).
 *
 * The menu item and the dialog each have their own suite; what NEITHER of them
 * can prove is that the two are actually connected — the context menu is
 * unmounted the moment an item is clicked, so a dialog owned by the menu would
 * close with it, and a host that forgot to pass `onSaveAsAsset` would produce a
 * menu entry that does nothing at all. That is what this file pins.
 *
 * Everything below the host is stubbed except the two pieces under test: the
 * real `ContextMenu` and the real `useContextMenuItems`. `SaveAsAssetDialog` is
 * a probe that reports the props it received, because the question here is
 * "did the right resource reach the dialog", not "does the dialog work".
 */

import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_k: string, def?: string) => def ?? _k }),
}));

// ─── Everything the host renders but this test does not exercise ───────────

vi.mock('./ResourcesShell', () => ({
  ResourcesShell: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));
vi.mock('./DownloadsView', () => ({ DownloadsView: () => null }));
vi.mock('./resources/generated/GeneratedView', () => ({ GeneratedView: () => null }));
vi.mock('./resources/assets/AssetsView', () => ({ AssetsView: () => null }));
vi.mock('./ModuleDisabledPage', () => ({ ModuleDisabledPage: () => null }));
vi.mock('./BatchSelectionToolbar', () => ({ BatchSelectionToolbar: () => null }));
vi.mock('./ResourcesModals', () => ({ ResourcesModals: () => null }));
vi.mock('./GalleryUploadDialog', () => ({ GalleryUploadDialog: () => null }));
vi.mock('../hooks/useModuleStatus', () => ({ useModuleStatus: () => ({ visible: true }) }));
vi.mock('../services/searchService', () => ({ semanticSearch: vi.fn(), hybridSearch: vi.fn() }));
vi.mock('../services/libraryService', () => ({ createLibrary: vi.fn() }));
vi.mock('../services/resourceService', () => ({
  renameFolder: vi.fn(),
  uploadNewVersion: vi.fn(),
  moveResourceItem: vi.fn(),
  moveFolder: vi.fn(),
  getFolderContentCount: vi.fn(),
  getResourceFileUrl: () => 'blob:x',
  copyResourceItem: vi.fn(),
  generateGenPrompt: vi.fn(),
  classifyResource: vi.fn(),
}));
vi.mock('../services/unifiedTagService', () => ({ fetchResourceTags: vi.fn() }));

/** The grid stands in for every card: one button per item that fires the same
 *  `onFileContextMenu` a real right-click does. */
vi.mock('./ResourceGrid', () => ({
  ResourceGrid: ({ sortedItems, onFileContextMenu }: any) => (
    <div>
      {sortedItems.map((item: any) => (
        <button
          key={item.id}
          data-testid={`row-${item.id}`}
          onClick={(e) => onFileContextMenu(e, item)}
        >
          {item.resource.filename}
        </button>
      ))}
    </div>
  ),
}));

const dialogProps = vi.fn();
vi.mock('./assets/SaveAsAssetDialog', () => ({
  SaveAsAssetDialog: (props: Record<string, unknown>) => {
    dialogProps(props);
    return (
      <div data-testid="save-as-asset-probe">
        <button data-testid="probe-close" onClick={props.onClose as () => void}>
          close
        </button>
      </div>
    );
  },
}));

// ─── The five hooks the host composes ──────────────────────────────────────

vi.mock('../hooks/useResourceUpload', () => ({
  useResourceUpload: () => ({
    upload: { overallProgress: 0 },
    uploading: false,
    dragOver: false,
    duplicateAlert: null,
    setDuplicateAlert: vi.fn(),
    batchDupDecision: null,
    fileInputRef: { current: null },
    folderInputRef: { current: null },
    handleUpload: vi.fn(),
    handleDragEnter: vi.fn(),
    handleDragOver: vi.fn(),
    handleDragLeave: vi.fn(),
    handleDrop: vi.fn(),
  }),
}));
vi.mock('../hooks/useResourceTouch', () => ({
  useResourceTouch: () => ({
    touchDragState: null,
    handleTouchDragMove: vi.fn(),
    handleTouchDragEnd: vi.fn(),
    isTouchDropTarget: () => false,
    getItemTouchHandlers: () => ({}),
    handleEmptyAreaTouchStart: vi.fn(),
    handleEmptyAreaTouchMove: vi.fn(),
    handleEmptyAreaTouchEnd: vi.fn(),
  }),
}));
vi.mock('../hooks/useFilterBarConfig', () => ({
  useFilterBarConfig: () => ({ toFilterParams: () => ({}) }),
}));

const ROWS = [
  {
    id: 'item-image',
    resource: {
      id: '742318905233409001',
      filename: 'sang-yao.png',
      mime_type: 'image/png',
      file_type: 'image',
    },
  },
  {
    id: 'item-video',
    resource: {
      id: '742318905233409002',
      filename: 'clip.mp4',
      mime_type: 'video/mp4',
      file_type: 'video',
    },
  },
];

vi.mock('../hooks/useResourcesDisplay', () => ({
  useResourcesDisplay: () => ({
    recycleItems: [],
    recycleSubFolders: [],
    trashedFolderPreviews: {},
    currentItems: ROWS,
    filteredItems: ROWS,
    sortedItems: ROWS,
    filteredFolders: [],
    allSelectableIds: [],
    breadcrumbSegments: [],
    sortOptions: [],
  }),
}));
vi.mock('../hooks/useResourceOperations', () => ({
  useResourceOperations: () => ({
    setRenamingResourceId: vi.fn(), setRenameValue: vi.fn(), setVersionTargetId: vi.fn(),
    setOperationTargetItems: vi.fn(), setOperationTargetFolders: vi.fn(),
    setFolderPickerMode: vi.fn(), setShareTarget: vi.fn(), setRenamingFolderId: vi.fn(),
    setRenameFolderValue: vi.fn(), setEditingSmartFolder: vi.fn(),
    handleDeleteSmartFolder: vi.fn(), setShowSmartFolderEditor: vi.fn(),
    handleRenameResourceConfirm: vi.fn(), handleRenameFolderConfirm: vi.fn(),
    handleCreateSmartFolder: vi.fn(), handleEditSmartFolder: vi.fn(),
    handleFolderPickerConfirm: vi.fn(),
    operationTargetItems: [], operationTargetFolders: [], folderPickerMode: null,
    renamingResourceId: null, renameValue: '', renamingFolderId: null,
    renameFolderValue: '', versionTargetId: null,
    showSmartFolderEditor: false, editingSmartFolder: null, shareTarget: null,
  }),
}));

const SCOPE = '742318905233407001';

const ctx: Record<string, unknown> = {
  isPersonal: true, scopeId: SCOPE, sidebarView: 'all',
  selectedFolderId: null, selectedSmartFolderId: null, selectedLibraryId: null,
  resPath: (p: string) => p, navigate: vi.fn(),
  isResourcesView: true, isRecycleView: false, isSharedView: false,
  isDownloadsView: false, isGeneratedView: false, isAssetsView: false, canUpload: true,
  resources: ROWS, setResources: vi.fn(), folders: [], childFolders: [], folderPreviews: {},
  trashedResources: [], trashedFolders: [], downloadedResources: [],
  libraries: [], setLibraries: vi.fn(), smartFolders: [], setSmartFolders: vi.fn(),
  resourceTagNamesMap: {}, allTags: [], loading: false, setLoading: vi.fn(), folderChain: [],
  recycleFolderId: null, setRecycleFolderId: vi.fn(), recycleFolderItems: [],
  pendingPermanentDelete: null, setPendingPermanentDelete: vi.fn(),
  pendingBatchPermanentDelete: null, setPendingBatchPermanentDelete: vi.fn(),
  pendingBatchPermanentDeleteFolders: null, setPendingBatchPermanentDeleteFolders: vi.fn(),
  selectedResource: null, setSelectedResource: vi.fn(),
  selectedFolder: null, setSelectedFolder: vi.fn(),
  selectedIds: new Set<string>(), setSelectedIds: vi.fn(),
  lastClickedId: null, setLastClickedId: vi.fn(),
  multiSelectMode: false, setMultiSelectMode: vi.fn(),
  viewMode: 'grid', setViewMode: vi.fn(), flattenActive: false,
  sortBy: 'name', setSortBy: vi.fn(),
  searchQuery: '', setSearchQuery: vi.fn(), debouncedSearch: '', setDebouncedSearch: vi.fn(),
  showInfoPanel: false, setShowInfoPanel: vi.fn(),
  infoPanelWidth: 320, setInfoPanelWidth: vi.fn(),
  loadFolders: vi.fn().mockResolvedValue(undefined),
  loadChildFolders: vi.fn().mockResolvedValue(undefined),
  loadTrashedResources: vi.fn().mockResolvedValue(undefined),
  loadDownloadedResources: vi.fn().mockResolvedValue(undefined),
  reloadResources: vi.fn().mockResolvedValue(undefined),
  setFilterParams: vi.fn(),
  handleTrashResource: vi.fn(), handleRestoreResource: vi.fn(),
  handlePermanentDelete: vi.fn(), confirmPermanentDelete: vi.fn(),
  canDo: () => true, addToast: vi.fn(), transcodingResourceIds: new Set<string>(),
  loadMoreResources: vi.fn(), hasMoreResources: false, isLoadingMoreResources: false,
  loadMoreTrashed: vi.fn(), hasMoreTrashed: false, isLoadingMoreTrashed: false,
};

vi.mock('../contexts/ResourcesContext', () => ({
  useResourcesContext: () => ctx,
}));

import { ResourcesViewInner } from './ResourcesViewInner';

const openMenuOn = (rowId: string) => fireEvent.click(screen.getByTestId(`row-${rowId}`));

beforeEach(() => {
  dialogProps.mockReset();
});

describe('ResourcesViewInner — As Asset wiring', () => {
  it('does not mount the dialog until the entry is clicked', () => {
    render(<ResourcesViewInner />);
    expect(screen.queryByTestId('save-as-asset-probe')).toBeNull();
  });

  it('opens the dialog on the right-clicked resource, and it survives the menu closing', async () => {
    render(<ResourcesViewInner />);
    openMenuOn('item-image');

    const entry = await screen.findByText('As Asset');
    fireEvent.click(entry);

    // The menu is gone; the dialog is not. A dialog rendered by the menu
    // would have unmounted with it.
    await waitFor(() => expect(screen.getByTestId('save-as-asset-probe')).toBeTruthy());
    expect(screen.queryByText('As Asset')).toBeNull();

    const props = dialogProps.mock.calls.at(-1)![0];
    expect(props.open).toBe(true);
    expect(props.scopeId).toBe(SCOPE);
    // The RESOURCE, not the `resource_items` row — and no `items` prop, which
    // is what makes the union land on the resource branch.
    expect(props.resource).toMatchObject({
      id: '742318905233409001',
      filename: 'sang-yao.png',
      mime_type: 'image/png',
    });
    expect(props.items).toBeUndefined();
  });

  it('closes on the dialog’s own onClose', async () => {
    render(<ResourcesViewInner />);
    openMenuOn('item-image');
    fireEvent.click(await screen.findByText('As Asset'));
    await waitFor(() => expect(screen.getByTestId('save-as-asset-probe')).toBeTruthy());

    fireEvent.click(screen.getByTestId('probe-close'));
    await waitFor(() => expect(screen.queryByTestId('save-as-asset-probe')).toBeNull());
  });

  it('offers no entry for a video row', async () => {
    render(<ResourcesViewInner />);
    openMenuOn('item-video');

    // Wait for the menu itself before asserting the absence.
    await screen.findByText('Send to Agent');
    expect(screen.queryByText('As Asset')).toBeNull();
  });
});
