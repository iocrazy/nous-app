// frontend/components/ResourcesViewInner.tsx

/**
 * ResourcesViewInner — consumes ResourcesContext and orchestrates the UI.
 * Extracted from ResourcesView.tsx for size management.
 */

import React, { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { DownloadsView } from './DownloadsView';
import { semanticSearch, hybridSearch } from '../services/searchService';
import type { Folder, SmartCollection } from '../types';
import {
  renameFolder,
  uploadNewVersion,
} from '../services/resourceService';
import { createLibrary } from '../services/libraryService';
import { ContextMenu } from './ContextMenu';
import { useResourcesContext } from '../contexts/ResourcesContext';
import { ResourcesShell } from './ResourcesShell';
import { ResourceGrid } from './ResourceGrid';
import { BatchSelectionToolbar } from './BatchSelectionToolbar';
import { useResourceUpload } from '../hooks/useResourceUpload';
import { useResourceOperations } from '../hooks/useResourceOperations';
import { useContextMenuItems } from '../hooks/useContextMenuItems';
import { useResourcesDisplay } from '../hooks/useResourcesDisplay';
import { useResourceTouch } from '../hooks/useResourceTouch';
import { useFilterBarConfig } from '../hooks/useFilterBarConfig';
import { ResourcesModals } from './ResourcesModals';
import {
  moveResourceItem,
  moveFolder,
} from '../services/resourceService';

// ─── Component ────────────────────────────────────────

export const ResourcesViewInner: React.FC = () => {
  const ctx = useResourcesContext();
  const {
    scopeType, scopeId, sidebarView, selectedFolderId, selectedSmartFolderId, selectedLibraryId,
    resPath, navigate,
    isResourcesView, isRecycleView, isSharedView, isDownloadsView, canUpload,
    resources, setResources, folders, childFolders, folderPreviews,
    trashedResources, trashedFolders, downloadedResources,
    libraries, setLibraries, smartFolders, setSmartFolders,
    resourceTagNamesMap, allTags, loading, setLoading, folderChain,
    recycleFolderId, setRecycleFolderId, recycleFolderItems,
    pendingPermanentDelete, setPendingPermanentDelete,
    pendingBatchPermanentDelete, setPendingBatchPermanentDelete,
    pendingBatchPermanentDeleteFolders, setPendingBatchPermanentDeleteFolders,
    selectedResource, setSelectedResource, selectedFolder, setSelectedFolder,
    selectedIds, setSelectedIds,
    lastClickedId, setLastClickedId, multiSelectMode, setMultiSelectMode,
    viewMode, setViewMode, sortBy, setSortBy,
    searchQuery, setSearchQuery, debouncedSearch, setDebouncedSearch,
    showInfoPanel, setShowInfoPanel, infoPanelWidth, setInfoPanelWidth,
    loadFolders, loadChildFolders, loadTrashedResources, loadDownloadedResources,
    reloadResources, setFilterParams,
    handleTrashResource: handleTrash, handleRestoreResource: handleRestore,
    handlePermanentDelete, confirmPermanentDelete,
    canDo, addToast, transcodingResourceIds,
  } = ctx;

  const { t } = useTranslation();

  // ─── New folder state ────────────────────────────────
  const [creatingFolder, setCreatingFolder] = useState(false);
  const [newFolderName, setNewFolderName] = useState('');
  const [savingFolder, setSavingFolder] = useState(false);
  const newFolderInputRef = useRef<HTMLInputElement>(null);

  // ─── AI search state ──────────────────────────────────
  const [aiSearchMatchedMediaIds, setAiSearchMatchedMediaIds] = useState<Set<string> | null>(null);
  const [isAISearching, setIsAISearching] = useState(false);

  // ─── Context menu state ──────────────────────────────
  const [contextMenu, setContextMenu] = useState<{
    x: number;
    y: number;
    type: 'file' | 'folder' | 'empty';
    target?: any;
  } | null>(null);

  // ─── Sidebar collapse ────────────────────────────────
  const [resSidebarCollapsed, setResSidebarCollapsed] = useState(false);

  // Focus new folder input when shown
  useEffect(() => {
    if (creatingFolder && newFolderInputRef.current) {
      newFolderInputRef.current.focus();
    }
  }, [creatingFolder]);

  // Clear AI search on view/folder change
  useEffect(() => {
    setAiSearchMatchedMediaIds(null);
  }, [sidebarView, selectedFolderId, selectedSmartFolderId, selectedLibraryId]);

  // ─── Upload hook ─────────────────────────────────────
  const {
    upload,
    uploading,
    dragOver,
    duplicateAlert,
    setDuplicateAlert,
    fileInputRef,
    folderInputRef,
    handleUpload,
    handleDragEnter,
    handleDragOver,
    handleDragLeave,
    handleDrop,
  } = useResourceUpload({
    scopeType,
    scopeId,
    selectedFolderId,
    selectedLibraryId,
    setResources,
    reloadResources,
    addToast,
  });

  // ─── Create folder ───────────────────────────────────
  const handleCreateFolder = async () => {
    const trimmed = newFolderName.trim();
    if (!trimmed || savingFolder) return;
    setSavingFolder(true);
    try {
      const { createFolder } = await import('../services/resourceService');
      await createFolder({
        name: trimmed,
        parent_id: selectedFolderId || null,
        scope_type: scopeType,
        scope_id: scopeId,
        ...(selectedLibraryId ? { library_id: selectedLibraryId } : {}),
      });
      setNewFolderName('');
      setCreatingFolder(false);
      await Promise.all([loadFolders(), loadChildFolders()]);
    } catch {
      // Keep input open on error
    } finally {
      setSavingFolder(false);
    }
  };

  // ─── Create library ──────────────────────────────────
  const handleCreateLibrary = async (name: string) => {
    const lib = await createLibrary({ name, scope_id: scopeId });
    setLibraries((prev) => [...prev, lib]);
    navigate(resPath(`/resources/library/${lib.id}`));
  };

  // ─── Resource selection ──────────────────────────────
  const handleResourceClick = useCallback((item: any) => {
    if (selectedResource?.id === item.id) {
      setSelectedResource(null);
      setSelectedIds(new Set());
    } else {
      setSelectedResource(item);
      setSelectedFolder(null);
    }
  }, [selectedResource, setSelectedResource, setSelectedIds, setSelectedFolder]);

  const handleResourceDoubleClick = useCallback((item: any) => {
    if (item.resource?.id) navigate(resPath(`/resources/file/${item.resource.id}`));
  }, [navigate, resPath]);

  // ─── AI search ───────────────────────────────────────
  const handleResourceQueryChange = useCallback((q: string) => {
    setAiSearchMatchedMediaIds(null);
    setSearchQuery(q);
  }, [setSearchQuery]);

  const handleResourceAISearch = useCallback(async (q: string, mode: 'hybrid' | 'semantic') => {
    setIsAISearching(true);
    try {
      const response = mode === 'semantic'
        ? await semanticSearch(q, 50)
        : await hybridSearch(q, {}, 50, 0.5);
      const ids = new Set(response.results.map((r: any) => String(r.media_id)));
      setAiSearchMatchedMediaIds(ids);
      setSearchQuery('');
      setDebouncedSearch('');
    } catch (error) {
      console.error('AI search failed:', error);
    } finally {
      setIsAISearching(false);
    }
  }, [setSearchQuery, setDebouncedSearch]);

  const handleResourceSearchClear = useCallback(() => {
    setSearchQuery('');
    setDebouncedSearch('');
    setAiSearchMatchedMediaIds(null);
  }, [setSearchQuery, setDebouncedSearch]);

  // ─── Context menu ────────────────────────────────────
  const handleFileContextMenu = useCallback((e: React.MouseEvent, item: any) => { e.preventDefault(); e.stopPropagation(); setContextMenu({ x: e.clientX, y: e.clientY, type: 'file', target: item }); }, []);
  const handleFolderContextMenu = useCallback((e: React.MouseEvent, folder: Folder) => { e.preventDefault(); e.stopPropagation(); setContextMenu({ x: e.clientX, y: e.clientY, type: 'folder', target: folder }); }, []);
  const handleEmptyAreaContextMenu = useCallback((e: React.MouseEvent) => { const t = e.target as HTMLElement; if (!t.closest('[data-context-item]')) { e.preventDefault(); setContextMenu({ x: e.clientX, y: e.clientY, type: 'empty' }); } }, []);
  const closeContextMenu = useCallback(() => setContextMenu(null), []);

  // ─── Drop on folder ──────────────────────────────────
  const handleDropOnFolder = useCallback(async (targetFolderId: string | null, droppedIds: string[]) => {
    try {
      for (const compositeId of droppedIds) {
        if (compositeId.startsWith('folder:')) {
          const fId = compositeId.replace('folder:', '');
          if (fId !== targetFolderId) await moveFolder(fId, targetFolderId, selectedLibraryId);
        } else if (compositeId.startsWith('item:')) {
          const itemId = compositeId.replace('item:', '');
          await moveResourceItem(itemId, targetFolderId, selectedLibraryId);
        }
      }
      await Promise.all([loadFolders(), loadChildFolders()]);
      await reloadResources();
      setSelectedIds(new Set());
    } catch { /* ignore */ }
  }, [selectedLibraryId, loadFolders, loadChildFolders, reloadResources, setSelectedIds]);

  // ─── Touch handlers (drag, long-press, double-tap) ────
  const {
    touchDragState,
    handleTouchDragMove,
    handleTouchDragEnd,
    isTouchDropTarget,
    getItemTouchHandlers,
    handleEmptyAreaTouchStart,
    handleEmptyAreaTouchMove,
    handleEmptyAreaTouchEnd,
  } = useResourceTouch({
    onDrop: handleDropOnFolder,
    selectedIds,
    isResourcesView,
    onFileContextMenu: handleFileContextMenu,
    onFolderContextMenu: handleFolderContextMenu,
    onEmptyAreaContextMenu: handleEmptyAreaContextMenu,
  });

  // ─── Filter bar (pinnable chip toolbar) ───────────────
  const filterBarConfig = useFilterBarConfig();

  // Scope-aware chip allowlist. Uploaded files have no download
  // `source_platform` and no social-graph counts (likes / comments /
  // favorites / shares), so those two chips would be permanently empty
  // in this scope. Hide them to avoid dead controls. Downloads view is
  // rendered by DownloadsView which owns its own FilterBar and does
  // not pass an allowlist — it sees the full chip set.
  const uploadsAllowedChips = useMemo<ReadonlyArray<
    import('./resources/filter/types').ChipId
  >>(
    () => ['tags', 'rating', 'type', 'ai_status', 'date_added', 'duration', 'aspect'],
    [],
  );

  // Distinct source platforms observed across the currently-loaded
  // resource set. Feeds the Source chip's option list so rare
  // platforms still appear even if absent from the hardcoded known list.
  const availablePlatforms = useMemo<string[]>(() => {
    const seen = new Set<string>();
    for (const item of resources) {
      const p = item.resource?.media?.source_platform;
      if (typeof p === 'string' && p) seen.add(p);
    }
    return Array.from(seen).sort();
  }, [resources]);

  // ─── Push filter bar params down to the server-side query. ──────────
  // The context fetch effect re-runs whenever these change.
  const filterParams = filterBarConfig.toFilterParams();
  const filterParamsKey = useMemo(
    () => JSON.stringify(filterParams),
    [filterParams],
  );
  useEffect(() => {
    setFilterParams(filterParams);
    // filterParamsKey is the JSON fingerprint — carrying filterParams
    // directly in the dep array would re-run every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filterParamsKey, setFilterParams]);

  // ─── Display computations (sort/breadcrumb/recycle; no filter) ──────
  const {
    recycleItems, recycleSubFolders, trashedFolderPreviews,
    currentItems, filteredItems, sortedItems,
    filteredFolders, allSelectableIds,
    breadcrumbSegments, sortOptions,
  } = useResourcesDisplay({
    sidebarView, resources, downloadedResources, trashedResources,
    trashedFolders, recycleFolderItems, recycleFolderId,
    childFolders, sortBy, debouncedSearch, resourceTagNamesMap,
    aiSearchMatchedMediaIds, scopeType, selectedFolderId, selectedLibraryId,
    selectedSmartFolderId, isSharedView, isRecycleView, isDownloadsView,
    smartFolders, libraries, folderChain, navigate, resPath, setRecycleFolderId,
  });

  // ─── Operations hook ──────────────────────────────────
  const ops = useResourceOperations({
    scopeType, scopeId, selectedFolderId, selectedLibraryId,
    resources, childFolders, sortedItems, selectedIds, allSelectableIds,
    isResourcesView, navigate, resPath,
    setResources, setSmartFolders, setSelectedIds,
    loadFolders, loadChildFolders, reloadResources, addToast,
  });

  const versionInputRef = useRef<HTMLInputElement>(null);
  const handleVersionFileSelected = useCallback(async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file || !ops.versionTargetId) return;
    try {
      await uploadNewVersion(ops.versionTargetId, file);
      addToast(t('resources.versionUploadSuccess'), 'success');
    } catch {
      addToast(t('resources.versionUploadFailed'), 'error');
    }
    ops.setVersionTargetId(null);
    e.target.value = '';
  }, [ops.versionTargetId, addToast, t]);

  // ─── Selection ────────────────────────────────────────
  const handleToggleSelect = useCallback((compositeId: string, e: React.MouseEvent) => {
    setMultiSelectMode(true);
    if (e.shiftKey && lastClickedId) {
      const startIdx = allSelectableIds.indexOf(lastClickedId);
      const endIdx = allSelectableIds.indexOf(compositeId);
      if (startIdx >= 0 && endIdx >= 0) {
        const [from, to] = startIdx < endIdx ? [startIdx, endIdx] : [endIdx, startIdx];
        setSelectedIds((prev) => {
          const next = new Set(prev);
          for (let i = from; i <= to; i++) next.add(allSelectableIds[i]);
          return next;
        });
      }
    } else {
      setSelectedIds((prev) => {
        const next = new Set(prev);
        if (next.has(compositeId)) next.delete(compositeId);
        else next.add(compositeId);
        return next;
      });
    }
    setLastClickedId(compositeId);
  }, [lastClickedId, allSelectableIds, setSelectedIds, setMultiSelectMode, setLastClickedId]);

  const handleCardClick = useCallback((compositeId: string, e?: React.MouseEvent) => {
    if (e && (e.metaKey || e.ctrlKey || e.shiftKey)) {
      handleToggleSelect(compositeId, e);
      return;
    }
    setMultiSelectMode(false);
    setSelectedIds(new Set([compositeId]));
    setLastClickedId(compositeId);
  }, [handleToggleSelect, setMultiSelectMode, setSelectedIds, setLastClickedId]);

  // ─── Context menu items (extracted to hook) ──────────
  const contextMenuItems = useContextMenuItems({
    contextMenu,
    scopeType, scopeId, selectedFolderId, selectedLibraryId,
    navigate, resPath, canDo,
    fileInputRef, setCreatingFolder, setLoading,
    setSelectedResource, setSelectedFolder, setShowInfoPanel,
    setResources, addToast, loadFolders, loadChildFolders, reloadResources,
    handleTrash, ops, versionInputRef,
  });

  const currentSortLabel = sortOptions.find((o) => o.value === sortBy)?.label ?? '';

  // ─── Shell props ──────────────────────────────────────
  const sidebarProps = useMemo(() => ({
    onSidebarDragOver: (e: React.DragEvent) => {
      if (e.dataTransfer.types.includes('application/mediahub-items')) { e.preventDefault(); e.stopPropagation(); }
    },
    onSidebarDrop: (e: React.DragEvent, targetFolderId: string | null) => {
      e.preventDefault(); e.stopPropagation();
      const raw = e.dataTransfer.getData('application/mediahub-items');
      if (raw) { try { const d = JSON.parse(raw); if (d.ids && Array.isArray(d.ids)) handleDropOnFolder(targetFolderId, d.ids); } catch { /* ignore */ } }
    },
    onCreateSmartFolder: () => ops.setShowSmartFolderEditor(true),
    onEditSmartFolder: (sf: SmartCollection) => ops.setEditingSmartFolder(sf),
    onDeleteSmartFolder: (id: string) => ops.handleDeleteSmartFolder({ id } as any),
    onCreateLibrary: handleCreateLibrary,
    onNewFolder: () => setCreatingFolder(true),
    onSmartFolderContextMenu: (e: React.MouseEvent, sf: SmartCollection) => {
      setContextMenu({ x: e.clientX, y: e.clientY, type: 'smartFolder' as any, target: sf as any });
    },
    collapsed: resSidebarCollapsed,
    onToggleCollapse: () => setResSidebarCollapsed((v) => !v),
  }), [handleDropOnFolder, ops, handleCreateLibrary, resSidebarCollapsed]);

  const infoPanelProps = useMemo(() => ({
    trashedFolderPreviews,
    onRenameFolder: async (folderId: string | number, name: string) => {
      try {
        await renameFolder(folderId, name);
        setSelectedFolder((prev: Folder | null) => prev ? { ...prev, name } : null);
        await Promise.all([loadFolders(), loadChildFolders()]);
      } catch (err) {
        console.error('Failed to rename folder:', err);
      }
    },
  }), [trashedFolderPreviews, loadFolders, loadChildFolders, setSelectedFolder]);

  // ─── ResourceGrid props ───────────────────────────────
  const gridProps = useMemo(() => ({
    breadcrumbSegments, filteredFolders, sortedItems, recycleSubFolders, trashedFolderPreviews,
    allSelectableIds, sortOptions,
    filterBarConfig, allTags, availablePlatforms,
    allowedChips: uploadsAllowedChips,
    currentSortLabel,
    onQueryChange: handleResourceQueryChange, onAISearch: handleResourceAISearch,
    onSearchClear: handleResourceSearchClear, isAISearching, uploading,
    overallProgress: upload.overallProgress, fileInputRef, folderInputRef,
    canUploadDrop: canUpload, dragOver, onDragEnter: handleDragEnter,
    onDragOver: handleDragOver, onDragLeave: handleDragLeave, onDrop: handleDrop,
    onResourceClick: handleResourceClick, onResourceDoubleClick: handleResourceDoubleClick,
    onFileContextMenu: handleFileContextMenu, onFolderContextMenu: handleFolderContextMenu,
    onEmptyAreaContextMenu: handleEmptyAreaContextMenu,
    onCardClick: handleCardClick, onToggleSelect: handleToggleSelect, onDropOnFolder: handleDropOnFolder,
    renamingResourceId: ops.renamingResourceId, renameValue: ops.renameValue,
    onRenameChange: ops.setRenameValue, onRenameResourceConfirm: ops.handleRenameResourceConfirm,
    onRenameResourceCancel: () => ops.setRenamingResourceId(null),
    onStartRenameResource: (id: string, name: string) => { ops.setRenamingResourceId(id); ops.setRenameValue(name); },
    renamingFolderId: ops.renamingFolderId, renameFolderValue: ops.renameFolderValue,
    onRenameFolderChange: ops.setRenameFolderValue, onRenameFolderConfirm: ops.handleRenameFolderConfirm,
    onRenameFolderCancel: () => ops.setRenamingFolderId(null),
    onStartRenameFolder: (id: string, name: string) => { ops.setRenamingFolderId(id); ops.setRenameFolderValue(name); },
    creatingFolder, newFolderName, savingFolder, newFolderInputRef,
    onNewFolderNameChange: setNewFolderName, onCreateFolder: handleCreateFolder,
    onCancelCreateFolder: () => { setCreatingFolder(false); setNewFolderName(''); },
    onStartCreateFolder: () => setCreatingFolder(true),
    onShowSmartFolderEditor: () => ops.setShowSmartFolderEditor(true),
    getItemTouchHandlers, isTouchDropTarget, touchDragState,
    onEmptyAreaTouchStart: handleEmptyAreaTouchStart, onEmptyAreaTouchMove: handleEmptyAreaTouchMove,
    onEmptyAreaTouchEnd: handleEmptyAreaTouchEnd, onTouchDragMove: handleTouchDragMove,
    onTouchDragEnd: handleTouchDragEnd,
  }), [
    breadcrumbSegments, filteredFolders, sortedItems, recycleSubFolders, trashedFolderPreviews,
    allSelectableIds, sortOptions, filterBarConfig, allTags, availablePlatforms,
    uploadsAllowedChips,
    currentSortLabel, handleResourceQueryChange, handleResourceAISearch, handleResourceSearchClear,
    isAISearching, uploading, upload.overallProgress, fileInputRef, folderInputRef, canUpload,
    dragOver, handleDragEnter, handleDragOver, handleDragLeave, handleDrop,
    handleResourceClick, handleResourceDoubleClick, handleFileContextMenu, handleFolderContextMenu,
    handleEmptyAreaContextMenu, handleCardClick, handleToggleSelect, handleDropOnFolder,
    ops, creatingFolder, newFolderName, savingFolder, newFolderInputRef,
    setNewFolderName, handleCreateFolder, setCreatingFolder,
    getItemTouchHandlers, isTouchDropTarget, touchDragState,
    handleEmptyAreaTouchStart, handleEmptyAreaTouchMove, handleEmptyAreaTouchEnd,
    handleTouchDragMove, handleTouchDragEnd,
  ]);

  // ─── Render ───────────────────────────────────────────

  return (
    <>
      <ResourcesShell sidebarProps={sidebarProps} infoPanelProps={infoPanelProps}>
        {isDownloadsView ? (
          <DownloadsView />
        ) : (
          <>
            {canUpload && (
              <>
                <input ref={fileInputRef} type="file" multiple className="hidden" onChange={(e) => { if (e.target.files?.length) { handleUpload(e.target.files); e.target.value = ''; } }} />
                <input ref={folderInputRef} type="file"
                  // @ts-ignore
                  webkitdirectory="" directory="" multiple className="hidden"
                  onChange={(e) => { if (e.target.files?.length) { handleUpload(e.target.files); e.target.value = ''; } }}
                />
              </>
            )}
            <ResourceGrid {...gridProps} />
          </>
        )}
      </ResourcesShell>

      {/* Batch Selection Toolbar */}
      <BatchSelectionToolbar
        selectedIds={selectedIds}
        isRecycleView={isRecycleView}
        isDownloadsView={isDownloadsView}
        currentItems={currentItems}
        sortedItems={sortedItems}
        recycleSubFolders={recycleSubFolders}
        childFolders={childFolders}
        scopeType={scopeType}
        scopeId={scopeId}
        selectedFolderId={selectedFolderId}
        selectedLibraryId={selectedLibraryId}
        setResources={setResources}
        setSelectedIds={setSelectedIds}
        setOperationTargetItems={ops.setOperationTargetItems}
        setOperationTargetFolders={ops.setOperationTargetFolders}
        setFolderPickerMode={ops.setFolderPickerMode}
        setPendingBatchPermanentDelete={setPendingBatchPermanentDelete}
        setPendingBatchPermanentDeleteFolders={setPendingBatchPermanentDeleteFolders}
        loadFolders={loadFolders}
        loadChildFolders={loadChildFolders}
        loadTrashedResources={loadTrashedResources}
        reloadResources={reloadResources}
      />

      {/* Context Menu */}
      {contextMenu && (
        <ContextMenu x={contextMenu.x} y={contextMenu.y} items={contextMenuItems} onClose={closeContextMenu} />
      )}

      {/* Hidden input for version upload */}
      <input ref={versionInputRef} type="file" className="hidden" onChange={handleVersionFileSelected} />

      {/* Overlay modals & permanent-delete confirmation */}
      <ResourcesModals
        showSmartFolderEditor={ops.showSmartFolderEditor}
        editingSmartFolder={ops.editingSmartFolder}
        onCreateSmartFolder={ops.handleCreateSmartFolder}
        onEditSmartFolder={ops.handleEditSmartFolder}
        onCloseSmartFolderEditor={() => ops.setShowSmartFolderEditor(false)}
        onCloseEditingSmartFolder={() => ops.setEditingSmartFolder(null)}
        shareTarget={ops.shareTarget}
        onCloseShareModal={() => ops.setShareTarget(null)}
        folderPickerMode={ops.folderPickerMode}
        operationTargetItems={ops.operationTargetItems}
        operationTargetFolders={ops.operationTargetFolders}
        scopeType={scopeType}
        scopeId={scopeId}
        selectedLibraryId={selectedLibraryId}
        onCloseFolderPicker={() => { ops.setFolderPickerMode(null); ops.setOperationTargetItems([]); ops.setOperationTargetFolders([]); }}
        onConfirmFolderPicker={ops.handleFolderPickerConfirm}
        duplicateAlert={duplicateAlert}
        pendingPermanentDelete={pendingPermanentDelete}
        pendingBatchPermanentDelete={pendingBatchPermanentDelete}
        pendingBatchPermanentDeleteFolders={pendingBatchPermanentDeleteFolders}
        onCancelPermanentDelete={() => { setPendingPermanentDelete(null); setPendingBatchPermanentDelete(null); setPendingBatchPermanentDeleteFolders(null); }}
        onConfirmPermanentDelete={confirmPermanentDelete}
        touchDragState={touchDragState}
      />
    </>
  );
};
