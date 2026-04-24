import React from 'react';
import { ResourcesSidebar, type ResourcesSidebarProps } from './ResourcesSidebar';
import { ResourcesInfoPanelWrapper } from './ResourcesInfoPanelWrapper';

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
export const ResourcesShell: React.FC<ResourcesShellProps> = ({
  sidebarProps,
  infoPanelProps,
  children,
}) => {
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
