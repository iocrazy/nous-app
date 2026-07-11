// memos-parity composer toolbar: quick-insert #tag / code block / link.
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

vi.mock('../../services/inspirationService', () => ({
  createNote: vi.fn(),
  uploadAttachment: vi.fn(),
}));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_k: string, f: string) => f }),
}));
vi.mock('../Toast', () => ({ useToast: () => ({ addToast: vi.fn() }) }));
// These tests assert Composer wires its toolbar buttons to the NoteEditor
// imperative handle (editorRef.current?.insertTag() etc.) — not the real
// TipTap command output (caret placement, selection-aware wrapping). Real
// command shapes are covered by NoteEditor.test.tsx's "command handle"
// suite. See noteEditorShim.tsx for the fake handle used here.
vi.mock('./NoteEditor', () => import('./testing/noteEditorShim'));

import { Composer } from './Composer';

describe('Composer toolbar quick-inserts', () => {
  it('# button calls the editor handle insertTag', () => {
    render(<Composer onCreated={vi.fn()} tagSuggestions={[]} />);
    const ta = screen.getByRole('textbox') as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: 'idea' } });
    fireEvent.click(screen.getByLabelText('Insert tag'));
    // caret/boundary-space placement is real-editor behavior, not Composer's
    // — covered by NoteEditor.test.tsx. Here we only assert the button is
    // wired to the handle at all.
    expect(ta.value).toContain('#');
  });

  it('code button calls the editor handle insertCodeBlock', () => {
    render(<Composer onCreated={vi.fn()} tagSuggestions={[]} />);
    const ta = screen.getByRole('textbox') as HTMLTextAreaElement;
    fireEvent.click(screen.getByLabelText('Insert code block'));
    expect(ta.value).toContain('```');
  });

  // Selection-aware fence-wrapping ("wrap selected text in ``` ```") was a
  // textarea-splice trick of the old Composer implementation. NoteEditor's
  // insertCodeBlock uses TipTap's native toggleCodeBlock command instead,
  // which is a real ProseMirror transaction — not reproducible against the
  // dumb textarea shim. Covered by NoteEditor.test.tsx instead.

  it('link button calls the editor handle insertLink', () => {
    render(<Composer onCreated={vi.fn()} tagSuggestions={[]} />);
    const ta = screen.getByRole('textbox') as HTMLTextAreaElement;
    fireEvent.click(screen.getByLabelText('Insert link'));
    expect(ta.value).toContain('[]()');
  });

  it('paperclip still opens the file input', () => {
    render(<Composer onCreated={vi.fn()} tagSuggestions={[]} />);
    expect(screen.getByLabelText('Attach file')).toBeTruthy();
    expect(screen.getByLabelText('Attach files')).toBeTruthy(); // hidden input
  });
});
