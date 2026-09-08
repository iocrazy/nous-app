/**
 * Phase 2a §4 — Task Center "Paused" section: what a person paused, with a
 * Resume per row. Empty == zero render (same rule as the attention strip).
 */
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { PausedSection } from './PausedSection';
import { IssueControlError } from '../../services/issuesService';
import { notifyIssuePauseChanged } from './issuePauseSignal';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string, fallback?: string) => fallback ?? key }),
}));
const listPaused = vi.fn();
const resumeIssue = vi.fn();
vi.mock('../../services/issuesService', async (importOriginal) => {
  const mod = await importOriginal<typeof import('../../services/issuesService')>();
  return { ...mod, listPaused: () => listPaused(), resumeIssue: (...a: unknown[]) => resumeIssue(...a) };
});

afterEach(cleanup);

// Wire shape of GET /issues/paused: string ids (the router str()s them).
const ROW = { issue_id: '5', identifier: 'MH-5', title: 'Paused with mail', paused_at: '2026-09-08T00:00:00Z', team_id: '8', project_id: null };

beforeEach(() => {
  listPaused.mockReset();
  resumeIssue.mockReset().mockResolvedValue({ issue_id: '5', dispatched: true, reason: 'dispatched', workflow_id: 'wf', run_id: null });
});

describe('PausedSection', () => {
  it('renders nothing when nothing is paused', async () => {
    listPaused.mockResolvedValue({ items: [], has_more: false });
    const { container } = render(<PausedSection />);
    await waitFor(() => expect(listPaused).toHaveBeenCalled());
    expect(container.firstChild).toBeNull();
  });

  it('lists paused issues and resumes one, dropping the row once the refetch no longer returns it', async () => {
    listPaused.mockResolvedValueOnce({ items: [ROW], has_more: false }).mockResolvedValueOnce({ items: [], has_more: false });
    render(<PausedSection />);
    expect(await screen.findByText('Paused with mail')).toBeTruthy();
    fireEvent.click(screen.getByTestId('paused-resume'));
    await waitFor(() => expect(resumeIssue).toHaveBeenCalledWith(5));
    await waitFor(() => expect(screen.queryByTestId('paused-row')).toBeNull());
  });

  it('keeps the row and shows the failure when resume is rejected', async () => {
    listPaused.mockResolvedValue({ items: [ROW], has_more: false });
    resumeIssue.mockRejectedValueOnce(new IssueControlError('not_paused', 409, 'issue is not paused'));
    render(<PausedSection />);
    await screen.findByText('Paused with mail');
    fireEvent.click(screen.getByTestId('paused-resume'));
    expect(await screen.findByText('Not paused any more')).toBeTruthy();
    expect(screen.getByTestId('paused-row')).toBeTruthy();
  });

  it('says 50+ when the server flags overflow', async () => {
    listPaused.mockResolvedValue({ items: [ROW], has_more: true });
    render(<PausedSection />);
    await screen.findByText('Paused with mail');
    expect(screen.getByTestId('paused-count').textContent).toBe('1+');
  });

  it('refetches at once when a pause is signalled from elsewhere', async () => {
    listPaused.mockResolvedValueOnce({ items: [], has_more: false }).mockResolvedValueOnce({ items: [ROW], has_more: false });
    render(<PausedSection />);
    await waitFor(() => expect(listPaused).toHaveBeenCalledTimes(1));
    notifyIssuePauseChanged(5);
    expect(await screen.findByText('Paused with mail')).toBeTruthy();
    expect(listPaused).toHaveBeenCalledTimes(2);
  });
});
