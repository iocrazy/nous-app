import { render, screen, cleanup } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { verdictBlock } from './VerdictBlock';
import type { IssueBlockContext } from '../issueBlocks';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string, fallback?: string, opts?: Record<string, unknown>) =>
    (fallback ?? key).replace(/\{\{(\w+)\}\}/g, (_, k) => String(opts?.[k] ?? '')) }),
}));

afterEach(cleanup);
const View = verdictBlock.component;

function ctx(execution_state: Record<string, unknown> | null): IssueBlockContext {
  return { issue: { id: '5', raw: { execution_state } }, rollup: null, originKind: null, phase: null, env: {} };
}

describe('VerdictBlock', () => {
  it('does not match without a verdict', () => {
    expect(verdictBlock.match(ctx(null))).toBe(false);
    expect(verdictBlock.match(ctx({ verification: { verdict: 'pass' } }))).toBe(true);
  });

  it('renders a pass', () => {
    render(<View ctx={ctx({ verification: { verdict: 'pass', attempt: 1, max_attempts: 2, reason: 'criteria_met' }, outcome_reason: 'All done' })} />);
    expect(screen.getByTestId('verdict-label').textContent).toBe('Verified');
    expect(screen.getByTestId('verdict-label').className).toContain('text-ok');
    expect(screen.getByTestId('verdict-claim').textContent).toContain('All done');
  });

  it('renders a rejection with attempt and unmet', () => {
    render(<View ctx={ctx({ verification: { verdict: 'fail', attempt: 1, max_attempts: 2, unmet: [{ criterion: 'two shots', why: 'none' }] } })} />);
    expect(screen.getByTestId('verdict-label').textContent).toBe('Rejected (attempt 1/2)');
    expect(screen.getByTestId('verdict-label').className).toContain('text-danger');
    expect(screen.getByText('two shots: none')).toBeTruthy();
  });

  it('renders unverified with the typed reason', () => {
    render(<View ctx={ctx({ verification: { verdict: 'unverified', attempt: 1, max_attempts: 2, reason: 'verifier_timeout' } })} />);
    expect(screen.getByTestId('verdict-label').textContent).toBe('Unverified: verifier_timeout');
    expect(screen.getByTestId('verdict-label').className).toContain('text-warn');
  });
});
