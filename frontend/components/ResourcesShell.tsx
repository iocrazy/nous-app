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
  return (
    <div className="flex h-full animate-in fade-in duration-300">
      {/* Left panel: Desktop sidebar navigation (hidden on mobile) */}
      <ResourcesSidebar {...sidebarProps} />

      {/* Center panel: Main content */}
      <div className="flex-1 min-w-0 flex flex-col">
        {children}
      </div>

      {/* Right panel: Info panel (collapsible) */}
      <ResourcesInfoPanelWrapper {...infoPanelProps} />
    </div>
  );
};
