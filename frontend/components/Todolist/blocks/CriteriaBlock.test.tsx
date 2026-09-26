import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { criteriaBlock } from './CriteriaBlock';
import type { IssueBlockContext } from '../issueBlocks';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string, fallback?: string) => fallback ?? key }),
}));
const updateIssue = vi.fn(async (..._args: unknown[]) => ({}));
vi.mock('../../../services/issuesService', () => ({ updateIssue: (...a: unknown[]) => updateIssue(...a) }));

afterEach(cleanup);
const View = criteriaBlock.component;

function ctx(raw: Record<string, unknown>): IssueBlockContext {
  return { issue: { id: '5', raw }, rollup: null, originKind: null, phase: null, env: { onIssueChanged: vi.fn() } };
}

describe('CriteriaBlock', () => {
  it('shows the criteria and the agent tag', () => {
    render(<View ctx={ctx({ acceptance_criteria: 'Two shots per scene', acceptance_criteria_source: 'agent' })} />);
    expect(screen.getByTestId('criteria-text').textContent).toBe('Two shots per scene');
    expect(screen.getByTestId('criteria-source').textContent).toBe('Proposed by agent');
  });

  it('shows the empty hint without criteria', () => {
    render(<View ctx={ctx({})} />);
    expect(screen.getByTestId('criteria-empty')).toBeTruthy();
  });

  it('saves an edit through PATCH', async () => {
    render(<View ctx={ctx({ acceptance_criteria: 'old', acceptance_criteria_source: 'user' })} />);
    fireEvent.click(screen.getByText('Edit'));
    fireEvent.change(screen.getByTestId('criteria-input'), { target: { value: 'new' } });
    fireEvent.click(screen.getByText('Save'));
    await waitFor(() => expect(updateIssue).toHaveBeenCalledWith('5', { acceptance_criteria: 'new' }));
  });

  it('clears through the flag', async () => {
    render(<View ctx={ctx({ acceptance_criteria: 'old', acceptance_criteria_source: 'user' })} />);
    fireEvent.click(screen.getByText('Edit'));
    fireEvent.click(screen.getByText('Clear'));
    await waitFor(() => expect(updateIssue).toHaveBeenCalledWith('5', { clear_acceptance_criteria: true }));
  });
});
