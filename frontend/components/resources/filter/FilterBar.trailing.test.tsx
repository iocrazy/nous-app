/**
 * FilterBar — the `trailing` slot.
 *
 * Search-only increments (the vector search Layer / Sort / legs chips) ride
 * in the existing chip row instead of adding a toolbar row, so the bar has to
 * mount whatever the caller passes AFTER its own controls — and nothing when
 * the caller passes nothing.
 */
import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_k: string, def?: string) => def ?? _k }),
}));
vi.mock('../../../contexts/ResourcesContext', () => ({
  useResourcesContext: () => ({ refreshTags: vi.fn() }),
}));
vi.mock('../../../services/unifiedTagService', () => ({ mergeTags: vi.fn() }));

import { FilterBar } from './FilterBar';

const config = {
  pinnedChips: [],
  availableChips: [],
  chipValues: {},
  hasActiveFilters: true,
  pinChip: vi.fn(),
  unpinChip: vi.fn(),
  reorderChips: vi.fn(),
  setChipValue: vi.fn(),
  clearChip: vi.fn(),
  clearAll: vi.fn(),
  isChipActive: () => false,
} as any;

describe('FilterBar — trailing slot', () => {
  it('renders the trailing node after Clear all', () => {
    render(<FilterBar config={config} allTags={[]} trailing={<span data-testid="tail">tail</span>} />);
    const bar = screen.getByTestId('resources-filter-bar');
    const tail = screen.getByTestId('tail');
    expect(bar.contains(tail)).toBe(true);
    const clearAll = screen.getByTitle('Clear all filters');
    // DOCUMENT_POSITION_FOLLOWING: the slot comes after the bar's own controls.
    expect(clearAll.compareDocumentPosition(tail) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('renders nothing extra without trailing', () => {
    render(<FilterBar config={config} allTags={[]} />);
    expect(screen.queryByTestId('tail')).toBeNull();
  });
});
