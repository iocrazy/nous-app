/**
 * Legacy storyboard cutover — frontend smoke (Phase B P4).
 *
 * The old workbench is retired: the project Storyboard tab now shows a migration
 * notice that routes to the Scripts tab, and the fullscreen workbench route
 * redirects (with a toast) to the same place. Both keep the user moving toward
 * where storyboarding now lives — the per-scene shot board in the script editor.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';

const navigate = vi.fn();
vi.mock('react-router-dom', async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>();
  return {
    ...actual,
    useNavigate: () => navigate,
    useParams: () => ({ teamId: 't1', projectId: 'p1' }),
  };
});

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}));

const addToast = vi.fn();
vi.mock('../Toast', () => ({
  useToast: () => ({ addToast }),
  ToastProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

import { ProjectStoryboardTab } from './ProjectStoryboardTab';
import { StoryboardWorkbench } from '../../pages/StoryboardWorkbench';

beforeEach(() => {
  navigate.mockClear();
  addToast.mockClear();
  cleanup();
});

describe('legacy storyboard cutover (frontend)', () => {
  it('project Storyboard tab shows the moved notice and routes to Scripts', () => {
    render(<ProjectStoryboardTab projectId="p1" />);
    expect(screen.getByTestId('storyboard-moved-notice')).toBeTruthy();
    fireEvent.click(screen.getByTestId('storyboard-go-to-scripts'));
    expect(navigate).toHaveBeenCalledWith('/team/t1/projects/p1?tab=scripts');
  });

  it('workbench route redirects to Scripts and toasts that it moved', () => {
    render(<StoryboardWorkbench />);
    expect(addToast).toHaveBeenCalledWith('projects.storyboardMoved.toast', 'info');
    expect(navigate).toHaveBeenCalledWith('/team/t1/projects/p1?tab=scripts', {
      replace: true,
    });
  });
});
