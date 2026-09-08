/**
 * Phase 2a §4 — the Reason row: why an issue is blocked / stalled, in full.
 */
import { render, screen, cleanup } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { statusBlock } from './StatusBlock';
import type { IssueBlockContext } from '../issueBlocks';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string, fallback?: string) => fallback ?? key }),
}));
vi.mock('../../../services/usageService', () => ({ usageService: { getIssueUsage: vi.fn(async () => null) } }));

afterEach(cleanup);

const StatusBlockView = statusBlock.component;

function ctx(status: string, phase: string, execution_state: Record<string, unknown> | null): IssueBlockContext {
  return {
    issue: { id: '5', status, updated_at: '2026-09-08T00:00:00Z', raw: { status, execution_state } },
    rollup: null,
    originKind: null,
    phase,
    env: {},
  };
}

describe('StatusBlock — Reason row (phase 2a §4)', () => {
  it('shows the full error_message on a blocked issue, untruncated', () => {
    const full = 'Provider returned 429 after 3 retries: ' + 'x'.repeat(300);
    render(<StatusBlockView ctx={ctx('blocked', 'blocked', { error_message: full })} />);
    const row = screen.getByTestId('status-reason');
    expect(row.querySelector('p')!.textContent).toBe(full);
    expect(row.querySelector('p')!.className).toContain('text-danger');
  });

  it('falls back to outcome_reason and uses the warn tone for an empty_output stall', () => {
    render(<StatusBlockView ctx={ctx('in_progress', 'idle', { agent_outcome: 'empty_output', outcome_reason: 'model produced no output' })} />);
    const row = screen.getByTestId('status-reason');
    expect(row.querySelector('p')!.textContent).toBe('model produced no output');
    expect(row.querySelector('p')!.className).toContain('text-warn');
  });

  it('draws no Reason row when the issue is neither blocked nor stalled', () => {
    render(<StatusBlockView ctx={ctx('in_progress', 'running', { error_message: 'stale from an earlier run' })} />);
    expect(screen.queryByTestId('status-reason')).toBeNull();
  });
});
