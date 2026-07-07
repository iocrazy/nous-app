import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

const createNote = vi.fn();
const uploadAttachment = vi.fn();
vi.mock('../../services/inspirationService', () => ({
  createNote: (...a: unknown[]) => createNote(...a),
  uploadAttachment: (...a: unknown[]) => uploadAttachment(...a),
}));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_k: string, fallback: string) => fallback }),
}));
const addToast = vi.fn();
vi.mock('../Toast', () => ({ useToast: () => ({ addToast }) }));

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

  it('typing # shows prefix-matched suggestions and click completes token', () => {
    render(<Composer onCreated={vi.fn()} tagSuggestions={['hooks', 'formats']} />);
    const ta = screen.getByRole('textbox') as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: 'idea #ho', selectionStart: 8 } });
    fireEvent.click(screen.getByText('#hooks'));
    expect(ta.value).toBe('idea #hooks ');
  });

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
});
