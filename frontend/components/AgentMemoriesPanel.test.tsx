/**
 * AgentMemoriesPanel — "My Agent Memories" user panel tests.
 * Mirrors MemoryPanel.test.tsx structure.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { AgentMemoriesPanel } from './AgentMemoriesPanel';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}));

const mockService = vi.hoisted(() => ({
  listAgentMemories: vi.fn(),
  deleteAgentMemory: vi.fn(),
}));

vi.mock('../services/agentMemoryService', () => mockService);

const ownItem = {
  id: 1,
  title: 'Own Memory Title',
  body_md: 'This is own memory body',
  kind: 'fact',
  scope: 'user',
  visibility: 'private',
  when_to_use: 'Always use this',
  created_at: '2026-01-01T00:00:00Z',
  is_owner: true,
};

const sharedItem = {
  id: 2,
  title: 'Shared Memory Title',
  body_md: 'This is shared memory body',
  kind: 'preference',
  scope: 'team',
  visibility: 'shared',
  when_to_use: 'For team tasks',
  created_at: '2026-01-02T00:00:00Z',
  is_owner: false,
};

describe('AgentMemoriesPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockService.listAgentMemories.mockResolvedValue([ownItem, sharedItem]);
    mockService.deleteAgentMemory.mockResolvedValue(undefined);
  });

  it('renders both own and team-shared memory rows', async () => {
    render(<AgentMemoriesPanel />);
    expect(await screen.findByText('Own Memory Title')).toBeTruthy();
    expect(screen.getByText('Shared Memory Title')).toBeTruthy();
  });

  it('own row has a delete button; team-shared row does not', async () => {
    render(<AgentMemoriesPanel />);
    await screen.findByText('Own Memory Title');
    // Only the own-row trash button should be present
    const deleteButtons = screen.getAllByTitle('agentMemories.delete');
    expect(deleteButtons).toHaveLength(1);
    // Shared row shows read-only note instead
    expect(screen.getByText('agentMemories.sharedByTeammate')).toBeTruthy();
  });

  it('clicking delete shows confirm then calls deleteAgentMemory and removes the row', async () => {
    render(<AgentMemoriesPanel />);
    await screen.findByText('Own Memory Title');

    // Click the Trash2 button on the own row
    fireEvent.click(screen.getByTitle('agentMemories.delete'));

    // Confirm dialog text should appear
    expect(screen.getByText('agentMemories.deleteConfirm')).toBeTruthy();

    // Click the confirm button
    fireEvent.click(screen.getByText('agentMemories.deleteConfirm'));

    await waitFor(() =>
      expect(mockService.deleteAgentMemory).toHaveBeenCalledWith(1),
    );
    expect(screen.queryByText('Own Memory Title')).toBeNull();
  });
});
