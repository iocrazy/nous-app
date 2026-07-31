import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

const useRouteError = vi.fn();
vi.mock('react-router-dom', () => ({ useRouteError: () => useRouteError() }));

const forceFreshReload = vi.fn();
vi.mock('../utils/staleChunkReload', async (importOriginal) => {
  const real = await importOriginal<typeof import('../utils/staleChunkReload')>();
  return { ...real, forceFreshReload: (...a: unknown[]) => forceFreshReload(...a) };
});

import RouterErrorPage from './RouterErrorPage';

describe('RouterErrorPage', () => {
  beforeEach(() => forceFreshReload.mockClear());

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
});
