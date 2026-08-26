// frontend/hooks/useContextMenuItems.tsx

/**
 * Returns the ContextMenuItem array for ResourcesViewInner based on
 * what was right-clicked (file, folder, smartFolder, or empty area).
 */

import React, { useEffect, useMemo, useState } from 'react';
import {
  FolderOpen, Upload, Trash2, Share2, Download,
  FolderPlus, ExternalLink, Pencil, Copy, Move, RefreshCw, Eye,
  Sparkles, Tag, Bookmark, Images, Bot,
  Lock,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import type { ContextMenuItem } from '../components/ContextMenu';
import type { Folder, SmartCollection, ResourceItem, Resource } from '../types';
import {
  getFolderContentCount,
  getResourceFileUrl,
  copyResourceItem,
  generateGenPrompt,
  classifyResource,
} from '../services/resourceService';
import { fetchResourceTags } from '../services/unifiedTagService';
import { hasToPublishTag, toggleToPublish } from '../services/toPublishService';
import { downloadWithAuth } from '../utils/download';
import { sendResourceToAgent } from '../utils/sendResourceToAgent';

// Publishing currently supports video only, mirroring the Distribution publish
// picker (uploads/generated videos; downloads aren't publishable). The
// "Mark to publish" menu item therefore shows for video resources only.
function isVideoResource(resource: Resource | undefined): boolean {
  if (!resource) return false;
  return resource.file_type === 'video'
    || Boolean(resource.mime_type && resource.mime_type.startsWith('video/'));
}

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
  /** Open the Upload Gallery dialog (empty-area menu). */
  onUploadGallery: () => void;
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
  onUploadGallery,
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

  // "Mark to publish" reflects whether the right-clicked video already carries
  // the well-known "To Publish" tag. Resolved synchronously from the row's
  // joined tags when present, else fetched when the menu opens. null =
  // unknown/loading → the item shows the default "Mark to publish" affordance.
  const [publishMark, setPublishMark] = useState<{
    resourceId: string | null;
    marked: boolean | null;
  }>({ resourceId: null, marked: null });

  useEffect(() => {
    if (!contextMenu || contextMenu.type !== 'file') {
      setPublishMark({ resourceId: null, marked: null });
      return undefined;
    }
    const resource = contextMenu.target?.resource as Resource | undefined;
    const rid = resource?.id ? String(resource.id) : null;
    if (!rid || !isVideoResource(resource)) {
      setPublishMark({ resourceId: rid, marked: null });
      return undefined;
    }
    // Prefer the joined tags array already present on the resource row.
    if (Array.isArray(resource?.tags)) {
      setPublishMark({ resourceId: rid, marked: hasToPublishTag(resource.tags) });
      return undefined;
    }
    // Otherwise resolve asynchronously; guard against a stale response landing
    // after the menu retargets to a different resource.
    let cancelled = false;
    setPublishMark({ resourceId: rid, marked: null });
    (async () => {
      try {
        const assoc = await fetchResourceTags(rid);
        if (cancelled) return;
        setPublishMark({ resourceId: rid, marked: hasToPublishTag(assoc.map((a) => a.tag)) });
      } catch (err) {
        console.error('resources: load to-publish mark failed', err);
        if (!cancelled) setPublishMark({ resourceId: rid, marked: false });
      }
    })();
    return () => { cancelled = true; };
  }, [contextMenu]);

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
      // Send to Agent: stage the resource as a chip in the floating chat.
      // No permission gate — this only puts the asset in front of an agent
      // the user already has. Before staging we top up whatever AI
      // processing it is missing, so the agent has something to read this
      // turn instead of answering "no transcript available" (spec F1/F3).
      items.push({
        label: t('resources.sendToAgent', 'Send to Agent'),
        icon: <Bot size={14} />,
        onClick: async () => {
          const resource = item.resource as Resource | undefined;
          if (!resourceId || !resource) return;
          await sendResourceToAgent(
            { ...resource, id: String(resourceId) },
            {
              scope: { type: isPersonal ? 'personal' : 'team', id: String(scopeId) },
              addToast,
              t,
            },
          );
        },
        disabled: !resourceId,
        divider: true,
      });
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
      // Mark to publish (video only): feeds the Distribution publish picker's
      // "To publish" filter via the shared "To Publish" tag.
      if (canDo('update') && isVideoResource(item.resource)) {
        const marked = publishMark.resourceId === String(resourceId) && publishMark.marked === true;
        items.push({
          label: marked
            ? t('resources.unmarkToPublish', 'Unmark to Publish')
            : t('resources.markToPublish', 'Mark to Publish'),
          icon: <Bookmark size={14} />,
          onClick: async () => {
            if (!resourceId) return;
            try {
              const nowMarked = await toggleToPublish(String(resourceId), marked);
              addToast(
                nowMarked
                  ? t('resources.markedToPublish', 'Marked to publish')
                  : t('resources.unmarkedToPublish', 'Removed from publish list'),
                'success',
              );
            } catch (err) {
              console.error('resources: toggle to-publish failed', err);
              addToast(t('resources.markToPublishFailed', 'Could not update publish mark'), 'error');
            }
          },
          disabled: !resourceId,
        });
      }
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
      // System folders (e.g. the cover-template library) keep their name and
      // place: the backend refuses rename / move / trash with a typed 409, so
      // offering those here would only produce a failure toast. Say why instead.
      if (folder.is_system) {
        items.push({ label: t('resources.systemFolderLocked', 'System folder — cannot be renamed, moved or trashed'), icon: <Lock size={14} />, onClick: () => {}, disabled: true, divider: true });
      }
      if (canDo('update') && !folder.is_system) items.push({ label: t('resources.rename'), icon: <Pencil size={14} />, onClick: () => { ops.setRenamingFolderId(folder.id); ops.setRenameFolderValue(folder.name); }, divider: true });
      if (canDo('copy')) items.push({ label: t('resources.copyTo'), icon: <Copy size={14} />, onClick: () => { ops.setOperationTargetItems([]); ops.setOperationTargetFolders([folder]); ops.setFolderPickerMode('copy'); } });
      if (canDo('move') && !folder.is_system) items.push({ label: t('resources.moveTo'), icon: <Move size={14} />, onClick: () => { ops.setOperationTargetItems([]); ops.setOperationTargetFolders([folder]); ops.setFolderPickerMode('move'); } });
      if (canDo('share')) items.push({ label: t('resources.share'), icon: <Share2 size={14} />, onClick: () => ops.setShareTarget({ folderId: String(folder.id) }) });
      if (canDo('delete') && !folder.is_system) {
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
        { label: t('resources.uploadGallery', 'Upload Gallery'), icon: <Images size={14} />, onClick: () => onUploadGallery() },
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
  }, [contextMenu, t, selectedLibraryId, navigate, resPath, handleTrash, ops, isPersonal, scopeId, selectedFolderId, loadFolders, loadChildFolders, reloadResources, canDo, fileInputRef, setSelectedResource, setSelectedFolder, setShowInfoPanel, addToast, setLoading, setResources, setCreatingFolder, onUploadGallery, versionInputRef, publishMark]);
}
