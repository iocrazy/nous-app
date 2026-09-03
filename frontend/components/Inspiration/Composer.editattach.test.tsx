// Attachments in EDIT mode: already-stored files can be removed, new ones can
// be added, and both are applied by Save — never behind the user's back.
//
// `deleteAttachment` had zero frontend callers before this; the shape of the
// wiring (and every failure branch of it) is pinned here.
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

const order: string[] = [];
const createNote = vi.fn();
const uploadAttachment = vi.fn();
const deleteAttachment = vi.fn();
vi.mock('../../services/inspirationService', () => ({
  createNote: (...a: unknown[]) => createNote(...a),
  uploadAttachment: (...a: unknown[]) => uploadAttachment(...a),
  deleteAttachment: (...a: unknown[]) => deleteAttachment(...a),
  attachmentUrlWithToken: (id: string) => `http://api.test/att/${id}`,
}));
vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ mediaToken: 'tok' }),
}));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (_k: string, f: string, vars?: Record<string, string>) =>
      vars ? f.replace('{{name}}', vars.name) : f,
  }),
}));
const addToast = vi.fn();
vi.mock('../Toast', () => ({ useToast: () => ({ addToast }) }));
vi.mock('./NoteEditor', () => import('./testing/noteEditorShim'));

import { Composer } from './Composer';
import type { InspirationNote, NoteAttachment } from '../../services/inspirationService';

const PIC: NoteAttachment = { id: 'a1', mime: 'image/png', size_bytes: 10, original_name: 'pic.png' };
const PDF: NoteAttachment = { id: 'a2', mime: 'application/pdf', size_bytes: 20, original_name: 'report.pdf' };
const PDF2: NoteAttachment = { id: 'a3', mime: 'application/pdf', size_bytes: 30, original_name: 'notes.pdf' };

const SAVED: InspirationNote = {
  id: '7', content_md: 'edited', tags: [], ref_hotspot: null, pinned: false, rating: 0,
  note_date: '2026-09-02', created_at: '2026-09-02T09:00:00+00:00',
  updated_at: '2026-09-02T09:00:00+00:00', attachments: [],
};

function renderEditor(over: Partial<React.ComponentProps<typeof Composer>> = {}) {
  const onSubmit = vi.fn(async (...a: unknown[]) => {
    order.push(`submit:${String(a[0])}`);
    return SAVED;
  });
  const props = {
    tagSuggestions: [],
    noteId: '7',
    prefill: { content: 'first idea' },
    existingAttachments: [PIC, PDF],
    onSubmit,
    ...over,
  } as React.ComponentProps<typeof Composer>;
  render(<Composer {...props} />);
  return { onSubmit };
}

const pick = (name: string) => {
  const input = screen.getByLabelText('Attach files') as HTMLInputElement;
  const file = new File(['x'], name, { type: 'image/png' });
  fireEvent.change(input, { target: { files: [file] } });
  return file;
};

