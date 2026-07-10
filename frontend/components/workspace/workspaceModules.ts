/**
 * Sidebar module registry for the PR-10b workspace shell (spec
 * `2026-07-10-projects-workspace-final.html`, decisions G1/G4/G6). Wave 1
 * gives every module except `overview` a shared `WorkspacePlaceholder` —
 * the real Episodes/Entities/Files panels land in Wave 2.
 */

import type { ComponentType } from 'react';
import { LayoutDashboard, Frame, ListVideo, Users, MapPin, FolderOpen, Trash2, Settings } from 'lucide-react';

export type WorkspaceModule =
  | 'overview'
  | 'canvas'
  | 'episodes'
  | 'script'
  | 'characters'
  | 'locations'
  | 'files'
  | 'trash'
  | 'settings';

export interface WorkspaceModuleDef {
  key: WorkspaceModule;
  labelKey: string;
  icon: ComponentType<{ className?: string; size?: number }>;
}

/** Overview / Canvas / Episodes — top-level items above the current-episode block. */
export const TOP_MODULES: WorkspaceModuleDef[] = [
  { key: 'overview', labelKey: 'projects.workspace.modules.overview', icon: LayoutDashboard },
  { key: 'canvas', labelKey: 'projects.workspace.modules.canvas', icon: Frame },
  { key: 'episodes', labelKey: 'projects.workspace.modules.episodes', icon: ListVideo },
];

/** ASSETS group — project-level entities (spec G13: main-library + appearances). */
export const ASSET_MODULES: WorkspaceModuleDef[] = [
  { key: 'characters', labelKey: 'projects.workspace.modules.characters', icon: Users },
  { key: 'locations', labelKey: 'projects.workspace.modules.locations', icon: MapPin },
  { key: 'files', labelKey: 'projects.workspace.modules.files', icon: FolderOpen },
];

/** MANAGE group — housekeeping, always last. */
export const MANAGE_MODULES: WorkspaceModuleDef[] = [
  { key: 'trash', labelKey: 'projects.workspace.modules.trash', icon: Trash2 },
  { key: 'settings', labelKey: 'projects.workspace.modules.settings', icon: Settings },
];

/** localStorage key for the last-selected episode, scoped per project. */
export function episodeStorageKey(projectId: string): string {
  return `mediahub.project.${projectId}.ep`;
}
