/**
 * Sidebar module registry for the PR-10b workspace shell (spec
 * `2026-07-10-projects-workspace-final.html`, decisions G1/G4/G6). Wave 1
 * routes every module to real content (Canvas was the last placeholder) —
 * the real Episodes/Entities/Files panels land in Wave 2.
 */

import type { ComponentType } from 'react';
import { LayoutDashboard, Frame, ListVideo, ListTodo, Users, MapPin,
  Package, Shirt, FolderOpen, Trash2, Settings } from 'lucide-react';

export type WorkspaceModule =
  | 'overview'
  | 'canvas'
  | 'episodes'
  | 'tasks'
  | 'script'
  | 'characters'
  | 'locations'
  | 'props'
  // Costumes has no `project_*` table behind it and never had a page here —
  // it arrives with the asset library (P3), which made all four of these one
  // view over `assets` filtered by type.
  | 'costumes'
  | 'files'
  | 'trash'
  | 'settings'
  // Stage Board (M2 PR-F F2) — NOT in TOP_MODULES/ASSET_MODULES/MANAGE_MODULES:
  // it never appears in the fixed sidebar menu, only reachable from clicking a
  // node in the dynamic Stages block (WorkspaceSidebar ~:332).
  | 'stage'
  // Storyboard's standalone page (IA redesign Task 2) — also not in the
  // fixed menu group arrays: reached from the 剧集 tree's 分镜 row
  // (WorkspaceSidebar's onOpenWorkView) or a storyboard-surface
  // workflow-strip node (handleSelectNode), same pattern as 'stage' above.
  | 'storyboard';

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
  { key: 'tasks', labelKey: 'projects.workspace.modules.tasks', icon: ListTodo },
];

/** ASSETS group — the project's view over the asset library (P3), one entry
 *  per asset type that a project references, plus Files. */
export const ASSET_MODULES: WorkspaceModuleDef[] = [
  { key: 'characters', labelKey: 'projects.workspace.modules.characters', icon: Users },
  { key: 'locations', labelKey: 'projects.workspace.modules.locations', icon: MapPin },
  { key: 'props', labelKey: 'projects.workspace.modules.props', icon: Package },
  { key: 'costumes', labelKey: 'projects.workspace.modules.costumes', icon: Shirt },
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
