// NoteCard's 0-5 star rating (mig 448). Reuses the shared RatingStars from
// detail/DetailCardKit — the same component the resource library rates with —
// so this file asserts the WIRING (value in, callback out), not the stars.
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';

vi.mock('../../services/inspirationService', () => ({
  attachmentUrlWithToken: (id: string) => `http://api.test/att/${id}`,
}));
vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ mediaToken: 'tok' }),
}));
vi.mock('./NoteMarkdown', () => ({
  NoteMarkdown: ({ source }: { source: string }) => <div data-testid="md">{source}</div>,
}));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_k: string, fallback: string) => fallback }),
}));

import { NoteCard } from './NoteCard';
import type { InspirationNote } from '../../services/inspirationService';

// Real wire shape: NoteOut gives id as a string, rating as a number.
const note = (over: Partial<InspirationNote> = {}): InspirationNote => ({
  id: '1',
  content_md: 'hello',
  tags: [],
  ref_hotspot: null,
  pinned: false,
  rating: 0,
  note_date: '2026-07-07',
  created_at: '2026-07-07T09:42:00+00:00',
  updated_at: '2026-07-07T09:42:00+00:00',
  attachments: [],
  ...over,
});

const stars = () => within(screen.getByLabelText('Rating')).getAllByRole('button');

const base = {
  onEdit: vi.fn(),
  onTogglePin: vi.fn(),
  onDelete: vi.fn(),
  onTagClick: vi.fn(),
};

describe('NoteCard rating', () => {
  it('renders five stars when onRating is provided', () => {
    render(<NoteCard note={note()} {...base} onRating={vi.fn()} />);
    expect(stars()).toHaveLength(5);
  });

  it('reports the clicked star back with the note', () => {
    const onRating = vi.fn();
    const n = note();
    render(<NoteCard note={n} {...base} onRating={onRating} />);
    fireEvent.click(stars()[3]);
    expect(onRating).toHaveBeenCalledWith(n, 4);
  });

  it('clicking the current rating clears it to 0', () => {
    // Clearing must reach the handler as a real 0, not as "nothing happened" —
    // the whole backend `is not None` chain exists to keep this value alive.
    const onRating = vi.fn();
    const n = note({ rating: 3 });
    render(<NoteCard note={n} {...base} onRating={onRating} />);
    fireEvent.click(stars()[2]);
    expect(onRating).toHaveBeenCalledWith(n, 0);
  });

  it('renders a note whose rating is missing without crashing', () => {
    // Pre-448 rows / partially-typed fixtures: treat as unrated, do not throw.
    const n = { ...note() } as InspirationNote & { rating?: number };
    delete n.rating;
    render(<NoteCard note={n as InspirationNote} {...base} onRating={vi.fn()} />);
    expect(stars()).toHaveLength(5);
  });

  it('omits the stars entirely when the page passes no onRating', () => {
    render(<NoteCard note={note()} {...base} />);
    expect(screen.queryByLabelText('Rating')).toBeNull();
  });
  it('exposes the rating group to the accessibility tree, not just to the DOM', () => {
    render(<NoteCard note={note()} {...base} onRating={vi.fn()} />);
    // Same reason as the composer's: role=generic forbids aria-label, so
    // without an explicit role this name exists only for the test runner.
    expect(screen.getByRole('group', { name: 'Rating' })).toBeTruthy();
  });
});
