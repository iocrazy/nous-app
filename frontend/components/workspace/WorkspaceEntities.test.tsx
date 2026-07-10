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
  characters: [
    { name: 'CLIENT', cue_count: 12, episode_ids: ['1', '2'] },
    { name: 'DEV', cue_count: 7, episode_ids: ['1'] },
  ],
  locations: [{ name: 'Radio Booth', scene_count: 3, episode_ids: ['1'] }],
};

beforeEach(() => {
  mockService.fetchProjectEntities.mockReset().mockResolvedValue(ENTITIES);
});

afterEach(() => cleanup());

describe('WorkspaceEntities', () => {
  it('renders character rows with cue counts and episode badges', async () => {
    render(<WorkspaceEntities kind="characters" projectId="p1" episodes={EPISODES} />);

    expect(await screen.findByTestId('ws-entities-row-0')).toHaveTextContent('CLIENT');
    expect(screen.getByTestId('ws-entities-count-0')).toHaveTextContent('12 cues');
    expect(screen.getByTestId('ws-entities-badge-0-1')).toHaveTextContent('Ep 1');
    expect(screen.getByTestId('ws-entities-badge-0-2')).toHaveTextContent('Ep 2');
    expect(screen.getByTestId('ws-entities-row-1')).toHaveTextContent('DEV');
    expect(screen.getByTestId('ws-entities-count-1')).toHaveTextContent('7 cues');
  });

  it('renders location rows with scene counts', async () => {
    render(<WorkspaceEntities kind="locations" projectId="p1" episodes={EPISODES} />);
    expect(await screen.findByTestId('ws-entities-row-0')).toHaveTextContent('Radio Booth');
    expect(screen.getByTestId('ws-entities-count-0')).toHaveTextContent('3 scenes');
  });

  it('filters rows by episode via the dropdown', async () => {
    render(<WorkspaceEntities kind="characters" projectId="p1" episodes={EPISODES} />);
    await screen.findByTestId('ws-entities-row-0');
    expect(screen.getByTestId('ws-entities-row-1')).toBeTruthy(); // DEV visible before filter

    fireEvent.click(screen.getByTestId('ws-entities-filter'));
    fireEvent.click(screen.getByTestId('ws-entities-filter-2'));

    await waitFor(() => {
      // Only CLIENT (episode_ids includes '2') should remain.
      expect(screen.getByTestId('ws-entities-row-0')).toHaveTextContent('CLIENT');
      expect(screen.queryByText('DEV')).toBeNull();
    });
  });

  it('renders the empty state when there are no rows', async () => {
    mockService.fetchProjectEntities.mockResolvedValue({ characters: [], locations: [] });
    render(<WorkspaceEntities kind="characters" projectId="p1" episodes={EPISODES} />);
    expect(await screen.findByTestId('ws-entities-empty')).toBeTruthy();
  });
});
