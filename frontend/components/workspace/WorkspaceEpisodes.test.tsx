/**
 * WorkspaceEpisodes (PR-10b Wave 2) — the Episodes management module.
 *
 * Pins: rows render from the progress feed with status chips + the derived
 * progress line (or the "empty" line for a scene/shot/render-less
 * episode); double-click → inline rename fires the PATCH; the ⋯ menu's
 * Move up swaps sort_order via two PATCH calls; deleting an episode that
 * still has scripts (409) surfaces a toast instead of throwing.
 */
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';

import { WorkspaceEpisodes } from './WorkspaceEpisodes';
import type { EpisodeProgress } from '../../types';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, opts?: Record<string, unknown>) => {
      if (key === 'projects.workspace.episodes.scriptScenes') return `Script ✓ ${opts?.count} scenes`;
      if (key === 'projects.workspace.episodes.shotsProgress') return `Shots ${opts?.done}/${opts?.total}`;
      if (key === 'projects.workspace.episodes.rendersCount') return `Renders ${opts?.count}`;
      if (key.startsWith('projects.workspace.episodes.')) return key.split('.').pop()!;
      if (key.startsWith('projects.workspace.episodeStatus.')) return key.split('.').pop()!;
      if (key.startsWith('projects.workspace.modules.')) return key.split('.').pop()!;
      return key;
    },
  }),
}));

const addToast = vi.fn();
vi.mock('../Toast', () => ({
  useToast: () => ({ addToast }),
}));

const mockService = vi.hoisted(() => ({
  createEpisode: vi.fn(),
  updateEpisode: vi.fn(),
  deleteEpisode: vi.fn(),
}));
vi.mock('../../services/projectsService', () => mockService);

const FakeApiError = vi.hoisted(
  () =>
    class extends Error {
      status: number;
      constructor(status: number) {
        super('boom');
        this.status = status;
      }
    },
);
vi.mock('../../services/apiClient', () => ({ ApiError: FakeApiError }));

const EPISODES: EpisodeProgress[] = [
  {
    episode_id: '1',
    title: 'Pilot',
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
    title: 'BTS teaser',
    sort_order: 20,
    script_count: 0,
    scene_count: 0,
    shots_total: 0,
    shots_done: 0,
    renders_count: 0,
    status: 'planned',
  },
];

beforeEach(() => {
  addToast.mockClear();
  mockService.createEpisode.mockReset().mockResolvedValue(undefined);
  mockService.updateEpisode.mockReset().mockResolvedValue(undefined);
  mockService.deleteEpisode.mockReset().mockResolvedValue(undefined);
});

afterEach(() => cleanup());

const noop = () => {};

