import React, { useRef, useCallback } from 'react';
import { ChevronLeft, ChevronRight } from 'lucide-react';
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
  /** Island mode — render bare panel for the shell's info island (no fixed overlay). */
  island?: boolean;
}

// ─── Component ───────────────────────────────────────────

export const ResourcesInfoPanelWrapper: React.FC<ResourcesInfoPanelWrapperProps> = ({
  trashedFolderPreviews,
  onRenameFolder,
  island = false,
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
    showInfoPanel,
    setShowInfoPanel,
    infoPanelWidth,
    setInfoPanelWidth,
    handleAddTag,
    handleRemoveTag,
    handleCreateTag,
    handleResourceUpdate,
  } = useResourcesContext();

  // ─── Resize handlers ─────────────────────────────────
  const isResizingPanelRef = useRef(false);
  const resizeStartRef = useRef({ x: 0, width: 0 });

  const handlePanelResizeStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    isResizingPanelRef.current = true;
    resizeStartRef.current = { x: e.clientX, width: infoPanelWidth };

    const onMouseMove = (ev: MouseEvent) => {
      if (!isResizingPanelRef.current) return;
      const delta = resizeStartRef.current.x - ev.clientX;
      const newWidth = Math.max(240, Math.min(600, resizeStartRef.current.width + delta));
      setInfoPanelWidth(newWidth);
    };

    const onMouseUp = () => {
      isResizingPanelRef.current = false;
      document.removeEventListener('mousemove', onMouseMove);
      document.removeEventListener('mouseup', onMouseUp);
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    };

    document.addEventListener('mousemove', onMouseMove);
    document.addEventListener('mouseup', onMouseUp);
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
  }, [infoPanelWidth, setInfoPanelWidth]);

  // Nothing to render when downloads view or no selection
  if (isDownloadsView || (!selectedResource?.resource && !selectedFolder)) {
    return null;
  }

  // ── Island mode: bare panel for the shell's info island. The shell owns the
  // width + reopen handle, so this renders only a collapse control + the inner
  // panel content (no fixed overlay, no own resize handle, no expand tab).
  if (island) {
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
              island
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
              island
              allTags={allTags}
              assignedTags={selectedResourceTags.map(item => item.tag).filter((t): t is Tag => !!t)}
              folderName={selectedResource.folder_id ? folders.find(f => f.id === selectedResource.folder_id)?.name : null}
              readOnly={isRecycleView}
              onClose={() => setSelectedResource(null)}
              onAddTag={handleAddTag}
              onRemoveTag={handleRemoveTag}
              onCreate={handleCreateTag}
              onUpdate={handleResourceUpdate}
            />
          ) : null}
        </div>
      </div>
    );
  }

  return (
    <>
      {/* ── Right panel: Fixed overlay, from TopBar bottom to viewport bottom ── */}
      <div
        className={`fixed top-14 bottom-0 right-0 z-40 flex bg-ink-900 border-l border-ink-800 transition-transform duration-300 ease-in-out shadow-2xl ${
          showInfoPanel ? 'translate-x-0' : 'translate-x-full'
        }`}
        style={{ width: `min(100vw, ${infoPanelWidth}px)` }}
      >
        {/* Collapse tab — attached to left edge of panel */}
        <button
          onClick={() => setShowInfoPanel(false)}
          className="absolute -left-10 bottom-8 w-10 h-12 bg-ink-900 border-l border-y border-ink-800 rounded-l-xl flex items-center justify-center text-ink-400 hover:text-ink-50 cursor-pointer hover:bg-ink-800 transition-colors z-10"
          title={t('resources.toggleInfoPanel')}
        >
          <ChevronRight size={20} />
        </button>

        {/* Resize handle — left edge blue line on hover */}
        <div
          onMouseDown={handlePanelResizeStart}
          className="w-1 h-full cursor-col-resize shrink-0 hover:bg-blue-500 active:bg-blue-500 transition-colors"
        />

        {/* Panel content */}
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
          />
        ) : null}
      </div>

      {/* Expand tab — fixed to viewport right edge, visible when panel is closed */}
      {!showInfoPanel && (
        <button
          onClick={() => setShowInfoPanel(true)}
          className="fixed bottom-8 right-0 w-10 h-12 bg-ink-900 border-l border-y border-ink-800 rounded-l-xl flex items-center justify-center text-ink-400 hover:text-ink-50 cursor-pointer hover:bg-ink-800 transition-all z-50"
          title={t('resources.toggleInfoPanel')}
        >
          <ChevronLeft size={20} />
        </button>
      )}
    </>
  );
};
