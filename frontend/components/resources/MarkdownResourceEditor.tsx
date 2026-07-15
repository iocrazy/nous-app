// frontend/components/resources/MarkdownResourceEditor.tsx
// Markdown text resources: reuse the platform's TipTap markdown editor
// (NoteEditor) for editing and the shared markdown renderer for read-only
// preview (spec 2026-07-14). WYSIWYG markdown is intentionally lossy on
// exact formatting — acceptable for content-first .md files.
import { NoteEditor } from '../Inspiration/NoteEditor';
import { NoteMarkdown } from '../Inspiration/NoteMarkdown';

interface Props {
  value: string;
  readOnly?: boolean;
  onChange?: (markdown: string) => void;
  // Accepted-but-ignored: lets the dispatcher call both editors with the same
  // prop shape (markdown has no per-language mode).
  ext?: string;
}

export function MarkdownResourceEditor({ value, readOnly, onChange }: Props) {
  if (readOnly) {
    return (
      <div className="w-full max-h-[calc(100vh-13rem)] overflow-auto px-1">
        <NoteMarkdown source={value} />
      </div>
    );
  }
  return (
    <div className="w-full max-h-[calc(100vh-13rem)] overflow-auto">
      <NoteEditor value={value} onChange={(md) => onChange?.(md)} minRows={12} />
    </div>
  );
}

export default MarkdownResourceEditor;
