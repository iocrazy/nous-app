/**
 * Phase 5b — SOP Stage Tool Catalog
 *
 * Maps tool slugs (stored in ``project_stages.tools_recommended``) to their
 * display metadata and the corresponding ProjectTab they navigate to.
 *
 * Slugs are hand-maintained here; they must stay in sync with the seeds in
 * migration 295_project_sop_stages.sql.  Unknown slugs are skipped silently
 * at render time (StageToolGrid).
 */

import {
  FolderOpen,
  FileText,
  Clapperboard,
  Download,
  KanbanSquare,
} from 'lucide-react';
import type { ComponentType } from 'react';
import type { ProjectTab } from '../../types';

export interface ToolDef {
  /** Unique identifier matching the JSONB slug array in project_stages. */
  slug: string;
  /** i18n key under ``projects.tools.*`` for the button label. */
  labelKey: string;
  /** The ProjectTab this tool navigates to when clicked. */
  tab: ProjectTab;
  /** Lucide icon component. */
  icon: ComponentType<{ className?: string }>;
}

/**
 * Canonical tool catalog for SOP stage tool grids.
 * Keyed by slug for O(1) look-up in StageToolGrid.
 */
export const TOOL_CATALOG: Record<string, ToolDef> = {
  files: {
    slug: 'files',
    labelKey: 'projects.tools.files',
    tab: 'files',
    icon: FolderOpen,
  },
  scripts: {
    slug: 'scripts',
    labelKey: 'projects.tools.scripts',
    tab: 'scripts',
    icon: FileText,
  },
  storyboard: {
    slug: 'storyboard',
    labelKey: 'projects.tools.storyboard',
    tab: 'storyboard',
    icon: Clapperboard,
  },
  output: {
    slug: 'output',
    labelKey: 'projects.tools.output',
    tab: 'output',
    icon: Download,
  },
  tasks: {
    slug: 'tasks',
    labelKey: 'projects.tools.tasks',
    tab: 'tasks',
    icon: KanbanSquare,
  },
} as const;
