/**
 * WorkspaceOverview (PR-10b Wave 1) — the workspace landing module.
 *
 * Pins: the Continue card surfaces the current episode's title + progress
 * line and its CTA fires the deep-link callback; the summary tiles
 * aggregate episode counts (Episodes/Storyboard) and read straight off the
 * project record (Files/Canvas). StageSuggestion is left disabled here
 * (VITE_FEATURE_PROJECT_AI_SUGGEST is unset in the test env), matching its
 * existing StageWorkbench gating — this suite doesn't need to mock it.
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
    t: (key: string, opts?: Record<string, unknown>) => {
      if (key === 'projects.workspace.overview.scriptScenes') return `${opts?.count} scenes`;
      if (key === 'projects.workspace.overview.shotsProgress') return `Shots ${opts?.done}/${opts?.total}`;
      if (key === 'projects.workspace.overview.rendersCount') return `Renders ${opts?.count}`;
      if (key.startsWith('projects.workspace.modules.')) return key.split('.').pop()!;
      if (key.startsWith('projects.workspace.episodeStatus.')) return key.split('.').pop()!;
      if (key === 'projects.card.activityFile') return 'Added files';
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
  it('shows the continue card for the current episode and fires the deep-link on click', () => {
    const onOpenScript = vi.fn();
    render(
      <WorkspaceOverview
        project={PROJECT}
        projectId="p1"
        currentStage={null}
        episodes={EPISODES}
        currentEpisode={EPISODES[0]}
        onOpenScript={onOpenScript}
        onSuggestionNavigate={noop}
      />,
    );

    const card = screen.getByTestId('ws-continue-card');
    expect(card).toHaveTextContent('Ep 1 — Pilot');
    expect(card).toHaveTextContent('4 scenes');
    expect(card).toHaveTextContent('Shots 9/12');
    expect(card).toHaveTextContent('Renders 1');

    fireEvent.click(screen.getByTestId('ws-open-studio-btn'));
    expect(onOpenScript).toHaveBeenCalledTimes(1);
  });

  it('aggregates summary tiles across all episodes and reads Files/Canvas from the project', () => {
    render(
      <WorkspaceOverview
        project={PROJECT}
        projectId="p1"
        currentStage={null}
        episodes={EPISODES}
        currentEpisode={EPISODES[0]}
        onOpenScript={noop}
        onSuggestionNavigate={noop}
      />,
    );

    expect(screen.getByTestId('ws-summary-episodes')).toHaveTextContent('2');
    expect(screen.getByTestId('ws-summary-episodes')).toHaveTextContent('boarding');
    // 9+0 done / 12+4 total across both episodes.
    expect(screen.getByTestId('ws-summary-storyboard')).toHaveTextContent('9/16');
    expect(screen.getByTestId('ws-summary-files')).toHaveTextContent('128');
    expect(screen.getByTestId('ws-summary-canvas')).toHaveTextContent('—');
  });

  it('renders the recent-activity line from project.latest_activity', () => {
    render(
      <WorkspaceOverview
        project={PROJECT}
        projectId="p1"
        currentStage={null}
        episodes={EPISODES}
        currentEpisode={EPISODES[0]}
        onOpenScript={noop}
        onSuggestionNavigate={noop}
      />,
    );
    expect(screen.getByTestId('ws-overview-activity')).toHaveTextContent('Added files');
    expect(screen.getByTestId('ws-overview-activity')).toHaveTextContent('Alice');
  });

  it('hides the continue card when there is no current episode', () => {
    render(
      <WorkspaceOverview
        project={PROJECT}
        projectId="p1"
        currentStage={null}
        episodes={[]}
        currentEpisode={null}
        onOpenScript={noop}
        onSuggestionNavigate={noop}
      />,
    );
    expect(screen.queryByTestId('ws-continue-card')).toBeNull();
  });
});
