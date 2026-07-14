// A single-code-block TipTap editor for non-markdown text resources
// (spec 2026-07-14). Byte-preserving: content is loaded via plainTextToDoc
// and read back via docToPlainText(getJSON()) — never through markdown.
// Optional lowlight syntax highlight by file extension.
import { useEffect } from 'react';
import { EditorContent, useEditor } from '@tiptap/react';
import Document from '@tiptap/extension-document';
import Text from '@tiptap/extension-text';
import CodeBlockLowlight from '@tiptap/extension-code-block-lowlight';
import { common, createLowlight } from 'lowlight';
import { plainTextToDoc, docToPlainText } from '../../utils/tiptapPlainText';
import { codeLangForExtension } from '../../utils/textResourceMode';

const lowlight = createLowlight(common);

interface Props {
  value: string;
  ext: string;
  readOnly?: boolean;
  onChange?: (text: string) => void;
}

export function PlainTextResourceEditor({ value, ext, readOnly, onChange }: Props) {
  const language = codeLangForExtension(ext);
  const editor = useEditor({
    editable: !readOnly,
    // A doc that holds exactly one code block — no paragraphs, no marks, so
    // nothing can transform the bytes.
    extensions: [
      Document.extend({ content: 'codeBlock' }),
      Text,
      CodeBlockLowlight.configure({ lowlight }),
    ],
    content: plainTextToDoc(value, language),
    onUpdate: ({ editor: ed }) => {
      onChange?.(docToPlainText(ed.getJSON()));
    },
  });

  // Keep editability in sync if the prop flips.
  useEffect(() => {
    editor?.setEditable(!readOnly);
  }, [editor, readOnly]);

  if (!editor) return null;
  return (
    <div className="w-full max-h-[calc(100vh-13rem)] overflow-auto rounded-lg bg-island-2 text-sm">
      <EditorContent editor={editor} className="p-3 font-mono" />
    </div>
  );
}

export default PlainTextResourceEditor;
