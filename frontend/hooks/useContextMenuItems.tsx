// frontend/hooks/useContextMenuItems.tsx

/**
 * Returns the ContextMenuItem array for ResourcesViewInner based on
 * what was right-clicked (file, folder, smartFolder, or empty area).
 */

import React, { useMemo } from 'react';
import {
  FolderOpen, Upload, Trash2, Share2, Download,
  FolderPlus, ExternalLink, Pencil, Copy, Move, RefreshCw, Eye,
  Sparkles, Tag,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import type { ContextMenuItem } from '../components/ContextMenu';
import type { Folder, SmartCollection, ResourceItem } from '../types';
import {
  getFolderContentCount,
  getResourceFileUrl,
  copyResourceItem,
  generateGenPrompt,
  classifyResource,
} from '../services/resourceService';
import { downloadWithAuth } from '../utils/download';

interface ContextMenuState {
  x: number;
  y: number;
  type: 'file' | 'folder' | 'empty';
  target?: any;
}

interface UseContextMenuItemsOptions {
  contextMenu: ContextMenuState | null;
  isPersonal: boolean;
  scopeId: string;
  selectedFolderId: string | null | undefined;
  selectedLibraryId: string | null | undefined;
  navigate: (path: string) => void;
  resPath: (path: string) => string;
  canDo: (action: string) => boolean;
  fileInputRef: React.RefObject<HTMLInputElement | null>;
  setCreatingFolder: (v: boolean) => void;
  setLoading: (v: boolean) => void;
  setSelectedResource: (v: any) => void;
  setSelectedFolder: (v: any) => void;
  setShowInfoPanel: (v: boolean) => void;
  setResources: React.Dispatch<React.SetStateAction<ResourceItem[]>>;
  addToast: (msg: string, type: 'success' | 'error' | 'info') => void;
  loadFolders: () => Promise<void>;
  loadChildFolders: () => Promise<void>;
  /** Re-fetch the current resource list honouring active filter params. */
  reloadResources: () => Promise<void>;
  handleTrash: (resourceId: string) => void;
  ops: {
    setRenamingResourceId: (id: string | null) => void;
    setRenameValue: (v: string) => void;
    setVersionTargetId: (id: string | null) => void;
    setOperationTargetItems: (items: ResourceItem[]) => void;
    setOperationTargetFolders: (folders: Folder[]) => void;
    setFolderPickerMode: (mode: 'copy' | 'move' | null) => void;
    setShareTarget: (v: any) => void;
    setRenamingFolderId: (id: string | null) => void;
    setRenameFolderValue: (v: string) => void;
    setEditingSmartFolder: (sf: SmartCollection | null) => void;
    handleDeleteSmartFolder: (sf: SmartCollection) => void;
    operationTargetItems: ResourceItem[];
    folderPickerMode: 'copy' | 'move' | null;
  };
  versionInputRef: React.RefObject<HTMLInputElement | null>;
}

export function useContextMenuItems({
  contextMenu,
  isPersonal,
  scopeId,
  selectedFolderId,
  selectedLibraryId,
  navigate,
  resPath,
  canDo,
  fileInputRef,
  setCreatingFolder,
  setLoading,
  setSelectedResource,
  setSelectedFolder,
  setShowInfoPanel,
  setResources,
  addToast,
  loadFolders,
  loadChildFolders,
  reloadResources,
  handleTrash,
  ops,
  versionInputRef,
}: UseContextMenuItemsOptions): ContextMenuItem[] {
  const { t } = useTranslation();

  return useMemo((): ContextMenuItem[] => {
    if (!contextMenu) return [];

    if (contextMenu.type === 'file') {
      const item = contextMenu.target;
      const resourceId = item.resource?.id;
      const items: ContextMenuItem[] = [
        { label: t('resources.viewDetails', 'View Details'), icon: <Eye size={14} />, onClick: () => { if (resourceId) navigate(resPath(`/resources/file/${resourceId}`)); }, disabled: !resourceId },
        { label: t('resources.openInNewTab'), icon: <ExternalLink size={14} />, onClick: () => { if (resourceId) window.open(getResourceFileUrl(String(resourceId)), '_blank'); }, disabled: !resourceId, divider: true },
      ];
      if (canDo('download')) {
        items.push({ label: t('resources.downloadOriginal'), icon: <Download size={14} />, onClick: () => { if (resourceId) downloadWithAuth(getResourceFileUrl(String(resourceId)), item.resource?.filename ?? 'download', { onSuccess: (f: string) => addToast(`Downloaded: ${f}`, 'success'), onError: (msg: string) => addToast(`Download failed (${msg})`, 'error') }); }, disabled: !resourceId });
      }
      // Asset AI (images only): reverse-prompt + 12-dimension auto-tag.
      // Both dispatch DBOS workflows — progress lives in the Task Center.
      if (item.resource?.file_type === 'image') {
        items.push({
          label: t('resources.generatePrompt', 'Generate Prompt'),
          icon: <Sparkles size={14} />,
          onClick: async () => {
            if (!resourceId) return;
            try {
              await generateGenPrompt(String(resourceId));
              addToast(t('resources.infoPanel.promptGenerating', 'Generating prompt from image...'), 'info');
            } catch (err) {
              console.error('Failed to start prompt generation:', err);
              addToast(err instanceof Error && err.message ? err.message : t('resources.infoPanel.promptGenerateFailed', 'Failed to generate prompt'), 'error');
            }
          },
          disabled: !resourceId,
        });
        items.push({
          label: t('resources.autoTag', 'Auto Tag'),
          icon: <Tag size={14} />,
          onClick: async () => {
            if (!resourceId) return;
            try {
              await classifyResource(String(resourceId));
              addToast(t('resources.infoPanel.autoTagging', 'Auto-tagging image...'), 'info');
            } catch (err) {
              console.error('Failed to start auto-tagging:', err);
              addToast(err instanceof Error && err.message ? err.message : t('resources.infoPanel.autoTagFailed', 'Auto-tagging failed'), 'error');
            }
          },
          disabled: !resourceId,
          divider: true,
        });
      }
      if (canDo('update')) {
        items.push({ label: t('resources.rename'), icon: <Pencil size={14} />, onClick: () => { if (resourceId) { ops.setRenamingResourceId(item.id); ops.setRenameValue(item.resource?.filename ?? ''); } } });
        items.push({ label: t('resources.uploadNewVersion'), icon: <Upload size={14} />, onClick: () => { if (resourceId) { ops.setVersionTargetId(String(resourceId)); setTimeout(() => versionInputRef.current?.click(), 0); } }, divider: true });
      }
      if (canDo('copy')) items.push({ label: t('resources.copyTo'), icon: <Copy size={14} />, onClick: () => { ops.setOperationTargetItems([item]); ops.setOperationTargetFolders([]); ops.setFolderPickerMode('copy'); } });
      if (canDo('move')) items.push({ label: t('resources.moveTo'), icon: <Move size={14} />, onClick: () => { ops.setOperationTargetItems([item]); ops.setOperationTargetFolders([]); ops.setFolderPickerMode('move'); } });
      if (canDo('share')) items.push({ label: t('resources.share'), icon: <Share2 size={14} />, onClick: () => ops.setShareTarget({ resourceId: String(item.resource?.id) }) });
      if (canDo('delete')) items.push({ label: t('resources.moveToTrash'), icon: <Trash2 size={14} />, onClick: () => { if (resourceId) handleTrash(resourceId); }, danger: true, divider: true });
      return items;
    }

    if (contextMenu.type === 'folder') {
      const folder = contextMenu.target as Folder;
      const folderUrl = selectedLibraryId
        ? resPath(`/resources/library/${selectedLibraryId}/folder/${folder.id}`)
        : resPath(`/resources/folder/${folder.id}`);
      const items: ContextMenuItem[] = [
        { label: t('resources.getInfo', 'Get Info'), icon: <Eye size={14} />, onClick: () => { setSelectedResource(null); setSelectedFolder(folder); setShowInfoPanel(true); } },
        { label: t('resources.openInNewTab'), icon: <ExternalLink size={14} />, onClick: () => window.open(folderUrl, '_blank') },
        { label: t('resources.open'), icon: <FolderOpen size={14} />, onClick: () => navigate(folderUrl) },
      ];
      if (canDo('update')) items.push({ label: t('resources.rename'), icon: <Pencil size={14} />, onClick: () => { ops.setRenamingFolderId(folder.id); ops.setRenameFolderValue(folder.name); }, divider: true });
      if (canDo('copy')) items.push({ label: t('resources.copyTo'), icon: <Copy size={14} />, onClick: () => { ops.setOperationTargetItems([]); ops.setOperationTargetFolders([folder]); ops.setFolderPickerMode('copy'); } });
      if (canDo('move')) items.push({ label: t('resources.moveTo'), icon: <Move size={14} />, onClick: () => { ops.setOperationTargetItems([]); ops.setOperationTargetFolders([folder]); ops.setFolderPickerMode('move'); } });
      if (canDo('share')) items.push({ label: t('resources.share'), icon: <Share2 size={14} />, onClick: () => ops.setShareTarget({ folderId: String(folder.id) }) });
      if (canDo('delete')) {
        items.push({
          label: t('resources.moveToTrash'), icon: <Trash2 size={14} />,
          onClick: async () => {
            try {
              const counts = await getFolderContentCount(folder.id);
              const hasContents = counts.resource_count > 0 || counts.subfolder_count > 0;
              if (hasContents) {
                const parts: string[] = [];
                if (counts.resource_count > 0) parts.push(`${counts.resource_count} file(s)`);
                if (counts.subfolder_count > 0) parts.push(`${counts.subfolder_count} sub-folder(s)`);
                if (!confirm(t('resources.trashFolderConfirm', { defaultValue: 'This folder contains {{contents}}. All contents will be moved to the recycle bin. Continue?', contents: parts.join(' and ') }))) return;
              }
              const { trashFolder } = await import('../services/resourceService');
              await trashFolder(folder.id);
              await loadFolders();
              await loadChildFolders();
              await reloadResources();
            } catch (err) {
              console.error('Failed to trash folder:', err);
              addToast(t('resources.trashFolderFailed', 'Failed to move folder to trash'), 'error');
            }
          },
          danger: true, divider: true,
        });
      }
      return items;
    }

    if ((contextMenu.type as string) === 'smartFolder') {
      const sf = contextMenu.target as unknown as SmartCollection;
      return [
        { label: t('smartFolder.editSmartFolder'), icon: <Pencil size={14} />, onClick: () => ops.setEditingSmartFolder(sf) },
        { label: t('smartFolder.deleteSmartFolder'), icon: <Trash2 size={14} />, onClick: () => ops.handleDeleteSmartFolder(sf), danger: true, divider: true },
      ];
    }

    // Empty area
    const emptyItems: ContextMenuItem[] = [];
    if (canDo('upload')) {
      emptyItems.push(
        { label: t('resources.uploadFile'), icon: <Upload size={14} />, onClick: () => fileInputRef.current?.click() },
        { label: t('resources.newFolder'), icon: <FolderPlus size={14} />, onClick: () => setCreatingFolder(true) },
      );
    }
    emptyItems.push({
      label: t('resources.refresh'), icon: <RefreshCw size={14} />,
      onClick: async () => {
        setLoading(true);
        try {
          await Promise.all([loadChildFolders()]);
          await reloadResources();
        } catch { /* ignore */ }
        setLoading(false);
      },
      divider: true,
    });
    if (ops.operationTargetItems.length > 0 && ops.folderPickerMode === null) {
      emptyItems.push({
        label: t('resources.paste'), icon: <Copy size={14} />,
        onClick: async () => {
          try {
            for (const item of ops.operationTargetItems) {
              if (item.resource?.id) await copyResourceItem(String(item.resource.id), scopeId, selectedFolderId, selectedLibraryId);
            }
            await reloadResources();
            ops.setOperationTargetItems([]);
          } catch { /* ignore */ }
        },
      });
    }
    return emptyItems;
  }, [contextMenu, t, selectedLibraryId, navigate, resPath, handleTrash, ops, isPersonal, scopeId, selectedFolderId, loadFolders, loadChildFolders, reloadResources, canDo, fileInputRef, setSelectedResource, setSelectedFolder, setShowInfoPanel, addToast, setLoading, setResources, setCreatingFolder, versionInputRef]);
}
