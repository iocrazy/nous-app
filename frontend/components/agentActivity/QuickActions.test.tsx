import { fireEvent, render } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { QuickActions } from './QuickActions';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, fallback?: string) => (typeof fallback === 'string' ? fallback : key),
  }),
}));

describe('QuickActions', () => {
  it('offers selection-scoped chips when context is "selection"', () => {
    const { getByTestId, getAllByTestId } = render(
      <QuickActions context="selection" onPick={vi.fn()} />,
    );
    expect(getByTestId('quick-actions').getAttribute('data-context')).toBe('selection');
    const ids = getAllByTestId('quick-action').map((el) => el.getAttribute('data-action'));
    expect(ids).toEqual(['tighten', 'alternatives', 'toShots']);
  });

  it('offers script-scoped chips when context is "script"', () => {
    const { getAllByTestId } = render(<QuickActions context="script" onPick={vi.fn()} />);
    const ids = getAllByTestId('quick-action').map((el) => el.getAttribute('data-action'));
    expect(ids).toEqual(['listScenes', 'shotList', 'continuity']);
  });

  it('renders nothing when context is "none"', () => {
    const { container } = render(<QuickActions context="none" onPick={vi.fn()} />);
    expect(container.firstChild).toBeNull();
  });

  it('clicking a chip only seeds the composer via onPick, without sending anything else', () => {
    const onPick = vi.fn();
    const { getAllByTestId } = render(<QuickActions context="selection" onPick={onPick} />);
    fireEvent.click(getAllByTestId('quick-action')[0]);
    expect(onPick).toHaveBeenCalledTimes(1);
    expect(onPick).toHaveBeenCalledWith('Tighten this passage without losing its meaning.');
  });
});
