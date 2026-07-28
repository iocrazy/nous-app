import React from 'react';
import { ChevronRight } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useResourcesContext } from '../contexts/ResourcesContext';
import { ResourceInfoPanel } from './ResourceInfoPanel';
import { FolderInfoPanel } from './FolderInfoPanel';
import { Tag } from '../types';

// ─── Types ───────────────────────────────────────────────

interface FolderPreviewItem {
  resource_id?: string | null;
  thumbnail_path?: string | null;
  cover_image_path?: string | null;
  mime_type?: string | null;
}

interface ResourcesInfoPanelWrapperProps {
  /** Preview map for trashed folders (keyed by folder id as string) */
  trashedFolderPreviews: Record<string, FolderPreviewItem[]>;
  /** Callback fired when user renames a folder from within the panel */
  onRenameFolder: (folderId: string | number, name: string) => Promise<void>;
}

// ─── Component ───────────────────────────────────────────

export const ResourcesInfoPanelWrapper: React.FC<ResourcesInfoPanelWrapperProps> = ({
  trashedFolderPreviews,
  onRenameFolder,
}) => {
  const { t } = useTranslation();
  const {
    isDownloadsView,
    isRecycleView,
    selectedResource,
    setSelectedResource,
    selectedFolder,
    setSelectedFolder,
    selectedResourceTags,
    allTags,
    folders,
    folderPreviews,
    setShowInfoPanel,
    handleAddTag,
    handleRemoveTag,
    handleCreateTag,
    handleResourceUpdate,
    refetchSelectedResourceTags,
  } = useResourcesContext();

  // Nothing to render when downloads view or no selection
  if (isDownloadsView || (!selectedResource?.resource && !selectedFolder)) {
    return null;
  }

  // Bare panel for the shell's info island. The shell owns the width + reopen
  // handle, so this renders only a collapse control + the inner panel content
  // (no fixed overlay, no own resize handle, no expand tab).
  return (
      <div className="h-full flex flex-col">
        <div className="flex justify-end px-2 py-1.5 shrink-0">
          <button
            onClick={() => setShowInfoPanel(false)}
            title={t('resources.toggleInfoPanel')}
            className="w-8 h-8 grid place-items-center rounded-lg text-content-2 hover:text-content hover:bg-island-2 transition-colors"
          >
            <ChevronRight size={18} />
          </button>
        </div>
        <div className="flex-1 min-h-0 overflow-auto">
          {selectedFolder ? (
            <FolderInfoPanel
              folder={selectedFolder}
              previewItems={
                isRecycleView
                  ? trashedFolderPreviews[String(selectedFolder.id)]
                  : folderPreviews[selectedFolder.id]
              }
              readOnly={isRecycleView}
              onClose={() => setSelectedFolder(null)}
              onRename={async (name) => {
                await onRenameFolder(selectedFolder.id, name);
              }}
            />
          ) : selectedResource?.resource ? (
            <ResourceInfoPanel
              resource={selectedResource.resource}
              allTags={allTags}
              assignedTags={selectedResourceTags.map(item => item.tag).filter((t): t is Tag => !!t)}
              folderName={selectedResource.folder_id ? folders.find(f => f.id === selectedResource.folder_id)?.name : null}
              readOnly={isRecycleView}
              onClose={() => setSelectedResource(null)}
              onAddTag={handleAddTag}
              onRemoveTag={handleRemoveTag}
              onCreate={handleCreateTag}
              onUpdate={handleResourceUpdate}
              onTagsChanged={refetchSelectedResourceTags}
            />
          ) : null}
        </div>
      </div>
  );
};
