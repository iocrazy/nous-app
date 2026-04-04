import { useEditor, EditorContent } from '@tiptap/react';
import StarterKit from '@tiptap/starter-kit';
import Placeholder from '@tiptap/extension-placeholder';
import { useEffect, useCallback } from 'react';
import { SceneHeading, Dialogue } from '../extensions';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface ScriptTipTapEditorProps {
  contentJson: Record<string, unknown> | null;
  onUpdate: (json: Record<string, unknown>, html: string) => void;
  placeholder?: string;
  editable?: boolean;
}

// ---------------------------------------------------------------------------
// Toolbar button helper
// ---------------------------------------------------------------------------

interface ToolbarButtonProps {
  active: boolean;
  onClick: () => void;
  title: string;
  children: React.ReactNode;
}

function ToolbarButton({ active, onClick, title, children }: ToolbarButtonProps) {
  return (
    <button
      type="button"
      title={title}
      onMouseDown={(e) => {
        e.preventDefault(); // prevent editor blur
        onClick();
      }}
      className={[
        'px-2 py-1 rounded text-sm font-medium transition-colors',
        active
          ? 'bg-indigo-600 text-white'
          : 'text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100',
      ].join(' ')}
    >
      {children}
    </button>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function ScriptTipTapEditor({
  contentJson,
  onUpdate,
  placeholder = 'Start writing...',
  editable = true,
}: ScriptTipTapEditorProps) {
  const editor = useEditor({
    extensions: [
      StarterKit.configure({
        heading: { levels: [1, 2, 3, 4] },
      }),
      Placeholder.configure({ placeholder }),
      SceneHeading,
      Dialogue,
    ],
    editable,
    content: contentJson ?? undefined,
    onUpdate: ({ editor: ed }) => {
      onUpdate(ed.getJSON() as Record<string, unknown>, ed.getHTML());
    },
  });

  // Sync external contentJson into editor when it changes (e.g. AI inject)
  useEffect(() => {
    if (!editor) return;
    if (editor.isFocused) return;

    const incoming = contentJson ?? null;
    if (incoming === null) return;

    // Compare serialised to avoid unnecessary setContent calls
    const current = JSON.stringify(editor.getJSON());
    const next = JSON.stringify(incoming);
    if (current !== next) {
      editor.commands.setContent(incoming, false);
    }
  }, [contentJson, editor]);

  // Sync editable prop
  useEffect(() => {
    if (!editor) return;
    editor.setEditable(editable);
  }, [editable, editor]);

  // Toolbar action helpers
  const setHeading = useCallback(
    (level: 1 | 2 | 3 | 4) => {
      editor?.chain().focus().toggleHeading({ level }).run();
    },
    [editor],
  );

  const setParagraph = useCallback(() => {
    editor?.chain().focus().setParagraph().run();
  }, [editor]);

  const toggleBold = useCallback(() => {
    editor?.chain().focus().toggleBold().run();
  }, [editor]);

  const toggleItalic = useCallback(() => {
    editor?.chain().focus().toggleItalic().run();
  }, [editor]);

  const isHeading = (level: 1 | 2 | 3 | 4) =>
    editor?.isActive('heading', { level }) ?? false;

  const isParagraph = editor?.isActive('paragraph') ?? false;
  const isBold = editor?.isActive('bold') ?? false;
  const isItalic = editor?.isActive('italic') ?? false;

  return (
    <div
      className="flex flex-col bg-zinc-900 rounded-lg border border-zinc-700"
      onKeyDown={(e) => e.stopPropagation()}
      onMouseDown={(e) => e.stopPropagation()}
    >
      {/* Toolbar — only shown in editable mode */}
      {editable && (
        <div className="flex items-center gap-1 px-2 py-1 border-b border-zinc-700">
          <ToolbarButton
            active={isHeading(1)}
            onClick={() => setHeading(1)}
            title="Heading 1"
          >
            H1
          </ToolbarButton>
          <ToolbarButton
            active={isHeading(2)}
            onClick={() => setHeading(2)}
            title="Heading 2"
          >
            H2
          </ToolbarButton>
          <ToolbarButton
            active={isHeading(3)}
            onClick={() => setHeading(3)}
            title="Heading 3"
          >
            H3
          </ToolbarButton>
          <ToolbarButton
            active={isHeading(4)}
            onClick={() => setHeading(4)}
            title="Heading 4"
          >
            H4
          </ToolbarButton>
          <ToolbarButton
            active={isParagraph}
            onClick={setParagraph}
            title="Body text"
          >
            Body
          </ToolbarButton>

          {/* Divider */}
          <div className="w-px h-4 bg-zinc-600 mx-1" aria-hidden="true" />

          <ToolbarButton active={isBold} onClick={toggleBold} title="Bold">
            <strong>B</strong>
          </ToolbarButton>
          <ToolbarButton active={isItalic} onClick={toggleItalic} title="Italic">
            <em>I</em>
          </ToolbarButton>
        </div>
      )}

      {/* Editor content */}
      <EditorContent
        editor={editor}
        className={[
          'px-4 py-3 min-h-[120px] text-zinc-100 text-sm leading-relaxed',
          'focus-within:outline-none',
          // Custom node styles via TailwindCSS arbitrary selectors
          '[&_.scene-heading]:text-amber-500 [&_.scene-heading]:font-semibold [&_.scene-heading]:text-base',
          '[&_.dialogue-line]:text-amber-500 [&_.dialogue-line_strong]:font-semibold',
          // Placeholder colour
          '[&_.is-editor-empty:first-child::before]:text-zinc-500',
          '[&_.is-editor-empty:first-child::before]:content-[attr(data-placeholder)]',
          '[&_.is-editor-empty:first-child::before]:float-left',
          '[&_.is-editor-empty:first-child::before]:pointer-events-none',
          '[&_.is-editor-empty:first-child::before]:h-0',
        ].join(' ')}
      />
    </div>
  );
}
