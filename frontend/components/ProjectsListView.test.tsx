/**
 * ProjectsListView (PR-9, G7) — homepage Queue/Grid toggle + view-persistence,
 * and confirms the retired Internal/External filter tabs are gone.
 */
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach } from 'vitest';

import { ProjectsListView } from './ProjectsListView';
import * as svc from '../services/projectsService';
import type { Project } from '../types';

vi.mock('react-i18next', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-i18next')>();
  return {
    ...actual,
    useTranslation: () => ({
      t: (key: string, fallback?: string | Record<string, unknown>) =>
        typeof fallback === 'string' ? fallback : key,
    }),
  };
});

vi.mock('../contexts/TeamContext', () => ({
  useTeamContext: () => ({ selectedTeamId: null, personalTeamId: 'personal-1' }),
}));

vi.mock('./Toast', () => ({ useToast: () => ({ addToast: vi.fn() }) }));

vi.mock('../services/projectsService', async () => {
  const actual = await vi.importActual<typeof import('../services/projectsService')>(
    '../services/projectsService',
  );
  return {
    ...actual,
    fetchProjectSuggestions: vi.fn().mockResolvedValue([]),
    updateProject: vi.fn(),
    deleteProject: vi.fn(),
  };
});

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

const PROJECTS = [
  makeProject({ id: '1', name: 'Spring Campaign', project_type: 'internal' }),
  makeProject({ id: '2', name: 'Client Reel', project_type: 'external' }),
];

const noop = () => {};

describe('ProjectsListView', () => {
  beforeEach(() => {
    localStorage.clear();
    vi.mocked(svc.fetchProjectSuggestions).mockResolvedValue([]);
  });

  it('defaults to Queue view when no stored preference exists', async () => {
    render(
      <ProjectsListView
        projects={PROJECTS}
        onProjectSelect={noop}
        onCreateProject={noop}
      />,
    );
    await waitFor(() => expect(svc.fetchProjectSuggestions).toHaveBeenCalled());
    expect(screen.getByTestId('home-view-queue-btn')).toBeTruthy();
    // Grid cards are not rendered while in queue view.
    expect(screen.queryByTestId('project-card')).toBeNull();
  });

  it('toggling to Grid persists the choice to localStorage and renders cards', async () => {
    render(
      <ProjectsListView
        projects={PROJECTS}
        onProjectSelect={noop}
        onCreateProject={noop}
      />,
    );
    await waitFor(() => expect(svc.fetchProjectSuggestions).toHaveBeenCalled());

    fireEvent.click(screen.getByTestId('home-view-grid-btn'));

    expect(localStorage.getItem('mediahub.projects.view')).toBe('grid');
    expect(screen.getAllByTestId('project-card')).toHaveLength(2);
    expect(screen.getByText('Spring Campaign')).toBeTruthy();
    expect(screen.getByText('Client Reel')).toBeTruthy();
  });

  it('restores Grid view from a stored preference on mount', async () => {
    localStorage.setItem('mediahub.projects.view', 'grid');
    render(
      <ProjectsListView
        projects={PROJECTS}
        onProjectSelect={noop}
        onCreateProject={noop}
      />,
    );
    await waitFor(() => expect(svc.fetchProjectSuggestions).toHaveBeenCalled());
    expect(screen.getAllByTestId('project-card')).toHaveLength(2);
  });

  it('does not render the retired Internal/External filter tabs', async () => {
    render(
      <ProjectsListView
        projects={PROJECTS}
        onProjectSelect={noop}
        onCreateProject={noop}
      />,
    );
    await waitFor(() => expect(svc.fetchProjectSuggestions).toHaveBeenCalled());
    fireEvent.click(screen.getByTestId('home-view-grid-btn'));

    // The old FilterTab strip rendered these as <button> elements; the card
    // type badges below render the same words as plain <span>s, so scope
    // the assertion to the button role to avoid a false negative there.
    expect(screen.queryByRole('button', { name: 'Internal' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'External' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'All Projects' })).toBeNull();
  });
});
