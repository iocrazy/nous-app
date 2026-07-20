/**
 * ProjectsPage — Recent view wiring (feature/projects-recent-view fix wave 1).
 *
 * Two findings under test:
 *
 * 1. Cross-team navigation: `GET /projects/recent-items` is owner-scoped
 *    across EVERY team the user belongs to, but the page only knows the
 *    CURRENTLY active team from the URL. `handleRecentSelect` must build
 *    the target URL from the item's OWN `team_id` (falling back to the
 *    personal team for a personal project), never the page's `teamId` —
 *    otherwise clicking a recent item from a different team silently opens
 *    the wrong (or a 404) workspace.
 * 2. Eager count: the sidebar "Recent" count must be correct on first
 *    render, not just after the user has clicked into the Recent view —
 *    so `fetchRecentItems` must fire on mount, not only on activation.
 */
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import type { RecentItem } from '../types';

const navigateMock = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return { ...actual, useNavigate: () => navigateMock };
});

vi.mock('../contexts/AuthContext', () => ({
  useAuth: () => ({ currentUserId: 'user-1' }),
}));

vi.mock('../contexts/TeamContext', () => ({
  useTeamContext: () => ({
    selectedTeamId: 'team-A',
    personalTeamId: 'personal-team-1',
  }),
}));

const fetchProjects = vi.fn();
const fetchRecentItems = vi.fn();
vi.mock('../services/projectsService', () => ({
  fetchProjects: (...args: unknown[]) => fetchProjects(...args),
  fetchRecentItems: (...args: unknown[]) => fetchRecentItems(...args),
}));

// ProjectFilterSidebar: render its projectCounts.recent (so the "eager
// count" assertion can read it straight off the DOM) and a button that
// flips the page into the Recent view.
vi.mock('../components/project/ProjectFilterSidebar', () => ({
  WORKFLOW_TEMPLATES_FILTER: 'workflow-templates',
  IDEATION_FILTER: 'ideation',
  ProjectFilterSidebar: (props: any) => (
    <div>
      <span data-testid="recent-count">{props.projectCounts.recent}</span>
      <button onClick={() => props.onFilterChange('recent')}>Recent</button>
    </div>
  ),
}));

vi.mock('../components/project/RecentItemsList', () => ({
  RecentItemsList: (props: any) => (
    <div>
      {props.items.map((item: RecentItem) => (
        <button key={`${item.kind}-${item.id}`} onClick={() => props.onSelect(item)}>
          {item.name}
        </button>
      ))}
    </div>
  ),
}));

vi.mock('../components/ProjectsListView', () => ({
  ProjectsListView: () => <div data-testid="projects-list-view" />,
}));
vi.mock('../components/VideoReviewPage', () => ({ VideoReviewPage: () => null }));
vi.mock('../components/CreateProjectModal', () => ({ CreateProjectModal: () => null }));
vi.mock('../components/workspace/ProjectWorkspace', () => ({
  ProjectWorkspace: () => <div data-testid="project-workspace" />,
}));

import { ProjectsPage } from './ProjectsPage';

const scriptItem: RecentItem = {
  kind: 'script',
  id: 's1',
  name: 'Episode 1',
  project_id: '500',
  project_name: 'Other Team Show',
  team_id: 'team-B', // DIFFERENT from the active page team ('team-A')
  updated_at: '2026-07-05T00:00:00+00:00',
};

const canvasItem: RecentItem = {
  kind: 'canvas',
  id: 'c1',
  name: 'Board A',
  project_id: '600',
  project_name: 'Personal Board',
  team_id: null, // personal project — falls back to the personal team
  updated_at: '2026-07-04T00:00:00+00:00',
};

describe('ProjectsPage — Recent view wiring', () => {
  beforeEach(() => {
    navigateMock.mockReset();
    fetchProjects.mockReset().mockResolvedValue([]);
    fetchRecentItems.mockReset().mockResolvedValue([scriptItem, canvasItem]);
  });

  it('fetches recent items eagerly on mount, before the Recent view is activated', async () => {
    render(
      <MemoryRouter initialEntries={['/team/team-A/projects']}>
        <Routes>
          <Route path="/team/:teamId/projects" element={<ProjectsPage />} />
        </Routes>
      </MemoryRouter>
    );

    await waitFor(() => expect(fetchRecentItems).toHaveBeenCalled());
    // The sidebar count must reflect the fetched items WITHOUT the user
    // ever clicking into the Recent view.
    await waitFor(() => expect(screen.getByTestId('recent-count').textContent).toBe('2'));
  });

  it('navigates a cross-team script item to ITS OWN team, not the active page team', async () => {
    render(
      <MemoryRouter initialEntries={['/team/team-A/projects']}>
        <Routes>
          <Route path="/team/:teamId/projects" element={<ProjectsPage />} />
        </Routes>
      </MemoryRouter>
    );

    await waitFor(() => expect(fetchRecentItems).toHaveBeenCalled());
    fireEvent.click(await screen.findByText('Recent'));
    fireEvent.click(await screen.findByText('Episode 1'));

    expect(navigateMock).toHaveBeenCalledWith('/team/team-B/projects/500?module=script');
    expect(navigateMock).not.toHaveBeenCalledWith(expect.stringContaining('/team/team-A/'));
  });

  it('navigates a personal (null team_id) canvas item to the personal team', async () => {
    render(
      <MemoryRouter initialEntries={['/team/team-A/projects']}>
        <Routes>
          <Route path="/team/:teamId/projects" element={<ProjectsPage />} />
        </Routes>
      </MemoryRouter>
    );

    await waitFor(() => expect(fetchRecentItems).toHaveBeenCalled());
    fireEvent.click(await screen.findByText('Recent'));
    fireEvent.click(await screen.findByText('Board A'));

    expect(navigateMock).toHaveBeenCalledWith('/team/personal-team-1/canvas/c1');
  });
});
