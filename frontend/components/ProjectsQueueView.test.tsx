/**
 * ProjectsQueueView (PR-9, G7) — homepage default work-queue.
 *
 * Covers: group sort order (stalled > generate > navigate > delivery, with
 * starred pinned first within a group), the one-click generate CTA firing
 * `generateMissingFrames` + the refetch callback, the navigate CTA opening
 * the project, and empty-kind rows being skipped entirely.
 */
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { ProjectsQueueView } from './ProjectsQueueView';
import * as svc from '../services/projectsService';
import type { Project, ProjectSuggestionItem } from '../types';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (k: string, o?: Record<string, unknown>) =>
      o && o.count != null ? `${k}:${o.count}` : k,
  }),
}));
vi.mock('./Toast', () => ({ useToast: () => ({ addToast: vi.fn() }) }));
vi.mock('../utils/relativeTime', () => ({ formatRelativeTime: () => '2h ago' }));

function makeProject(overrides: Partial<Project>): Project {
  return {
    id: '1',
    name: 'Project',
    description: null,
    owner_id: 'u1',
    team_id: null,
    project_type: 'internal',
    project_group: null,
    announcement: null,
    is_starred: false,
    color_label: null,
    archived_at: null,
    file_count: 0,
    created_at: '2026-06-01T00:00:00Z',
    updated_at: '2026-07-01T00:00:00Z',
    ...overrides,
  };
}

const pDelivery = makeProject({ id: '1', name: 'Delivery Proj' });
const pGenA = makeProject({ id: '2', name: 'Gen Proj A' });
const pGenB = makeProject({ id: '3', name: 'Gen Proj B', is_starred: true });
const pStalled = makeProject({ id: '4', name: 'Stalled Proj' });
const pEmpty = makeProject({ id: '5', name: 'Empty Kind Proj' });

const PROJECTS = [pDelivery, pGenA, pGenB, pStalled, pEmpty];

const iDelivery: ProjectSuggestionItem = {
  project_id: '1',
  name: 'Delivery Proj',
  stage_slug: 'delivery',
  kind: 'delivery_nav',
  stalled: false,
  action: { type: 'navigate', tab: 'output', label_key: 'projects.suggest.cta_delivery', count: null },
};

const iGenA: ProjectSuggestionItem = {
  project_id: '2',
  name: 'Gen Proj A',
  stage_slug: 'storyboard',
  kind: 'storyboard_generate',
  stalled: false,
  progress: { total: 12, done: 9, empty: 3, generating: 0, failed: 0, script_count: 2, scene_count: 5 },
  action: { type: 'generate_missing_frames', label_key: 'projects.suggest.ctaGenerate', count: 3 },
};

const iGenB: ProjectSuggestionItem = {
  project_id: '3',
  name: 'Gen Proj B',
  stage_slug: 'storyboard',
  kind: 'storyboard_generate',
  stalled: false,
  progress: { total: 8, done: 6, empty: 2, generating: 0, failed: 0, script_count: 1, scene_count: 3 },
  action: { type: 'generate_missing_frames', label_key: 'projects.suggest.ctaGenerate', count: 2 },
};

const iStalled: ProjectSuggestionItem = {
  project_id: '4',
  name: 'Stalled Proj',
  stage_slug: 'review',
  kind: 'review_nav',
  stalled: true,
  action: { type: 'navigate', tab: 'shares', label_key: 'projects.suggest.cta_review', count: null },
};

const iEmpty: ProjectSuggestionItem = {
  project_id: '5',
  name: 'Empty Kind Proj',
  stage_slug: null,
  kind: '',
  stalled: false,
  action: null,
};

// Deliberately out of expected render order to prove the component sorts,
// not just passes through.
const SUGGESTIONS = [iDelivery, iGenA, iGenB, iStalled, iEmpty];

