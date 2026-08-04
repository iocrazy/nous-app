import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { CreateProjectModal } from './CreateProjectModal';
import { createProject } from '../services/projectsService';
import { fetchMyTeams } from '../services/teamService';
import { fetchTemplates } from '../services/workflowService';

// t returns the provided default when present, else the key — enough for the
// button/label text these assertions match against.
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, d?: string) => d ?? k }),
}));
vi.mock('../services/projectsService', () => ({ createProject: vi.fn() }));
vi.mock('../services/teamService', () => ({ fetchMyTeams: vi.fn() }));
vi.mock('../services/workflowService', () => ({ fetchTemplates: vi.fn() }));

const asMock = (fn: unknown) => fn as ReturnType<typeof vi.fn>;

describe('CreateProjectModal defaultTeamId', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    asMock(fetchMyTeams).mockResolvedValue([{ id: '42', name: 'Team X' }]);
    asMock(createProject).mockResolvedValue({ id: '1', name: 'My P' });
    asMock(fetchTemplates).mockResolvedValue([]);
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

describe('CreateProjectModal workflow picker (personal workspace)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    asMock(fetchMyTeams).mockResolvedValue([]);
    asMock(createProject).mockResolvedValue({ id: '1', name: 'Solo' });
  });

  it('loads templates for a personal (no-team) project and submits the chosen one', async () => {
    asMock(fetchTemplates).mockResolvedValue([
      { id: 'tpl-1', name: 'Short-form', node_count: 5, is_default: true },
      { id: 'tpl-2', name: 'Long-form', node_count: 11, is_default: false },
    ]);

    render(
      <CreateProjectModal
        isOpen
        onClose={() => {}}
        onProjectCreated={() => {}}
        defaultTeamId=""
      />,
    );

    // fetchTemplates is called with the personal (empty) teamId — the server
    // resolves that to the caller's own personal team.
    await waitFor(() => expect(fetchTemplates).toHaveBeenCalledWith(''));

    // The picker appears without any team selected, defaulted to the
    // is_default template.
    const picker = await screen.findByTestId('create-project-workflow');
    expect(picker).toBeInTheDocument();
    expect(screen.getByTestId('workflow-template-tpl-1')).toBeInTheDocument();

    fireEvent.click(screen.getByTestId('workflow-template-tpl-2'));

    fireEvent.change(screen.getByTestId('project-name-input'), {
      target: { value: 'Solo' },
    });
    // Exact match (not the /create/i regex the other describe block uses) —
    // the "No workflow" sentinel card's untranslated key
    // ("projects.workflow.create.noWorkflow") also contains "create" as a
    // substring under this suite's t-mock, so a substring match is ambiguous
    // once the picker is showing.
    fireEvent.click(screen.getByRole('button', { name: 'common.create' }));

    await waitFor(() =>
      expect(createProject).toHaveBeenCalledWith(
        expect.objectContaining({
          team_id: undefined,
          workflow_template_id: 'tpl-2',
        }),
      ),
    );
  });

  it('renders no workflow block when the personal team has no templates yet', async () => {
    asMock(fetchTemplates).mockResolvedValue([]);

    render(
      <CreateProjectModal
        isOpen
        onClose={() => {}}
        onProjectCreated={() => {}}
        defaultTeamId=""
      />,
    );

    await waitFor(() => expect(fetchTemplates).toHaveBeenCalledWith(''));
    expect(screen.queryByTestId('create-project-workflow')).not.toBeInTheDocument();
  });
});
