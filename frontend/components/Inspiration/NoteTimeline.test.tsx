import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_k: string, fallback: string) => fallback }),
}));
vi.mock('./NoteCard', () => ({
  NoteCard: ({ note }: { note: any }) => <div data-testid={`note-${note.id}`}>{note.content_md}</div>,
}));
vi.mock('../../utils/formatDate', () => ({
  formatDateShort: (date: string) => date,
  formatDateOnlyShort: (date: string) => date,
}));

import { NoteTimeline } from './NoteTimeline';
import type { InspirationNote } from '../../services/inspirationService';

const N = (id: string, over: Partial<InspirationNote> = {}): InspirationNote => ({
  id,
  content_md: `note-${id}`,
  tags: [],
  ref_hotspot: null,
  pinned: false,
  rating: 0,
  note_date: '2026-07-09',
  created_at: '2026-07-09T09:42:00+00:00',
  updated_at: '2026-07-09T09:42:00+00:00',
  attachments: [],
  ...over,
});

const handlers = {
  onEdit: vi.fn(),
  onTogglePin: vi.fn(),
  onDelete: vi.fn(),
  onTagClick: vi.fn(),
};

describe('NoteTimeline', () => {
  it('pinned notes float into a Pinned group above day groups', () => {
    const notes = [N('3', { pinned: false }), N('2', { pinned: true }), N('1', { pinned: false })];
    render(
      <NoteTimeline
        notes={notes}
        {...handlers}
        hasMore={false}
        loading={false}
        loadMore={vi.fn()}
      />,
    );
    const headings = screen.getAllByText(/Pinned|2026-07-09/i);
    expect(headings[0].textContent).toMatch(/Pinned/i);
  });

  it('empty with filters shows no-matching copy', () => {
    render(
      <NoteTimeline
        notes={[]}
        {...handlers}
        filtered
        hasMore={false}
        loading={false}
        loadMore={vi.fn()}
      />,
    );
    expect(screen.getByText(/no matching notes/i)).toBeTruthy();
  });

  it('empty without filters keeps the capture-first copy', () => {
    render(
      <NoteTimeline
        notes={[]}
        {...handlers}
        hasMore={false}
        loading={false}
        loadMore={vi.fn()}
      />,
    );
    expect(screen.getByText(/capture your first idea/i)).toBeTruthy();
  });
});
