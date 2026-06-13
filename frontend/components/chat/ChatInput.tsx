/**
 * ChatInput — tiptap-based rich-text composer.
 *
 * Extensions:
 *   - StarterKit (basic rich text, hard-break on Shift+Enter)
 *   - Placeholder (data-placeholder attribute)
 *   - resourceMention (ResourceRefNode atoms + insertResourceRef command)
 *
 * Picker wiring (external-popover approach):
 *   When the user types "@", ChatInput fires `onMentionRequest(query)`.
 *   AIChatPanel listens, renders <ResourcePickerSuggestion> in an absolutely-
 *   positioned overlay, and calls `editorRef.current.commands.insertResourceRef`
 *   on pick. This keeps ChatInput unaware of the picker UI while giving
 *   AIChatPanel full control over popover placement + data fetching.
 *
 * onSend signature (Approach A — two-arg):
 *   onSend(text: string, refAttachments: ResourceRefAttachment[])
 *   `text` is the plain-text rendering of the doc (resource chips render as
 *   "@Name"). `refAttachments` is the list of resourceRef nodes found in the doc.
 */

import React, { useCallback, useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { Send } from 'lucide-react';
import { useEditor, EditorContent } from '@tiptap/react';
import StarterKit from '@tiptap/starter-kit';
import Placeholder from '@tiptap/extension-placeholder';
import type { Editor } from '@tiptap/core';
import type { ClipboardEvent } from 'react';
import { createResourceMentionExtension } from './ChatInputResourceMention';
import type { ResourceRefAttachment, ResourceSearchResult } from '../../types';

export interface ChatInputProps {
  onSend: (text: string, refAttachments: ResourceRefAttachment[]) => void;
  onAttach?: () => void;
  disabled?: boolean;
  placeholder?: string;
  /** File-paste handler forwarded to tiptap handlePaste. */
  onPaste?: (e: ClipboardEvent) => void;
  /**
   * Called when the user types "@" so the parent can open the resource picker.
   * The parent should call `editor.commands.insertResourceRef(item)` on pick.
   */
  onMentionRequest?: (query: string) => void;
  /** Expose the editor instance so the parent can call insertResourceRef. */
  editorRef?: React.MutableRefObject<Editor | null>;
}

/** Walk the tiptap doc and collect all resourceRef nodes. */
function collectRefAttachments(editor: Editor): ResourceRefAttachment[] {
  const refs: ResourceRefAttachment[] = [];
  editor.state.doc.descendants((node) => {
    if (node.type.name === 'resourceRef') {
      refs.push({
        kind: 'resource_ref',
        resource_id: String(node.attrs.resourceId ?? ''),
        name: String(node.attrs.name ?? ''),
        mime: String(node.attrs.mime ?? ''),
        scope: node.attrs.scope ?? { type: 'personal', id: '' },
      });
    }
  });
  return refs;
}

export function ChatInput({
  onSend,
  onAttach: _onAttach,
  disabled = false,
  placeholder,
  onPaste,
  onMentionRequest,
  editorRef,
}: ChatInputProps): React.ReactElement {
  const { t } = useTranslation();
  const resolvedPlaceholder = placeholder ?? t('chat.typeMessage');

  // Keep stable refs so the editor key-handler closure doesn't re-bind
  const onSendRef = useRef(onSend);
  const disabledRef = useRef(disabled);
  useEffect(() => { onSendRef.current = onSend; }, [onSend]);
  useEffect(() => { disabledRef.current = disabled; }, [disabled]);

  // Dummy pick callback — the parent drives actual picker via onMentionRequest
  const dummyPick = useCallback(
    (_query: string): Promise<ResourceSearchResult | null> => Promise.resolve(null),
    [],
  );

  const editor = useEditor({
    extensions: [
      StarterKit.configure({
        // Disable hard-break so Shift+Enter is treated like a normal character
        // and Enter alone can trigger send without inserting a newline.
        // (StarterKit includes hardBreak by default; we override the keymap.)
        hardBreak: false,
      }),
      Placeholder.configure({ placeholder: resolvedPlaceholder }),
      createResourceMentionExtension({ onPick: dummyPick }),
    ],
    editorProps: {
      attributes: {
        class: [
          'flex-1 resize-none rounded-lg px-3 py-2',
          'bg-ink-800 border border-ink-700',
          'text-sm text-ink-200 placeholder-ink-500',
          'focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500/50',
          'disabled:opacity-40 disabled:cursor-not-allowed',
          'leading-6 min-h-[36px] max-h-[120px] overflow-y-auto',
          'tiptap-composer',
        ].join(' '),
      },
      handleKeyDown(view, event) {
        // "@" trigger — fire mention request
        if (event.key === '@' && onMentionRequest) {
          // Let the character be inserted first, then notify on the next tick
          setTimeout(() => {
            onMentionRequest('');
          }, 0);
          return false; // don't prevent insertion
        }
        // Enter (without Shift) → send
        if (event.key === 'Enter' && !event.shiftKey) {
          event.preventDefault();
          if (disabledRef.current) return true;
          const currentEditor = view.dom.closest('[data-tiptap-editor]')
            ? null // unused but typed for safety
            : null;
          void currentEditor;
          // We need the editor instance — accessed via the ref set below
          const ed = editorInstanceRef.current;
          if (!ed) return true;
          const text = ed.getText().trim();
          const refs = collectRefAttachments(ed);
          if (!text && refs.length === 0) return true;
          onSendRef.current(text, refs);
          ed.commands.clearContent(true);
          return true;
        }
        return false;
      },
      handlePaste(_view, event) {
        if (!onPaste) return false;
        // Forward to the React paste handler if the parent provided one.
        // Cast: tiptap gives us the raw DOM ClipboardEvent; the React synthetic
        // type is compatible for our purposes (we only read clipboardData.files).
        onPaste(event as unknown as ClipboardEvent);
        // Return false so tiptap also does its own paste handling (for text).
        return false;
      },
    },
    editable: !disabled,
  });

  // Stable internal ref to the editor for use inside closures
  const editorInstanceRef = useRef<Editor | null>(null);
  useEffect(() => {
    editorInstanceRef.current = editor;
    if (editorRef) editorRef.current = editor;
  }, [editor, editorRef]);

  // Item 2: live query tracking — after "@" is typed, track subsequent chars
  // and fire onMentionRequest(query) on each change so the picker filters live.
  const onMentionRequestRef = useRef(onMentionRequest);
  useEffect(() => { onMentionRequestRef.current = onMentionRequest; }, [onMentionRequest]);

  useEffect(() => {
    if (!editor) return;
    const handleUpdate = () => {
      const cb = onMentionRequestRef.current;
      if (!cb) return;
      const { from } = editor.state.selection;
      // Inspect up to 80 chars before the caret to find the last '@'
      const textBefore = editor.state.doc.textBetween(Math.max(0, from - 80), from);
      const atIdx = textBefore.lastIndexOf('@');
      if (atIdx === -1) return;
      const queryCandidate = textBefore.slice(atIdx + 1);
      // Stop tracking if the query contains whitespace or terminal punctuation
      if (/[\s\n,;]/.test(queryCandidate)) return;
      // Only update if we already have a query (initial '@' open is handled by
      // the handleKeyDown '@' handler — this only updates the existing query)
      if (atIdx >= 0 && textBefore[atIdx] === '@') {
        cb(queryCandidate);
      }
    };
    editor.on('update', handleUpdate);
    return () => { editor.off('update', handleUpdate); };
  }, [editor]);

  // Re-apply editable flag when disabled changes (editor must be live)
  useEffect(() => {
    if (editor) {
      editor.setEditable(!disabled);
    }
  }, [editor, disabled]);

  const handleSendClick = useCallback(() => {
    if (disabled || !editor) return;
    const text = editor.getText().trim();
    const refs = collectRefAttachments(editor);
    if (!text && refs.length === 0) return;
    onSend(text, refs);
    editor.commands.clearContent(true);
  }, [disabled, editor, onSend]);

  return (
    <div className="flex items-end gap-2 p-2 bg-ink-900 border-t border-ink-700/50">
      <div className="flex-1 min-w-0">
        <EditorContent editor={editor} />
      </div>

      <button
        type="button"
        onClick={handleSendClick}
        disabled={disabled}
        title="Send message"
        className="
          flex-shrink-0 p-1.5 rounded-lg
          bg-indigo-600 hover:bg-indigo-500
          text-white transition-colors
          disabled:opacity-40 disabled:cursor-not-allowed
        "
      >
        <Send size={16} />
      </button>
    </div>
  );
}