describe('ProjectsQueueView', () => {
  it('sorts stalled first, generate second (starred pinned within group), delivery last, and skips empty-kind rows', () => {
    render(
      <ProjectsQueueView
        projects={PROJECTS}
        suggestions={SUGGESTIONS}
        onProjectSelect={vi.fn()}
        onRefetchSuggestions={vi.fn()}
      />,
    );

    const rows = screen.getAllByTestId('queue-row');
    // Empty-kind row skipped: 4 rows, not 5.
    expect(rows).toHaveLength(4);

    const names = rows.map((row) => row.textContent);
    // Stalled first, then starred generate before non-starred generate,
    // then delivery last.
    expect(names[0]).toContain('Stalled Proj');
    expect(names[1]).toContain('Gen Proj B'); // starred, pinned first in generate group
    expect(names[2]).toContain('Gen Proj A');
    expect(names[3]).toContain('Delivery Proj');

    expect(screen.queryByText('Empty Kind Proj')).toBeNull();
  });

  it('generate CTA fires generateMissingFrames and the refetch callback', async () => {
    const gen = vi.spyOn(svc, 'generateMissingFrames').mockResolvedValue({
      dispatched_count: 3,
      task_ids: ['t1', 't2', 't3'],
    });
    const onRefetch = vi.fn();

    render(
      <ProjectsQueueView
        projects={PROJECTS}
        suggestions={SUGGESTIONS}
        onProjectSelect={vi.fn()}
        onRefetchSuggestions={onRefetch}
      />,
    );

    const ctas = screen.getAllByTestId('queue-cta');
    // Row order: Stalled, Gen B (starred), Gen A, Delivery — Gen B's CTA is index 1.
    fireEvent.click(ctas[1]);

    await waitFor(() => expect(gen).toHaveBeenCalledWith('3'));
    await waitFor(() => expect(onRefetch).toHaveBeenCalled());
  });

  it('navigate CTA calls onProjectSelect with the matching project, not generateMissingFrames', async () => {
    const gen = vi.spyOn(svc, 'generateMissingFrames');
    const onSelect = vi.fn();

    render(
      <ProjectsQueueView
        projects={PROJECTS}
        suggestions={SUGGESTIONS}
        onProjectSelect={onSelect}
        onRefetchSuggestions={vi.fn()}
      />,
    );

    const ctas = screen.getAllByTestId('queue-cta');
    // Row order: Stalled(0), Gen B(1), Gen A(2), Delivery(3) — Delivery is navigate.
    fireEvent.click(ctas[3]);

    expect(onSelect).toHaveBeenCalledWith(pDelivery);
    expect(gen).not.toHaveBeenCalled();
  });

  it('clicking anywhere on the row (not the CTA) also calls onProjectSelect', () => {
    const onSelect = vi.fn();

    render(
      <ProjectsQueueView
        projects={PROJECTS}
        suggestions={SUGGESTIONS}
        onProjectSelect={onSelect}
        onRefetchSuggestions={vi.fn()}
      />,
    );

    const rows = screen.getAllByTestId('queue-row');
    // Row order: Stalled(0), Gen B(1), Gen A(2), Delivery(3).
    fireEvent.click(rows[3]);

    expect(onSelect).toHaveBeenCalledWith(pDelivery);
  });

  it('clicking the generate CTA does not also trigger row navigation', async () => {
    const gen = vi.spyOn(svc, 'generateMissingFrames').mockResolvedValue({
      dispatched_count: 2,
      task_ids: ['t1'],
    });
    const onSelect = vi.fn();

    render(
      <ProjectsQueueView
        projects={PROJECTS}
        suggestions={SUGGESTIONS}
        onProjectSelect={onSelect}
        onRefetchSuggestions={vi.fn()}
      />,
    );

    const ctas = screen.getAllByTestId('queue-cta');
    // Row order: Stalled(0), Gen B(1) generate CTA, Gen A(2), Delivery(3).
    fireEvent.click(ctas[1]);

    await waitFor(() => expect(gen).toHaveBeenCalledWith('3'));
    expect(onSelect).not.toHaveBeenCalled();
  });

  it('renders the empty state when no rows survive filtering', () => {
    render(
      <ProjectsQueueView
        projects={PROJECTS}
        suggestions={[iEmpty]}
        onProjectSelect={vi.fn()}
        onRefetchSuggestions={vi.fn()}
      />,
    );

    expect(screen.getByTestId('projects-queue-empty')).toBeTruthy();
    expect(screen.queryByTestId('queue-row')).toBeNull();
  });
});
