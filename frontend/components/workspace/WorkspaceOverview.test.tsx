/**
 * WorkspaceOverview (合一终稿, 2026-07-11) — the workspace landing module.
 *
 * Pins: the film-styled Continue card surfaces the current episode's title +
 * mono read-out (SC · SHOTS · CUTS) and its CTA fires the deep-link callback;
 * the summary tiles aggregate episode counts (Episodes/Storyboard) and read
 * straight off the project record (Files/Canvas). The old StageSuggestion card
 * is gone (advancing stages happens elsewhere now).
 */
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import { describe, expect, it, vi, afterEach } from 'vitest';

import { WorkspaceOverview } from './WorkspaceOverview';
import type { EpisodeProgress, Project } from '../../types';

// relativeTime pulls in the real i18n instance via formatDate — stub it so
// this suite doesn't need initReactI18next (mirrors StageWorkbench.test.tsx).
vi.mock('../../utils/relativeTime', () => ({
  formatRelativeTime: () => '2h ago',
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, opts?: unknown) => {
      if (key.startsWith('projects.workspace.modules.')) return key.split('.').pop()!;
      if (key.startsWith('projects.workspace.episodeStatus.')) return key.split('.').pop()!;
      if (key === 'projects.card.activityFile') return 'Added files';
      if (key === 'projects.workspace.overview.awaiting')
        return `${(opts as { count?: number } | undefined)?.count ?? 0} awaiting you`;
      return key;
    },
  }),
}));

const PROJECT: Project = {
  id: 'p1',
  name: 'Spring Campaign',
  description: null,
  owner_id: 'u1',
  team_id: 't1',
  project_type: 'internal',
  project_group: null,
  announcement: null,
  is_starred: false,
  color_label: null,
  archived_at: null,
  file_count: 128,
  created_at: '2026-06-01T00:00:00Z',
  updated_at: '2026-07-01T00:00:00Z',
  latest_activity: { kind: 'file', actor: 'Alice', at: '2026-07-08T00:00:00Z', stalled: false },
};

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

afterEach(() => cleanup());

const noop = () => {};

describe('WorkspaceOverview', () => {
  it('shows the film-styled continue card and fires the deep-link on click', () => {
    const onOpenScript = vi.fn();
    render(
      <WorkspaceOverview
        project={PROJECT}
        episodes={EPISODES}
        currentEpisode={EPISODES[0]}
        epNumber={1}
        onOpenScript={onOpenScript}
      />,
    );

    const card = screen.getByTestId('ws-continue-card');
    expect(card).toHaveTextContent('Ep 1 — Pilot');
    expect(card).toHaveTextContent('CONTINUE · EP1');
    // Mono read-out: {scene_count} SC · SHOTS {done}/{total} · CUTS {renders}.
    expect(card).toHaveTextContent('4 SC · SHOTS 9/12 · CUTS 1');

    // The CTA opens the episode workspace, so it reads "Open Episode" (not the
    // old "Open studio" — there is no separate studio). Key renamed to match.
    const cta = screen.getByTestId('ws-open-episode-btn');
    expect(cta).toHaveTextContent('projects.workspace.overview.openEpisode');
    fireEvent.click(cta);
    expect(onOpenScript).toHaveBeenCalledTimes(1);
  });

  it('aggregates summary tiles across all episodes and reads Files/Canvas from the project', () => {
    render(
      <WorkspaceOverview
        project={PROJECT}
        episodes={EPISODES}
        currentEpisode={EPISODES[0]}
        epNumber={1}
        onOpenScript={noop}
      />,
    );

    expect(screen.getByTestId('ws-summary-episodes')).toHaveTextContent('2');
    expect(screen.getByTestId('ws-summary-episodes')).toHaveTextContent('boarding');
    // 9+0 done / 12+4 total across both episodes.
    expect(screen.getByTestId('ws-summary-storyboard')).toHaveTextContent('9/16');
    expect(screen.getByTestId('ws-summary-files')).toHaveTextContent('128');
    expect(screen.getByTestId('ws-summary-canvas')).toHaveTextContent('—');
  });

  it('renders one rollup row per episode and deep-links on click', () => {
    const onSelectEpisode = vi.fn();
    render(
      <WorkspaceOverview
        project={PROJECT}
        episodes={EPISODES}
        currentEpisode={EPISODES[0]}
        epNumber={1}
        onOpenScript={noop}
        onSelectEpisode={onSelectEpisode}
      />,
    );

    const rollup = screen.getByTestId('ws-rollup');
    expect(rollup).toBeTruthy();
    // One row per episode, current episode flagged.
    const row1 = screen.getByTestId('ws-rollup-row-1');
    expect(row1).toHaveAttribute('data-current', 'true');
    expect(row1).toHaveTextContent('4 SC · SHOTS 9/12 · CUTS 1');
    // boarding → 3 of 5 stage segments lit.
    expect(screen.getByTestId('ws-rollup-stages-1')).toHaveAttribute('data-fill', '3');
    // drafting → 2 lit.
    expect(screen.getByTestId('ws-rollup-stages-2')).toHaveAttribute('data-fill', '2');

    fireEvent.click(screen.getByTestId('ws-rollup-row-2'));
    expect(onSelectEpisode).toHaveBeenCalledWith('2');
  });

  it('shows the awaiting hint only for episodes parked at the planned stage', () => {
    const withPlanned: EpisodeProgress[] = [
      { ...EPISODES[0], status: 'planned' },
      { ...EPISODES[1], episode_id: '3', status: 'planned' },
      EPISODES[1],
    ];
    const { rerender } = render(
      <WorkspaceOverview
        project={PROJECT}
        episodes={withPlanned}
        currentEpisode={withPlanned[0]}
        epNumber={1}
        onOpenScript={noop}
      />,
    );
    expect(screen.getByTestId('ws-rollup-awaiting')).toHaveTextContent('2 awaiting you');

    // No planned episodes → hint hidden.
    rerender(
      <WorkspaceOverview
        project={PROJECT}
        episodes={EPISODES}
        currentEpisode={EPISODES[0]}
        epNumber={1}
        onOpenScript={noop}
      />,
    );
    expect(screen.queryByTestId('ws-rollup-awaiting')).toBeNull();
  });

  it('renders the recent-activity line from project.latest_activity', () => {
    render(
      <WorkspaceOverview
        project={PROJECT}
        episodes={EPISODES}
        currentEpisode={EPISODES[0]}
        epNumber={1}
        onOpenScript={noop}
      />,
    );
    expect(screen.getByTestId('ws-overview-activity')).toHaveTextContent('Added files');
    expect(screen.getByTestId('ws-overview-activity')).toHaveTextContent('Alice');
  });

  it('hides the continue card when there is no current episode', () => {
    render(
      <WorkspaceOverview
        project={PROJECT}
        episodes={[]}
        currentEpisode={null}
        epNumber={null}
        onOpenScript={noop}
      />,
    );
    expect(screen.queryByTestId('ws-continue-card')).toBeNull();
  });
});
