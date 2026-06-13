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
import { ProjectAssetsTree, type ProjectAssetsSelection } from './resources/ProjectAssetsTree';
import { fetchCanvasAssets, type CanvasAssetItem } from '../services/projectAssetsService';
import type { Resource } from '../types';
import {
  moveResourceItem,
  moveFolder,
} from '../services/resourceService';
import {
  RESOURCE_SCOPE_OPTIONS,
  loadResourceSearchScope,
  saveResourceSearchScope,
  type ResourceSearchField,
} from './resourceSearchScope';

// ─── Component ────────────────────────────────────────

export const ResourcesViewInner: React.FC = () => {
  const ctx = useResourcesContext();
  const {
    isPersonal, scopeId, sidebarView, selectedFolderId, selectedSmartFolderId, selectedLibraryId,
    resPath, navigate,
    isResourcesView, isRecycleView, isSharedView, isDownloadsView, isTempView, isProjectAssetsView, canUpload,
    resources, setResources, folders, childFolders, folderPreviews,
    trashedResources, trashedFolders, downloadedResources,
    tempResources,
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

  // ─── Resource search scope (Eagle-style) ─────────────
  const [resourceSearchScope, setResourceSearchScope] = useState<ResourceSearchField[]>(
    () => loadResourceSearchScope(),
  );
  const handleResourceSearchScopeChange = useCallback((next: string[]) => {
    const typed = next as ResourceSearchField[];
    setResourceSearchScope(typed);
    saveResourceSearchScope(typed);
  }, []);

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
  // Single-click selection is delayed 250ms so a double-click can cancel it.
  // Opening the info panel synchronously shrinks the grid, and the auto-fill
  // (.downloads-grid) columns reflow — which would move the card out from under
  // a double-click's second click, landing it on (and navigating into) the
  // wrong card. Mirrors the DownloadsView fix (commit 8a7fa3b0).
  const clickTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const handleResourceClick = useCallback((item: any) => {
    if (clickTimerRef.current) clearTimeout(clickTimerRef.current);
    clickTimerRef.current = setTimeout(() => {
      clickTimerRef.current = null;
      if (selectedResource?.id === item.id) {
        setSelectedResource(null);
        setSelectedIds(new Set());
      } else {
        setSelectedResource(item);
        setSelectedFolder(null);
      }
    }, 250);
  }, [selectedResource, setSelectedResource, setSelectedIds, setSelectedFolder]);

  const handleResourceDoubleClick = useCallback((item: any) => {
    // Cancel the pending single-click selection to prevent grid reflow before nav.
    if (clickTimerRef.current) {
      clearTimeout(clickTimerRef.current);
      clickTimerRef.current = null;
    }
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
    aiSearchMatchedMediaIds, isPersonal, selectedFolderId, selectedLibraryId,
    selectedSmartFolderId, isSharedView, isRecycleView, isDownloadsView,
    smartFolders, libraries, folderChain, navigate, resPath, setRecycleFolderId,
    searchScope: resourceSearchScope,
  });

  // ─── Operations hook ──────────────────────────────────
  const ops = useResourceOperations({
    isPersonal, scopeId, selectedFolderId, selectedLibraryId,
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
    isPersonal, scopeId, selectedFolderId, selectedLibraryId,
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

  // ─── Temp view: sorted resources (no folders) ────────
  const tempSortedItems = useMemo(() => {
    const items = [...tempResources];
    switch (sortBy) {
      case 'newest': return items.sort((a, b) => new Date(b.resource?.created_at ?? b.created_at).getTime() - new Date(a.resource?.created_at ?? a.created_at).getTime());
      case 'oldest': return items.sort((a, b) => new Date(a.resource?.created_at ?? a.created_at).getTime() - new Date(b.resource?.created_at ?? b.created_at).getTime());
      case 'name-az': return items.sort((a, b) => (a.resource?.filename ?? '').localeCompare(b.resource?.filename ?? ''));
      case 'name-za': return items.sort((a, b) => (b.resource?.filename ?? '').localeCompare(a.resource?.filename ?? ''));
      case 'largest': return items.sort((a, b) => (b.resource?.file_size_bytes ?? 0) - (a.resource?.file_size_bytes ?? 0));
      case 'smallest': return items.sort((a, b) => (a.resource?.file_size_bytes ?? 0) - (b.resource?.file_size_bytes ?? 0));
      default: return items;
    }
  }, [tempResources, sortBy]);

  const tempAllSelectableIds = useMemo(
    () => tempSortedItems.map((i) => `item:${i.id}`),
    [tempSortedItems],
  );

  const tempBreadcrumbSegments = useMemo(
    () => [{ label: t('resources.temp'), href: resPath('/resources/temp') }],
    [t, resPath],
  );

  // ─── Project Assets view: tree selection + canvas-assets fetch ────────
  const [paSelection, setPaSelection] = useState<ProjectAssetsSelection>({ kind: 'chat-uploads' });
  const [canvasAssets, setCanvasAssets] = useState<CanvasAssetItem[]>([]);
  const [canvasAssetsLoading, setCanvasAssetsLoading] = useState(false);

  useEffect(() => {
    if (!isProjectAssetsView || paSelection.kind !== 'canvas') return;
    let cancelled = false;
    setCanvasAssetsLoading(true);
    fetchCanvasAssets(paSelection.canvasId)
      .then((items) => { if (!cancelled) setCanvasAssets(items); })
      .catch((err) => { console.error('[ProjectAssets] canvas assets load failed:', err); if (!cancelled) setCanvasAssets([]); })
      .finally(() => { if (!cancelled) setCanvasAssetsLoading(false); });
    return () => { cancelled = true; };
  }, [isProjectAssetsView, paSelection]);

  // Adapter: Chat Uploads reuses tempSortedItems; a canvas selection adapts
  // CanvasAssetItem → ResourceItem so ResourceGrid can render it unchanged.
  const projectAssetsItems = useMemo<typeof tempSortedItems>(() => {
    if (paSelection.kind === 'chat-uploads') return tempSortedItems;
    return canvasAssets.map((a) => ({
      id: a.id,
      resource_id: a.id,
      scope_id: scopeId,
      folder_id: null,
      library_id: null,
      added_by: null,
      created_at: a.created_at,
      // Only the fields ResourceGrid/ResourceCard actually read are filled.
      // Resource has ~20 required columns the grid ignores, so cast the
      // nested object rather than fabricate meaningless defaults.
      resource: {
        id: a.id,
        filename: a.filename,
        file_type: a.file_type,
        mime_type: a.mime_type,
        thumbnail_path: a.thumbnail_path,
        cover_image_path: a.cover_image_path,
        created_at: a.created_at,
      } as Resource,
    }));
  }, [paSelection, canvasAssets, tempSortedItems, scopeId]);

  // ─── ResourceGrid props ───────────────────────────────
  const gridProps = useMemo(() => ({
    breadcrumbSegments, filteredFolders, sortedItems, recycleSubFolders, trashedFolderPreviews,
    allSelectableIds, sortOptions,
    filterBarConfig, allTags, availablePlatforms,
    allowedChips: uploadsAllowedChips,
    currentSortLabel,
    onQueryChange: handleResourceQueryChange, onAISearch: handleResourceAISearch,
    onSearchClear: handleResourceSearchClear, isAISearching, uploading,
    searchScope: resourceSearchScope,
    onSearchScopeChange: handleResourceSearchScopeChange,
    scopeOptions: RESOURCE_SCOPE_OPTIONS,
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
    // Pagination lifted to props, switched per view: the TOP-LEVEL recycle bin
    // gets its own keyset loadMore; inside a trashed sub-folder the data is the
    // (still drain-loaded) recycleFolderItems, so no keyset → hasMore=false;
    // everything else uses the main resources list's. (loadMore is only ever
    // called when hasMore is true, so its value is irrelevant when false.)
    loadMore: ctx.isRecycleView ? ctx.loadMoreTrashed : ctx.loadMoreResources,
    hasMore: ctx.isRecycleView
      ? (ctx.recycleFolderId ? false : ctx.hasMoreTrashed)
      : ctx.hasMoreResources,
    isLoadingMore: ctx.isRecycleView
      ? (ctx.recycleFolderId ? false : ctx.isLoadingMoreTrashed)
      : ctx.isLoadingMoreResources,
  }), [
    ctx.isRecycleView, ctx.recycleFolderId,
    ctx.loadMoreResources, ctx.hasMoreResources, ctx.isLoadingMoreResources,
    ctx.loadMoreTrashed, ctx.hasMoreTrashed, ctx.isLoadingMoreTrashed,
    breadcrumbSegments, filteredFolders, sortedItems, recycleSubFolders, trashedFolderPreviews,
    allSelectableIds, sortOptions, filterBarConfig, allTags, availablePlatforms,
    uploadsAllowedChips,
    currentSortLabel, handleResourceQueryChange, handleResourceAISearch, handleResourceSearchClear,
    isAISearching, resourceSearchScope, handleResourceSearchScopeChange,
    uploading, upload.overallProgress, fileInputRef, folderInputRef, canUpload,
    dragOver, handleDragEnter, handleDragOver, handleDragLeave, handleDrop,
    handleResourceClick, handleResourceDoubleClick, handleFileContextMenu, handleFolderContextMenu,
    handleEmptyAreaContextMenu, handleCardClick, handleToggleSelect, handleDropOnFolder,
    ops, creatingFolder, newFolderName, savingFolder, newFolderInputRef,
    setNewFolderName, handleCreateFolder, setCreatingFolder,
    getItemTouchHandlers, isTouchDropTarget, touchDragState,
    handleEmptyAreaTouchStart, handleEmptyAreaTouchMove, handleEmptyAreaTouchEnd,
    handleTouchDragMove, handleTouchDragEnd,
  ]);

  // Temp view props — same shape as gridProps but with no folders, temp
  // resources as items, and a custom breadcrumb.
  const tempGridProps = useMemo(() => ({
    ...gridProps,
    breadcrumbSegments: tempBreadcrumbSegments,
    filteredFolders: [] as import('../types').Folder[],
    sortedItems: tempSortedItems,
    allSelectableIds: tempAllSelectableIds,
    // disable upload drag-drop in temp view — uploads go via chat only
    canUploadDrop: false,
  }), [gridProps, tempBreadcrumbSegments, tempSortedItems, tempAllSelectableIds]);

  // ─── Render ───────────────────────────────────────────

  return (
    <>
      <ResourcesShell sidebarProps={sidebarProps} infoPanelProps={infoPanelProps}>
        {isDownloadsView ? (
          <DownloadsView />
        ) : isProjectAssetsView ? (
          <div className="flex flex-1 min-h-0">
            <ProjectAssetsTree
              selection={paSelection}
              onSelect={(sel) => {
                if (sel.kind === 'canvas') {
                  setCanvasAssets([]);
                  setCanvasAssetsLoading(true);
                }
                setPaSelection(sel);
              }}
              chatUploadsCount={tempSortedItems.length}
            />
            <div className="flex-1 min-w-0">
              {canvasAssetsLoading && paSelection.kind === 'canvas' ? (
                <div className="p-6 text-ink-500">{t('common.loading')}</div>
              ) : (
                <ResourceGrid
                  {...tempGridProps}
                  breadcrumbSegments={[{ label: t('resources.projectAssets') }]}
                  sortedItems={projectAssetsItems}
                  allSelectableIds={projectAssetsItems.map((i) => `item:${i.id}`)}
                />
              )}
            </div>
          </div>
        ) : isTempView ? (
          <ResourceGrid {...tempGridProps} />
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
        currentItems={isTempView ? tempSortedItems : currentItems}
        sortedItems={isTempView ? tempSortedItems : sortedItems}
        recycleSubFolders={recycleSubFolders}
        childFolders={childFolders}
        isPersonal={isPersonal}
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
        isPersonal={isPersonal}
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
