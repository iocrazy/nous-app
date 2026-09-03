// Composer driven in EDIT mode: same shell as the quick-capture box, but the
// submit goes through an injected `onSubmit` instead of the hardcoded
// `createNote`, and nothing is cleared afterwards (the parent closes the
// modal). Attachment add/remove in edit mode lives in Composer.editattach.test.tsx.
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

const createNote = vi.fn();
const uploadAttachment = vi.fn();
vi.mock('../../services/inspirationService', () => ({
  createNote: (...a: unknown[]) => createNote(...a),
  uploadAttachment: (...a: unknown[]) => uploadAttachment(...a),
  // Composer's edit path also imports deleteAttachment, and the
  // AttachmentView it renders there imports attachmentUrlWithToken —
  // stubbed so this factory covers Composer's whole import surface, not
  // just the calls these create-mode tests happen to reach.
  deleteAttachment: vi.fn(),
  attachmentUrlWithToken: (id: string) => `http://api.test/att/${id}`,
}));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_k: string, f: string) => f }),
}));
const addToast = vi.fn();
vi.mock('../Toast', () => ({ useToast: () => ({ addToast }) }));
vi.mock('./NoteEditor', () => import('./testing/noteEditorShim'));

import { Composer } from './Composer';
import type { InspirationNote } from '../../services/inspirationService';

// Real wire shape (NoteOut): ids are strings, rating is a number.
const NOTE: InspirationNote = {
  id: '7',
  content_md: 'first idea',
  tags: [],
  ref_hotspot: null,
  pinned: false,
  rating: 0,
  note_date: '2026-09-02',
  created_at: '2026-09-02T09:00:00+00:00',
  updated_at: '2026-09-02T09:00:00+00:00',
  attachments: [],
};

const REF = { hotspot_id: '42', title: 'Silent vlog 2.1B', source: 'DOUYIN', heat: 98.4 };

describe('Composer edit mode', () => {
  beforeEach(() => {
    createNote.mockReset();
    uploadAttachment.mockReset();
    addToast.mockReset();
  });

  it('submits through onSubmit and never touches createNote', async () => {
    const onSubmit = vi.fn().mockResolvedValue(NOTE);
    render(
      <Composer
        tagSuggestions={[]}
        noteId="7"
        prefill={{ content: 'first idea' }}
        onSubmit={onSubmit}
      />,
    );
    const ta = screen.getByRole('textbox');
    fireEvent.change(ta, { target: { value: 'edited body' } });
    fireEvent.click(screen.getByText('Save'));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledWith('edited body', undefined));
    expect(createNote).not.toHaveBeenCalled();
  });

  it('keeps the content after a successful save — the parent owns closing', async () => {
    const onSubmit = vi.fn().mockResolvedValue(NOTE);
    render(
      <Composer
        tagSuggestions={[]}
        noteId="7"
        prefill={{ content: 'first idea' }}
        onSubmit={onSubmit}
      />,
    );
    const ta = screen.getByRole('textbox') as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: 'edited body' } });
    fireEvent.click(screen.getByText('Save'));
    await waitFor(() => expect(onSubmit).toHaveBeenCalled());
    // New-note mode clears here; edit mode must NOT — the modal is about to be
    // unmounted by the parent, and blanking it first would flash an empty box.
    expect(ta.value).toBe('edited body');
  });

  it('submitLabel replaces the button text', () => {
    render(
      <Composer
        tagSuggestions={[]}
        noteId="7"
        prefill={{ content: 'x' }}
        onSubmit={vi.fn()}
        submitLabel="Save Changes"
      />,
    );
    expect(screen.getByText('Save Changes')).toBeTruthy();
    expect(screen.queryByText('Save')).toBeNull();
  });

  it('a failed save toasts the reason and keeps the content', async () => {
    const onSubmit = vi.fn().mockRejectedValue(new Error('note update failed'));
    render(
      <Composer
        tagSuggestions={[]}
        noteId="7"
        prefill={{ content: 'first idea' }}
        onSubmit={onSubmit}
      />,
    );
    const ta = screen.getByRole('textbox') as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: 'edited body' } });
    fireEvent.click(screen.getByText('Save'));
    await waitFor(() =>
      expect(addToast).toHaveBeenCalledWith('note update failed', 'error'),
    );
    expect(ta.value).toBe('edited body');
  });

  it('offers no rating stars — the card owns the rating', () => {
    const { container } = render(
      <Composer
        tagSuggestions={[]}
        noteId="7"
        prefill={{ content: 'x' }}
        onSubmit={vi.fn()}
      />,
    );
    // Two writers for one value would need a "who wins" story; NoteCard's
    // stars already have one (seq guard + 5 tests), so the modal has none.
    expect(screen.queryByRole('group', { name: 'New note rating' })).toBeNull();
    // Count the stars themselves too: asserting only on the labelled wrapper
    // would stay green if someone rendered RatingStars WITHOUT that wrapper,
    // which is the same bug with the evidence removed.
    expect(container.querySelectorAll('.lucide-star')).toHaveLength(0);
  });

  it('shows the reference read-only: the PATCH body has no ref_hotspot field', () => {
    render(
      <Composer
        tagSuggestions={[]}
        noteId="7"
        prefill={{ content: 'x', refHotspot: REF }}
        onSubmit={vi.fn()}
      />,
    );
    expect(screen.getByText('Silent vlog 2.1B')).toBeTruthy();
    // A remove button here would be a silent no-op: NoteUpdateIn accepts only
    // content_md / pinned / rating, so a dropped ref could never be persisted.
    expect(screen.queryByLabelText('Remove hotspot reference')).toBeNull();
  });
});
