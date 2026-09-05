/**
 * Chat ⇄ Trajectory over the same messages: one run block per assistant turn
 * with a run id, and flipping the view never refetches a settled run.
 */
import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { AIChatMessage } from '../../types';
import { __clearRunToolActivityCache } from '../agentActivity/useRunToolActivity';
import { ChatTrajectoryView } from './ChatTrajectoryView';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string, def?: unknown) => (typeof def === 'string' ? def : key) }),
}));

const getRunEvents = vi.fn();
vi.mock('../../services/aiLibraryService', () => ({
  aiLibraryService: { getRunEvents: (...args: unknown[]) => getRunEvents(...args) },
}));

const msg = (id: string, role: 'user' | 'assistant', runId?: string): AIChatMessage =>
  ({
    id,
    session_id: 's',
    role,
    content: 'x',
    created_at: '2026-09-05T00:00:00Z',
    metadata_json: runId ? { run_id: runId } : undefined,
  }) as unknown as AIChatMessage;

beforeEach(() => {
  getRunEvents.mockReset();
  __clearRunToolActivityCache();
  getRunEvents.mockResolvedValue({
    items: [
      { seq: 1, event_type: 'step_start', payload: { turn: 1, step: 1 }, created_at: '', turn: 1, step: 1 },
      { seq: 2, event_type: 'tool_call', payload: { tool: 'ReadScene', iteration: 1 }, created_at: '' },
      { seq: 3, event_type: 'step_end', payload: { turn: 1, step: 1, duration_ms: 900 }, created_at: '', turn: 1, step: 1 },
    ],
    count: 3,
  });
});

describe('ChatTrajectoryView', () => {
  it('renders one run block per assistant turn with a run id, skipping user turns and run-less replies', async () => {
    render(
      <ChatTrajectoryView
        messages={[msg('u1', 'user'), msg('a1', 'assistant', '701'), msg('a2', 'assistant'), msg('a3', 'assistant', '702')]}
        isRunning={false}
      />,
    );
    const blocks = await screen.findAllByTestId('chat-run-block');
    expect(blocks.map((b) => b.getAttribute('data-run-id'))).toEqual(['701', '702']);
    expect(getRunEvents).toHaveBeenCalledTimes(2);
    // the folded step shows up in each block
    await screen.findAllByTestId('traj-step');
  });

  it('flipping the view does not refetch a settled run (cache), and says so when there are no runs', async () => {
    const view = render(<ChatTrajectoryView messages={[msg('a1', 'assistant', '701')]} isRunning={false} />);
    await screen.findByTestId('chat-run-block');
    expect(getRunEvents).toHaveBeenCalledTimes(1);
    view.unmount();
    render(<ChatTrajectoryView messages={[msg('a1', 'assistant', '701')]} isRunning={false} />);
    await screen.findByTestId('chat-run-block');
    expect(getRunEvents).toHaveBeenCalledTimes(1);
    view.unmount();
    render(<ChatTrajectoryView messages={[msg('u1', 'user')]} isRunning={false} />);
    expect(screen.getByText('No agent runs in this conversation yet')).toBeTruthy();
  });
});
