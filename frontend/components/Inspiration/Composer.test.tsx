import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';

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
  useTranslation: () => ({ t: (_k: string, fallback: string) => fallback }),
}));
const addToast = vi.fn();
vi.mock('../Toast', () => ({ useToast: () => ({ addToast }) }));
// Composer's own tests exercise wiring (state/submit/staged files), not
// TipTap editing — swap in the shared textarea shim. See noteEditorShim.tsx.
vi.mock('./NoteEditor', () => import('./testing/noteEditorShim'));

import { Composer } from './Composer';

describe('Composer', () => {
  beforeEach(() => {
    createNote.mockReset();
    uploadAttachment.mockReset();
    addToast.mockReset();
  });

  it('cmd+enter creates note and clears input', async () => {
    createNote.mockResolvedValue({ id: '1', attachments: [], tags: [] });
    render(<Composer onCreated={vi.fn()} tagSuggestions={[]} />);
    const ta = screen.getByRole('textbox');
    fireEvent.change(ta, { target: { value: 'quick idea #x' } });
    fireEvent.keyDown(ta, { key: 'Enter', metaKey: true });
    await waitFor(() => expect(createNote).toHaveBeenCalledWith('quick idea #x', undefined));
    expect((ta as HTMLTextAreaElement).value).toBe('');
  });

  it('empty content does not submit', () => {
    render(<Composer onCreated={vi.fn()} tagSuggestions={[]} />);
    fireEvent.click(screen.getByText('Save'));
    expect(createNote).not.toHaveBeenCalled();
  });

  // '#tag' autocomplete (typing '#', prefix-matched chips, click-to-complete)
  // now lives entirely inside NoteEditor — see
  // NoteEditor.tags.test.tsx ("inline #tag autocomplete").

  it('staged files upload after note creation and onCreated gets merged note', async () => {
    createNote.mockResolvedValue({ id: '9', attachments: [], tags: [] });
    uploadAttachment.mockResolvedValue({ id: 'a1', mime: 'image/png', size_bytes: 4, original_name: 'p.png' });
    const onCreated = vi.fn();
    render(<Composer onCreated={onCreated} tagSuggestions={[]} />);
    const input = screen.getByLabelText('Attach files') as HTMLInputElement;
    const file = new File(['x'], 'p.png', { type: 'image/png' });
    fireEvent.change(input, { target: { files: [file] } });
    expect(screen.getByText('p.png')).toBeTruthy();
    const ta = screen.getByRole('textbox');
    fireEvent.change(ta, { target: { value: 'with file' } });
    fireEvent.click(screen.getByText('Save'));
    await waitFor(() => expect(uploadAttachment).toHaveBeenCalledWith('9', file));
    await waitFor(() =>
      expect(onCreated).toHaveBeenCalledWith(
        expect.objectContaining({ id: '9', attachments: [expect.objectContaining({ id: 'a1' })] }),
      ),
    );
  });

  it('create failure toasts error and keeps text', async () => {
    createNote.mockRejectedValue(new Error('boom'));
    render(<Composer onCreated={vi.fn()} tagSuggestions={[]} />);
    const ta = screen.getByRole('textbox') as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: 'keep me' } });
    fireEvent.click(screen.getByText('Save'));
    await waitFor(() => expect(addToast).toHaveBeenCalledWith('boom', 'error'));
    expect(ta.value).toBe('keep me');
  });

  it('a failed upload keeps the file staged with a Retry action instead of silently dropping it', async () => {
    createNote.mockResolvedValue({ id: '9', attachments: [], tags: [] });
    uploadAttachment.mockRejectedValue(new Error('network down'));
    const onCreated = vi.fn();
    render(<Composer onCreated={onCreated} tagSuggestions={[]} />);
    const input = screen.getByLabelText('Attach files') as HTMLInputElement;
    const file = new File(['x'], 'p.png', { type: 'image/png' });
    fireEvent.change(input, { target: { files: [file] } });
    const ta = screen.getByRole('textbox');
    fireEvent.change(ta, { target: { value: 'with file' } });
    fireEvent.click(screen.getByText('Save'));

    await waitFor(() => expect(uploadAttachment).toHaveBeenCalledWith('9', file));
    // note itself was still created and reported up — only the attachment failed
    await waitFor(() =>
      expect(onCreated).toHaveBeenCalledWith(
        expect.objectContaining({ id: '9', attachments: [] }),
      ),
    );
    // the failed file stays visible with a Retry action, it is not dropped
    expect(screen.getByText('p.png')).toBeTruthy();
    expect(screen.getByLabelText('Retry p.png')).toBeTruthy();
  });

  it('clicking Retry re-uploads against the original note and clears the chip on success', async () => {
    createNote.mockResolvedValue({ id: '9', attachments: [], tags: [] });
    uploadAttachment.mockRejectedValueOnce(new Error('network down'));
    const onCreated = vi.fn();
    const onAttachmentUploaded = vi.fn();
    render(
      <Composer onCreated={onCreated} onAttachmentUploaded={onAttachmentUploaded} tagSuggestions={[]} />,
    );
    const input = screen.getByLabelText('Attach files') as HTMLInputElement;
    const file = new File(['x'], 'p.png', { type: 'image/png' });
    fireEvent.change(input, { target: { files: [file] } });
    const ta = screen.getByRole('textbox');
    fireEvent.change(ta, { target: { value: 'with file' } });
    fireEvent.click(screen.getByText('Save'));
    await waitFor(() => expect(screen.getByLabelText('Retry p.png')).toBeTruthy());

    uploadAttachment.mockResolvedValueOnce({
      id: 'a1', mime: 'image/png', size_bytes: 4, original_name: 'p.png',
    });
    fireEvent.click(screen.getByLabelText('Retry p.png'));

    await waitFor(() => expect(uploadAttachment).toHaveBeenCalledWith('9', file));
    await waitFor(() =>
      expect(onAttachmentUploaded).toHaveBeenCalledWith(
        '9',
        expect.objectContaining({ id: 'a1' }),
      ),
    );
    expect(screen.queryByText('p.png')).toBeNull();
  });
  // ── bottom row: real rating in, fake "Private" out ───────────────────────

  it('picking a rating sends it in the SAME createNote call', async () => {
    createNote.mockResolvedValue({ id: '1', attachments: [], tags: [] });
    render(<Composer onCreated={vi.fn()} tagSuggestions={[]} />);
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'rated idea' } });
    // RatingStars renders 5 icon-only buttons; the wrapper carries the label.
    const stars = within(screen.getByLabelText('Rating')).getAllByRole('button');
    fireEvent.click(stars[3]); // 4 stars
    fireEvent.click(screen.getByText('Save'));
    await waitFor(() =>
      expect(createNote).toHaveBeenCalledWith('rated idea', undefined, 4),
    );
  });

  it('an untouched rating is omitted entirely — 0 is already the DB default', async () => {
    createNote.mockResolvedValue({ id: '1', attachments: [], tags: [] });
    render(<Composer onCreated={vi.fn()} tagSuggestions={[]} />);
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'plain idea' } });
    fireEvent.click(screen.getByText('Save'));
    await waitFor(() => expect(createNote).toHaveBeenCalled());
    // Exactly two args, not three-with-0: nothing in the request says "the
    // user rated this note zero" when the user never touched the stars.
    expect(createNote.mock.calls[0]).toEqual(['plain idea', undefined]);
  });

  it('has no decorative "Private" control', () => {
    render(<Composer onCreated={vi.fn()} tagSuggestions={[]} />);
    // It used to be a <span> with a chevron and no onClick, over a schema that
    // has no visibility column at all — a control that promised a choice
    // nothing could make. Removed rather than wired up.
    expect(screen.queryByText('Private')).toBeNull();
  });
});
