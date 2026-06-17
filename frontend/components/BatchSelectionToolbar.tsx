// frontend/components/BatchSelectionToolbar.tsx

/**
 * Fixed bottom toolbar shown when one or more items are selected.
 * Renders restore/delete actions for recycle view, and move/copy/trash for normal view.
 */

import React, { useState } from 'react';
import {
  Trash2, Move, Copy, RefreshCw, X, Sparkles, Tag, Loader2, Package,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import type { Folder, ResourceItem } from '../types';
import {
  trashResources,
  restoreFolder,
  restoreResource,
  batchAssetAi,
  exportTrainingSet,
} from '../services/resourceService';
import { useToast } from './Toast';

interface BatchSelectionToolbarProps {
  selectedIds: Set<string>;
  isRecycleView: boolean;
  isDownloadsView: boolean;
  currentItems: ResourceItem[];
  sortedItems: ResourceItem[];
  recycleSubFolders: Folder[];
  childFolders: Folder[];
  isPersonal: boolean;
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
  /** Re-fetch the current resource list honouring active filter params. */
  reloadResources: () => Promise<void>;
}

export const BatchSelectionToolbar: React.FC<BatchSelectionToolbarProps> = ({
  selectedIds,
  isRecycleView,
  isDownloadsView,
  currentItems,
  sortedItems,
  recycleSubFolders,
  childFolders,
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
  reloadResources,
}) => {
  const { t, i18n } = useTranslation();
  const { addToast } = useToast();
  const [aiBusy, setAiBusy] = useState<'caption' | 'classify' | 'export' | null>(null);

  if (selectedIds.size === 0 || isDownloadsView) return null;

  // Image resources in the current selection — batch AI only applies to them.
  const selectedImageIds = sortedItems
    .filter(
      (i) =>
        selectedIds.has(`item:${i.id}`) &&
        i.resource?.id &&
        i.resource?.file_type === 'image',
    )
    .map((i) => String(i.resource!.id));

  const runBatchAi = async (operation: 'caption' | 'classify') => {
    if (selectedImageIds.length === 0 || aiBusy) return;
    setAiBusy(operation);
    try {
      const { dispatched, skipped } = await batchAssetAi(
        selectedImageIds.slice(0, 50),
        operation,
      );
      if (dispatched.length > 0) {
        addToast(
          t('resources.batchAiDispatched', '{{count}} tasks started — see Task Center', {
            count: dispatched.length,
          }),
          'success',
        );
        setSelectedIds(new Set());
      }
      if (skipped.length > 0) {
        addToast(
          t('resources.batchAiSkipped', '{{count}} items skipped', {
            count: skipped.length,
          }),
          'info',
        );
      }
    } catch (err) {
      console.error(`Batch ${operation} failed:`, err);
      addToast(
        err instanceof Error && err.message
          ? err.message
          : t('resources.batchAiFailed', 'Batch AI dispatch failed'),
        'error',
      );
    } finally {
      setAiBusy(null);
    }
  };

  return (
    <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-50 flex flex-nowrap items-center gap-3 bg-ink-900 border border-ink-700 rounded-xl px-5 py-3 shadow-2xl max-w-[calc(100vw-1.5rem)] overflow-x-auto">
      <span className="shrink-0 whitespace-nowrap text-sm text-ink-300 font-medium">
        {t('resources.selected', { count: selectedIds.size })}
      </span>
      <div className="shrink-0 w-px h-5 bg-ink-700" />
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
            title={t('resources.batchRestore')}
            className="shrink-0 whitespace-nowrap flex items-center gap-1.5 px-3 py-1.5 text-sm text-emerald-400 hover:text-emerald-300 hover:bg-emerald-900/30 rounded-lg transition-colors"
          >
            <RefreshCw size={14} />
            <span className="hidden xl:inline">{t('resources.batchRestore')}</span>
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
            title={t('resources.batchPermanentDelete')}
            className="shrink-0 whitespace-nowrap flex items-center gap-1.5 px-3 py-1.5 text-sm text-red-400 hover:text-red-300 hover:bg-red-900/30 rounded-lg transition-colors"
          >
            <Trash2 size={14} />
            <span className="hidden xl:inline">{t('resources.batchPermanentDelete')}</span>
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
            title={t('resources.batchMove')}
            className="shrink-0 whitespace-nowrap flex items-center gap-1.5 px-3 py-1.5 text-sm text-ink-300 hover:text-ink-50 hover:bg-ink-800 rounded-lg transition-colors"
          >
            <Move size={14} />
            <span className="hidden xl:inline">{t('resources.batchMove')}</span>
          </button>
          <button
            onClick={() => {
              const items = sortedItems.filter((i) => selectedIds.has(`item:${i.id}`));
              setOperationTargetItems(items);
              setOperationTargetFolders([]);
              setFolderPickerMode('copy');
            }}
            title={t('resources.batchCopy')}
            className="shrink-0 whitespace-nowrap flex items-center gap-1.5 px-3 py-1.5 text-sm text-ink-300 hover:text-ink-50 hover:bg-ink-800 rounded-lg transition-colors"
          >
            <Copy size={14} />
            <span className="hidden xl:inline">{t('resources.batchCopy')}</span>
          </button>
          {selectedImageIds.length > 0 && (
            <>
              <button
                onClick={() => runBatchAi('caption')}
                disabled={aiBusy !== null}
                title={t('resources.batchGeneratePromptHint', 'Reverse-engineer prompts for the selected images')}
                className="shrink-0 whitespace-nowrap flex items-center gap-1.5 px-3 py-1.5 text-sm text-ink-300 hover:text-ink-50 hover:bg-ink-800 rounded-lg transition-colors disabled:opacity-50"
              >
                {aiBusy === 'caption' ? <Loader2 size={14} className="animate-spin" /> : <Sparkles size={14} />}
                <span className="hidden xl:inline">{t('resources.batchGeneratePrompt', 'Prompts')}</span>
              </button>
              <button
                onClick={() => runBatchAi('classify')}
                disabled={aiBusy !== null}
                title={t('resources.batchAutoTagHint', 'Auto-tag the selected images across 12 dimensions')}
                className="shrink-0 whitespace-nowrap flex items-center gap-1.5 px-3 py-1.5 text-sm text-ink-300 hover:text-ink-50 hover:bg-ink-800 rounded-lg transition-colors disabled:opacity-50"
              >
                {aiBusy === 'classify' ? <Loader2 size={14} className="animate-spin" /> : <Tag size={14} />}
                <span className="hidden xl:inline">{t('resources.batchAutoTag', 'Auto Tag')}</span>
              </button>
              <button
                onClick={async () => {
                  if (aiBusy) return;
                  setAiBusy('export');
                  try {
                    await exportTrainingSet(
                      selectedImageIds.slice(0, 100),
                      (i18n.language || '').startsWith('zh') ? 'zh' : 'en',
                    );
                    addToast(t('resources.trainingSetExported', 'Training set downloaded'), 'success');
                  } catch (err) {
                    console.error('Training-set export failed:', err);
                    addToast(
                      err instanceof Error && err.message
                        ? err.message
                        : t('resources.trainingSetExportFailed', 'Export failed'),
                      'error',
                    );
                  } finally {
                    setAiBusy(null);
                  }
                }}
                disabled={aiBusy !== null}
                title={t('resources.trainingSetHint', 'Download images + .txt prompt captions (LoRA training format)')}
                className="shrink-0 whitespace-nowrap flex items-center gap-1.5 px-3 py-1.5 text-sm text-ink-300 hover:text-ink-50 hover:bg-ink-800 rounded-lg transition-colors disabled:opacity-50"
              >
                {aiBusy === 'export' ? <Loader2 size={14} className="animate-spin" /> : <Package size={14} />}
                <span className="hidden xl:inline">{t('resources.trainingSet', 'Training Set')}</span>
              </button>
            </>
          )}
          <button
            onClick={async () => {
              try {
                const resourceIds = sortedItems
                  .filter((i) => selectedIds.has(`item:${i.id}`) && i.resource?.id)
                  .map((i) => String(i.resource!.id));
                if (resourceIds.length > 0) {
                  await trashResources(resourceIds, scopeId, selectedFolderId);
                }
                const folderIds = childFolders
                  .filter((f) => selectedIds.has(`folder:${f.id}`))
                  .map((f) => String(f.id));
                for (const fid of folderIds) {
                  const { trashFolder } = await import('../services/resourceService');
                  await trashFolder(fid);
                }
                if (resourceIds.length > 0 || folderIds.length > 0) {
                  await reloadResources();
                  await Promise.all([loadFolders(), loadChildFolders()]);
                  setSelectedIds(new Set());
                }
              } catch (err) {
                console.error('Batch delete failed:', err);
              }
            }}
            title={t('resources.batchDelete')}
            className="shrink-0 whitespace-nowrap flex items-center gap-1.5 px-3 py-1.5 text-sm text-red-400 hover:text-red-300 hover:bg-red-900/30 rounded-lg transition-colors"
          >
            <Trash2 size={14} />
            <span className="hidden xl:inline">{t('resources.batchDelete')}</span>
          </button>
        </>
      )}
      <div className="shrink-0 w-px h-5 bg-ink-700" />
      <button
        onClick={() => setSelectedIds(new Set())}
        className="shrink-0 p-1.5 text-ink-400 hover:text-ink-50 hover:bg-ink-800 rounded-lg transition-colors"
      >
        <X size={14} />
      </button>
    </div>
  );
};
