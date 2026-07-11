// Shared test double for NoteEditor. Composer's own tests only need to
// verify Composer's WIRING (state, submit, staged files, toolbar button ->
// imperative handle call) — not TipTap's real editing behavior, which is
// covered by NoteEditor.test.tsx and NoteEditor.tags.test.tsx directly.
//
// A plain textarea honors the same props contract (value/onChange/
// placeholder/autoFocus/onSubmit/onFiles) so `getByRole('textbox')` queries
// in existing Composer tests keep working unchanged. The imperative handle
// mimics NoteEditor's command shapes with simple string concatenation —
// good enough to prove Composer calls the right command, not to prove the
// command's real markdown output (that's NoteEditor's job).
//
// Usage in a test file:
//   vi.mock('./NoteEditor', () => import('./testing/noteEditorShim'));
import React from 'react';

interface ShimProps {
  value: string;
  onChange: (markdown: string) => void;
  placeholder?: string;
  autoFocus?: boolean;
  onSubmit?: () => void;
  onFiles?: (files: File[]) => void;
}

export const NoteEditor = React.forwardRef(function NoteEditorShim(
  { value, onChange, placeholder, autoFocus, onSubmit, onFiles }: ShimProps,
  ref: React.Ref<{ insertTag: () => void; insertCodeBlock: () => void; insertLink: () => void; focus: () => void }>,
) {
  React.useImperativeHandle(ref, () => ({
    insertTag: () => onChange(value + '#'),
    insertCodeBlock: () => onChange(value + '\n```\n\n```\n'),
    insertLink: () => onChange(value + '[]()'),
    focus: () => {},
  }));
  return (
    <textarea
      aria-label="note-editor"
      autoFocus={autoFocus}
      placeholder={placeholder}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      onKeyDown={(e) => {
        if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) onSubmit?.();
      }}
      onPaste={(e) => {
        const files = Array.from(e.clipboardData.files);
        if (files.length) onFiles?.(files);
      }}
    />
  );
});
