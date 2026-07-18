import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import type { Tag } from '../../types';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_k: string, f?: string) => f ?? _k }),
}));
// The bar loads its Source list from topicService on mount — stub it out.
vi.mock('../../services/topicService', () => ({
  getSourceHealth: vi.fn().mockResolvedValue([]),
}));

import { TopicFilterBar } from './TopicFilterBar';

const tag = (over: Partial<Tag>): Tag => ({
  id: '1',
  name: 'AI',
  name_zh: null,
  color: '#6366f1',
  icon: null,
  type: 'user',
  origin: 'curated',
  created_at: '',
  ...over,
});

function renderBar(props: Partial<React.ComponentProps<typeof TopicFilterBar>> = {}) {
  const onTagIdsChange = vi.fn();
  render(
    <TopicFilterBar
      view="all"
      onView={vi.fn()}
      category="all"
      onCategory={vi.fn()}
      selectedSources={[]}
      onSources={vi.fn()}
      dates={[]}
      onDay={vi.fn()}
      allTags={[tag({ id: '1', name: 'AI' })]}
      selectedTagIds={[]}
      onTagIdsChange={onTagIdsChange}
      {...props}
    />,
  );
  return { onTagIdsChange };
}

// The chip's toggle button label is "Tag" when inactive and "Tag · <name>"
// once a tag is selected, so open it via a prefix-matching role query.
const openTagChip = () => fireEvent.click(screen.getByRole('button', { name: /^Tag/ }));

describe('TopicFilterBar tag chip', () => {
  it('renders a Tag chip', () => {
    renderBar();
    expect(screen.getByRole('button', { name: /^Tag/ })).toBeTruthy();
  });

  it('selecting a tag adds its id via onTagIdsChange', () => {
    const { onTagIdsChange } = renderBar();
    openTagChip();
    fireEvent.click(screen.getByText('AI'));
    expect(onTagIdsChange).toHaveBeenCalledWith(['1']);
  });

  it('deselecting an already-selected tag removes its id', () => {
    const { onTagIdsChange } = renderBar({ selectedTagIds: ['1'] });
    openTagChip();
    fireEvent.click(screen.getByText('AI'));
    expect(onTagIdsChange).toHaveBeenCalledWith([]);
  });

  it('excludes shadow tags (origin=note) from the option list', () => {
    renderBar({
      allTags: [tag({ id: '1', name: 'AI' }), tag({ id: '9', name: 'shadowword', origin: 'note' })],
    });
    openTagChip();
    expect(screen.getByText('AI')).toBeTruthy();
    expect(screen.queryByText('shadowword')).toBeNull();
  });
});
