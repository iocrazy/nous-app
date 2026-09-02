import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

vi.mock('../../services/inspirationService', () => ({
  attachmentUrlWithToken: (id: string) => `http://api.test/att/${id}`,
}));
vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ mediaToken: 'tok' }),
}));
// NoteMarkdown's real props are `{ source, onToggleTask?, onTagClick? }`
// (verified by reading components/Inspiration/NoteMarkdown.tsx). Tags now render
// as inline chips INSIDE NoteMarkdown, so the mock exposes an onTagClick relay
// button to verify NoteCard threads the filter callback through (the real chip
// rendering is covered by NoteMarkdown's own tests).
vi.mock('./NoteMarkdown', () => ({
  NoteMarkdown: ({ source, onTagClick }: { source: string; onTagClick?: (t: string) => void }) => (
    <div data-testid="md">
      {source}
      {onTagClick && (
        <button data-testid="md-tag" onClick={() => onTagClick('hooks')}>
          inline-chip
        </button>
      )}
    </div>
  ),
}));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_k: string, fallback: string) => fallback }),
}));

import { NoteCard } from './NoteCard';
import type { InspirationNote } from '../../services/inspirationService';

const note = (over: Partial<InspirationNote> = {}): InspirationNote => ({
  id: '1',
  content_md: 'hello #hooks',
  tags: ['hooks'],
  ref_hotspot: null,
  pinned: false,
  rating: 0,
  note_date: '2026-07-07',
  created_at: '2026-07-07T09:42:00+00:00',
  updated_at: '2026-07-07T09:42:00+00:00',
  attachments: [],
  ...over,
});

describe('NoteCard', () => {
  it('renders the markdown body and no separate below-body chip row', () => {
    render(
      <NoteCard note={note()} onEdit={vi.fn()} onTogglePin={vi.fn()} onDelete={vi.fn()} onTagClick={vi.fn()} />,
    );
    expect(screen.getByTestId('md').textContent).toContain('hello #hooks');
    // the old duplicate chip row rendered a standalone `#hooks` button — gone now
    expect(screen.queryByRole('button', { name: '#hooks' })).toBeNull();
  });

  it('threads onTagClick down to NoteMarkdown for inline chips', () => {
    const onTagClick = vi.fn();
    render(
      <NoteCard note={note()} onEdit={vi.fn()} onTogglePin={vi.fn()} onDelete={vi.fn()} onTagClick={onTagClick} />,
    );
    fireEvent.click(screen.getByTestId('md-tag'));
    expect(onTagClick).toHaveBeenCalledWith('hooks');
  });

  it('renders hotspot reference card when present', () => {
    render(
      <NoteCard
        note={note({ ref_hotspot: { title: 'Silent vlog passes 2.1B', source: 'DOUYIN', heat: 98.4, url: 'https://x' } })}
        onEdit={vi.fn()} onTogglePin={vi.fn()} onDelete={vi.fn()} onTagClick={vi.fn()}
      />,
    );
    expect(screen.getByText('Silent vlog passes 2.1B')).toBeTruthy();
    expect(screen.getByText('DOUYIN')).toBeTruthy();
  });

  it('menu exposes edit, pin, delete actions', () => {
    const onDelete = vi.fn();
    render(
      <NoteCard note={note()} onEdit={vi.fn()} onTogglePin={vi.fn()} onDelete={onDelete} onTagClick={vi.fn()} />,
    );
    fireEvent.click(screen.getByLabelText('Note actions'));
    fireEvent.click(screen.getByText('Delete'));
    expect(onDelete).toHaveBeenCalled();
  });
});
