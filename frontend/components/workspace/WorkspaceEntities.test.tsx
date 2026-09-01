/**
 * WorkspaceEntities — the module-key → asset-type mapping, and nothing else.
 *
 * It matters because the two vocabularies are different by design: the
 * sidebar's keys are plural workspace modules, the library's are singular
 * asset types. A wrong entry here renders one type's assets under another
 * type's label, which nothing downstream would notice — the panel would ask
 * for a valid type and get a valid answer.
 *
 * It also pins WHICH component every module renders. `ProjectAssetsPanel` is
 * mocked, so a module routed anywhere else renders no `panel` testid and the
 * case below fails — that is the guard against a revert of P3 Task 5's swap
 * back to the (now deleted) `CharacterLibrary` / `EntityLibrary` walls over
 * `project_characters` / `project_lib_entities`.
 */
import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

const personalTeamId = '727145299382534777';
vi.mock('../../contexts/TeamContext', () => ({
  useTeamContext: () => ({ personalTeamId }),
}));

vi.mock('./ProjectAssetsPanel', () => ({
  ProjectAssetsPanel: (props: {
    assetType: string;
    projectId: string;
    scopeId: string | null;
    teamId?: string;
  }) => (
    <div
      data-testid="panel"
      data-asset-type={props.assetType}
      data-project-id={props.projectId}
      data-scope-id={props.scopeId ?? ''}
      data-team-id={props.teamId ?? ''}
    />
  ),
}));

import { WorkspaceEntities } from './WorkspaceEntities';

describe('WorkspaceEntities', () => {
  it.each([
    ['characters', 'character'],
    ['locations', 'location'],
    ['props', 'prop'],
    ['costumes', 'costume'],
  ] as const)('maps the %s module to the %s asset type', (kind, type) => {
    render(<WorkspaceEntities kind={kind} projectId="p1" projectTeamId="42" teamId="42" />);
    expect(screen.getByTestId('panel').getAttribute('data-asset-type')).toBe(type);
  });

  it('renders the assets panel for every module — no legacy library wall', () => {
    // Positive AND negative: the panel must be the ONLY thing each module
    // mounts. A revert that re-introduced a bible-card wall would either drop
    // the `panel` testid (routed elsewhere) or add one of the retired testids
    // alongside it (rendered as well) — this fails on both.
    //
    // The list is EVERY testid the two deleted components rendered, not just
    // their happy paths. `character-library` / `entity-library` are the wall's
    // containers and `*-card` its rows, so a revert whose data happened to be
    // EMPTY would have rendered only the empty-state ids — and the four-name
    // list would have missed it. Verified against the deleted source rather
    // than remembered:
    //   git show ce5e24df^:frontend/components/workspace/CharacterLibrary.tsx \
    //     | grep -o 'data-testid="[a-z-]*"' | sort -u
    //   git show ce5e24df^:frontend/components/workspace/EntityLibrary.tsx \
    //     | grep -o 'data-testid="[a-z-]*"' | sort -u
    const RETIRED = [
      'character-library',
      'character-library-empty',
      'character-import-hint',
      'character-card',
      'entity-library',
      'entity-library-empty',
      'entity-card',
    ];
    for (const kind of ['characters', 'locations', 'props', 'costumes'] as const) {
      const { container, unmount } = render(
        <WorkspaceEntities kind={kind} projectId="p1" projectTeamId="42" teamId="42" />,
      );
      expect(container.querySelectorAll('[data-testid="panel"]')).toHaveLength(1);
      for (const retired of RETIRED) {
        expect(container.querySelector(`[data-testid="${retired}"]`)).toBeNull();
      }
      unmount();
    }
  });

  it('uses the project’s own team as the asset scope', () => {
    render(<WorkspaceEntities kind="characters" projectId="p1" projectTeamId="42" teamId="42" />);
    expect(screen.getByTestId('panel').getAttribute('data-scope-id')).toBe('42');
  });

  it('falls back to the personal team for a team-less project', () => {
    // `projects.team_id` is NULL on a personal project; its assets live in the
    // owner's personal team.
    render(<WorkspaceEntities kind="props" projectId="p1" projectTeamId={null} />);
    const panel = screen.getByTestId('panel');
    expect(panel.getAttribute('data-scope-id')).toBe(personalTeamId);
    expect(panel.getAttribute('data-team-id')).toBe('');
  });
});
