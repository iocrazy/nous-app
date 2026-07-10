/**
 * StageWorkbench (Phase B B2) — stage-driven workbench header.
 *
 * Pins the guided-advance behavior: the Advance button targets the NEXT
 * catalog stage, the final stage shows a terminal marker instead, and
 * recommended-tool cards navigate to their tab.
 */
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach } from 'vitest';

import { StageWorkbench } from './StageWorkbench';
import type { ProjectStage } from '../../types';

const mockService = vi.hoisted(() => ({
  fetchStageCatalog: vi.fn(),
  setCurrentStage: vi.fn(),
  fetchStageHistory: vi.fn(),
  fetchStageSuggestion: vi.fn(),
}));
vi.mock('../../services/projectsService', () => mockService);

// StageSelector fetches the catalog too; stub it to a no-op child so this
// suite exercises only the workbench card.
vi.mock('./StageSelector', () => ({
  StageSelector: () => <div data-testid="stage-selector" />,
}));

// relativeTime pulls in the real i18n instance via formatDate — stub it so
// this suite doesn't need initReactI18next (mirrors ProjectCard.test.tsx).
// Pulled in transitively via StageHistoryDrawer.
vi.mock('../../utils/relativeTime', () => ({
  formatRelativeTime: () => '2h ago',
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, opts?: Record<string, unknown>) => {
      if (key === 'projects.workbench.currentStageOf')
        return `Current stage · ${opts?.index} of ${opts?.total}`;
      if (key === 'projects.workbench.advanceTo') return `Advance to ${opts?.stage}`;
      if (key === 'projects.workbench.toolFrames') return `${opts?.done}/${opts?.total} frames`;
      // Stage slug labels echo the slug's last segment; descriptions echo the key
      // (so `description !== descKey` is false → description hidden unless real).
      return key;
    },
  }),
}));

const CATALOG: ProjectStage[] = [
  { id: '1', slug: 'planning', name: 'Planning', sort_order: 10, tools_recommended: [] },
  { id: '2', slug: 'script', name: 'Script', sort_order: 20, tools_recommended: [] },
  {
    id: '3',
    slug: 'storyboard',
    name: 'Storyboard',
    sort_order: 30,
    tools_recommended: ['storyboard', 'scripts'],
  },
  { id: '6', slug: 'delivery', name: 'Delivery', sort_order: 60, tools_recommended: [] },
];

const noop = () => {};

beforeEach(() => {
  mockService.fetchStageCatalog.mockResolvedValue(CATALOG);
  mockService.setCurrentStage.mockReset();
  mockService.fetchStageHistory.mockReset();
  mockService.fetchStageHistory.mockResolvedValue([]);
  mockService.fetchStageSuggestion.mockReset();
  mockService.fetchStageSuggestion.mockResolvedValue({
    stage_slug: null,
    kind: '',
    progress: null,
    action: null,
  });
});

describe('StageWorkbench', () => {
  it('renders nothing when the project has no current stage', () => {
    const { container } = render(
      <StageWorkbench
        projectId="p1"
        currentStage={null}
        onStageChange={noop}
        setActiveTab={noop}
      />,
    );
    expect(container.querySelector('h2')).toBeNull();
  });

  it('shows the current stage position, recommended tools, and advance target', async () => {
    render(
      <StageWorkbench
        projectId="p1"
        currentStage={CATALOG[2]}
        onStageChange={noop}
        setActiveTab={noop}
      />,
    );
    // "3 of 4" — position in the catalog, not sort_order.
    expect(await screen.findByText('Current stage · 3 of 4')).toBeTruthy();
    // Advance targets the NEXT catalog entry (delivery), not a sort_order guess.
    expect(screen.getByText('Advance to projects.stages.delivery')).toBeTruthy();
    // Two recommended tool cards from tools_recommended.
    expect(screen.getByText('projects.tools.storyboard')).toBeTruthy();
    expect(screen.getByText('projects.tools.scripts')).toBeTruthy();
  });

  it('advances to the next stage and propagates the result', async () => {
    const onStageChange = vi.fn();
    mockService.setCurrentStage.mockResolvedValue(CATALOG[3]);
    render(
      <StageWorkbench
        projectId="p1"
        currentStage={CATALOG[2]}
        onStageChange={onStageChange}
        setActiveTab={noop}
      />,
    );
    fireEvent.click(await screen.findByText('Advance to projects.stages.delivery'));
    await waitFor(() =>
      expect(mockService.setCurrentStage).toHaveBeenCalledWith('p1', '6'),
    );
    expect(onStageChange).toHaveBeenCalledWith(CATALOG[3]);
  });

  it('shows a final-stage marker instead of advance on the last stage', async () => {
    render(
      <StageWorkbench
        projectId="p1"
        currentStage={CATALOG[3]}
        onStageChange={noop}
        setActiveTab={noop}
      />,
    );
    expect(await screen.findByText('projects.workbench.finalStage')).toBeTruthy();
    expect(screen.queryByText(/Advance to/)).toBeNull();
  });

  it('navigates to a tool tab when a tool card is clicked', async () => {
    const setActiveTab = vi.fn();
    render(
      <StageWorkbench
        projectId="p1"
        currentStage={CATALOG[2]}
        onStageChange={noop}
        setActiveTab={setActiveTab}
      />,
    );
    fireEvent.click(await screen.findByText('projects.tools.storyboard'));
    expect(setActiveTab).toHaveBeenCalledWith('storyboard');
  });

  it('shows the storyboard progress subtitle on the storyboard toolcard when the stage is storyboard', async () => {
    mockService.fetchStageSuggestion.mockResolvedValue({
      stage_slug: 'storyboard',
      kind: 'storyboard_generate',
      progress: { total: 12, done: 9, empty: 3, generating: 0, failed: 0, script_count: 2, scene_count: 5 },
      action: { type: 'generate_missing_frames', label_key: 'projects.suggest.ctaGenerate', count: 3 },
    });
    render(
      <StageWorkbench
        projectId="p1"
        currentStage={CATALOG[2]}
        onStageChange={noop}
        setActiveTab={noop}
      />,
    );
    expect(await screen.findByText('9/12 frames')).toBeTruthy();
    // Other toolcards (scripts) stay label-only — no subtitle for them.
    expect(screen.getByText('projects.tools.scripts').nextSibling).toBeNull();
  });

  it('does not fetch or show a subtitle when the current stage is not storyboard', async () => {
    render(
      <StageWorkbench
        projectId="p1"
        currentStage={CATALOG[1]}
        onStageChange={noop}
        setActiveTab={noop}
      />,
    );
    await screen.findByText('Current stage · 2 of 4');
    expect(mockService.fetchStageSuggestion).not.toHaveBeenCalled();
  });

  it('opens the stage-history drawer when the history button is clicked', async () => {
    render(
      <StageWorkbench
        projectId="p1"
        currentStage={CATALOG[2]}
        onStageChange={noop}
        setActiveTab={noop}
      />,
    );
    const btn = await screen.findByTestId('stage-history-btn');
    fireEvent.click(btn);
    expect(await screen.findByTestId('stage-history-drawer')).toBeInTheDocument();
  });
});
