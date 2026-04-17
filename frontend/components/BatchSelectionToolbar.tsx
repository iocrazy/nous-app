// frontend/components/BatchSelectionToolbar.tsx

/**
 * Fixed bottom toolbar shown when one or more items are selected.
 * Renders restore/delete actions for recycle view, and move/copy/trash for normal view.
 */

import React from 'react';
import { Trash2, Move, Copy, RefreshCw, X } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import type { Folder, ResourceItem } from '../types';
import {
  fetchResources,
  trashResources,
  restoreFolder,
  restoreResource,
} from '../services/resourceService';

interface BatchSelectionToolbarProps {
  selectedIds: Set<string>;
  isRecycleView: boolean;
  isDownloadsView: boolean;
  currentItems: ResourceItem[];
  sortedItems: ResourceItem[];
  recycleSubFolders: Folder[];
  childFolders: Folder[];
  scopeType: 'personal' | 'team';
  scopeId: string;
  selectedFolderId: string | null | undefined;
  selectedLibraryId: string | null | undefined;
  setResources: React.Dispatch<React.SetStateAction<ResourceItem[]>>;
  setSelectedIds: React.Dispatch<React.SetStateAction<Set<string>>>;
  setOperationTargetItems: React.Dispatch<React.SetStateAction<ResourceItem[]>>;
  setOperationTargetFolders: React.Dispatch<React.SetStateAction<Folder[]>>;
  setFolderPickerMode: React.Dispatch<React.SetStateAction<'copy' | 'move' | null>>;
  setPendingBatchPermanentDelete: React.Dispatch<React.SetStateAction<string[] | null>>;
  setPendingBatchPermanentDeleteFolders: React.Dispatch<React.SetStateAction<string[] | null>>;
  loadFolders: () => Promise<void>;
  loadChildFolders: () => Promise<void>;
  loadTrashedResources: () => Promise<void>;
}

export const BatchSelectionToolbar: React.FC<BatchSelectionToolbarProps> = ({
  selectedIds,
  isRecycleView,
  isDownloadsView,
  currentItems,
  sortedItems,
  recycleSubFolders,
  childFolders,
  scopeType,
  scopeId,
  selectedFolderId,
  selectedLibraryId,
  setResources,
  setSelectedIds,
  setOperationTargetItems,
  setOperationTargetFolders,
  setFolderPickerMode,
  setPendingBatchPermanentDelete,
  setPendingBatchPermanentDeleteFolders,
  loadFolders,
  loadChildFolders,
  loadTrashedResources,
}) => {
  const { t } = useTranslation();

  if (selectedIds.size === 0 || isDownloadsView) return null;

  return (
    <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-50 flex items-center gap-3 bg-zinc-900 border border-zinc-700 rounded-xl px-5 py-3 shadow-2xl">
      <span className="text-sm text-zinc-300 font-medium">
        {t('resources.selected', { count: selectedIds.size })}
      </span>
      <div className="w-px h-5 bg-zinc-700" />
      {isRecycleView ? (
        <>
          <button
            onClick={async () => {
              const resourceIds = currentItems
                .filter((i) => selectedIds.has(`item:${i.id}`) && i.resource?.id)
                .map((i) => String(i.resource!.id));
              for (const id of resourceIds) await restoreResource(id);
              const folderIds = recycleSubFolders
                .filter((f) => selectedIds.has(`folder:${f.id}`))
                .map((f) => String(f.id));
              for (const fid of folderIds) await restoreFolder(fid);
              if (resourceIds.length > 0 || folderIds.length > 0) await loadTrashedResources();
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
                const resourceIds = sortedItems
                  .filter((i) => selectedIds.has(`item:${i.id}`) && i.resource?.id)
                  .map((i) => String(i.resource!.id));
                if (resourceIds.length > 0) {
                  await trashResources(resourceIds, scopeType, scopeId, selectedFolderId);
                }
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
  );
};