describe('Composer edit-mode attachments', () => {
  beforeEach(() => {
    order.length = 0;
    createNote.mockReset();
    uploadAttachment.mockReset();
    deleteAttachment.mockReset();
    addToast.mockReset();
    deleteAttachment.mockImplementation(async (id: string) => {
      order.push(`delete:${id}`);
    });
    uploadAttachment.mockImplementation(async (noteId: string, file: File) => {
      order.push(`upload:${noteId}:${file.name}`);
      return { id: 'new1', mime: 'image/png', size_bytes: 1, original_name: file.name };
    });
  });

  it('renders the note\'s stored attachments with a remove affordance', () => {
    renderEditor();
    expect(screen.getByLabelText('Remove pic.png')).toBeTruthy();
    expect(screen.getByLabelText('Remove report.pdf')).toBeTruthy();
  });

  it('new-note mode renders no stored-attachment section at all', () => {
    render(<Composer onCreated={vi.fn()} tagSuggestions={[]} />);
    expect(screen.queryByLabelText(/^Remove /)).toBeNull();
  });

  it('clicking remove only STAGES the removal — nothing is deleted until Save', async () => {
    renderEditor();
    fireEvent.click(screen.getByLabelText('Remove pic.png'));
    expect(screen.queryByLabelText('Remove pic.png')).toBeNull();
    // Cancelling the modal (unmount) must leave the file intact on the server,
    // so the DELETE cannot fire on click. Deletion is irreversible.
    await waitFor(() => expect(deleteAttachment).not.toHaveBeenCalled());
  });

  it('Save uploads new files and applies removals BEFORE the content PATCH', async () => {
    const { onSubmit } = renderEditor();
    pick('added.png');
    fireEvent.click(screen.getByLabelText('Remove report.pdf'));
    fireEvent.click(screen.getByText('Save'));

    await waitFor(() => expect(onSubmit).toHaveBeenCalled());
    // The PATCH response carries the note's attachment list, so it has to be
    // the LAST call — otherwise the parent stores a list that predates this
    // very save and the card shows files that are already gone.
    expect(order).toEqual(['upload:7:added.png', 'delete:a2', 'submit:first idea']);
    expect(createNote).not.toHaveBeenCalled();
  });

  it('reports each successful removal up so the card can drop it immediately', async () => {
    const onAttachmentDeleted = vi.fn();
    renderEditor({ onAttachmentDeleted });
    fireEvent.click(screen.getByLabelText('Remove report.pdf'));
    fireEvent.click(screen.getByText('Save'));
    await waitFor(() => expect(onAttachmentDeleted).toHaveBeenCalledWith('7', 'a2'));
  });

  it('a failed upload aborts the save: typed toast, file kept with Retry, no PATCH', async () => {
    uploadAttachment.mockRejectedValue(new Error('network down'));
    const { onSubmit } = renderEditor();
    pick('added.png');
    fireEvent.click(screen.getByLabelText('Remove report.pdf'));
    fireEvent.click(screen.getByText('Save'));

    await waitFor(() =>
      expect(addToast).toHaveBeenCalledWith('Upload failed: added.png: network down', 'error'),
    );
    // Aborting is the point: closing the modal here would take the only copy
    // of the failed file with it, leaving a toast as the sole trace.
    expect(onSubmit).not.toHaveBeenCalled();
    expect(deleteAttachment).not.toHaveBeenCalled();
    expect(screen.getByLabelText('Retry added.png')).toBeTruthy();
  });

  it('a failed removal aborts the save: typed toast, the file comes back, no PATCH', async () => {
    deleteAttachment.mockRejectedValue(new Error('HTTP 502'));
    const { onSubmit } = renderEditor();
    fireEvent.click(screen.getByLabelText('Remove report.pdf'));
    fireEvent.click(screen.getByText('Save'));

    await waitFor(() =>
      expect(addToast).toHaveBeenCalledWith('Remove failed: report.pdf: HTTP 502', 'error'),
    );
    expect(onSubmit).not.toHaveBeenCalled();
    // Restored, not silently dropped — the UI must not claim a removal the
    // server refused.
    expect(await screen.findByLabelText('Remove report.pdf')).toBeTruthy();
  });

  it('a removal that already succeeded is not replayed by the next Save', async () => {
    const onSubmit = vi
      .fn()
      .mockRejectedValueOnce(new Error('note update failed'))
      .mockResolvedValue(SAVED);
    renderEditor({ onSubmit });
    fireEvent.click(screen.getByLabelText('Remove report.pdf'));
    fireEvent.click(screen.getByText('Save'));
    await waitFor(() => expect(addToast).toHaveBeenCalledWith('note update failed', 'error'));
    expect(deleteAttachment).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByText('Save'));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(2));
    // Re-issuing the DELETE would 404 on a file that is already gone and turn
    // a recoverable PATCH failure into a permanently unsaveable modal.
    expect(deleteAttachment).toHaveBeenCalledTimes(1);
  });
  it('keeps an already-uploaded file visible as a stored attachment when a later step aborts', async () => {
    uploadAttachment.mockReset();
    uploadAttachment
      .mockImplementationOnce(async (noteId: string, file: File) => {
        order.push(`upload:${noteId}:${file.name}`);
        return { id: 'new1', mime: 'image/png', size_bytes: 1, original_name: file.name };
      })
      .mockRejectedValueOnce(new Error('network down'));
    const { onSubmit } = renderEditor();
    pick('first.png');
    pick('second.png');
    fireEvent.click(screen.getByText('Save'));

    await waitFor(() =>
      expect(addToast).toHaveBeenCalledWith('Upload failed: second.png: network down', 'error'),
    );
    expect(onSubmit).not.toHaveBeenCalled();
    // first.png IS on the server now. The modal stays open, so if it merely
    // left the pending row it would be invisible here — and the obvious user
    // move (pick the same file again) would put a duplicate on the server.
    // AttachmentView renders stored images as <img alt={original_name}>.
    expect(screen.getByAltText('first.png')).toBeTruthy();
    expect(screen.getByLabelText('Retry second.png')).toBeTruthy();
  });

  it('restores a failed removal to its original position, not the end of the list', async () => {
    deleteAttachment.mockRejectedValue(new Error('HTTP 502'));
    // Same mime family, so AttachmentView renders them in list order and the
    // DOM order IS the list order.
    renderEditor({ existingAttachments: [PDF, PDF2] });
    expect(
      screen.getAllByLabelText(/^Remove /).map((b) => b.getAttribute('aria-label')),
    ).toEqual(['Remove report.pdf', 'Remove notes.pdf']);

    fireEvent.click(screen.getByLabelText('Remove report.pdf'));
    fireEvent.click(screen.getByText('Save'));
    await waitFor(() =>
      expect(addToast).toHaveBeenCalledWith('Remove failed: report.pdf: HTTP 502', 'error'),
    );

    // Appending it would silently reorder the user's attachments as a side
    // effect of a failure that changed nothing.
    await waitFor(() =>
      expect(
        screen.getAllByLabelText(/^Remove /).map((b) => b.getAttribute('aria-label')),
      ).toEqual(['Remove report.pdf', 'Remove notes.pdf']),
    );
  });
});