describe('WorkspaceEpisodes', () => {
  it('renders rows with status chips and the derived progress line', () => {
    render(
      <WorkspaceEpisodes
        projectId="p1"
        episodes={EPISODES}
        onEpisodesChanged={noop}
        onOpenEpisode={noop}
      />,
    );

    const row1 = screen.getByTestId('ws-episode-row-1');
    expect(row1).toHaveTextContent('Pilot');
    expect(screen.getByTestId('ws-episode-status-1')).toHaveTextContent('boarding');
    expect(screen.getByTestId('ws-episode-progress-1')).toHaveTextContent('Script ✓ 4 scenes');
    expect(screen.getByTestId('ws-episode-progress-1')).toHaveTextContent('Shots 9/12');
    expect(screen.getByTestId('ws-episode-progress-1')).toHaveTextContent('Renders 1');
    expect(screen.getByTestId('ws-episode-open-1')).toHaveTextContent('open');

    // Empty episode shows the "empty" progress line and "Start" CTA.
    expect(screen.getByTestId('ws-episode-progress-2')).toHaveTextContent('empty');
    expect(screen.getByTestId('ws-episode-open-2')).toHaveTextContent('start');
  });

  it('creates a new episode via the header button', async () => {
    const onEpisodesChanged = vi.fn();
    render(
      <WorkspaceEpisodes
        projectId="p1"
        episodes={EPISODES}
        onEpisodesChanged={onEpisodesChanged}
        onOpenEpisode={noop}
      />,
    );
    fireEvent.click(screen.getByTestId('ws-episodes-new-btn'));
    await waitFor(() => expect(mockService.createEpisode).toHaveBeenCalledWith('p1'));
    await waitFor(() => expect(onEpisodesChanged).toHaveBeenCalled());
  });

  it('double-click renames a title and PATCHes it', async () => {
    const onEpisodesChanged = vi.fn();
    render(
      <WorkspaceEpisodes
        projectId="p1"
        episodes={EPISODES}
        onEpisodesChanged={onEpisodesChanged}
        onOpenEpisode={noop}
      />,
    );
    fireEvent.doubleClick(screen.getByTestId('ws-episode-title-1'));
    const input = screen.getByTestId('ws-episode-rename-input-1');
    fireEvent.change(input, { target: { value: 'Pilot (v2)' } });
    fireEvent.keyDown(input, { key: 'Enter' });

    await waitFor(() =>
      expect(mockService.updateEpisode).toHaveBeenCalledWith('1', { title: 'Pilot (v2)' }),
    );
    await waitFor(() => expect(onEpisodesChanged).toHaveBeenCalled());
  });

  it('Move up swaps sort_order with the previous row via two PATCH calls', async () => {
    const onEpisodesChanged = vi.fn();
    render(
      <WorkspaceEpisodes
        projectId="p1"
        episodes={EPISODES}
        onEpisodesChanged={onEpisodesChanged}
        onOpenEpisode={noop}
      />,
    );
    fireEvent.click(screen.getByTestId('ws-episode-menu-2'));
    fireEvent.click(screen.getByTestId('ws-episode-menu-moveup-2'));

    await waitFor(() => {
      expect(mockService.updateEpisode).toHaveBeenCalledWith('2', { sort_order: 10 });
      expect(mockService.updateEpisode).toHaveBeenCalledWith('1', { sort_order: 20 });
    });
    await waitFor(() => expect(onEpisodesChanged).toHaveBeenCalled());
  });

  it('deleting a non-empty episode (409) surfaces a toast, not a thrown error', async () => {
    mockService.deleteEpisode.mockRejectedValue(new FakeApiError(409));
    const onEpisodesChanged = vi.fn();
    render(
      <WorkspaceEpisodes
        projectId="p1"
        episodes={EPISODES}
        onEpisodesChanged={onEpisodesChanged}
        onOpenEpisode={noop}
      />,
    );
    fireEvent.click(screen.getByTestId('ws-episode-menu-1'));
    fireEvent.click(screen.getByTestId('ws-episode-menu-delete-1'));
    fireEvent.click(screen.getByTestId('ws-episode-delete-confirm-yes-1'));

    await waitFor(() => expect(addToast).toHaveBeenCalledWith('notEmpty', 'error'));
    expect(onEpisodesChanged).not.toHaveBeenCalled();
  });

  it('deleting an empty episode succeeds and refetches', async () => {
    const onEpisodesChanged = vi.fn();
    render(
      <WorkspaceEpisodes
        projectId="p1"
        episodes={EPISODES}
        onEpisodesChanged={onEpisodesChanged}
        onOpenEpisode={noop}
      />,
    );
    fireEvent.click(screen.getByTestId('ws-episode-menu-2'));
    fireEvent.click(screen.getByTestId('ws-episode-menu-delete-2'));
    fireEvent.click(screen.getByTestId('ws-episode-delete-confirm-yes-2'));

    await waitFor(() => expect(mockService.deleteEpisode).toHaveBeenCalledWith('2'));
    await waitFor(() => expect(onEpisodesChanged).toHaveBeenCalled());
  });

  it('Open fires onOpenEpisode with the episode id', () => {
    const onOpenEpisode = vi.fn();
    render(
      <WorkspaceEpisodes
        projectId="p1"
        episodes={EPISODES}
        onEpisodesChanged={noop}
        onOpenEpisode={onOpenEpisode}
      />,
    );
    fireEvent.click(screen.getByTestId('ws-episode-open-1'));
    expect(onOpenEpisode).toHaveBeenCalledWith('1');
  });
});
