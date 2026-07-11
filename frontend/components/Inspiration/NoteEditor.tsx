// frontend/components/Inspiration/NoteEditor.tsx
// The one editor (user decision 2026-07-11): TipTap everywhere. Markdown-in /
// markdown-out wrapper used by the quick-capture composer and the edit modal.
// Notion-style input rules come free: '## '→heading, '[ ] '→task, ```→code.
//
// Markdown round-trip uses the official @tiptap/markdown extension (v3):
//   - registration: add `Markdown` to `extensions`
//   - parse-in:     `contentType: 'markdown'` editor option (real option name,
//                    verified in @tiptap/markdown dist types) + `setContent`
//                    with `{ contentType: 'markdown' }`
//   - serialize:    `editor.getMarkdown()` (method installed by the extension)
import { forwardRef, useEffect, useImperativeHandle, useMemo, useRef, useState } from 'react';
import { EditorContent, useEditor } from '@tiptap/react';
import StarterKit from '@tiptap/starter-kit';
import Placeholder from '@tiptap/extension-placeholder';
import TaskList from '@tiptap/extension-task-list';
import TaskItem from '@tiptap/extension-task-item';
import CodeBlockLowlight from '@tiptap/extension-code-block-lowlight';
import { Markdown } from '@tiptap/markdown';
import { common, createLowlight } from 'lowlight';
import { Extension } from '@tiptap/core';
import type { Editor } from '@tiptap/core';
import { findActiveTag } from './noteTags';
import './noteEditor.css';

const lowlight = createLowlight(common);

export interface NoteEditorHandle {
  insertTag: () => void;
  insertCodeBlock: () => void;
  insertLink: () => void;
  focus: () => void;
}

interface Props {
  value: string;
  onChange: (markdown: string) => void;
  placeholder?: string;
  autoFocus?: boolean;
  onSubmit?: () => void;
  onFiles?: (files: File[]) => void;
  tagSuggestions?: string[];
  minRows?: number;
}

/** Serialize the editor to markdown. `getMarkdown()` already normalizes a
 * truly empty document to '' (MarkdownManager.isEmptyOutput), so `!content
 * .trim()` guards in consumers keep working. Deliberately NOT gated on
 * `editor.isEmpty` — that is true for any single contentless textblock,
 * which would swallow a just-inserted empty code block. */
function toMarkdown(editor: Editor): string {
  return editor.getMarkdown();
}

