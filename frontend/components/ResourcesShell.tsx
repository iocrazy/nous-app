import React, { useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';
import { ResourcesSidebar, type ResourcesSidebarProps } from './ResourcesSidebar';
import { ResourcesInfoPanelWrapper } from './ResourcesInfoPanelWrapper';
import { useIslandWork } from '../contexts/IslandWorkContext';
import { useResourcesContext } from '../contexts/ResourcesContext';

// ─── Types ───────────────────────────────────────────────

interface FolderPreviewItem {
  resource_id?: string | null;
  thumbnail_path?: string | null;
  cover_image_path?: string | null;
  mime_type?: string | null;
}

export interface ResourcesShellProps {
  /** Props forwarded to ResourcesSidebar */
  sidebarProps: ResourcesSidebarProps;
  /** Props for ResourcesInfoPanelWrapper */
  infoPanelProps: {
    trashedFolderPreviews: Record<string, FolderPreviewItem[]>;
    onRenameFolder: (folderId: string | number, name: string) => Promise<void>;
  };
  /** The main content area (ResourceGrid, DownloadsView, etc.) */
  children: React.ReactNode;
}

// ─── Component ───────────────────────────────────────────

/**
 * ResourcesShell — layout adapter for the resources page.
 *
 * All business logic (handlers, state, modals) stays in ResourcesView.
 * This component is purely structural.
 *
 * The rail + content render inside the shell's work island (no AppLayout
 * padding to escape, no global topbar offset). The info panel is portaled into
 * the shell's info island; the shell owns its width + reopen handle. The page's
 * ResourcesContext stays the source of truth for selection + panel visibility,
 * which we mirror into the shell's island-work context via effects.
 */
export const ResourcesShell: React.FC<ResourcesShellProps> = ({ sidebarProps, infoPanelProps, children }) => {
  const { showInfoPanel, setShowInfoPanel, selectedResource, selectedFolder, isDownloadsView } = useResourcesContext();
  const { infoIslandEl, infoVisible, setInfoVisible, setInfoAvailable } = useIslandWork();

  const hasSelection = !isDownloadsView && (!!selectedResource?.resource || !!selectedFolder);

  // On the Downloads view, DownloadsView owns the info island (it portals its own
  // DownloadInfoPanel + drives infoAvailable/infoVisible). The shell must NOT touch
  // those here, or the two would fight over the same island-work state.
  // Page tells the shell whether there's an info panel to (re)open.
  useEffect(() => { if (!isDownloadsView) setInfoAvailable(hasSelection); }, [hasSelection, isDownloadsView, setInfoAvailable]);
  // Island visibility = user intent (showInfoPanel) AND a selection exists.
  useEffect(() => { if (!isDownloadsView) setInfoVisible(showInfoPanel && hasSelection); }, [showInfoPanel, hasSelection, isDownloadsView, setInfoVisible]);
  // Only the reopen direction is mirrored back here: the shell flips infoVisible→true via its
  // reopen handle, so we reflect that into showInfoPanel (ResourcesContext stays the source of
  // truth). The collapse/hide direction flows the OTHER way (showInfoPanel→false drives effect 2),
  // never via setInfoVisible(false) from the shell — that asymmetry is what keeps this loop-free.
  // Guarded on the RISING EDGE of infoVisible (false → true), not its level: in the
  // commit where showInfoPanel flips to false, effect 2 has only *queued*
  // infoVisible=false — this effect still reads the stale true, and a level check
  // would flip showInfoPanel straight back, so the collapse button looked dead.
  const prevInfoVisible = useRef(infoVisible);
  useEffect(() => {
    const rose = infoVisible && !prevInfoVisible.current;
    prevInfoVisible.current = infoVisible;
    if (!isDownloadsView && rose && !showInfoPanel) setShowInfoPanel(true);
  }, [infoVisible, showInfoPanel, isDownloadsView, setShowInfoPanel]);

  return (
    <div className="flex h-full min-h-0">
      <ResourcesSidebar {...sidebarProps} />
      <div className="flex-1 min-w-0 flex flex-col">{children}</div>
      {!isDownloadsView && infoIslandEl && createPortal(
        <ResourcesInfoPanelWrapper {...infoPanelProps} />,
        infoIslandEl,
      )}
    </div>
  );
};
