/**
 * StageSuggestion (Phase B B3) — stage-aware "next step" guided card.
 *
 * Pins: the message + CTA follow the current stage; the script stage
 * specializes on script count; an unknown stage renders nothing.
 */
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach } from 'vitest';

import { StageSuggestion } from './StageSuggestion';
import type { ProjectStage } from '../../types';

const mockService = vi.hoisted(() => ({ fetchScriptProjects: vi.fn() }));
vi.mock('../../services/scriptService', () => mockService);

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, opts?: Record<string, unknown>) => {
      if (key === 'projects.suggest.script') return `You have ${opts?.count} scripts`;
      return key;
    },
  }),
}));

function stage(slug: string): ProjectStage {
  return { id: '1', slug, name: slug, sort_order: 10, tools_recommended: [] };
}

const noop = () => {};

beforeEach(() => {
  mockService.fetchScriptProjects.mockReset();
  mockService.fetchScriptProjects.mockResolvedValue({ data: [], total: 0 });
});

describe('StageSuggestion', () => {
  it('renders nothing for an unknown stage slug', () => {
    const { container } = render(
      <StageSuggestion projectId="p1" currentStage={stage('mystery')} setActiveTab={noop} />,
    );
    expect(container.querySelector('[data-testid="stage-suggestion"]')).toBeNull();
  });

  it('renders nothing when there is no current stage', () => {
    const { container } = render(
      <StageSuggestion projectId="p1" currentStage={null} setActiveTab={noop} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it('shows the generation suggestion and an Output CTA', () => {
    render(
      <StageSuggestion projectId="p1" currentStage={stage('generation')} setActiveTab={noop} />,
    );
    expect(screen.getByText('projects.suggest.generation')).toBeTruthy();
    expect(screen.getByText('projects.suggest.ctaOutput')).toBeTruthy();
    // Non-script stages don't hit the script-count endpoint.
    expect(mockService.fetchScriptProjects).not.toHaveBeenCalled();
  });

  it('specializes the script stage on the live script count', async () => {
    mockService.fetchScriptProjects.mockResolvedValue({ data: [], total: 3 });
    render(
      <StageSuggestion projectId="p1" currentStage={stage('script')} setActiveTab={noop} />,
    );
    expect(await screen.findByText('You have 3 scripts')).toBeTruthy();
    expect(mockService.fetchScriptProjects).toHaveBeenCalledWith('p1', 1, 1);
  });

  it('falls back to the empty-script message when the project has no scripts', async () => {
    mockService.fetchScriptProjects.mockResolvedValue({ data: [], total: 0 });
    render(
      <StageSuggestion projectId="p1" currentStage={stage('script')} setActiveTab={noop} />,
    );
    await waitFor(() =>
      expect(screen.getByText('projects.suggest.scriptEmpty')).toBeTruthy(),
    );
  });

  it('navigates to the CTA tab when clicked', () => {
    const setActiveTab = vi.fn();
    render(
      <StageSuggestion
        projectId="p1"
        currentStage={stage('review')}
        setActiveTab={setActiveTab}
      />,
    );
    fireEvent.click(screen.getByText('projects.suggest.ctaFiles'));
    expect(setActiveTab).toHaveBeenCalledWith('files');
  });
});
