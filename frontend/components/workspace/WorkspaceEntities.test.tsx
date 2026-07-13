/**
 * WorkspaceEntities — thin router over the three ASSETS libraries (CC4+SP3):
 * characters → CharacterLibrary, locations/props → EntityLibrary. Library
 * behavior is covered in their own suites.
 */

import { render, screen, cleanup } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('./CharacterLibrary', () => ({
  CharacterLibrary: ({ projectId }: { projectId: string }) => (
    <div data-testid="character-library-mock" data-project={projectId} />
  ),
}));
vi.mock('./EntityLibrary', () => ({
  EntityLibrary: ({ entityType, projectId }: { entityType: string; projectId: string }) => (
    <div data-testid="entity-library-mock" data-type={entityType} data-project={projectId} />
  ),
}));

import { WorkspaceEntities } from './WorkspaceEntities';

afterEach(() => cleanup());

describe('WorkspaceEntities', () => {
  it('characters → CharacterLibrary', () => {
    render(<WorkspaceEntities kind="characters" projectId="p1" />);
    expect(screen.getByTestId('character-library-mock').getAttribute('data-project')).toBe('p1');
  });

  it('locations → EntityLibrary(location)', () => {
    render(<WorkspaceEntities kind="locations" projectId="p1" />);
    expect(screen.getByTestId('entity-library-mock').getAttribute('data-type')).toBe('location');
  });

  it('props → EntityLibrary(prop)', () => {
    render(<WorkspaceEntities kind="props" projectId="p1" />);
    expect(screen.getByTestId('entity-library-mock').getAttribute('data-type')).toBe('prop');
  });
});