export const NoteEditor = forwardRef<NoteEditorHandle, Props>(function NoteEditor(
  { value, onChange, placeholder, autoFocus, onSubmit, onFiles, tagSuggestions = [], minRows = 2 },
  ref,
) {
  // Latest-callback refs: the TipTap extensions and editorProps below are
  // captured once at editor creation, so they must read through refs to see
  // the current props (not the first render's closures).
  const onChangeRef = useRef(onChange);
  const onSubmitRef = useRef(onSubmit);
  const onFilesRef = useRef(onFiles);
  onChangeRef.current = onChange;
  onSubmitRef.current = onSubmit;
  onFilesRef.current = onFiles;

  // #tag autocomplete: `from` is the absolute doc position of the '#' that
  // opened the active token, `prefix` is the (lowercased) text typed after
  // it. Recomputed on every doc/selection update from the current textblock
  // — findActiveTag (shared with the backend's note_tags.py mirror) decides
  // whether the caret sits inside a taggable token at all.
  const [activeTag, setActiveTag] = useState<{ from: number; prefix: string } | null>(null);

  const updateActiveTag = (ed: Editor) => {
    const { $from } = ed.state.selection;
    if ($from.parent.type.name === 'codeBlock') {
      setActiveTag(null);
      return;
    }
    const text = $from.parent.textBetween(0, $from.parentOffset, '\n', '￼');
    const found = findActiveTag(text, text.length);
    if (!found) {
      setActiveTag(null);
      return;
    }
    setActiveTag({ from: $from.start() + found.start, prefix: found.prefix });
  };

  const editor = useEditor({
    extensions: [
      StarterKit.configure({ codeBlock: false }),
      CodeBlockLowlight.configure({ lowlight }),
      TaskList,
      TaskItem.configure({ nested: true }),
      Placeholder.configure({ placeholder: placeholder ?? '' }),
      Markdown,
      Extension.create({
        name: 'submitKeymap',
        addKeyboardShortcuts() {
          return {
            'Mod-Enter': () => {
              onSubmitRef.current?.();
              return true;
            },
          };
        },
      }),
    ],
    content: value,
    contentType: 'markdown',
    editorProps: {
      attributes: { class: 'note-editor-content focus:outline-none' },
      handlePaste: (_view, event) => {
        const files = Array.from(event.clipboardData?.files ?? []);
        if (files.length && onFilesRef.current) {
          onFilesRef.current(files);
          return true;
        }
        return false;
      },
      handleDrop: (_view, event) => {
        const files = Array.from(event.dataTransfer?.files ?? []);
        if (files.length && onFilesRef.current) {
          event.preventDefault();
          onFilesRef.current(files);
          return true;
        }
        return false;
      },
    },
    onUpdate: ({ editor: ed }) => {
      onChangeRef.current(toMarkdown(ed));
      updateActiveTag(ed);
    },
    onSelectionUpdate: ({ editor: ed }) => {
      updateActiveTag(ed);
    },
    onBlur: () => {
      // Chips prevent mousedown default (see render below) so a chip click
      // never fires this in the first place; this only catches genuine
      // focus-away (Tab, clicking elsewhere in the page).
      setActiveTag(null);
    },
  });

  // Controlled-value sync: when the parent swaps `value` from outside (e.g.
  // clearing after submit, or loading a note into the edit modal), reset the
  // document. Compares against the current serialization so the editor's own
  // onChange echo never triggers a caret-destroying setContent loop.
  useEffect(() => {
    if (!editor || toMarkdown(editor) === value) return;
    editor.commands.setContent(value, { contentType: 'markdown', emitUpdate: false });
  }, [editor, value]);

  useEffect(() => {
    if (!editor || !autoFocus) return;
    editor.commands.focus('start');
    if (import.meta.env.MODE === 'test') {
      // Test-only backdoor: jsdom cannot observe the ProseMirror caret from
      // the DOM, so expose "selection is at document start" for assertions.
      (window as unknown as Record<string, unknown>).__noteEditorSelAtStart =
        editor.state.selection.from <= 1;
    }
  }, [editor, autoFocus]);

  useEffect(() => {
    if (!editor || import.meta.env.MODE !== 'test') return;
    // Test-only backdoor: expose the live editor instance so tests (and the
    // Task 2 composer tests) can drive commands jsdom cannot simulate.
    (window as unknown as Record<string, unknown>).__noteEditorInstance = editor;
  }, [editor]);

  const suggestions = useMemo(() => {
    if (!activeTag) return [];
    const prefix = activeTag.prefix.toLowerCase();
    return tagSuggestions
      .filter((s) => s.toLowerCase().startsWith(prefix) && s.toLowerCase() !== prefix)
      .slice(0, 6);
  }, [activeTag, tagSuggestions]);

  const completeTag = (tag: string) => {
    if (!editor || !activeTag) return;
    const to = activeTag.from + 1 + activeTag.prefix.length; // '#' + prefix
    editor.chain().focus().deleteRange({ from: activeTag.from, to }).insertContent(`#${tag} `).run();
    setActiveTag(null);
  };

  useImperativeHandle(ref, () => ({
    insertTag: () => {
      if (!editor) return;
      const from = editor.state.selection.from;
      const before = editor.state.doc.textBetween(Math.max(0, from - 1), from);
      editor
        .chain()
        .focus()
        .insertContent(before && !/[\s(（]/.test(before) ? ' #' : '#')
        .run();
    },
    insertCodeBlock: () => {
      editor?.chain().focus().toggleCodeBlock().run();
    },
    insertLink: () => {
      editor?.chain().focus().insertContent('[]()').run();
    },
    focus: () => {
      editor?.commands.focus();
    },
  }));

  return (
    <div className="w-full">
      <div
        className="max-h-[50vh] w-full overflow-y-auto text-[13.5px] text-content"
        style={{ minHeight: `${minRows * 1.55}em` }}
      >
        <EditorContent editor={editor} />
      </div>
      {suggestions.length > 0 && (
        <div
          className="flex flex-wrap gap-1.5 pt-1"
          // Buttons are focusable by default, which would blur the editor on
          // mousedown before onClick runs — losing the caret position (and,
          // via the onBlur handler above, hiding these chips) before the tag
          // replacement fires. Suppressing the default keeps focus (and the
          // selection) in the editor through the whole click.
          onMouseDown={(event) => event.preventDefault()}
        >
          {suggestions.map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => completeTag(s)}
              className="rounded bg-indigo-500/15 px-2 py-0.5 text-xs text-indigo-300 hover:bg-indigo-500/25"
            >
              #{s}
            </button>
          ))}
        </div>
      )}
    </div>
  );
});
