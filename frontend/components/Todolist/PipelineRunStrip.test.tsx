/**
 * PipelineRunStrip (W2b) — the active-relay strip and its two-stage cancel.
 *
 * The service module is mocked so the test drives the component's own state:
 * the run load, the arm→confirm→fire cancel handshake, and the post-cancel
 * re-fetch. Snowflake ids stay strings end-to-end.
 */

import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { PipelineRunStrip } from './PipelineRunStrip';
import type { PipelineRun } from '../../services/pipelinesService';
import * as svc from '../../services/pipelinesService';

vi.mock('../../services/pipelinesService', () => ({
  listIssuePipelineRuns: vi.fn(),
  cancelRun: vi.fn(),
}));

const mockList = vi.mocked(svc.listIssuePipelineRuns);
const mockCancel = vi.mocked(svc.cancelRun);

function runningRun(over: Partial<PipelineRun> = {}): PipelineRun {
  return {
    id: '900',
    pipeline_id: '500',
    parent_issue_id: '1000',
    current_step: 2,
    status: 'running',
    halted_reason: null,
    started_by_user_id: null,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    completed_at: null,
    pipeline_name: 'Content Relay',
    total_steps: 3,
    current_agent_id: 'a2',
    ...over,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('PipelineRunStrip cancel', () => {
  it('shows a Cancel run button while the run is running', async () => {
    mockList.mockResolvedValueOnce([runningRun()]);
    render(<PipelineRunStrip issueId="1000" agentsById={{}} />);
    const btn = await screen.findByTestId('pipeline-run-cancel');
    expect(btn).toHaveTextContent('Cancel run');
  });

  it('does not render a cancel button for a terminal run', async () => {
    mockList.mockResolvedValueOnce([runningRun({ status: 'completed' })]);
    render(<PipelineRunStrip issueId="1000" agentsById={{}} />);
    await screen.findByTestId('pipeline-run-strip');
    expect(screen.queryByTestId('pipeline-run-cancel')).toBeNull();
  });

  it('requires two clicks: the first arms confirm, only the second fires', async () => {
    mockList.mockResolvedValue([runningRun()]);
    mockCancel.mockResolvedValueOnce(runningRun({ status: 'cancelled' }));
    render(<PipelineRunStrip issueId="1000" agentsById={{}} />);

    const btn = await screen.findByTestId('pipeline-run-cancel');
    // First click arms — no request yet.
    fireEvent.click(btn);
    expect(screen.getByTestId('pipeline-run-cancel')).toHaveTextContent(
      'Confirm cancel?',
    );
    expect(mockCancel).not.toHaveBeenCalled();

    // Second click fires the cancel with the run's string id.
    fireEvent.click(screen.getByTestId('pipeline-run-cancel'));
    await waitFor(() => expect(mockCancel).toHaveBeenCalledTimes(1));
    expect(mockCancel).toHaveBeenCalledWith('900');
  });

  it('re-fetches after a successful cancel', async () => {
    mockList.mockResolvedValue([runningRun()]);
    mockCancel.mockResolvedValueOnce(runningRun({ status: 'cancelled' }));
    render(<PipelineRunStrip issueId="1000" agentsById={{}} />);

    const btn = await screen.findByTestId('pipeline-run-cancel');
    fireEvent.click(btn); // arm
    fireEvent.click(screen.getByTestId('pipeline-run-cancel')); // fire

    // The local refresh bump triggers a second list load (initial + post-cancel).
    await waitFor(() => expect(mockList).toHaveBeenCalledTimes(2));
  });
});
