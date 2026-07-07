import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

vi.mock('../../services/inspirationService', () => ({
  attachmentUrlWithToken: (id: string) => `http://api.test/att/${id}`,
}));
vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ mediaToken: 'tok' }),
}));
// NoteMarkdown's real props are `{ source: string, onToggleTask?: (index) => void }`
// (verified by reading components/Inspiration/NoteMarkdown.tsx) — the mock mirrors
// that shape and preserves the previous MarkdownBody mock's testid/text contract.
vi.mock('./NoteMarkdown', () => ({
  NoteMarkdown: ({ source }: { source: string }) => <div data-testid="md">{source}</div>,
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
  note_date: '2026-07-07',
  created_at: '2026-07-07T09:42:00+00:00',
  updated_at: '2026-07-07T09:42:00+00:00',
  attachments: [],
  ...over,
});

describe('NoteCard', () => {
  it('renders markdown body and tag chips', () => {
    render(
      <NoteCard note={note()} onEdit={vi.fn()} onTogglePin={vi.fn()} onDelete={vi.fn()} onTagClick={vi.fn()} />,
    );
    expect(screen.getByTestId('md').textContent).toBe('hello #hooks');
    expect(screen.getByText('#hooks')).toBeTruthy();
  });

  it('tag chip click bubbles the tag', () => {
    const onTagClick = vi.fn();
    render(
      <NoteCard note={note()} onEdit={vi.fn()} onTogglePin={vi.fn()} onDelete={vi.fn()} onTagClick={onTagClick} />,
    );
    fireEvent.click(screen.getByText('#hooks'));
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
