/**
 * WorkspaceEntities — the project workspace's ASSETS group (Characters /
 * Locations / Props / Costumes), on the asset library (P3 Task 5).
 *
 * All this file does now is translate the SIDEBAR MODULE key (plural, the
 * workspace's vocabulary) into an ASSET TYPE (singular, the library's) and
 * hand the panel the project's asset scope. The mapping is explicit rather
 * than a de-pluralizing regex: the two vocabularies are allowed to diverge,
 * and a silent mismatch here would render one type's page under another's
 * label.
 *
 * What it no longer does: delegate to `CharacterLibrary` / `EntityLibrary`
 * over `project_characters` / `project_lib_entities`. Those components, their
 * services (`charactersService` / `libEntitiesService`) and the bible cards'
 * `EntityAssetStrip` were deleted in P3 Task 6 — this file has no legacy
 * branch left to fall back to. The backend's old extract endpoints still
 * exist; the rename PR owns them.
 */

import { useTeamContext } from '../../contexts/TeamContext';
import type { AssetType } from '../../services/assetsService';
import { ProjectAssetsPanel } from './ProjectAssetsPanel';

/** Sidebar module key → asset type. */
const TYPE_FOR_MODULE = {
  characters: 'character',
  locations: 'location',
  props: 'prop',
  costumes: 'costume',
} as const satisfies Record<string, AssetType>;

export type WorkspaceEntityKind = keyof typeof TYPE_FOR_MODULE;

interface WorkspaceEntitiesProps {
  kind: WorkspaceEntityKind;
  projectId: string;
  /** `projects.team_id` — null for a personal project, whose asset scope is
   *  its owner's personal team. */
  projectTeamId: string | null;
  /** The URL's team segment, for the cross-module link into the resources
   *  module's asset sheet. */
  teamId?: string;
}

export function WorkspaceEntities({
  kind,
  projectId,
  projectTeamId,
  teamId,
}: WorkspaceEntitiesProps) {
  const { personalTeamId } = useTeamContext();
  // See `ProjectAssetsPanel`'s scope note for why this resolution exists and
  // where it is knowingly approximate.
  const scopeId = projectTeamId ?? personalTeamId;

  return (
    <ProjectAssetsPanel
      assetType={TYPE_FOR_MODULE[kind]}
      projectId={projectId}
      scopeId={scopeId}
      teamId={teamId}
    />
  );
}
