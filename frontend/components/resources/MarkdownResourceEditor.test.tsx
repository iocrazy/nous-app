// frontend/components/resources/MarkdownResourceEditor.test.tsx
import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

// Stub the heavy TipTap NoteEditor — this test only checks dispatch (edit vs
// read-only), not TipTap internals.
vi.mock('../Inspiration/NoteEditor', () => ({
  NoteEditor: ({ value }: { value: string }) => <div data-testid="note-editor">{value}</div>,
}));
vi.mock('../Inspiration/NoteMarkdown', () => ({
  NoteMarkdown: ({ source }: { source: string }) => <div data-testid="note-markdown">{source}</div>,
}));

import { MarkdownResourceEditor } from './MarkdownResourceEditor';

describe('MarkdownResourceEditor', () => {
  it('renders the read-only markdown renderer when readOnly', () => {
    render(<MarkdownResourceEditor value={'# Hi'} readOnly />);
    expect(screen.getByTestId('note-markdown')).toHaveTextContent('# Hi');
    expect(screen.queryByTestId('note-editor')).toBeNull();
  });
  it('renders the editor when not readOnly', () => {
    render(<MarkdownResourceEditor value={'# Hi'} onChange={() => {}} />);
    expect(screen.getByTestId('note-editor')).toHaveTextContent('# Hi');
    expect(screen.queryByTestId('note-markdown')).toBeNull();
  });
});
