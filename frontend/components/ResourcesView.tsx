import React, { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { createPortal } from 'react-dom';
import { useLocation } from 'react-router-dom';
import {
  AlertTriangle,
  FolderOpen,
  Upload,
  Trash2,
  Share2,
  Download,
  FolderPlus,
  Check,
  X,
  ExternalLink,
  Pencil,
  Copy,
  Move,
  RefreshCw,
  Eye,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { DownloadsView } from './DownloadsView';
import { semanticSearch, hybridSearch } from '../services/searchService';
import { Folder, ResourceItem, SmartCollection, Library } from '../types';
import {
  fetchResources,
  createFolder,
  uploadResource,
  createSmartFolder,
  updateSmartFolder,
  deleteSmartFolder,
  renameFolder,
  moveResourceItem,
  moveResourceItems,
  copyResourceItem,
  moveFolder,
  trashResources,
  renameResource,
  uploadNewVersion,
  checkDuplicate,
  linkExistingResource,
  getFolderContentCount,
  restoreFolder,
  restoreResource,
  fetchSmartFolders,
} from '../services/resourceService';
import type { SmartFolderRules } from '../services/resourceService';
import { createLibrary } from '../services/libraryService';
// ResourceCard and FolderCard are now rendered by ResourceGrid
import { ContextMenu, ContextMenuItem } from './ContextMenu';
import { Breadcrumb, BreadcrumbSegment } from './Breadcrumb';
import { SmartFolderEditor } from './SmartFolderEditor';
import { ShareModal } from './ShareModal';
import { FolderPickerModal } from './FolderPickerModal';
import { useFileKeyboard } from '../hooks/useFileKeyboard';
import { useTouchDragDrop } from '../hooks/useTouchDragDrop';
import { useUpload, type UploadFileProgress } from '../contexts/UploadContext';
import { computeFileHash } from '../utils/fileHash';
import { downloadWithAuth } from '../utils/download';
import { getResourceFileUrl } from '../services/resourceService';
import { DuplicateFileAlert } from './DuplicateFileAlert';
import { Resource } from '../types';
import { ResourcesProvider, useResourcesContext } from '../contexts/ResourcesContext';
import type { SortBy } from '../contexts/ResourcesContext';
import { ResourcesShell } from './ResourcesShell';
import { ResourceGrid } from './ResourceGrid';
import {
  MAX_FILE_SIZE,
  validateFile,
} from '../utils/uploadValidation';

// ─── Props ─────────────────────────────────────────────

interface ResourcesViewProps {
  scopeType: 'personal' | 'team';
  scopeId: string;
}


// Skeletons moved to ResourceGrid.tsx

// ─── Main Component (wrapper with context provider) ───

export const ResourcesView: React.FC<ResourcesViewProps> = ({ scopeType, scopeId }) => {
  return (
    <ResourcesProvider scopeType={scopeType} scopeId={scopeId}>
      <ResourcesViewInner />
    </ResourcesProvider>
  );
};

// ─── Inner Component (consumes context) ───────────────

const ResourcesViewInner: React.FC = () => {
  const ctx = useResourcesContext();
  const {
    scopeType, scopeId, teamId, sidebarView, selectedFolderId, selectedSmartFolderId, selectedLibraryId,
    resPath, navigate,
    isResourcesView, isRecycleView, isSharedView, isDownloadsView, canUpload,
    resources, setResources, folders, childFolders, folderPreviews,
    trashedResources, trashedFolders, downloadedResources,
    libraries, setLibraries, smartFolders, setSmartFolders,
    resourceTagNamesMap, loading, setLoading, folderChain,
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
    handleTrashResource: handleTrash, handleRestoreResource: handleRestore,
    handlePermanentDelete, confirmPermanentDelete,
    canDo, addToast, transcodingResourceIds,
  } = ctx;

  const { t } = useTranslation();
  const location = useLocation();

  // ─── UI-only state (stays in ResourcesView) ──────────

  // New folder inline input
  const [creatingFolder, setCreatingFolder] = useState(false);
  const [newFolderName, setNewFolderName] = useState('');
  const [savingFolder, setSavingFolder] = useState(false);
  const newFolderInputRef = useRef<HTMLInputElement>(null);

  // Upload state (shared via context so TopBar TaskCenter can display transfers)
  const upload = useUpload();
  const [dragOver, setDragOver] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const dragCounterRef = useRef(0);


  // Sort / filter UI
  type FilterType = 'video' | 'image' | 'audio' | 'document' | 'other';
  const [activeFilters, setActiveFilters] = useState<Set<FilterType>>(new Set());

  // AI search state
  const [aiSearchMatchedMediaIds, setAiSearchMatchedMediaIds] = useState<Set<string> | null>(null);
  const [isAISearching, setIsAISearching] = useState(false);

  // Mobile-specific state
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false);
  const lastTapRef = useRef<{ id: string; time: number } | null>(null);

  // Version upload via context menu
  const versionInputRef = useRef<HTMLInputElement>(null);
  const [versionTargetId, setVersionTargetId] = useState<string | null>(null);

  // Aliases for readability
  const uploading = upload.isUploading;

  // Duplicate detection
  const [duplicateAlert, setDuplicateAlert] = useState<{
    file: File;
    existing: Resource;
    remainingDuplicates: number;
    resolve: (decision: { action: 'use-existing' | 'keep-both' | 'cancel'; applyToAll: boolean }) => void;
  } | null>(null);

  // Context menu
  const [contextMenu, setContextMenu] = useState<{
    x: number;
    y: number;
    type: 'file' | 'folder' | 'empty';
    target?: ResourceItem | Folder;
  } | null>(null);

  // Rename state
  const [renamingResourceId, setRenamingResourceId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState('');
  const [renamingFolderId, setRenamingFolderId] = useState<string | null>(null);
  const [renameFolderValue, setRenameFolderValue] = useState('');

  // Smart folder editor
  const [showSmartFolderEditor, setShowSmartFolderEditor] = useState(false);
  const [editingSmartFolder, setEditingSmartFolder] = useState<SmartCollection | null>(null);

  // Share target (for ShareModal)
  const [shareTarget, setShareTarget] = useState<{
    resourceId?: string;
    folderId?: string;
    libraryId?: string;
  } | null>(null);

  // Copy/Move operations
  const [folderPickerMode, setFolderPickerMode] = useState<'copy' | 'move' | null>(null);
  const [operationTargetItems, setOperationTargetItems] = useState<ResourceItem[]>([]);
  const [operationTargetFolders, setOperationTargetFolders] = useState<Folder[]>([]);

  // ─── Clipboard state for keyboard shortcuts ──────────
  const [clipboardItems, setClipboardItems] = useState<ResourceItem[]>([]);
  const [clipboardMode, setClipboardMode] = useState<'copy' | 'cut' | null>(null);

  // ─── Folder upload ref ───────────────────────────────
  const folderInputRef = useRef<HTMLInputElement>(null);

  // Auto-close mobile sidebar on navigation
  useEffect(() => { setMobileSidebarOpen(false); }, [location.pathname]);

  // ─── Focus new folder/library input ─────────────────

  useEffect(() => {
    if (creatingFolder && newFolderInputRef.current) {
      newFolderInputRef.current.focus();
    }
  }, [creatingFolder]);


  // ─── Create folder handler ───────────────────────────

  const handleCreateFolder = async () => {
    const trimmed = newFolderName.trim();
    if (!trimmed || savingFolder) return;
    setSavingFolder(true);
    try {
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
    } catch (err) {
      console.error('Failed to create folder:', err);
      // Keep input open on error
    } finally {
      setSavingFolder(false);
    }
  };

  // ─── Create library handler ─────────────────────────

  const handleCreateLibrary = async (name: string) => {
    const lib = await createLibrary({ name, scope_id: scopeId });
    setLibraries((prev) => [...prev, lib]);
    navigate(resPath(`/resources/library/${lib.id}`));
  };

  // ─── Upload handler ──────────────────────────────────

  const handleUpload = useCallback(async (files: FileList | File[]) => {
    if (!files.length || uploading) return;

    // Validate files and build initial progress entries
    const validFiles: File[] = [];
    const initialProgress: UploadFileProgress[] = [];

    for (let i = 0; i < files.length; i++) {
      const file = files[i];
      const validationError = validateFile(file);
      const id = `${Date.now()}-${i}`;

      if (validationError) {
        initialProgress.push({
          id,
          filename: file.name,
          percent: 0,
          status: 'error',
          error: t(`resources.${validationError}`),
          fileSize: file.size,
          bytesUploaded: 0,
          speed: 0,
        });
      } else {
        validFiles.push(file);
        initialProgress.push({
          id,
          filename: file.name,
          percent: 0,
          status: 'uploading',
          fileSize: file.size,
          bytesUploaded: 0,
          speed: 0,
        });
      }
    }

    if (validFiles.length === 0 && initialProgress.length > 0) {
      // All files failed validation — show errors briefly
      upload.setItems(initialProgress);
      return;
    }

    // Append new items to existing history (don't replace)
    upload.setItems((prev) => [...prev, ...initialProgress]);
    upload.setIsUploading(true);
    upload.setOverallProgress(0);

    const batchStartTime = Date.now();
    upload.setUploadStartTime(batchStartTime);

    let completedCount = 0;
    let linkedCount = 0;
    // Map valid files to their progress entry IDs
    const validFileEntryIds = initialProgress
      .filter((p) => p.status === 'uploading')
      .map((p) => p.id);

    // Track "Apply to all" preference across the batch
    let batchDupAction: 'use-existing' | 'keep-both' | null = null;

    for (let i = 0; i < validFiles.length; i++) {
      const file = validFiles[i];
      const entryId = validFileEntryIds[i];
      const fileStartTime = Date.now();

      try {
        // ── Duplicate detection ──
        upload.setItems((prev) =>
          prev.map((p) => p.id === entryId ? { ...p, percent: 0 } : p)
        );

        const fileHash = await computeFileHash(file);
        const dupResult = await checkDuplicate(fileHash, file.size);

        if (dupResult.duplicate && dupResult.existing) {
          let action = batchDupAction;

          if (!action) {
            // Show duplicate alert dialog and wait for user decision
            const remainingToCheck = validFiles.length - i - 1;
            const decision = await new Promise<{ action: 'use-existing' | 'keep-both' | 'cancel'; applyToAll: boolean }>((resolve) => {
              setDuplicateAlert({
                file,
                existing: dupResult.existing as Resource,
                remainingDuplicates: remainingToCheck,
                resolve,
              });
            });
            setDuplicateAlert(null);
            action = decision.action;
            if (decision.applyToAll) {
              batchDupAction = decision.action === 'cancel' ? null : decision.action;
            }
          }

          if (action === 'cancel') {
            upload.setItems((prev) =>
              prev.map((p) => p.id === entryId ? { ...p, status: 'error', error: t('common.cancel') } : p)
            );
            continue;
          }

          if (action === 'use-existing') {
            // Link existing resource instead of uploading
            await linkExistingResource(
              String(dupResult.existing.id),
              scopeType,
              scopeId,
              selectedFolderId,
              selectedLibraryId,
            );
            linkedCount++;
            completedCount++;
            const fileSz = file.size;
            upload.setItems((prev) =>
              prev.map((p) => p.id === entryId
                ? { ...p, percent: 100, status: 'complete', bytesUploaded: fileSz, speed: 0 }
                : p
              )
            );
            upload.setOverallProgress(Math.round((completedCount / validFiles.length) * 100));
            continue;
          }
          // action === 'keep-both' → fall through to normal upload
        }

        // ── Normal upload ──
        await uploadResource(
          file,
          scopeType,
          scopeId,
          selectedFolderId,
          (progress) => {
            const fileEntry = initialProgress.find((p) => p.id === entryId);
            const fileSz = fileEntry?.fileSize || 0;
            const bytesUploaded = Math.round(fileSz * progress / 100);
            const elapsedSec = Math.max((Date.now() - fileStartTime) / 1000, 0.5);
            const speed = bytesUploaded > 0 ? Math.round(bytesUploaded / elapsedSec) : 0;
            upload.setItems((prev) =>
              prev.map((p) => p.id === entryId ? { ...p, percent: progress, bytesUploaded, speed } : p)
            );
            const overall = Math.round(((completedCount + progress / 100) / validFiles.length) * 100);
            upload.setOverallProgress(overall);
          },
          selectedLibraryId,
        );

        completedCount++;
        const fileEntry = initialProgress.find((p) => p.id === entryId);
        const fileSz = fileEntry?.fileSize || 0;
        upload.setItems((prev) =>
          prev.map((p) => p.id === entryId ? { ...p, percent: 100, status: 'complete', bytesUploaded: fileSz, speed: 0 } : p)
        );
      } catch (err) {
        console.error('Upload failed:', err);
        upload.setItems((prev) =>
          prev.map((p) => p.id === entryId
            ? { ...p, status: 'error', error: t('resources.uploadFailed') }
            : p
          )
        );
      }
    }

    // Show toast for linked files
    if (linkedCount > 0) {
      addToast(
        linkedCount === 1
          ? t('resources.linkedExisting')
          : t('resources.linkedExistingCount', { count: linkedCount }),
        'success'
      );
    }

    // Refresh resource list
    try {
      const items = await fetchResources(scopeType, scopeId, selectedFolderId, selectedLibraryId);
      setResources(items);
    } catch { /* ignore */ }

    upload.setIsUploading(false);
    upload.setOverallProgress(0);
  }, [scopeType, scopeId, selectedFolderId, selectedLibraryId, uploading, t, upload, addToast]);

  // ─── Drag & drop (robust nested-element handling) ────

  const handleDragEnter = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    dragCounterRef.current++;
    if (e.dataTransfer.types.includes('Files')) {
      setDragOver(true);
    }
  }, []);

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    dragCounterRef.current--;
    if (dragCounterRef.current === 0) {
      setDragOver(false);
    }
  }, []);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    dragCounterRef.current = 0;
    setDragOver(false);
    if (e.dataTransfer.files.length) handleUpload(e.dataTransfer.files);
  }, [handleUpload]);

  // ─── Resource selection & detail panel ───────────────

  const handleResourceClick = useCallback((item: ResourceItem) => {
    // Single click: toggle — click same item to deselect
    if (selectedResource?.id === item.id) {
      setSelectedResource(null);
      setSelectedIds(new Set());
    } else {
      setSelectedResource(item);
      setSelectedFolder(null);
    }
  }, [selectedResource, setSelectedResource, setSelectedIds, setSelectedFolder]);

  // Double click: navigate to detail page
  const handleResourceDoubleClick = useCallback((item: ResourceItem) => {
    if (item.resource?.id) {
      navigate(resPath(`/resources/file/${item.resource.id}`));
    }
  }, [navigate, resPath]);

  // ToolbarSearch callbacks for resources
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
      const ids = new Set(response.results.map(r => String(r.media_id)));
      setAiSearchMatchedMediaIds(ids);
      setSearchQuery(''); // Clear keyword filter
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

  // Clear AI search filter on view/folder change
  useEffect(() => {
    setAiSearchMatchedMediaIds(null);
  }, [sidebarView, selectedFolderId, selectedSmartFolderId, selectedLibraryId]);

  // ─── Context menu handlers ─────────────────────────

  const handleFileContextMenu = useCallback((e: React.MouseEvent, item: ResourceItem) => {
    e.preventDefault();
    e.stopPropagation();
    setContextMenu({ x: e.clientX, y: e.clientY, type: 'file', target: item });
  }, []);

  const handleFolderContextMenu = useCallback((e: React.MouseEvent, folder: Folder) => {
    e.preventDefault();
    e.stopPropagation();
    setContextMenu({ x: e.clientX, y: e.clientY, type: 'folder', target: folder });
  }, []);

  const handleEmptyAreaContextMenu = useCallback((e: React.MouseEvent) => {
    // Only trigger when not right-clicking on a card (cards have their own context menus)
    const target = e.target as HTMLElement;
    if (!target.closest('[data-context-item]')) {
      e.preventDefault();
      setContextMenu({ x: e.clientX, y: e.clientY, type: 'empty' });
    }
  }, []);

  const closeContextMenu = useCallback(() => {
    setContextMenu(null);
  }, []);

  const handleFolderPickerConfirm = useCallback(async (targetFolderId: string | null, targetLibraryId?: string | null) => {
    try {
      if (folderPickerMode === 'move') {
        // Move folders
        for (const folder of operationTargetFolders) {
          await moveFolder(folder.id, targetFolderId, targetLibraryId);
        }
        // Move items
        if (operationTargetItems.length === 1) {
          await moveResourceItem(operationTargetItems[0].id, targetFolderId, targetLibraryId);
        } else if (operationTargetItems.length > 1) {
          await moveResourceItems(operationTargetItems.map((i) => i.id), targetFolderId, targetLibraryId);
        }
        // Reload
        await Promise.all([loadFolders(), loadChildFolders()]);
        const items = await fetchResources(scopeType, scopeId, selectedFolderId, selectedLibraryId);
        setResources(items);
        const totalMoved = operationTargetItems.length + operationTargetFolders.length;
        addToast(t('resources.moveSuccess', { count: totalMoved }), 'success');
      } else if (folderPickerMode === 'copy') {
        for (const item of operationTargetItems) {
          if (item.resource?.id) {
            await copyResourceItem(String(item.resource.id), scopeType, scopeId, targetFolderId, targetLibraryId);
          }
        }
        addToast(t('resources.copySuccess', { count: operationTargetItems.length }), 'success');
      }
    } catch (err) {
      console.error('Folder operation failed:', err);
    }
    setFolderPickerMode(null);
    setOperationTargetItems([]);
    setOperationTargetFolders([]);
  }, [folderPickerMode, operationTargetItems, operationTargetFolders, scopeType, scopeId, selectedFolderId, selectedLibraryId, loadFolders, loadChildFolders, addToast, t]);

  // ─── Handle drag-drop onto folder ──────────────────

  const handleDropOnFolder = useCallback(async (targetFolderId: string | null, droppedIds: string[]) => {
    try {
      for (const compositeId of droppedIds) {
        if (compositeId.startsWith('folder:')) {
          const fId = compositeId.replace('folder:', '');
          if (fId !== targetFolderId) {
            await moveFolder(fId, targetFolderId, selectedLibraryId);
          }
        } else if (compositeId.startsWith('item:')) {
          const itemId = compositeId.replace('item:', '');
          await moveResourceItem(itemId, targetFolderId, selectedLibraryId);
        }
      }
      // Refresh
      await Promise.all([loadFolders(), loadChildFolders()]);
      const items = await fetchResources(scopeType, scopeId, selectedFolderId, selectedLibraryId);
      setResources(items);
      setSelectedIds(new Set());
    } catch { /* ignore */ }
  }, [selectedLibraryId, scopeType, scopeId, selectedFolderId, loadFolders, loadChildFolders]);

  // ─── Touch drag-and-drop (mobile) ────────────────
  const {
    dragState: touchDragState,
    startDrag: startTouchDrag,
    handleTouchMove: handleTouchDragMove,
    handleTouchEnd: handleTouchDragEnd,
    isDropTarget: isTouchDropTarget,
  } = useTouchDragDrop({
    onDrop: (dragIds, targetFolderId) => {
      handleDropOnFolder(targetFolderId, dragIds);
    },
    selectedIds,
  });

  // ─── Double-tap detection (mobile) ─────────────
  const DOUBLE_TAP_DELAY = 300;
  const handleTap = useCallback(
    (id: string, onDoubleTap: () => void) => {
      const now = Date.now();
      if (lastTapRef.current && lastTapRef.current.id === id && now - lastTapRef.current.time < DOUBLE_TAP_DELAY) {
        lastTapRef.current = null;
        onDoubleTap();
      } else {
        lastTapRef.current = { id, time: now };
      }
    },
    [],
  );

  // ─── Long-press handlers for mobile context menu + drag ──
  const getItemTouchHandlers = useCallback(
    (itemType: 'file' | 'folder', item: any) => {
      let timer: ReturnType<typeof setTimeout> | null = null;
      let startPos: { x: number; y: number } | null = null;
      let dragStarted = false;

      return {
        onTouchStart: (e: React.TouchEvent) => {
          const touch = e.touches[0];
          startPos = { x: touch.clientX, y: touch.clientY };
          dragStarted = false;
          timer = setTimeout(() => {
            const mockEvent = {
              preventDefault: () => {},
              stopPropagation: () => {},
              clientX: touch.clientX,
              clientY: touch.clientY,
            } as unknown as React.MouseEvent;
            if (itemType === 'file') {
              handleFileContextMenu(mockEvent, item);
            } else {
              handleFolderContextMenu(mockEvent, item);
            }
            timer = null;
          }, 500);
        },
        onTouchMove: (e: React.TouchEvent) => {
          if (!startPos || !timer || dragStarted) return;
          const touch = e.touches[0];
          const dx = touch.clientX - startPos.x;
          const dy = touch.clientY - startPos.y;
          if (Math.sqrt(dx * dx + dy * dy) > 10) {
            clearTimeout(timer);
            timer = null;
            dragStarted = true;
            const compositeId = itemType === 'folder'
              ? `folder:${item.id}`
              : `item:${item.id}`;
            startTouchDrag(compositeId, e);
          }
        },
        onTouchEnd: () => {
          if (timer) { clearTimeout(timer); timer = null; }
          startPos = null;
          dragStarted = false;
        },
        onTouchCancel: () => {
          if (timer) { clearTimeout(timer); timer = null; }
          startPos = null;
          dragStarted = false;
        },
      };
    },
    [handleFileContextMenu, handleFolderContextMenu, startTouchDrag],
  );

  // ─── Empty area long-press for mobile ──────────
  const emptyAreaTouchRef = useRef<{
    timer: ReturnType<typeof setTimeout> | null;
    startPos: { x: number; y: number } | null;
  }>({ timer: null, startPos: null });

  const handleEmptyAreaTouchStart = useCallback((e: React.TouchEvent) => {
    if (!isResourcesView) return;
    const target = e.target as HTMLElement;
    if (target.closest('[data-context-item]')) return;
    const touch = e.touches[0];
    emptyAreaTouchRef.current.startPos = { x: touch.clientX, y: touch.clientY };
    emptyAreaTouchRef.current.timer = setTimeout(() => {
      handleEmptyAreaContextMenu({
        preventDefault: () => {},
        clientX: touch.clientX,
        clientY: touch.clientY,
        target,
      } as unknown as React.MouseEvent);
    }, 500);
  }, [isResourcesView, handleEmptyAreaContextMenu]);

  const handleEmptyAreaTouchMove = useCallback((e: React.TouchEvent) => {
    const ref = emptyAreaTouchRef.current;
    if (!ref.timer || !ref.startPos) return;
    const touch = e.touches[0];
    const dx = touch.clientX - ref.startPos.x;
    const dy = touch.clientY - ref.startPos.y;
    if (Math.sqrt(dx * dx + dy * dy) > 10) {
      clearTimeout(ref.timer);
      ref.timer = null;
    }
  }, []);

  const handleEmptyAreaTouchEnd = useCallback(() => {
    const ref = emptyAreaTouchRef.current;
    if (ref.timer) { clearTimeout(ref.timer); ref.timer = null; }
    ref.startPos = null;
  }, []);

  // ─── Sidebar drop handlers ────────────────────────

  const handleSidebarDragOver = useCallback((e: React.DragEvent) => {
    if (e.dataTransfer.types.includes('application/mediahub-items')) {
      e.preventDefault();
      e.stopPropagation();
    }
  }, []);

  const handleSidebarDrop = useCallback((e: React.DragEvent, targetFolderId: string | null) => {
    e.preventDefault();
    e.stopPropagation();
    const raw = e.dataTransfer.getData('application/mediahub-items');
    if (raw) {
      try {
        const data = JSON.parse(raw);
        if (data.ids && Array.isArray(data.ids)) {
          handleDropOnFolder(targetFolderId, data.ids);
        }
      } catch { /* ignore */ }
    }
  }, [handleDropOnFolder]);

  const handleDeleteSmartFolder = useCallback(async (sf: SmartCollection) => {
    if (!confirm(t('smartFolder.confirmDelete'))) return;
    await deleteSmartFolder(String(sf.id));
    const updated = await fetchSmartFolders(scopeType, scopeId);
    setSmartFolders(updated);
    // If we're viewing the deleted folder, go back to resources root
    if (selectedSmartFolderId === String(sf.id)) {
      navigate(resPath('/resources'));
    }
  }, [scopeType, scopeId, selectedSmartFolderId, navigate, resPath, t]);

  // Handle version file selection from hidden input
  const handleVersionFileSelected = useCallback(async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file || !versionTargetId) return;
    try {
      await uploadNewVersion(versionTargetId, file);
      addToast(t('resources.versionUploadSuccess'), 'success');
    } catch (err) {
      console.error('Version upload failed:', err);
      addToast(t('resources.versionUploadFailed'), 'error');
    }
    setVersionTargetId(null);
    // Reset input so same file can be re-selected
    e.target.value = '';
  }, [versionTargetId, addToast, t]);

  // Build context menu items based on type
  const contextMenuItems = useMemo((): ContextMenuItem[] => {
    if (!contextMenu) return [];

    if (contextMenu.type === 'file') {
      const item = contextMenu.target as ResourceItem;
      const resourceId = item.resource?.id;
      const items: ContextMenuItem[] = [
        {
          label: t('resources.viewDetails', 'View Details'),
          icon: <Eye size={14} />,
          onClick: () => {
            if (resourceId) navigate(resPath(`/resources/file/${resourceId}`));
          },
          disabled: !resourceId,
        },
        {
          label: t('resources.openInNewTab'),
          icon: <ExternalLink size={14} />,
          onClick: () => {
            if (resourceId) window.open(getResourceFileUrl(String(resourceId)), '_blank');
          },
          disabled: !resourceId,
          divider: true,
        },
      ];
      if (canDo('download')) {
        items.push({
          label: t('resources.downloadOriginal'),
          icon: <Download size={14} />,
          onClick: () => {
            if (resourceId) {
              downloadWithAuth(getResourceFileUrl(String(resourceId)), item.resource?.filename ?? 'download', {
                onSuccess: (f) => addToast(`Downloaded: ${f}`, 'success'),
                onError: (msg) => addToast(`Download failed (${msg})`, 'error'),
              });
            }
          },
          disabled: !resourceId,
        });
      }
      if (canDo('update')) {
        items.push({
          label: t('resources.rename'),
          icon: <Pencil size={14} />,
          onClick: () => {
            if (resourceId) {
              setRenamingResourceId(item.id);
              setRenameValue(item.resource?.filename ?? '');
            }
          },
        });
        items.push({
          label: t('resources.uploadNewVersion'),
          icon: <Upload size={14} />,
          onClick: () => {
            if (resourceId) {
              setVersionTargetId(String(resourceId));
              // Trigger hidden file input after a tick so context menu closes first
              setTimeout(() => versionInputRef.current?.click(), 0);
            }
          },
          divider: true,
        });
      }
      if (canDo('copy')) {
        items.push({
          label: t('resources.copyTo'),
          icon: <Copy size={14} />,
          onClick: () => {
            setOperationTargetItems([item]);
            setOperationTargetFolders([]);
            setFolderPickerMode('copy');
          },
        });
      }
      if (canDo('move')) {
        items.push({
          label: t('resources.moveTo'),
          icon: <Move size={14} />,
          onClick: () => {
            setOperationTargetItems([item]);
            setOperationTargetFolders([]);
            setFolderPickerMode('move');
          },
        });
      }
      if (canDo('share')) {
        items.push({
          label: t('resources.share'),
          icon: <Share2 size={14} />,
          onClick: () => {
            setShareTarget({ resourceId: String(item.resource?.id) });
          },
        });
      }
      if (canDo('delete')) {
        items.push({
          label: t('resources.moveToTrash'),
          icon: <Trash2 size={14} />,
          onClick: () => {
            if (resourceId) handleTrash(resourceId);
          },
          danger: true,
          divider: true,
        });
      }
      return items;
    }

    if (contextMenu.type === 'folder') {
      const folder = contextMenu.target as Folder;
      const folderUrl = selectedLibraryId
        ? resPath(`/resources/library/${selectedLibraryId}/folder/${folder.id}`)
        : resPath(`/resources/folder/${folder.id}`);
      const items: ContextMenuItem[] = [
        {
          label: t('resources.getInfo', 'Get Info'),
          icon: <Eye size={14} />,
          onClick: () => {
            setSelectedResource(null);
            setSelectedFolder(folder);
            setShowInfoPanel(true);
          },
        },
        {
          label: t('resources.openInNewTab'),
          icon: <ExternalLink size={14} />,
          onClick: () => {
            window.open(folderUrl, '_blank');
          },
        },
        {
          label: t('resources.open'),
          icon: <FolderOpen size={14} />,
          onClick: () => navigate(folderUrl),
        },
      ];
      if (canDo('update')) {
        items.push({
          label: t('resources.rename'),
          icon: <Pencil size={14} />,
          onClick: () => {
            setRenamingFolderId(folder.id);
            setRenameFolderValue(folder.name);
          },
          divider: true,
        });
      }
      if (canDo('copy')) {
        items.push({
          label: t('resources.copyTo'),
          icon: <Copy size={14} />,
          onClick: () => {
            setOperationTargetItems([]);
            setOperationTargetFolders([folder]);
            setFolderPickerMode('copy');
          },
        });
      }
      if (canDo('move')) {
        items.push({
          label: t('resources.moveTo'),
          icon: <Move size={14} />,
          onClick: () => {
            setOperationTargetItems([]);
            setOperationTargetFolders([folder]);
            setFolderPickerMode('move');
          },
        });
      }
      if (canDo('share')) {
        items.push({
          label: t('resources.share'),
          icon: <Share2 size={14} />,
          onClick: () => {
            setShareTarget({ folderId: String(folder.id) });
          },
        });
      }
      if (canDo('delete')) {
        items.push({
          label: t('resources.moveToTrash'),
          icon: <Trash2 size={14} />,
          onClick: async () => {
            try {
              // Check folder contents before trashing
              const counts = await getFolderContentCount(folder.id);
              const hasContents = counts.resource_count > 0 || counts.subfolder_count > 0;

              if (hasContents) {
                const parts: string[] = [];
                if (counts.resource_count > 0) {
                  parts.push(`${counts.resource_count} file(s)`);
                }
                if (counts.subfolder_count > 0) {
                  parts.push(`${counts.subfolder_count} sub-folder(s)`);
                }
                const msg = t('resources.trashFolderConfirm', {
                  defaultValue: 'This folder contains {{contents}}. All contents will be moved to the recycle bin. Continue?',
                  contents: parts.join(' and '),
                });
                if (!confirm(msg)) return;
              }

              const { trashFolder } = await import('../services/resourceService');
              await trashFolder(folder.id);
              await loadFolders();
              await loadChildFolders();
              const items = await fetchResources(scopeType, scopeId, selectedFolderId, selectedLibraryId);
              setResources(items);
            } catch (err) {
              console.error('Failed to trash folder:', err);
              addToast(t('resources.trashFolderFailed', 'Failed to move folder to trash'), 'error');
            }
          },
          danger: true,
          divider: true,
        });
      }
      return items;
    }

    if ((contextMenu.type as string) === 'smartFolder') {
      const sf = contextMenu.target as unknown as SmartCollection;
      return [
        {
          label: t('smartFolder.editSmartFolder'),
          icon: <Pencil size={14} />,
          onClick: () => setEditingSmartFolder(sf),
        },
        {
          label: t('smartFolder.deleteSmartFolder'),
          icon: <Trash2 size={14} />,
          onClick: () => handleDeleteSmartFolder(sf),
          danger: true,
          divider: true,
        },
      ];
    }

    // Empty area
    const emptyItems: ContextMenuItem[] = [];
    if (canDo('upload')) {
      emptyItems.push(
        {
          label: t('resources.uploadFile'),
          icon: <Upload size={14} />,
          onClick: () => fileInputRef.current?.click(),
        },
        {
          label: t('resources.newFolder'),
          icon: <FolderPlus size={14} />,
          onClick: () => setCreatingFolder(true),
        },
      );
    }
    emptyItems.push({
      label: t('resources.refresh'),
      icon: <RefreshCw size={14} />,
      onClick: async () => {
        setLoading(true);
        try {
          await Promise.all([loadChildFolders()]);
          const items = await fetchResources(scopeType, scopeId, selectedFolderId, selectedLibraryId);
          setResources(items);
        } catch { /* ignore */ }
        setLoading(false);
      },
      divider: true,
    });
    // Paste option when there are items in clipboard (operationTargetItems from copy)
    if (operationTargetItems.length > 0 && folderPickerMode === null) {
      emptyItems.push({
        label: t('resources.paste'),
        icon: <Copy size={14} />,
        onClick: async () => {
          try {
            for (const item of operationTargetItems) {
              if (item.resource?.id) {
                await copyResourceItem(String(item.resource.id), scopeType, scopeId, selectedFolderId, selectedLibraryId);
              }
            }
            const items = await fetchResources(scopeType, scopeId, selectedFolderId, selectedLibraryId);
            setResources(items);
            setOperationTargetItems([]);
          } catch { /* ignore */ }
        },
      });
    }
    return emptyItems;
  }, [contextMenu, t, selectedLibraryId, navigate, resPath, handleTrash, handleDeleteSmartFolder, scopeType, scopeId, selectedFolderId, loadFolders, loadChildFolders, canDo]);

  // ─── Rename handlers ─────────────────────────────────

  const handleRenameResourceConfirm = useCallback(async () => {
    if (!renamingResourceId || !renameValue.trim()) {
      setRenamingResourceId(null);
      return;
    }
    const item = resources.find((r) => r.id === renamingResourceId);
    if (!item?.resource?.id) {
      setRenamingResourceId(null);
      return;
    }
    try {
      await renameResource(item.resource.id, renameValue.trim());
      // Update local state
      setResources((prev) =>
        prev.map((r) =>
          r.id === renamingResourceId && r.resource
            ? { ...r, resource: { ...r.resource, filename: renameValue.trim() } }
            : r
        )
      );
      addToast(t('resources.renamedNotification', { name: renameValue.trim() }), 'success');
    } catch { /* ignore */ }
    setRenamingResourceId(null);
  }, [renamingResourceId, renameValue, resources, addToast, t]);

  const handleRenameFolderConfirm = useCallback(async () => {
    if (!renamingFolderId || !renameFolderValue.trim()) {
      setRenamingFolderId(null);
      return;
    }
    try {
      await renameFolder(renamingFolderId, renameFolderValue.trim());
      await Promise.all([loadFolders(), loadChildFolders()]);
      addToast(t('resources.renamedNotification', { name: renameFolderValue.trim() }), 'success');
    } catch { /* ignore */ }
    setRenamingFolderId(null);
  }, [renamingFolderId, renameFolderValue, loadFolders, loadChildFolders, addToast, t]);

  // ─── Smart Folder CRUD ──────────────────────────────

  const handleCreateSmartFolder = useCallback(async (name: string, rules: SmartFolderRules) => {
    await createSmartFolder(name, scopeType, scopeId, rules);
    const updated = await fetchSmartFolders(scopeType, scopeId);
    setSmartFolders(updated);
    setShowSmartFolderEditor(false);
    // Navigate to the newly created smart folder
    const newest = updated[updated.length - 1];
    if (newest) navigate(resPath(`/resources/smart/${newest.id}`));
  }, [scopeType, scopeId, navigate, resPath]);

  const handleEditSmartFolder = useCallback(async (name: string, rules: SmartFolderRules) => {
    if (!editingSmartFolder) return;
    await updateSmartFolder(String(editingSmartFolder.id), { name, rules });
    const updated = await fetchSmartFolders(scopeType, scopeId);
    setSmartFolders(updated);
    setEditingSmartFolder(null);
  }, [editingSmartFolder, scopeType, scopeId]);

  // ─── Breadcrumb ───────────────────────────────────────

  const breadcrumbSegments = useMemo((): BreadcrumbSegment[] => {
    if (isSharedView) return [{ label: t('resources.sharedManagement') }];
    if (isRecycleView) {
      const segments: BreadcrumbSegment[] = [
        {
          label: t('resources.recycleBin'),
          onClick: recycleFolderId ? () => setRecycleFolderId(null) : undefined,
        },
      ];
      if (recycleFolderId) {
        // Build folder chain from recycleFolderId up to root
        const chain: Folder[] = [];
        let currentId: string | null = recycleFolderId;
        while (currentId) {
          const f = trashedFolders.find((tf) => String(tf.id) === currentId);
          if (!f) break;
          chain.unshift(f);
          currentId = f.parent_id ? String(f.parent_id) : null;
          // Stop if parent is not in trashed set (we've reached the root trashed folder)
          if (currentId && !trashedFolders.some((tf) => String(tf.id) === currentId)) break;
        }
        chain.forEach((f, idx) => {
          const isLast = idx === chain.length - 1;
          segments.push({
            label: f.name,
            onClick: isLast ? undefined : () => setRecycleFolderId(String(f.id)),
          });
        });
      }
      return segments;
    }
    if (isDownloadsView) return [{ label: t('resources.downloads') }];

    if (selectedSmartFolderId) {
      const sf = smartFolders.find((s) => String(s.id) === selectedSmartFolderId);
      return [
        { label: t('resources.smartFolders'), onClick: () => {} },
        { label: sf?.name ?? '' },
      ];
    }

    if (scopeType === 'team' && selectedLibraryId) {
      const lib = libraries.find((l) => String(l.id) === selectedLibraryId);
      const libName = lib?.name ?? t('resources.allFiles');
      const segments: BreadcrumbSegment[] = [];

      segments.push({
        label: libName,
        onClick: selectedFolderId ? () => navigate(resPath(`/resources/library/${selectedLibraryId}`)) : undefined,
      });

      if (selectedFolderId && folderChain.length > 0) {
        folderChain.forEach((f, idx) => {
          const isLast = idx === folderChain.length - 1;
          segments.push({
            label: f.name,
            onClick: isLast ? undefined : () => navigate(resPath(`/resources/library/${selectedLibraryId}/folder/${f.id}`)),
          });
        });
      }

      return segments;
    }

    // Personal mode
    const segments: BreadcrumbSegment[] = [];
    segments.push({
      label: t('resources.myResources'),
      onClick: selectedFolderId ? () => navigate(resPath('/resources')) : undefined,
    });

    if (selectedFolderId && folderChain.length > 0) {
      folderChain.forEach((f, idx) => {
        const isLast = idx === folderChain.length - 1;
        segments.push({
          label: f.name,
          onClick: isLast ? undefined : () => navigate(resPath(`/resources/folder/${f.id}`)),
        });
      });
    }

    return segments;
  }, [isSharedView, isRecycleView, isDownloadsView, selectedSmartFolderId, smartFolders, scopeType, selectedLibraryId, selectedFolderId, libraries, folders, folderChain, t, navigate, resPath, recycleFolderId, trashedFolders]);

  // ─── Sort ────────────────────────────────────────────

  // Filter trashed resources by current recycle folder
  const recycleItems = useMemo(() => {
    if (!recycleFolderId) {
      // Show only resources not inside any trashed folder
      const trashedFolderIds = new Set(trashedFolders.map((f) => String(f.id)));
      return trashedResources.filter((item) => {
        const fid = item.folder_id ? String(item.folder_id) : null;
        return !fid || !trashedFolderIds.has(fid);
      });
    }
    // Inside a trashed folder: use items fetched directly from resource_items
    return recycleFolderItems;
  }, [trashedResources, trashedFolders, recycleFolderId, recycleFolderItems]);

  // Sub-folders at current recycle level
  const recycleSubFolders = useMemo(() => {
    if (!recycleFolderId) {
      // Root level: show only folders whose parent is NOT also trashed
      const trashedIds = new Set(trashedFolders.map((f) => String(f.id)));
      return trashedFolders.filter(
        (f) => !f.parent_id || !trashedIds.has(String(f.parent_id))
      );
    }
    // Inside a folder: show its direct children
    return trashedFolders.filter(
      (f) => f.parent_id && String(f.parent_id) === recycleFolderId
    );
  }, [trashedFolders, recycleFolderId]);

  // Build preview thumbnails for trashed folders from trashedResources
  const trashedFolderPreviews = useMemo(() => {
    const map: Record<string, Array<{ resource_id?: string | null; thumbnail_path?: string | null; cover_image_path?: string | null; mime_type?: string | null }>> = {};
    for (const item of trashedResources) {
      const fid = item.folder_id ? String(item.folder_id) : null;
      if (!fid) continue;
      if (!map[fid]) map[fid] = [];
      if (map[fid].length < 4) {
        const r = item.resource;
        map[fid].push({
          resource_id: r?.id ? String(r.id) : null,
          thumbnail_path: r?.thumbnail_path || null,
          cover_image_path: r?.cover_image_path || null,
          mime_type: r?.mime_type || null,
        });
      }
    }
    return map;
  }, [trashedResources]);

  const currentItems = sidebarView === 'recycle'
    ? recycleItems
    : sidebarView === 'downloads'
      ? downloadedResources
      : resources;

  // Apply filter and search
  const filteredItems = useMemo(() => {
    let items = currentItems;

    // Apply type filters
    if (activeFilters.size > 0) {
      items = items.filter((item) => {
        const mime = item.resource?.mime_type || '';
        if (activeFilters.has('video') && mime.startsWith('video/')) return true;
        if (activeFilters.has('image') && mime.startsWith('image/')) return true;
        if (activeFilters.has('audio') && mime.startsWith('audio/')) return true;
        if (activeFilters.has('document') && (
          mime.startsWith('application/pdf') ||
          mime.startsWith('application/msword') ||
          mime.startsWith('application/vnd.') ||
          mime.startsWith('text/')
        )) return true;
        if (activeFilters.has('other')) {
          const isKnown = mime.startsWith('video/') || mime.startsWith('image/') || mime.startsWith('audio/') ||
            mime.startsWith('application/pdf') || mime.startsWith('application/msword') ||
            mime.startsWith('application/vnd.') || mime.startsWith('text/');
          if (!isKnown) return true;
        }
        return false;
      });
    }

    // Apply keyword search
    if (debouncedSearch.trim()) {
      const q = debouncedSearch.trim().toLowerCase();
      items = items.filter((item) => {
        const filename = (item.resource?.filename || '').toLowerCase();
        const folderName = (item.resource?.folder_name || '').toLowerCase();
        const notes = (item.resource?.notes || '').toLowerCase();
        const tagNames = (resourceTagNamesMap[String(item.resource?.id)] || '').toLowerCase();
        return filename.includes(q) || folderName.includes(q) || notes.includes(q) || tagNames.includes(q);
      });
    }

    // Apply AI search filter (match by media_id)
    if (aiSearchMatchedMediaIds) {
      items = items.filter((item) => {
        const mediaId = item.resource?.media_id;
        return mediaId && aiSearchMatchedMediaIds.has(String(mediaId));
      });
    }

    return items;
  }, [currentItems, activeFilters, debouncedSearch, aiSearchMatchedMediaIds, resourceTagNamesMap]);

  const sortedItems = useMemo(() => {
    const items = [...filteredItems];
    switch (sortBy) {
      case 'newest':
        return items.sort((a, b) => new Date(b.resource?.created_at ?? b.created_at).getTime() - new Date(a.resource?.created_at ?? a.created_at).getTime());
      case 'oldest':
        return items.sort((a, b) => new Date(a.resource?.created_at ?? a.created_at).getTime() - new Date(b.resource?.created_at ?? b.created_at).getTime());
      case 'name-az':
        return items.sort((a, b) => (a.resource?.filename ?? '').localeCompare(b.resource?.filename ?? ''));
      case 'name-za':
        return items.sort((a, b) => (b.resource?.filename ?? '').localeCompare(a.resource?.filename ?? ''));
      case 'largest':
        return items.sort((a, b) => (b.resource?.file_size_bytes ?? 0) - (a.resource?.file_size_bytes ?? 0));
      case 'smallest':
        return items.sort((a, b) => (a.resource?.file_size_bytes ?? 0) - (b.resource?.file_size_bytes ?? 0));
      default:
        return items;
    }
  }, [filteredItems, sortBy]);

  // Filter folders by search query
  const filteredFolders = useMemo(() => {
    if (!debouncedSearch.trim()) return childFolders;
    const q = debouncedSearch.trim().toLowerCase();
    return childFolders.filter((f) => f.name.toLowerCase().includes(q));
  }, [childFolders, debouncedSearch]);

  // Build ordered list of all selectable IDs for shift-click range selection
  const allSelectableIds = useMemo(() => {
    const ids: string[] = [];
    filteredFolders.forEach((f) => ids.push(`folder:${f.id}`));
    sortedItems.forEach((i) => ids.push(`item:${i.id}`));
    return ids;
  }, [filteredFolders, sortedItems]);

  const handleToggleSelect = useCallback((compositeId: string, e: React.MouseEvent) => {
    // Checkbox click always enters multi-select mode
    setMultiSelectMode(true);
    if (e.shiftKey && lastClickedId) {
      const allIds = allSelectableIds;
      const startIdx = allIds.indexOf(lastClickedId);
      const endIdx = allIds.indexOf(compositeId);
      if (startIdx >= 0 && endIdx >= 0) {
        const [from, to] = startIdx < endIdx ? [startIdx, endIdx] : [endIdx, startIdx];
        setSelectedIds((prev) => {
          const next = new Set(prev);
          for (let i = from; i <= to; i++) next.add(allIds[i]);
          return next;
        });
      }
    } else {
      // Toggle this item (no need for Cmd/Ctrl in multi-select mode)
      setSelectedIds((prev) => {
        const next = new Set(prev);
        if (next.has(compositeId)) next.delete(compositeId);
        else next.add(compositeId);
        return next;
      });
    }
    setLastClickedId(compositeId);
  }, [lastClickedId, allSelectableIds]);

  const handleCardClick = useCallback((compositeId: string, e?: React.MouseEvent) => {
    if (e && (e.metaKey || e.ctrlKey || e.shiftKey)) {
      handleToggleSelect(compositeId, e);
      return;
    }
    // Normal click exits multi-select mode → single select
    setMultiSelectMode(false);
    setSelectedIds(new Set([compositeId]));
    setLastClickedId(compositeId);
  }, [handleToggleSelect]);

  // ─── Keyboard shortcuts ─────────────────────────────

  useFileKeyboard({
    allSelectableIds,
    selectedIds,
    setSelectedIds,
    enabled: isResourcesView && !renamingResourceId && !renamingFolderId && !creatingFolder,
    onDelete: useCallback(() => {
      const resourceIds = sortedItems
        .filter((i) => selectedIds.has(`item:${i.id}`) && i.resource?.id)
        .map((i) => String(i.resource!.id));
      if (resourceIds.length > 0) {
        trashResources(resourceIds, scopeType, scopeId, selectedFolderId).then(async () => {
          const items = await fetchResources(scopeType, scopeId, selectedFolderId, selectedLibraryId);
          setResources(items);
          setSelectedIds(new Set());
          addToast(t('resources.trashedNotification', { name: `${resourceIds.length} items` }), 'success');
        }).catch((err) => { console.error('Failed to trash resources:', err); });
      }
    }, [sortedItems, selectedIds, scopeType, scopeId, selectedFolderId, selectedLibraryId, addToast, t]),
    onRename: useCallback((compositeId: string) => {
      if (compositeId.startsWith('folder:')) {
        const fId = compositeId.replace('folder:', '');
        const folder = childFolders.find((f) => f.id === fId);
        if (folder) {
          setRenamingFolderId(fId);
          setRenameFolderValue(folder.name);
        }
      } else if (compositeId.startsWith('item:')) {
        const itemId = compositeId.replace('item:', '');
        const item = resources.find((r) => r.id === itemId);
        if (item?.resource) {
          setRenamingResourceId(itemId);
          setRenameValue(item.resource.filename ?? '');
        }
      }
    }, [childFolders, resources]),
    onOpen: useCallback((compositeId: string) => {
      if (compositeId.startsWith('folder:')) {
        const fId = compositeId.replace('folder:', '');
        if (selectedLibraryId) {
          navigate(resPath(`/resources/library/${selectedLibraryId}/folder/${fId}`));
        } else {
          navigate(resPath(`/resources/folder/${fId}`));
        }
      } else if (compositeId.startsWith('item:')) {
        const itemId = compositeId.replace('item:', '');
        const item = resources.find((r) => r.id === itemId);
        if (item?.resource?.id) {
          navigate(resPath(`/resources/file/${item.resource.id}`));
        }
      }
    }, [selectedLibraryId, navigate, resPath, resources]),
    onNewFolder: useCallback(() => setCreatingFolder(true), []),
    onCopy: useCallback(() => {
      const items = sortedItems.filter((i) => selectedIds.has(`item:${i.id}`));
      if (items.length > 0) {
        setClipboardItems(items);
        setClipboardMode('copy');
        addToast(t('resources.copiedToClipboard', { count: items.length }), 'info');
      }
    }, [sortedItems, selectedIds, addToast, t]),
    onCut: useCallback(() => {
      const items = sortedItems.filter((i) => selectedIds.has(`item:${i.id}`));
      if (items.length > 0) {
        setClipboardItems(items);
        setClipboardMode('cut');
        addToast(t('resources.cutToClipboard', { count: items.length }), 'info');
      }
    }, [sortedItems, selectedIds, addToast, t]),
    onPaste: useCallback(async () => {
      if (clipboardItems.length === 0 || !clipboardMode) return;
      try {
        if (clipboardMode === 'copy') {
          for (const item of clipboardItems) {
            if (item.resource?.id) {
              await copyResourceItem(String(item.resource.id), scopeType, scopeId, selectedFolderId, selectedLibraryId);
            }
          }
          addToast(t('resources.copySuccess', { count: clipboardItems.length }), 'success');
        } else {
          // cut = move
          if (clipboardItems.length === 1) {
            await moveResourceItem(clipboardItems[0].id, selectedFolderId, selectedLibraryId);
          } else {
            await moveResourceItems(clipboardItems.map((i) => i.id), selectedFolderId, selectedLibraryId);
          }
          addToast(t('resources.moveSuccess', { count: clipboardItems.length }), 'success');
          setClipboardItems([]);
          setClipboardMode(null);
        }
        const items = await fetchResources(scopeType, scopeId, selectedFolderId, selectedLibraryId);
        setResources(items);
      } catch { /* ignore */ }
    }, [clipboardItems, clipboardMode, scopeType, scopeId, selectedFolderId, selectedLibraryId, addToast, t]),
  });

  const toggleFilter = useCallback((type: FilterType) => {
    setActiveFilters((prev) => {
      const next = new Set(prev);
      if (next.has(type)) next.delete(type);
      else next.add(type);
      return next;
    });
  }, []);

  const filterOptions: { value: FilterType; label: string }[] = [
    { value: 'video', label: t('smartFolder.fileTypes.video') },
    { value: 'image', label: t('smartFolder.fileTypes.image') },
    { value: 'audio', label: t('smartFolder.fileTypes.audio') },
    { value: 'document', label: t('smartFolder.fileTypes.document') },
    { value: 'other', label: t('smartFolder.fileTypes.other') },
  ];

  // Sort options
  const sortOptions: { value: SortBy; label: string }[] = [
    { value: 'newest', label: t('resources.sortNewest') },
    { value: 'oldest', label: t('resources.sortOldest') },
    { value: 'name-az', label: t('resources.sortNameAZ') },
    { value: 'name-za', label: t('resources.sortNameZA') },
    { value: 'largest', label: t('resources.sortLargest') },
    { value: 'smallest', label: t('resources.sortSmallest') },
  ];

  const currentSortLabel = sortOptions.find((o) => o.value === sortBy)?.label ?? '';

  // ─── Render ──────────────────────────────────────────


  // ─── Shell props (sidebar + info panel) ─────────────

  const [resSidebarCollapsed, setResSidebarCollapsed] = useState(false);

  const sidebarProps = useMemo(() => ({
    onSidebarDragOver: handleSidebarDragOver,
    onSidebarDrop: handleSidebarDrop,
    onCreateSmartFolder: () => setShowSmartFolderEditor(true),
    onEditSmartFolder: (sf: SmartCollection) => setEditingSmartFolder(sf),
    onDeleteSmartFolder: (id: string) => handleDeleteSmartFolder({ id } as any),
    onCreateLibrary: handleCreateLibrary,
    onNewFolder: () => setCreatingFolder(true),
    onSmartFolderContextMenu: (e: React.MouseEvent, sf: SmartCollection) => {
      setContextMenu({ x: e.clientX, y: e.clientY, type: 'smartFolder' as any, target: sf as any });
    },
    collapsed: resSidebarCollapsed,
    onToggleCollapse: () => setResSidebarCollapsed(v => !v),
  }), [handleSidebarDragOver, handleSidebarDrop, handleDeleteSmartFolder, handleCreateLibrary, resSidebarCollapsed]);

  const infoPanelProps = useMemo(() => ({
    trashedFolderPreviews,
    onRenameFolder: async (folderId: string | number, name: string) => {
      try {
        await renameFolder(folderId, name);
        setSelectedFolder(prev => prev ? { ...prev, name } : null);
        await Promise.all([loadFolders(), loadChildFolders()]);
      } catch (err) {
        console.error('Failed to rename folder:', err);
      }
    },
  }), [trashedFolderPreviews, loadFolders, loadChildFolders]);

  // ─── Render ──────────────────────────────────────────

  return (
    <>
      <ResourcesShell sidebarProps={sidebarProps} infoPanelProps={infoPanelProps}>
        {isDownloadsView ? (
          <DownloadsView />
        ) : (
          <>
            {/* Hidden file inputs — must be outside ResourceGrid so they persist */}
            {canUpload && (
              <>
                <input
                  ref={fileInputRef}
                  type="file"
                  multiple
                  className="hidden"
                  onChange={(e) => {
                    if (e.target.files?.length) {
                      handleUpload(e.target.files);
                      e.target.value = '';
                    }
                  }}
                />
                <input
                  ref={folderInputRef}
                  type="file"
                  // @ts-ignore - webkitdirectory is non-standard but widely supported
                  webkitdirectory=""
                  directory=""
                  multiple
                  className="hidden"
                  onChange={(e) => {
                    if (e.target.files?.length) {
                      handleUpload(e.target.files);
                      e.target.value = '';
                    }
                  }}
                />
              </>
            )}
            <ResourceGrid
              breadcrumbSegments={breadcrumbSegments}
              filteredFolders={filteredFolders}
              sortedItems={sortedItems}
              recycleSubFolders={recycleSubFolders}
              trashedFolderPreviews={trashedFolderPreviews}
              allSelectableIds={allSelectableIds}
              filterOptions={filterOptions}
              sortOptions={sortOptions}
              activeFilters={activeFilters}
              toggleFilter={toggleFilter}
              clearFilters={() => setActiveFilters(new Set())}
              currentSortLabel={currentSortLabel}
              onQueryChange={handleResourceQueryChange}
              onAISearch={handleResourceAISearch}
              onSearchClear={handleResourceSearchClear}
              isAISearching={isAISearching}
              uploading={uploading}
              overallProgress={upload.overallProgress}
              fileInputRef={fileInputRef}
              folderInputRef={folderInputRef}
              canUploadDrop={canUpload}
              dragOver={dragOver}
              onDragEnter={handleDragEnter}
              onDragOver={handleDragOver}
              onDragLeave={handleDragLeave}
              onDrop={handleDrop}
              onResourceClick={handleResourceClick}
              onResourceDoubleClick={handleResourceDoubleClick}
              onFileContextMenu={handleFileContextMenu}
              onFolderContextMenu={handleFolderContextMenu}
              onEmptyAreaContextMenu={handleEmptyAreaContextMenu}
              onCardClick={handleCardClick}
              onToggleSelect={handleToggleSelect}
              onDropOnFolder={handleDropOnFolder}
              renamingResourceId={renamingResourceId}
              renameValue={renameValue}
              onRenameChange={setRenameValue}
              onRenameResourceConfirm={handleRenameResourceConfirm}
              onRenameResourceCancel={() => setRenamingResourceId(null)}
              onStartRenameResource={(id, name) => { setRenamingResourceId(id); setRenameValue(name); }}
              renamingFolderId={renamingFolderId}
              renameFolderValue={renameFolderValue}
              onRenameFolderChange={setRenameFolderValue}
              onRenameFolderConfirm={handleRenameFolderConfirm}
              onRenameFolderCancel={() => setRenamingFolderId(null)}
              onStartRenameFolder={(id, name) => { setRenamingFolderId(id); setRenameFolderValue(name); }}
              creatingFolder={creatingFolder}
              newFolderName={newFolderName}
              savingFolder={savingFolder}
              newFolderInputRef={newFolderInputRef}
              onNewFolderNameChange={setNewFolderName}
              onCreateFolder={handleCreateFolder}
              onCancelCreateFolder={() => { setCreatingFolder(false); setNewFolderName(''); }}
              onStartCreateFolder={() => setCreatingFolder(true)}
              onShowSmartFolderEditor={() => setShowSmartFolderEditor(true)}
              getItemTouchHandlers={getItemTouchHandlers}
              isTouchDropTarget={isTouchDropTarget}
              touchDragState={touchDragState}
              onEmptyAreaTouchStart={handleEmptyAreaTouchStart}
              onEmptyAreaTouchMove={handleEmptyAreaTouchMove}
              onEmptyAreaTouchEnd={handleEmptyAreaTouchEnd}
              onTouchDragMove={handleTouchDragMove}
              onTouchDragEnd={handleTouchDragEnd}
            />
          </>
        )}
      </ResourcesShell>

      {/* ── Overlays (outside Shell — rendered as portals / fixed elements) ── */}

      {/* Batch Selection Toolbar */}
      {selectedIds.size > 0 && !isDownloadsView && (
        <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-50 flex items-center gap-3 bg-zinc-900 border border-zinc-700 rounded-xl px-5 py-3 shadow-2xl">
          <span className="text-sm text-zinc-300 font-medium">
            {t('resources.selected', { count: selectedIds.size })}
          </span>
          <div className="w-px h-5 bg-zinc-700" />
          {isRecycleView ? (
            <>
              <button
                onClick={async () => {
                  // Restore selected resources
                  const resourceIds = currentItems
                    .filter((i) => selectedIds.has(`item:${i.id}`) && i.resource?.id)
                    .map((i) => String(i.resource!.id));
                  for (const id of resourceIds) {
                    await restoreResource(id);
                  }
                  // Restore selected folders (cascade)
                  const folderIds = recycleSubFolders
                    .filter((f) => selectedIds.has(`folder:${f.id}`))
                    .map((f) => String(f.id));
                  for (const fid of folderIds) {
                    await restoreFolder(fid);
                  }
                  if (resourceIds.length > 0 || folderIds.length > 0) {
                    await loadTrashedResources();
                  }
                  setSelectedIds(new Set());
                }}
                className="flex items-center gap-1.5 px-3 py-1.5 text-sm text-emerald-400 hover:text-emerald-300 hover:bg-emerald-900/30 rounded-lg transition-colors"
              >
                <RefreshCw size={14} />
                {t('resources.batchRestore')}
              </button>
              <button
                onClick={() => {
                  const resourceIds = currentItems
                    .filter((i) => selectedIds.has(`item:${i.id}`) && i.resource?.id)
                    .map((i) => String(i.resource!.id));
                  const folderIds = recycleSubFolders
                    .filter((f) => selectedIds.has(`folder:${f.id}`))
                    .map((f) => String(f.id));
                  setPendingBatchPermanentDelete(resourceIds);
                  setPendingBatchPermanentDeleteFolders(folderIds);
                }}
                className="flex items-center gap-1.5 px-3 py-1.5 text-sm text-red-400 hover:text-red-300 hover:bg-red-900/30 rounded-lg transition-colors"
              >
                <Trash2 size={14} />
                {t('resources.batchPermanentDelete')}
              </button>
            </>
          ) : (
            <>
              <button
                onClick={() => {
                  const items = sortedItems.filter((i) => selectedIds.has(`item:${i.id}`));
                  const flds = childFolders.filter((f) => selectedIds.has(`folder:${f.id}`));
                  setOperationTargetItems(items);
                  setOperationTargetFolders(flds);
                  setFolderPickerMode('move');
                }}
                className="flex items-center gap-1.5 px-3 py-1.5 text-sm text-zinc-300 hover:text-white hover:bg-zinc-800 rounded-lg transition-colors"
              >
                <Move size={14} />
                {t('resources.batchMove')}
              </button>
              <button
                onClick={() => {
                  const items = sortedItems.filter((i) => selectedIds.has(`item:${i.id}`));
                  setOperationTargetItems(items);
                  setOperationTargetFolders([]);
                  setFolderPickerMode('copy');
                }}
                className="flex items-center gap-1.5 px-3 py-1.5 text-sm text-zinc-300 hover:text-white hover:bg-zinc-800 rounded-lg transition-colors"
              >
                <Copy size={14} />
                {t('resources.batchCopy')}
              </button>
              <button
                onClick={async () => {
                  try {
                    // Trash selected resources
                    const resourceIds = sortedItems
                      .filter((i) => selectedIds.has(`item:${i.id}`) && i.resource?.id)
                      .map((i) => String(i.resource!.id));
                    if (resourceIds.length > 0) {
                      await trashResources(resourceIds, scopeType, scopeId, selectedFolderId);
                    }

                    // Trash selected folders (cascade)
                    const folderIds = childFolders
                      .filter((f) => selectedIds.has(`folder:${f.id}`))
                      .map((f) => String(f.id));
                    for (const fid of folderIds) {
                      const { trashFolder } = await import('../services/resourceService');
                      await trashFolder(fid);
                    }

                    if (resourceIds.length > 0 || folderIds.length > 0) {
                      const items = await fetchResources(scopeType, scopeId, selectedFolderId, selectedLibraryId);
                      setResources(items);
                      await Promise.all([loadFolders(), loadChildFolders()]);
                      setSelectedIds(new Set());
                    }
                  } catch (err) {
                    console.error('Batch delete failed:', err);
                  }
                }}
                className="flex items-center gap-1.5 px-3 py-1.5 text-sm text-red-400 hover:text-red-300 hover:bg-red-900/30 rounded-lg transition-colors"
              >
                <Trash2 size={14} />
                {t('resources.batchDelete')}
              </button>
            </>
          )}
          <div className="w-px h-5 bg-zinc-700" />
          <button
            onClick={() => setSelectedIds(new Set())}
            className="p-1.5 text-zinc-400 hover:text-white hover:bg-zinc-800 rounded-lg transition-colors"
          >
            <X size={14} />
          </button>
        </div>
      )}

      {/* Context Menu */}
      {contextMenu && (
        <ContextMenu
          x={contextMenu.x}
          y={contextMenu.y}
          items={contextMenuItems}
          onClose={closeContextMenu}
        />
      )}

      {/* Hidden file input for version upload via context menu */}
      <input
        ref={versionInputRef}
        type="file"
        className="hidden"
        onChange={handleVersionFileSelected}
      />

      {/* Smart Folder Editor (Create) */}
      {showSmartFolderEditor && (
        <SmartFolderEditor
          onSave={handleCreateSmartFolder}
          onClose={() => setShowSmartFolderEditor(false)}
        />
      )}

      {/* Smart Folder Editor (Edit) */}
      {editingSmartFolder && (
        <SmartFolderEditor
          initialName={editingSmartFolder.name}
          initialRules={editingSmartFolder.smart_rules as SmartFolderRules | undefined}
          onSave={handleEditSmartFolder}
          onClose={() => setEditingSmartFolder(null)}
        />
      )}

      {/* Share Modal */}
      {shareTarget && (
        <ShareModal
          isOpen={true}
          onClose={() => setShareTarget(null)}
          resourceId={shareTarget.resourceId}
          folderId={shareTarget.folderId}
        />
      )}

      {/* Folder Picker Modal (Copy/Move) */}
      {folderPickerMode && (
        <FolderPickerModal
          isOpen={true}
          onClose={() => {
            setFolderPickerMode(null);
            setOperationTargetItems([]);
            setOperationTargetFolders([]);
          }}
          onConfirm={handleFolderPickerConfirm}
          mode={folderPickerMode}
          scopeType={scopeType}
          scopeId={scopeId}
          currentLibraryId={selectedLibraryId}
          excludeFolderIds={operationTargetFolders.map((f) => f.id)}
          movingItems={[
            ...operationTargetItems.map((item) => ({
              name: item.resource?.filename || 'Untitled',
              thumbnail: item.resource?.thumbnail_path || null,
              mediaType: item.resource?.mime_type || undefined,
            })),
            ...operationTargetFolders.map((f) => ({
              name: f.name,
              mediaType: 'folder',
            })),
          ]}
        />
      )}

      {/* Duplicate File Alert */}
      {duplicateAlert && (
        <DuplicateFileAlert
          file={duplicateAlert.file}
          existing={duplicateAlert.existing}
          remainingDuplicates={duplicateAlert.remainingDuplicates}
          onUseExisting={(applyToAll) => {
            duplicateAlert.resolve({ action: 'use-existing', applyToAll });
          }}
          onKeepBoth={(applyToAll) => {
            duplicateAlert.resolve({ action: 'keep-both', applyToAll });
          }}
          onCancel={() => duplicateAlert.resolve({ action: 'cancel', applyToAll: false })}
        />
      )}

      {/* Permanent Delete Confirmation Dialog */}
      {(pendingPermanentDelete || pendingBatchPermanentDelete || pendingBatchPermanentDeleteFolders) && (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
          <div
            className="absolute inset-0 bg-black/60 backdrop-blur-sm"
            onClick={() => { setPendingPermanentDelete(null); setPendingBatchPermanentDelete(null); setPendingBatchPermanentDeleteFolders(null); }}
          />
          <div className="relative bg-zinc-900 border border-zinc-800 rounded-2xl shadow-2xl w-full max-w-sm mx-4">
            <div className="p-6 text-center">
              <div className="w-12 h-12 rounded-full bg-red-500/10 flex items-center justify-center mx-auto mb-4">
                <AlertTriangle className="text-red-500" size={24} />
              </div>
              <h3 className="text-lg font-semibold text-white mb-2">
                {t('resources.confirmPermanentDelete')}
              </h3>
              <p className="text-sm text-zinc-400">
                {t('resources.permanentDeleteWarning')}
              </p>
            </div>
            <div className="flex gap-3 p-4 border-t border-zinc-800">
              <button
                onClick={() => { setPendingPermanentDelete(null); setPendingBatchPermanentDelete(null); setPendingBatchPermanentDeleteFolders(null); }}
                className="flex-1 px-4 py-2 text-sm font-medium text-zinc-300 bg-zinc-800 hover:bg-zinc-700 rounded-lg transition-colors"
              >
                {t('common.cancel')}
              </button>
              <button
                onClick={confirmPermanentDelete}
                className="flex-1 px-4 py-2 text-sm font-medium text-white bg-red-600 hover:bg-red-500 rounded-lg transition-colors"
              >
                {t('resources.deleteForever')}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Touch drag preview — portal to body */}
      {touchDragState.isDragging && touchDragState.dragPosition && createPortal(
        <div
          className="fixed z-[100] pointer-events-none flex items-center gap-2 bg-zinc-800/90 backdrop-blur-sm border border-zinc-600 rounded-lg px-3 py-2 shadow-2xl"
          style={{
            left: touchDragState.dragPosition.x - 40,
            top: touchDragState.dragPosition.y - 20,
          }}
        >
          <Move size={14} className="text-indigo-400" />
          <span className="text-sm text-zinc-200">
            {touchDragState.dragIds.length === 1 ? 'Moving item' : `${touchDragState.dragIds.length} items`}
          </span>
        </div>,
        document.body,
      )}
    </>
  );
};
