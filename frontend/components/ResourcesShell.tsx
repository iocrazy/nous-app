import React, { useEffect } from 'react';
import { createPortal } from 'react-dom';
import { ResourcesSidebar, type ResourcesSidebarProps } from './ResourcesSidebar';
import { ResourcesInfoPanelWrapper } from './ResourcesInfoPanelWrapper';
import { islandUI } from '../utils/featureFlags';
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
 * Handles the overall flex layout:
 *   Desktop:  [Sidebar] [Content] [InfoPanel]
 *   Mobile:   [Content] only (sidebar hidden via CSS, info panel is overlay)
 *
 * All business logic (handlers, state, modals) stays in ResourcesView.
 * This component is purely structural.
 */
export const ResourcesShell: React.FC<ResourcesShellProps> = (props) => {
  // Island mode is a stable build-time constant for the session, so this early
  // return cannot violate the Rules of Hooks: the classic branch below calls no
  // hooks, and the island child encapsulates its own hooks.
  if (islandUI()) {
    return <ResourcesShellIsland {...props} />;
  }

  const { sidebarProps, infoPanelProps, children } = props;
  // Mobile uses min-h-screen so content can grow past the viewport (viewport
  // scrolls naturally + infinite scroll observer fires). Desktop keeps
  // sm:h-full to stay inside the sm:h-screen + sm:overflow-hidden frame set
  // up by AppLayout — internal scroll is handled by DownloadsView's
  // md:overflow-y-auto content area. The previous ``style={height:100vh}``
  // pinned mobile to the viewport and clipped everything below, which
  // silently broke infinite scroll on the Downloads grid / list.
  return (
    <div className="flex min-h-screen sm:h-full sm:min-h-0 sm:-m-8 sm:-mt-20 sm:-mb-8">
      {/* Left panel: Desktop sidebar navigation (hidden on mobile) */}
      <ResourcesSidebar {...sidebarProps} />

      {/* Center panel: Main content */}
      <div className="flex-1 min-w-0 flex flex-col sm:pt-14">
        {children}
      </div>

      {/* Right panel: Info panel (collapsible) */}
      <ResourcesInfoPanelWrapper {...infoPanelProps} />
    </div>
  );
};

/**
 * ResourcesShellIsland — island-shell variant of the resources layout.
 *
 * The rail + content render inside the shell's work island (no AppLayout
 * padding to escape, no global topbar offset). The info panel is portaled into
 * the shell's info island; the shell owns its width + reopen handle. The page's
 * ResourcesContext stays the source of truth for selection + panel visibility,
 * which we mirror into the shell's island-work context via effects.
 */
const ResourcesShellIsland: React.FC<ResourcesShellProps> = ({ sidebarProps, infoPanelProps, children }) => {
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
  // Guarded so it can't loop.
  useEffect(() => { if (!isDownloadsView && infoVisible && !showInfoPanel) setShowInfoPanel(true); }, [infoVisible, showInfoPanel, isDownloadsView, setShowInfoPanel]);

  return (
    <div className="flex h-full min-h-0">
      <ResourcesSidebar {...sidebarProps} island />
      <div className="flex-1 min-w-0 flex flex-col">{children}</div>
      {!isDownloadsView && infoIslandEl && createPortal(
        <ResourcesInfoPanelWrapper island {...infoPanelProps} />,
        infoIslandEl,
      )}
    </div>
  );
};
