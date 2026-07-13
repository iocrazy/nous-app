/**
 * WorkspaceEntities — the Characters/Locations ASSETS "main library" module
 * (spec frame: "Characters / Locations 主库(ASSETS)", decision G13). One
 * component serves both `characters` and `locations` (prop-driven) since
 * they share the same main-library + episode-badge layout, just a
 * different count label and data source field.
 *
 * Entities are derived server-side from script cues/scene headers — this
 * view is read-only (no create/edit; the note line makes that explicit).
 */

import { CharacterLibrary } from './CharacterLibrary';
import { EntityLibrary } from './EntityLibrary';

interface WorkspaceEntitiesProps {
  kind: 'characters' | 'locations' | 'props';
  projectId: string;
}

export function WorkspaceEntities({ kind, projectId }: WorkspaceEntitiesProps) {
  // All three ASSETS libraries are authored bible-card walls now (CC4 + SP3):
  // characters keep their dedicated component; locations/props share the
  // generalized EntityLibrary. The old derived read-only list is retired —
  // Extract materializes the derivation into rows instead.
  if (kind === 'characters') return <CharacterLibrary projectId={projectId} />;
  return (
    <EntityLibrary
      entityType={kind === 'locations' ? 'location' : 'prop'}
      projectId={projectId}
    />
  );
}
