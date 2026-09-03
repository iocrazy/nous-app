import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

const createNote = vi.fn();
vi.mock('../../services/inspirationService', () => ({
  createNote: (...a: unknown[]) => createNote(...a),
  uploadAttachment: vi.fn(),
  // Composer's edit path also imports deleteAttachment, and the
  // AttachmentView it renders there imports attachmentUrlWithToken —
  // stubbed so this factory covers Composer's whole import surface, not
  // just the calls these create-mode tests happen to reach.
  deleteAttachment: vi.fn(),
  attachmentUrlWithToken: (id: string) => `http://api.test/att/${id}`,
}));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (_k: string, f: string) => f }) }));
vi.mock('../Toast', () => ({ useToast: () => ({ addToast: vi.fn() }) }));
vi.mock('./NoteEditor', () => import('./testing/noteEditorShim'));

import { Composer } from './Composer';

describe('Composer autoFocus', () => {
  it('autoFocus focuses the editor', () => {
    render(
      <Composer
        onCreated={vi.fn()}
        tagSuggestions={[]}
        autoFocus
        prefill={{ content: '\n\n#food #shortform', refHotspot: { title: 'x' } }}
      />,
    );
    const ta = screen.getByRole('textbox') as HTMLTextAreaElement;
    expect(document.activeElement).toBe(ta);
    // caret-at-document-start on autoFocus is real ProseMirror selection
    // behavior, not Composer's — covered by NoteEditor.test.tsx
    // ("autoFocus puts the caret at the document start").
    expect(ta.value).toContain('#food #shortform');
  });
});
