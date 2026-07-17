import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_k: string, f: string) => f }),
}));

import { HotspotCard } from './HotspotCard';

const HS = (over = {}) => ({
  id: '7',
  title: 'Topic 7',
  tags: [],
  source_label: 'WEIBO',
  ...over,
});

describe('HotspotCard children', () => {
  it('renders without children unchanged (legacy callers)', () => {
    const onSelect = vi.fn();
    render(<HotspotCard hotspot={HS()} onSelect={onSelect} />);
    expect(screen.getByText('Topic 7')).toBeTruthy();
    // Card is still clickable/selectable.
    fireEvent.click(screen.getByText('Topic 7'));
    expect(onSelect).toHaveBeenCalledTimes(1);
  });

  it('renders children inside the card root, below the body', () => {
    const { container } = render(
      <HotspotCard hotspot={HS()} onSelect={vi.fn()}>
        <div data-testid="expand">expanded content</div>
      </HotspotCard>,
    );
    const root = container.firstElementChild as HTMLElement;
    const expand = screen.getByTestId('expand');
    // The expansion lives INSIDE the single card container.
    expect(root.contains(expand)).toBe(true);
    expect(screen.getByText('expanded content')).toBeTruthy();
  });

  it('does not toggle the card (onSelect) when clicking inside the children', () => {
    const onSelect = vi.fn();
    render(
      <HotspotCard hotspot={HS()} onSelect={onSelect}>
        <button data-testid="inner">Inner action</button>
      </HotspotCard>,
    );
    fireEvent.click(screen.getByTestId('inner'));
    expect(onSelect).not.toHaveBeenCalled();
  });
});
