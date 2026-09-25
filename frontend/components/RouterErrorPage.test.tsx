import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

const useRouteError = vi.fn();
vi.mock('react-router-dom', () => ({ useRouteError: () => useRouteError() }));

const forceFreshReload = vi.fn();
vi.mock('../utils/staleChunkReload', async (importOriginal) => {
  const real = await importOriginal<typeof import('../utils/staleChunkReload')>();
  return { ...real, forceFreshReload: (...a: unknown[]) => forceFreshReload(...a) };
});

const reportError = vi.fn();
vi.mock('../services/errorReporter', () => ({
  reportError: (...a: unknown[]) => reportError(...a),
}));

import RouterErrorPage from './RouterErrorPage';

describe('RouterErrorPage', () => {
  beforeEach(() => {
    forceFreshReload.mockClear();
    reportError.mockClear();
  });

  it('shows the new-version copy for a stale chunk error and refreshes via forceFreshReload', () => {
    useRouteError.mockReturnValue(
      new TypeError('Failed to fetch dynamically imported module: https://x/assets/ResourcesPage-4Givt445.js'),
    );
    render(<RouterErrorPage />);
    expect(screen.getByText('A new version is available')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Refresh Now' }));
    expect(forceFreshReload).toHaveBeenCalled();
    expect(screen.queryByText('Go Home')).toBeNull();
  });

  it('shows the generic copy with the raw message for other errors', () => {
    useRouteError.mockReturnValue(new Error('boom from a loader'));
    render(<RouterErrorPage />);
    expect(screen.getByText('Something went wrong')).toBeInTheDocument();
    expect(screen.getByText('boom from a loader')).toBeInTheDocument();
    expect(screen.getByText('Go Home')).toBeInTheDocument();
  });

  // A render crash that lands here is otherwise invisible: React Router's
  // errorElement is a boundary, so the error never reaches the window 'error'
  // listener that installErrorReporter() hooks, and this page renders outside
  // every provider — nothing else can report it. The 2026-09-25 e2e:prod red
  // ("[tiptap error]: The editor view is not available") left zero rows in
  // frontend_error_logs for exactly this reason.
  it('reports a render crash through the error reporter once, with the page as component', () => {
    const err = new Error('[tiptap error]: The editor view is not available.');
    useRouteError.mockReturnValue(err);
    const { rerender } = render(<RouterErrorPage />);
    expect(reportError).toHaveBeenCalledTimes(1);
    expect(reportError).toHaveBeenCalledWith(err, { type: 'react_boundary', component: 'RouterErrorPage' });
    rerender(<RouterErrorPage />);
    expect(reportError).toHaveBeenCalledTimes(1);
  });

  it('does not report stale-chunk errors (deploy noise, already handled by the refresh CTA)', () => {
    useRouteError.mockReturnValue(
      new TypeError('Failed to fetch dynamically imported module: https://x/assets/ResourcesPage-4Givt445.js'),
    );
    render(<RouterErrorPage />);
    expect(reportError).not.toHaveBeenCalled();
  });
});
