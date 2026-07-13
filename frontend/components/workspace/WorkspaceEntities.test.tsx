/**
 * WorkspaceEntities (PR-10b Wave 2) — the Characters/Locations ASSETS main
 * library module.
 *
 * Pins: rows render from `fetchProjectEntities`, with the right count
 * field per `kind`; the episode filter dropdown narrows rows to those
 * whose `episode_ids` include the selected episode; the empty state
 * renders when the (filtered) list is empty.
 */
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';

import { WorkspaceEntities } from './WorkspaceEntities';
import type { EpisodeProgress, ProjectEntities } from '../../types';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, opts?: Record<string, unknown>) => {
      if (key === 'projects.workspace.entities.cueCount') return `${opts?.count} cues`;
      if (key === 'projects.workspace.entities.sceneCount') return `${opts?.count} scenes`;
      if (key === 'projects.workspace.entities.epChip') return `Ep ${opts?.n}`;
      if (key.startsWith('projects.workspace.entities.')) return key.split('.').pop()!;
      if (key.startsWith('projects.workspace.modules.')) return key.split('.').pop()!;
      return key;
    },
  }),
}));

const mockService = vi.hoisted(() => ({
  fetchProjectEntities: vi.fn(),
}));
vi.mock('../../services/projectsService', () => mockService);
// characters now renders the authored bible-card library (PR-CC4) — mock it;
// its own behavior is covered in CharacterLibrary.test.tsx.
vi.mock('./CharacterLibrary', () => ({
  CharacterLibrary: ({ projectId }: { projectId: string }) => (
    <div data-testid="character-library-mock" data-project={projectId} />
  ),
}));

const EPISODES: EpisodeProgress[] = [
  {
    episode_id: '1',
    title: 'Ep 1 — Pilot',
    sort_order: 10,
    script_count: 1,
    scene_count: 4,
    shots_total: 12,
    shots_done: 9,
    renders_count: 1,
    status: 'boarding',
  },
  {
    episode_id: '2',
    title: 'Ep 2 — Cutdown',
    sort_order: 20,
    script_count: 1,
    scene_count: 2,
    shots_total: 4,
    shots_done: 0,
    renders_count: 0,
    status: 'drafting',
  },
];

const ENTITIES: ProjectEntities = {
  characters: [],
  locations: [
    { name: 'Radio Booth', scene_count: 3, episode_ids: ['1', '2'] },
    { name: 'Diner', scene_count: 1, episode_ids: ['1'] },
  ],
};

beforeEach(() => {
  mockService.fetchProjectEntities.mockReset().mockResolvedValue(ENTITIES);
});

afterEach(() => cleanup());

describe('WorkspaceEntities', () => {
  it('characters kind renders the bible-card library (PR-CC4)', () => {
    render(<WorkspaceEntities kind="characters" projectId="p1" episodes={EPISODES} />);
    const lib = screen.getByTestId('character-library-mock');
    expect(lib).toBeInTheDocument();
    expect(lib.getAttribute('data-project')).toBe('p1');
    // The derived-list machinery must not fetch for characters anymore.
    expect(mockService.fetchProjectEntities).not.toHaveBeenCalled();
  });

  it('renders location rows with scene counts and episode badges', async () => {
    render(<WorkspaceEntities kind="locations" projectId="p1" episodes={EPISODES} />);
    expect(await screen.findByTestId('ws-entities-row-0')).toHaveTextContent('Radio Booth');
    expect(screen.getByTestId('ws-entities-count-0')).toHaveTextContent('3 scenes');
    expect(screen.getByTestId('ws-entities-badge-0-1')).toHaveTextContent('Ep 1');
    expect(screen.getByTestId('ws-entities-badge-0-2')).toHaveTextContent('Ep 2');
    expect(screen.getByTestId('ws-entities-row-1')).toHaveTextContent('Diner');
    expect(screen.getByTestId('ws-entities-count-1')).toHaveTextContent('1 scene');
  });

  it('filters rows by episode via the dropdown', async () => {
    render(<WorkspaceEntities kind="locations" projectId="p1" episodes={EPISODES} />);
    await screen.findByTestId('ws-entities-row-0');
    expect(screen.getByTestId('ws-entities-row-1')).toBeTruthy(); // Diner visible before filter

    fireEvent.click(screen.getByTestId('ws-entities-filter'));
    fireEvent.click(screen.getByTestId('ws-entities-filter-2'));

    await waitFor(() => {
      // Only Radio Booth (episode_ids includes '2') should remain.
      expect(screen.getByTestId('ws-entities-row-0')).toHaveTextContent('Radio Booth');
      expect(screen.queryByText('Diner')).toBeNull();
    });
  });

  it('renders the empty state when there are no rows', async () => {
    mockService.fetchProjectEntities.mockResolvedValue({ characters: [], locations: [] });
    render(<WorkspaceEntities kind="locations" projectId="p1" episodes={EPISODES} />);
    expect(await screen.findByTestId('ws-entities-empty')).toBeTruthy();
  });
});
