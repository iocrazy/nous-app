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
import { useFileKeyboard } from './useFileKeyboard';
import type { Folder, ResourceItem, SmartCollection } from '../types';

interface UseResourceOperationsOptions {
  scopeType: 'personal' | 'team';
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
  scopeType,
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
    try {
      await renameFolder(renamingFolderId, renameFolderValue.trim());
      await Promise.all([loadFolders(), loadChildFolders()]);
      addToast(t('resources.renamedNotification', { name: renameFolderValue.trim() }), 'success');
    } catch { /* ignore */ }
    setRenamingFolderId(null);
  }, [renamingFolderId, renameFolderValue, loadFolders, loadChildFolders, addToast, t]);

  // ─── Folder picker confirm ─────────────────────────

  const handleFolderPickerConfirm = useCallback(async (targetFolderId: string | null, targetLibraryId?: string | null) => {
    try {
      if (folderPickerMode === 'move') {
        for (const folder of operationTargetFolders) {
          await moveFolder(folder.id, targetFolderId, targetLibraryId);
        }
        if (operationTargetItems.length === 1) {
          await moveResourceItem(operationTargetItems[0].id, targetFolderId, targetLibraryId);
        } else if (operationTargetItems.length > 1) {
          await moveResourceItems(operationTargetItems.map((i) => i.id), targetFolderId, targetLibraryId);
        }
        await Promise.all([loadFolders(), loadChildFolders()]);
        await reloadResources();
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
    } catch { /* ignore */ }
    setFolderPickerMode(null);
    setOperationTargetItems([]);
    setOperationTargetFolders([]);
  }, [folderPickerMode, operationTargetItems, operationTargetFolders, scopeType, scopeId, loadFolders, loadChildFolders, reloadResources, addToast, t]);

  // ─── Smart Folder CRUD ─────────────────────────────

  const handleCreateSmartFolder = useCallback(async (name: string, rules: SmartFolderRules) => {
    await createSmartFolder(name, scopeType, scopeId, rules);
    const updated = await fetchSmartFolders(scopeType, scopeId);
    setSmartFolders(updated);
    setShowSmartFolderEditor(false);
    const newest = updated[updated.length - 1];
    if (newest) navigate(resPath(`/resources/smart/${newest.id}`));
  }, [scopeType, scopeId, navigate, resPath, setSmartFolders]);

  const handleEditSmartFolder = useCallback(async (name: string, rules: SmartFolderRules) => {
    if (!editingSmartFolder) return;
    await updateSmartFolder(String(editingSmartFolder.id), { name, rules });
    const updated = await fetchSmartFolders(scopeType, scopeId);
    setSmartFolders(updated);
    setEditingSmartFolder(null);
  }, [editingSmartFolder, scopeType, scopeId, setSmartFolders]);

  const handleDeleteSmartFolder = useCallback(async (sf: SmartCollection) => {
    if (!confirm(t('smartFolder.confirmDelete'))) return;
    await deleteSmartFolder(String(sf.id));
    const updated = await fetchSmartFolders(scopeType, scopeId);
    setSmartFolders(updated);
  }, [scopeType, scopeId, setSmartFolders, t]);

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
        trashResources(resourceIds, scopeType, scopeId, selectedFolderId).then(async () => {
          await reloadResources();
          setSelectedIds(new Set());
          addToast(t('resources.trashedNotification', { name: `${resourceIds.length} items` }), 'success');
        }).catch((err) => { console.error('Failed to trash resources:', err); });
      }
    }, [sortedItems, selectedIds, scopeType, scopeId, selectedFolderId, reloadResources, addToast, t, setSelectedIds]),
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
              await copyResourceItem(String(item.resource.id), scopeType, scopeId, selectedFolderId, selectedLibraryId);
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
    }, [clipboardItems, clipboardMode, scopeType, scopeId, selectedFolderId, selectedLibraryId, reloadResources, addToast, t]),
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
