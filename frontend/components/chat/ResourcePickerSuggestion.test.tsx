import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ResourcePickerSuggestion } from './ResourcePickerSuggestion';
import type { ResourceSearchResult } from '../../types';

const ROWS: ResourceSearchResult[] = [
  { id: '1', name: 'story.md', kind: 'doc', mime: 'text/markdown', size: 100,
    scope: { type: 'personal', id: 'u' }, updated_at: '2026-05-24T00:00:00Z',
    thumbnail_url: null },
  { id: '2', name: 'pitch.mp4', kind: 'video', mime: 'video/mp4', size: 18000000,
    scope: { type: 'team', id: 't1' }, updated_at: '2026-05-20T00:00:00Z',
    thumbnail_url: null },
];

describe('ResourcePickerSuggestion', () => {
  it('renders rows with name + scope + relative-time', () => {
    const onSelect = vi.fn();
    render(<ResourcePickerSuggestion items={ROWS} query="" loading={false}
                                      counts={{ all: 2, video: 1, image: 0, doc: 1, audio: 0, pdf: 0 }}
                                      activeKind="" onKindChange={() => {}} onSelect={onSelect} />);
    expect(screen.getByText('story.md')).toBeInTheDocument();
    expect(screen.getByText('pitch.mp4')).toBeInTheDocument();
    expect(screen.getByText(/personal/i)).toBeInTheDocument();
  });

  it('calls onSelect when row clicked', () => {
    const onSelect = vi.fn();
    render(<ResourcePickerSuggestion items={ROWS} query="" loading={false}
                                      counts={{ all: 2, video: 1, image: 0, doc: 1, audio: 0, pdf: 0 }}
                                      activeKind="" onKindChange={() => {}} onSelect={onSelect} />);
    fireEvent.click(screen.getByText('story.md').closest('button')!);
    expect(onSelect).toHaveBeenCalledWith(ROWS[0]);
  });

  it('shows empty state when no items', () => {
    const onSelect = vi.fn();
    render(<ResourcePickerSuggestion items={[]} query="xyz" loading={false}
                                      counts={{ all: 0, video: 0, image: 0, doc: 0, audio: 0, pdf: 0 }}
                                      activeKind="" onKindChange={() => {}} onSelect={onSelect} />);
    // Accept either the resolved English string OR the raw i18n key (no
    // i18next instance is initialized in unit tests, so useTranslation
    // returns the key verbatim).
    expect(
      screen.getByText(/no resources match|chat\.mentionPicker\.noResults/i),
    ).toBeInTheDocument();
  });
});
