/**
 * RecentItemsList — rows render a kind icon + name + project name; clicking a
 * row calls onSelect with that item; empty list shows the empty state.
 */
import { render, screen, fireEvent } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { RecentItemsList } from './RecentItemsList';
import type { RecentItem } from '../../types';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, d?: string) => d ?? k }),
}));
vi.mock('../../utils/relativeTime', () => ({ formatRelativeTime: () => '2h ago' }));

const items: RecentItem[] = [
  { kind: 'script', id: 's1', name: 'Episode 1', project_id: '10', project_name: 'My Show', updated_at: '2026-07-05T00:00:00+00:00' },
  { kind: 'canvas', id: 'c1', name: 'Board A', project_id: '10', project_name: 'My Show', updated_at: '2026-07-04T00:00:00+00:00' },
];

describe('RecentItemsList', () => {
  it('renders one row per item with name + project name + icon', () => {
    render(<RecentItemsList items={items} onSelect={vi.fn()} />);
    const rows = screen.getAllByTestId('recent-row');
    expect(rows).toHaveLength(2);
    expect(screen.getByText('Episode 1')).toBeTruthy();
    expect(screen.getByText('Board A')).toBeTruthy();
    expect(screen.getAllByTestId('recent-project')[0].textContent).toBe('My Show');
    expect(screen.getAllByTestId('recent-icon')).toHaveLength(2);
  });

  it('calls onSelect with the clicked item', () => {
    const onSelect = vi.fn();
    render(<RecentItemsList items={items} onSelect={onSelect} />);
    fireEvent.click(screen.getAllByTestId('recent-row')[1]);
    expect(onSelect).toHaveBeenCalledWith(items[1]);
  });

  it('shows the empty state when there are no items', () => {
    render(<RecentItemsList items={[]} onSelect={vi.fn()} />);
    expect(screen.getByTestId('recent-empty')).toBeTruthy();
    expect(screen.getByText('No recent items')).toBeTruthy();
  });
});
