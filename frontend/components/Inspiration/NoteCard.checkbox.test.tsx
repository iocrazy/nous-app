import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render } from '@testing-library/react';

vi.mock('../../services/inspirationService', () => ({ attachmentUrlWithToken: (id: string) => `u/${id}` }));
vi.mock('../../contexts/AuthContext', () => ({ useAuth: () => ({ mediaToken: 'tok' }) }));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (_k: string, f: string) => f }) }));

import { NoteCard } from './NoteCard';
import type { InspirationNote } from '../../services/inspirationService';

const note = (over: Partial<InspirationNote> = {}): InspirationNote => ({
  id: '1', content_md: '- [ ] a\n- [ ] b', tags: [], ref_hotspot: null, pinned: false, rating: 0,
  note_date: '2026-07-07', created_at: '2026-07-07T09:42:00+00:00',
  updated_at: '2026-07-07T09:42:00+00:00', attachments: [], ...over,
});

describe('NoteCard checkbox toggle', () => {
  it('clicking a task checkbox reports (note, index)', () => {
    const onToggleTask = vi.fn();
    const { container } = render(
      <NoteCard note={note()} onEdit={vi.fn()} onTogglePin={vi.fn()} onDelete={vi.fn()} onTagClick={vi.fn()} onToggleTask={onToggleTask} />,
    );
    const boxes = container.querySelectorAll('input[type="checkbox"]');
    fireEvent.click(boxes[1]);
    expect(onToggleTask).toHaveBeenCalledWith(expect.objectContaining({ id: '1' }), 1);
  });
});
