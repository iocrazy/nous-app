import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { NewIssueDialog } from './NewIssueDialog';
import type { AgentRef } from './types';

const AGENTS: AgentRef[] = [];

function fillTitleAndSubmit(title: string) {
  fireEvent.change(screen.getByPlaceholderText('Issue title'), { target: { value: title } });
  fireEvent.click(screen.getByRole('button', { name: /Create/i }));
}

describe('NewIssueDialog project linkage', () => {
  it('locked mode shows the project read-only and stamps project_id on submit', async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(
      <NewIssueDialog
        agents={AGENTS}
        teamId={42}
        lockedProjectId="99887766554433"
        lockedProjectName="Neon Short"
        onClose={vi.fn()}
        onSubmit={onSubmit}
      />,
    );
    // The project chip is read-only text, not a picker.
    expect(screen.getByText('Neon Short')).toBeTruthy();
    expect(screen.queryByRole('option', { name: 'No project' })).toBeNull();

    fillTitleAndSubmit('Cut the teaser');
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    expect(onSubmit.mock.calls[0][0]).toMatchObject({
      title: 'Cut the teaser',
      project_id: '99887766554433',
      team_id: 42,
    });
  });

  it('picker mode submits the chosen project id', async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(
      <NewIssueDialog
        agents={AGENTS}
        teamId={42}
        projects={[
          { id: '111', name: 'Alpha' },
          { id: '222', name: 'Beta' },
        ]}
        onClose={vi.fn()}
        onSubmit={onSubmit}
      />,
    );
    // Selecting a project routes it into the payload.
    fireEvent.change(screen.getByDisplayValue('No project'), { target: { value: '222' } });
    fillTitleAndSubmit('Draft outline');
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    expect(onSubmit.mock.calls[0][0].project_id).toBe('222');
  });

  it('omits project_id when none is chosen', async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(
      <NewIssueDialog
        agents={AGENTS}
        teamId={42}
        projects={[{ id: '111', name: 'Alpha' }]}
        onClose={vi.fn()}
        onSubmit={onSubmit}
      />,
    );
    fillTitleAndSubmit('No project task');
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    expect(onSubmit.mock.calls[0][0].project_id).toBeUndefined();
  });
});
