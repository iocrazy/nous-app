// frontend/components/ResourcesModals.tsx

/**
 * Portal-style overlay components for ResourcesViewInner:
 * Smart Folder editors, Share modal, Folder Picker, Duplicate Alert,
 * Permanent Delete confirmation, and Touch drag preview.
 */

import React from 'react';
import { createPortal } from 'react-dom';
import { AlertTriangle, Move } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { SmartFolderEditor } from './SmartFolderEditor';
import { ShareModal } from './ShareModal';
import { FolderPickerModal } from './FolderPickerModal';
import { DuplicateFileAlert } from './DuplicateFileAlert';
import type { SmartCollection, ResourceItem, Folder } from '../types';
import type { DuplicateAlertState } from '../hooks/useResourceUpload';

interface ResourcesModalsProps {
  // Smart folder
  showSmartFolderEditor: boolean;
  editingSmartFolder: SmartCollection | null;
  onCreateSmartFolder: (name: string, rules: any[]) => Promise<void>;
  onEditSmartFolder: (name: string, rules: any[]) => Promise<void>;
  onCloseSmartFolderEditor: () => void;
  onCloseEditingSmartFolder: () => void;
  // Share
  shareTarget: { resourceId?: string; folderId?: string } | null;
  onCloseShareModal: () => void;
  // Folder picker
  folderPickerMode: 'copy' | 'move' | null;
  operationTargetItems: ResourceItem[];
  operationTargetFolders: Folder[];
  scopeType: 'personal' | 'team';
  scopeId: string;
  selectedLibraryId: string | null | undefined;
  onCloseFolderPicker: () => void;
  onConfirmFolderPicker: (folderId: string | null, libraryId?: string | null) => Promise<void>;
  // Duplicate alert
  duplicateAlert: DuplicateAlertState | null;
  // Permanent delete
  pendingPermanentDelete: string | null;
  pendingBatchPermanentDelete: string[] | null;
  pendingBatchPermanentDeleteFolders: string[] | null;
  onCancelPermanentDelete: () => void;
  onConfirmPermanentDelete: () => void;
  // Touch drag
  touchDragState: { isDragging: boolean; dragPosition: { x: number; y: number } | null; dragIds: string[] };
}

export const ResourcesModals: React.FC<ResourcesModalsProps> = ({
  showSmartFolderEditor,
  editingSmartFolder,
  onCreateSmartFolder,
  onEditSmartFolder,
  onCloseSmartFolderEditor,
  onCloseEditingSmartFolder,
  shareTarget,
  onCloseShareModal,
  folderPickerMode,
  operationTargetItems,
  operationTargetFolders,
  scopeType,
  scopeId,
  selectedLibraryId,
  onCloseFolderPicker,
  onConfirmFolderPicker,
  duplicateAlert,
  pendingPermanentDelete,
  pendingBatchPermanentDelete,
  pendingBatchPermanentDeleteFolders,
  onCancelPermanentDelete,
  onConfirmPermanentDelete,
  touchDragState,
}) => {
  const { t } = useTranslation();

  const showDeleteConfirm = !!(pendingPermanentDelete || pendingBatchPermanentDelete || pendingBatchPermanentDeleteFolders);

  return (
    <>
      {/* Smart Folder Editor (Create) */}
      {showSmartFolderEditor && (
        <SmartFolderEditor onSave={onCreateSmartFolder} onClose={onCloseSmartFolderEditor} />
      )}

      {/* Smart Folder Editor (Edit) */}
      {editingSmartFolder && (
        <SmartFolderEditor
          initialName={editingSmartFolder.name}
          initialRules={editingSmartFolder.smart_rules as any}
          onSave={onEditSmartFolder}
          onClose={onCloseEditingSmartFolder}
        />
      )}

      {/* Share Modal */}
      {shareTarget && (
        <ShareModal
          isOpen={true}
          onClose={onCloseShareModal}
          resourceId={shareTarget.resourceId}
          folderId={shareTarget.folderId}
        />
      )}

      {/* Folder Picker Modal */}
      {folderPickerMode && (
        <FolderPickerModal
          isOpen={true}
          onClose={onCloseFolderPicker}
          onConfirm={onConfirmFolderPicker}
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
            ...operationTargetFolders.map((f) => ({ name: f.name, mediaType: 'folder' })),
          ]}
        />
      )}

      {/* Duplicate File Alert */}
      {duplicateAlert && (
        <DuplicateFileAlert
          file={duplicateAlert.file}
          existing={duplicateAlert.existing}
          remainingDuplicates={duplicateAlert.remainingDuplicates}
          onUseExisting={(applyToAll) => duplicateAlert.resolve({ action: 'use-existing', applyToAll })}
          onKeepBoth={(applyToAll) => duplicateAlert.resolve({ action: 'keep-both', applyToAll })}
          onCancel={() => duplicateAlert.resolve({ action: 'cancel', applyToAll: false })}
        />
      )}

      {/* Permanent Delete Confirmation */}
      {showDeleteConfirm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
          <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={onCancelPermanentDelete} />
          <div className="relative bg-zinc-900 border border-zinc-800 rounded-2xl shadow-2xl w-full max-w-sm mx-4">
            <div className="p-6 text-center">
              <div className="w-12 h-12 rounded-full bg-red-500/10 flex items-center justify-center mx-auto mb-4">
                <AlertTriangle className="text-red-500" size={24} />
              </div>
              <h3 className="text-lg font-semibold text-white mb-2">{t('resources.confirmPermanentDelete')}</h3>
              <p className="text-sm text-zinc-400">{t('resources.permanentDeleteWarning')}</p>
            </div>
            <div className="flex gap-3 p-4 border-t border-zinc-800">
              <button onClick={onCancelPermanentDelete} className="flex-1 px-4 py-2 text-sm font-medium text-zinc-300 bg-zinc-800 hover:bg-zinc-700 rounded-lg transition-colors">
                {t('common.cancel')}
              </button>
              <button onClick={onConfirmPermanentDelete} className="flex-1 px-4 py-2 text-sm font-medium text-white bg-red-600 hover:bg-red-500 rounded-lg transition-colors">
                {t('resources.deleteForever')}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Touch drag preview */}
      {touchDragState.isDragging && touchDragState.dragPosition && createPortal(
        <div
          className="fixed z-[100] pointer-events-none flex items-center gap-2 bg-zinc-800/90 backdrop-blur-sm border border-zinc-600 rounded-lg px-3 py-2 shadow-2xl"
          style={{ left: touchDragState.dragPosition.x - 40, top: touchDragState.dragPosition.y - 20 }}
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
