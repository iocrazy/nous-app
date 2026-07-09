/**
 * StageSuggestion (Phase B B3) — data-aware "next step" guided card.
 *
 * Pins: the message + CTA are driven by the `/stage-suggestion` payload
 * (kind + progress + action), not a static per-stage table. The storyboard
 * one-click CTA fires the batch-generate endpoint directly; a `navigate`
 * action switches tabs instead. An empty payload (no kind/action) renders
 * nothing.
 */
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { test, expect, vi } from 'vitest';
import { StageSuggestion } from './StageSuggestion';
import * as svc from '../../services/projectsService';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, o?: any) => (o?.count != null ? `${k}:${o.count}` : k) }),
}));
vi.mock('../../components/Toast', () => ({ useToast: () => ({ addToast: vi.fn() }) }));

const stage = { id: '30', slug: 'storyboard', name: 'Storyboarding' } as any;

test('storyboard_generate renders count and fires batch on click', async () => {
  vi.spyOn(svc, 'fetchStageSuggestion').mockResolvedValue({
    stage_slug: 'storyboard', kind: 'storyboard_generate',
    progress: { total: 12, done: 9, empty: 3, generating: 0, failed: 0, script_count: 2, scene_count: 5 },
    action: { type: 'generate_missing_frames', label_key: 'projects.suggest.ctaGenerate', count: 3 },
  });
  const gen = vi.spyOn(svc, 'generateMissingFrames').mockResolvedValue({ dispatched_count: 3, task_ids: ['t1', 't2', 't3'] });

  render(<StageSuggestion projectId="p1" currentStage={stage} setActiveTab={vi.fn()} />);
  const btn = await screen.findByTestId('suggest-cta');
  expect(btn.textContent).toContain('3');
  fireEvent.click(btn);
  await waitFor(() => expect(gen).toHaveBeenCalledWith('p1'));
});

test('navigate kind switches tab, does not call batch', async () => {
  vi.spyOn(svc, 'fetchStageSuggestion').mockResolvedValue({
    stage_slug: 'planning', kind: 'planning_nav', progress: null,
    action: { type: 'navigate', tab: 'scripts', label_key: 'projects.suggest.cta_planning', count: null },
  });
  const setTab = vi.fn();
  render(<StageSuggestion projectId="p1" currentStage={{ ...stage, slug: 'planning' }} setActiveTab={setTab} />);
  const btn = await screen.findByTestId('suggest-cta');
  fireEvent.click(btn);
  expect(setTab).toHaveBeenCalledWith('scripts');
});

test('empty kind renders nothing', async () => {
  vi.spyOn(svc, 'fetchStageSuggestion').mockResolvedValue({
    stage_slug: null, kind: '', progress: null, action: null,
  });
  const { container } = render(<StageSuggestion projectId="p1" currentStage={null} setActiveTab={vi.fn()} />);
  await waitFor(() => expect(container.querySelector('[data-testid="stage-suggestion"]')).toBeNull());
});
