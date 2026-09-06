// frontend/hooks/useResourceOperations.ts

/**
 * Operations hook for ResourcesView.
 * Handles copy/move/rename/trash, folder picker, smart folder CRUD,
 * keyboard shortcuts, and clipboard state.
 */

import { useState, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import {
  createSmartFolder,
  updateSmartFolder,
  deleteSmartFolder,
  fetchSmartFolders,
  renameFolder,
  renameResource,
  moveResourceItem,
  moveResourceItems,
  moveFolder,
  copyResourceItem,
  trashResources,
} from '../services/resourceService';
import type { SmartFolderRules } from '../services/resourceService';
import { partitionMovableFolders, describeFolderMutationFailure } from './moveBatch';
import { useFileKeyboard } from './useFileKeyboard';
import type { Folder, ResourceItem, SmartCollection } from '../types';

interface UseResourceOperationsOptions {
  isPersonal: boolean;
  scopeId: string;
  selectedFolderId: string | null | undefined;
  selectedLibraryId: string | null | undefined;
  resources: ResourceItem[];
  childFolders: Folder[];
  sortedItems: ResourceItem[];
  selectedIds: Set<string>;
  allSelectableIds: string[];
  isResourcesView: boolean;
  navigate: (path: string) => void;
  resPath: (path: string) => string;
  setResources: React.Dispatch<React.SetStateAction<ResourceItem[]>>;
  setSmartFolders: React.Dispatch<React.SetStateAction<SmartCollection[]>>;
  setSelectedIds: React.Dispatch<React.SetStateAction<Set<string>>>;
  loadFolders: () => Promise<void>;
  loadChildFolders: () => Promise<void>;
  /** Re-fetch the current resource list honouring active filter params. */
  reloadResources: () => Promise<void>;
  addToast: (msg: string, type: 'success' | 'error' | 'info') => void;
}

export function useResourceOperations({
  isPersonal,
  scopeId,
  selectedFolderId,
  selectedLibraryId,
  resources,
  childFolders,
  sortedItems,
  selectedIds,
  allSelectableIds,
  isResourcesView,
  navigate,
  resPath,
  setResources,
  setSmartFolders,
  setSelectedIds,
  loadFolders,
  loadChildFolders,
  reloadResources,
  addToast,
}: UseResourceOperationsOptions) {
  const { t } = useTranslation();

  // ─── Rename state ──────────────────────────────────
  const [renamingResourceId, setRenamingResourceId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState('');
  const [renamingFolderId, setRenamingFolderId] = useState<string | null>(null);
  const [renameFolderValue, setRenameFolderValue] = useState('');

  // ─── Smart folder editor state ─────────────────────
  const [showSmartFolderEditor, setShowSmartFolderEditor] = useState(false);
  const [editingSmartFolder, setEditingSmartFolder] = useState<SmartCollection | null>(null);

  // ─── Share target ──────────────────────────────────
  const [shareTarget, setShareTarget] = useState<{
    resourceId?: string;
    folderId?: string;
    libraryId?: string;
  } | null>(null);

  // ─── Copy/Move state ───────────────────────────────
  const [folderPickerMode, setFolderPickerMode] = useState<'copy' | 'move' | null>(null);
  const [operationTargetItems, setOperationTargetItems] = useState<ResourceItem[]>([]);
  const [operationTargetFolders, setOperationTargetFolders] = useState<Folder[]>([]);

  // ─── Clipboard state ───────────────────────────────
  const [clipboardItems, setClipboardItems] = useState<ResourceItem[]>([]);
  const [clipboardMode, setClipboardMode] = useState<'copy' | 'cut' | null>(null);

  // ─── Version upload state ──────────────────────────
  const [versionTargetId, setVersionTargetId] = useState<string | null>(null);

  // ─── Rename handlers ───────────────────────────────

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
  }, [renamingResourceId, renameValue, resources, addToast, t, setResources]);

  const handleRenameFolderConfirm = useCallback(async () => {
    if (!renamingFolderId || !renameFolderValue.trim()) {
      setRenamingFolderId(null);
      return;
    }
    // `renameFolder` writes `folders` through PostgREST directly, so like the
    // move path it never met the mig 441 API guard — and since mig 457 the DB
    // trigger refuses it. Pre-refuse what we can see, and echo whatever the
    // server refuses; a rename that silently does nothing is the defect this
    // ticket exists to remove.
    const target = childFolders.find((f) => f.id === renamingFolderId);
    if (target?.is_system) {
      addToast(t('resources.systemFolderLocked'), 'error');
      setRenamingFolderId(null);
      return;
    }
    try {
      await renameFolder(renamingFolderId, renameFolderValue.trim());
      await Promise.all([loadFolders(), loadChildFolders()]);
      addToast(t('resources.renamedNotification', { name: renameFolderValue.trim() }), 'success');
    } catch (err) {
      // The pre-refuse above only sees `childFolders`; a folder renamed from
      // somewhere else still reaches the server, so this arm is not dead code.
      if (describeFolderMutationFailure(err) === 'system_folder') {
        addToast(t('resources.systemFolderLocked'), 'error');
      } else {
        addToast(t('resources.renameFailed', 'Rename Failed'), 'error');
        console.error('[useResourceOperations] rename failed:', err);
      }
    }
    setRenamingFolderId(null);
  }, [renamingFolderId, renameFolderValue, childFolders, loadFolders, loadChildFolders, addToast, t]);

  // ─── Folder picker confirm ─────────────────────────

  const handleFolderPickerConfirm = useCallback(async (targetFolderId: string | null, targetLibraryId?: string | null) => {
    if (folderPickerMode === 'move') {
      // The context menu hides Move on a system folder, but that menu only
      // ever sees the one right-clicked folder — a multi-select batch walks
      // straight past it. Refuse the locked ones here, out loud, BEFORE
      // anything moves, so a partial batch is never a surprise.
      const { movable, locked } = partitionMovableFolders(operationTargetFolders);
      if (locked.length > 0) {
        addToast(t('resources.systemFolderLocked'), 'error');
      }
      try {
        for (const folder of movable) {
          await moveFolder(folder.id, targetFolderId, targetLibraryId);
        }
        if (operationTargetItems.length === 1) {
          await moveResourceItem(operationTargetItems[0].id, targetFolderId, targetLibraryId);
        } else if (operationTargetItems.length > 1) {
          await moveResourceItems(operationTargetItems.map((i) => i.id), targetFolderId, targetLibraryId);
        }
        const totalMoved = movable.length + operationTargetItems.length;
        // Zero happens when the whole selection was locked: the error toast
        // above already said so, and "Moved 0 files" would contradict it.
        if (totalMoved > 0) {
          addToast(t('resources.moveSuccess', { count: totalMoved }), 'success');
        }
      } catch (err) {
        if (describeFolderMutationFailure(err) === 'system_folder') {
          addToast(t('resources.systemFolderLocked'), 'error');
        } else {
          addToast(t('resources.moveFailed', 'Move failed — nothing was changed for the remaining items'), 'error');
          console.error('[useResourceOperations] move failed:', err);
        }
      } finally {
        // In `finally` because a batch can fail halfway: the folders that did
        // move must still show up in their new home.
        await Promise.all([loadFolders(), loadChildFolders()]);
        await reloadResources();
      }
    } else if (folderPickerMode === 'copy') {
      try {
        for (const item of operationTargetItems) {
          if (item.resource?.id) {
            await copyResourceItem(String(item.resource.id), scopeId, targetFolderId, targetLibraryId);
          }
        }
        addToast(t('resources.copySuccess', { count: operationTargetItems.length }), 'success');
      } catch (err) {
        addToast(t('resources.copyFailed', 'Copy failed'), 'error');
        console.error('[useResourceOperations] copy failed:', err);
      }
    }
    setFolderPickerMode(null);
    setOperationTargetItems([]);
    setOperationTargetFolders([]);
  }, [folderPickerMode, operationTargetItems, operationTargetFolders, isPersonal, scopeId, loadFolders, loadChildFolders, reloadResources, addToast, t]);

  // ─── Smart Folder CRUD ─────────────────────────────

  const handleCreateSmartFolder = useCallback(async (name: string, rules: SmartFolderRules) => {
    await createSmartFolder(name, scopeId, rules);
    const updated = await fetchSmartFolders(scopeId);
    setSmartFolders(updated);
    setShowSmartFolderEditor(false);
    const newest = updated[updated.length - 1];
    if (newest) navigate(resPath(`/resources/smart/${newest.id}`));
  }, [scopeId, navigate, resPath, setSmartFolders]);

  const handleEditSmartFolder = useCallback(async (name: string, rules: SmartFolderRules) => {
    if (!editingSmartFolder) return;
    await updateSmartFolder(String(editingSmartFolder.id), { name, rules });
    const updated = await fetchSmartFolders(scopeId);
    setSmartFolders(updated);
    setEditingSmartFolder(null);
  }, [editingSmartFolder, scopeId, setSmartFolders]);

  const handleDeleteSmartFolder = useCallback(async (sf: SmartCollection) => {
    if (!confirm(t('smartFolder.confirmDelete'))) return;
    await deleteSmartFolder(String(sf.id));
    const updated = await fetchSmartFolders(scopeId);
    setSmartFolders(updated);
  }, [scopeId, setSmartFolders, t]);

  // ─── Keyboard shortcuts ────────────────────────────

  useFileKeyboard({
    allSelectableIds,
    selectedIds,
    setSelectedIds,
    enabled: isResourcesView && !renamingResourceId && !renamingFolderId,
    onDelete: useCallback(() => {
      const resourceIds = sortedItems
        .filter((i) => selectedIds.has(`item:${i.id}`) && i.resource?.id)
        .map((i) => String(i.resource!.id));
      if (resourceIds.length > 0) {
        trashResources(resourceIds, scopeId, selectedFolderId).then(async () => {
          await reloadResources();
          setSelectedIds(new Set());
          addToast(t('resources.trashedNotification', { name: `${resourceIds.length} items` }), 'success');
        }).catch((err) => { console.error('Failed to trash resources:', err); });
      }
    }, [sortedItems, selectedIds, scopeId, selectedFolderId, reloadResources, addToast, t, setSelectedIds]),
    onRename: useCallback((compositeId: string) => {
      if (compositeId.startsWith('folder:')) {
        const fId = compositeId.replace('folder:', '');
        const folder = childFolders.find((f) => f.id === fId);
        if (folder) { setRenamingFolderId(fId); setRenameFolderValue(folder.name); }
      } else if (compositeId.startsWith('item:')) {
        const itemId = compositeId.replace('item:', '');
        const item = resources.find((r) => r.id === itemId);
        if (item?.resource) { setRenamingResourceId(itemId); setRenameValue(item.resource.filename ?? ''); }
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
        if (item?.resource?.id) navigate(resPath(`/resources/file/${item.resource.id}`));
      }
    }, [selectedLibraryId, navigate, resPath, resources]),
    onNewFolder: useCallback(() => {}, []), // handled by caller
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
              await copyResourceItem(String(item.resource.id), scopeId, selectedFolderId, selectedLibraryId);
            }
          }
          addToast(t('resources.copySuccess', { count: clipboardItems.length }), 'success');
        } else {
          if (clipboardItems.length === 1) {
            await moveResourceItem(clipboardItems[0].id, selectedFolderId, selectedLibraryId);
          } else {
            await moveResourceItems(clipboardItems.map((i) => i.id), selectedFolderId, selectedLibraryId);
          }
          addToast(t('resources.moveSuccess', { count: clipboardItems.length }), 'success');
          setClipboardItems([]);
          setClipboardMode(null);
        }
        await reloadResources();
      } catch { /* ignore */ }
    }, [clipboardItems, clipboardMode, isPersonal, scopeId, selectedFolderId, selectedLibraryId, reloadResources, addToast, t]),
  });

  return {
    // Rename
    renamingResourceId, setRenamingResourceId,
    renameValue, setRenameValue,
    renamingFolderId, setRenamingFolderId,
    renameFolderValue, setRenameFolderValue,
    handleRenameResourceConfirm,
    handleRenameFolderConfirm,
    // Smart folders
    showSmartFolderEditor, setShowSmartFolderEditor,
    editingSmartFolder, setEditingSmartFolder,
    handleCreateSmartFolder, handleEditSmartFolder, handleDeleteSmartFolder,
    // Share
    shareTarget, setShareTarget,
    // Copy/Move
    folderPickerMode, setFolderPickerMode,
    operationTargetItems, setOperationTargetItems,
    operationTargetFolders, setOperationTargetFolders,
    handleFolderPickerConfirm,
    // Clipboard
    clipboardItems, setClipboardItems,
    clipboardMode, setClipboardMode,
    // Version
    versionTargetId, setVersionTargetId,
  };
}
