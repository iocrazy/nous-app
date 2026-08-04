import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

const mockStatus = vi.fn();
vi.mock('./hooks/useModuleStatus', () => ({
  useModuleStatus: (id: string) => mockStatus(id),
}));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_k: string, fb?: string) => fb ?? _k }),
}));

import { GlobalModuleGuard } from './components/GlobalModuleGuard';

describe('GlobalModuleGuard', () => {
  it('renders children when visible', () => {
    mockStatus.mockReturnValue({ enabled: true, visible: true, loading: false });
    render(<GlobalModuleGuard id="shares"><div data-testid="inner" /></GlobalModuleGuard>);
    expect(screen.getByTestId('inner')).toBeTruthy();
  });

  it('renders nothing while loading', () => {
    mockStatus.mockReturnValue({ enabled: true, visible: true, loading: true });
    const { container } = render(
      <GlobalModuleGuard id="shares"><div data-testid="inner" /></GlobalModuleGuard>,
    );
    expect(container.innerHTML).toBe('');
  });

  it('renders disabled page when hidden', () => {
    mockStatus.mockReturnValue({ enabled: true, visible: false, loading: false });
    render(<GlobalModuleGuard id="shares"><div data-testid="inner" /></GlobalModuleGuard>);
    expect(screen.queryByTestId('inner')).toBeNull();
    expect(screen.getByText('Feature Unavailable')).toBeTruthy();
  });
});
