import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { CreateProjectModal } from './CreateProjectModal';
import { createProject } from '../services/projectsService';
import { fetchMyTeams } from '../services/teamService';

// t returns the provided default when present, else the key — enough for the
// button/label text these assertions match against.
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, d?: string) => d ?? k }),
}));
vi.mock('../services/projectsService', () => ({ createProject: vi.fn() }));
vi.mock('../services/teamService', () => ({ fetchMyTeams: vi.fn() }));

const asMock = (fn: unknown) => fn as ReturnType<typeof vi.fn>;

describe('CreateProjectModal defaultTeamId', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    asMock(fetchMyTeams).mockResolvedValue([{ id: '42', name: 'Team X' }]);
    asMock(createProject).mockResolvedValue({ id: '1', name: 'My P' });
  });

  it('pre-selects the passed collaborative team and stamps it on create', async () => {
    render(
      <CreateProjectModal
        isOpen
        onClose={() => {}}
        onProjectCreated={() => {}}
        defaultTeamId="42"
      />,
    );
    await waitFor(() => expect(fetchMyTeams).toHaveBeenCalled());

    fireEvent.change(screen.getByTestId('project-name-input'), {
      target: { value: 'My P' },
    });
    fireEvent.click(screen.getByRole('button', { name: /create/i }));

    await waitFor(() =>
      expect(createProject).toHaveBeenCalledWith(
        expect.objectContaining({ team_id: '42' }),
      ),
    );
  });

  it('personal workspace (empty default) creates a team-less project', async () => {
    render(
      <CreateProjectModal
        isOpen
        onClose={() => {}}
        onProjectCreated={() => {}}
        defaultTeamId=""
      />,
    );
    await waitFor(() => expect(fetchMyTeams).toHaveBeenCalled());

    fireEvent.change(screen.getByTestId('project-name-input'), {
      target: { value: 'Solo' },
    });
    fireEvent.click(screen.getByRole('button', { name: /create/i }));

    await waitFor(() =>
      expect(createProject).toHaveBeenCalledWith(
        expect.objectContaining({ team_id: undefined }),
      ),
    );
  });
});
