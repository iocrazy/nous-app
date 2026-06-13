/**
 * Paperclip-style reply composer at the bottom of an issue detail
 * (A8.3 wired to real agents via aiLibraryService).
 *
 * Task 5 additions: replaced the plain <textarea> with a tiptap editor wired
 * to the reusable @-mention primitives (createResourceMentionExtension +
 * ResourceChipNode + ResourcePickerSuggestion). On "@" we open a resource
 * picker scoped to `teamId`; on pick we insert an inline chip; on send we
 * emit staged file attachments AND collected resource refs together.
 *
 * Preserved from before: agent picker, attachment chip strip
 * (ChatAttachmentPicker), paste (useComposerPaste), drag-drop
 * (useComposerDropzone), and ⌘↩ / Ctrl+↩ to send (multi-line replies, NOT
 * plain Enter).
 */

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Send, ChevronDown } from 'lucide-react';
import { useEditor, EditorContent } from '@tiptap/react';
import StarterKit from '@tiptap/starter-kit';
import Placeholder from '@tiptap/extension-placeholder';
import type { Editor } from '@tiptap/core';
import type { ClipboardEvent } from 'react';
import type { AgentRef } from './types';
import { ChatAttachmentPicker } from '../ChatAttachmentPicker';
import type { StagedAttachment } from '../ChatAttachmentPicker';
import { useChatAttachmentUpload } from '../../hooks/useChatAttachmentUpload';
import { useComposerDropzone } from '../../hooks/useComposerDropzone';
import { useComposerPaste } from '../../hooks/useComposerPaste';
import { useResourceSearch } from '../../hooks/useResourceSearch';
import { createResourceMentionExtension } from '../chat/ChatInputResourceMention';
import { ResourcePickerSuggestion } from '../chat/ResourcePickerSuggestion';
import type { ResourceRefAttachment, ResourceSearchResult } from '../../types';

/** Merged attachment payload the parent forwards to the backend: staged file
 *  uploads plus collected resource references from @-mention chips. */
export type ComposerAttachment = StagedAttachment | ResourceRefAttachment;

interface IssueReplyBoxProps {
  agents: AgentRef[];
  defaultAgentId?: string | null;
  /** Parent owns submission, returns rejection on error so we can keep content.
   *  Third arg carries staged attachments + resource refs (may be empty). */
  onSubmit: (body: string, agentId: string | null, attachments: ComposerAttachment[]) => Promise<void>;
  disabled?: boolean;
  /** Scope the @-mention resource picker to this team + personal resources. */
  teamId?: string;
}

/** Walk the tiptap doc and collect all resourceRef nodes (mirrors ChatInput). */
function collectRefs(editor: Editor): ResourceRefAttachment[] {
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

export const IssueReplyBox: React.FC<IssueReplyBoxProps> = ({
  agents,
  defaultAgentId,
  onSubmit,
  disabled,
  teamId,
}) => {
  const { t } = useTranslation();
  const [agentId, setAgentId] = useState<string | null>(defaultAgentId ?? null);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [stagedAttachments, setStagedAttachments] = useState<StagedAttachment[]>([]);

  const selectedAgent = agents.find((a) => a.id === agentId) ?? null;

  // Upload pipeline — shared with ChatAttachmentPicker
  const { handleFiles, uploading } = useChatAttachmentUpload({
    attachments: stagedAttachments,
    onChange: setStagedAttachments,
  });

  // Block send while any pasted/dropped file is still uploading — otherwise
  // hitting Cmd+Enter mid-upload silently drops the in-flight chips.
  const inputBlocked = disabled || submitting || uploading;

  // Drag-and-drop handler for the wrapper div
  const { rootProps, isDragActive } = useComposerDropzone({
    onFiles: handleFiles,
    disabled: inputBlocked,
  });

  // Paste-from-clipboard handler for the editor
  const { onPaste } = useComposerPaste({
    onFiles: handleFiles,
    disabled: inputBlocked,
  });

  // --- Resource @-mention picker state (mirrors AIChatPanel) ---
  const editorRef = useRef<Editor | null>(null);
  const [mentionOpen, setMentionOpen] = useState(false);
  const [mentionQuery, setMentionQuery] = useState('');
  const [mentionActiveKind, setMentionActiveKind] = useState<
    '' | 'video' | 'image' | 'doc' | 'audio' | 'pdf'
  >('');
  const [mentionActiveIndex, setMentionActiveIndex] = useState(0);
  const { data: mentionData, loading: mentionLoading } = useResourceSearch(
    mentionQuery,
    mentionActiveKind,
    teamId,
  );

  // submit() must be declared BEFORE useEditor — the editor's handleKeyDown
  // closure references it (avoid a TDZ reference). Access the latest editor
  // through editorRef so this stays stable across renders.
  const submit = useCallback(async () => {
    const editor = editorRef.current;
    if (!editor) return;
    const text = editor.getText().trim();
    const refs = collectRefs(editor);
    if ((!text && refs.length === 0) || submitting || uploading) return;
    setSubmitting(true);
    try {
      await onSubmit(text, agentId, [...stagedAttachments, ...refs]);
      editor.commands.clearContent(true);
      setStagedAttachments([]);
    } catch {
      // parent toasts; keep editor content AND chips so the user can retry
    } finally {
      setSubmitting(false);
    }
  }, [agentId, onSubmit, stagedAttachments, submitting, uploading]);

  // Keep a stable ref to submit so the editor key-handler closure (created
  // once) always calls the latest version without re-binding the editor.
  const submitRef = useRef(submit);
  useEffect(() => { submitRef.current = submit; }, [submit]);

  // onPaste may change identity; forward through a ref so handlePaste is stable.
  const onPasteRef = useRef(onPaste);
  useEffect(() => { onPasteRef.current = onPaste; }, [onPaste]);

  const editor = useEditor({
    extensions: [
      StarterKit.configure({ hardBreak: false }),
      Placeholder.configure({ placeholder: 'Reply' }),
      // The parent drives the actual picker; onPick is a no-op resolver.
      createResourceMentionExtension({ onPick: () => Promise.resolve(null) }),
    ],
    editorProps: {
      attributes: {
        class: [
          'w-full bg-transparent px-3 py-2 text-[14px] text-ink-200',
          'placeholder-ink-600 focus:outline-none',
          'min-h-[72px] max-h-[200px] overflow-y-auto leading-6',
          'tiptap-composer',
        ].join(' '),
      },
      handleKeyDown(_view, event) {
        // ⌘↩ / Ctrl+↩ → send (NOT plain Enter — issue replies are multi-line)
        if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
          event.preventDefault();
          void submitRef.current();
          return true;
        }
        // "@" → open the resource picker. Let the char insert first, then open
        // on the next tick with an empty query (live tracking updates it after).
        if (event.key === '@') {
          setTimeout(() => {
            setMentionQuery('');
            setMentionActiveIndex(0);
            setMentionOpen(true);
          }, 0);
          return false; // don't prevent insertion
        }
        return false;
      },
      handlePaste(_view, event) {
        const cb = onPasteRef.current;
        if (!cb) return false;
        // tiptap gives the raw DOM ClipboardEvent; the React synthetic type is
        // compatible for our purposes (we only read clipboardData.files).
        cb(event as unknown as ClipboardEvent);
        return false; // let tiptap also handle text paste
      },
    },
    editable: !inputBlocked,
  });

  // Expose the editor to closures + the submit callback.
  useEffect(() => {
    editorRef.current = editor;
  }, [editor]);

  // Live-query tracking — after "@" is typed, follow subsequent chars and
  // update mentionQuery; close the picker on whitespace / no preceding "@".
  useEffect(() => {
    if (!editor) return;
    const handleUpdate = () => {
      const { from } = editor.state.selection;
      const textBefore = editor.state.doc.textBetween(Math.max(0, from - 80), from);
      const atIdx = textBefore.lastIndexOf('@');
      if (atIdx === -1) {
        setMentionOpen(false);
        return;
      }
      const queryCandidate = textBefore.slice(atIdx + 1);
      if (/[\s\n,;]/.test(queryCandidate)) {
        setMentionOpen(false);
        return;
      }
      setMentionQuery(queryCandidate);
    };
    editor.on('update', handleUpdate);
    return () => { editor.off('update', handleUpdate); };
  }, [editor]);

  // Keep the editor's editable flag synced with the blocked state.
  useEffect(() => {
    if (editor) editor.setEditable(!inputBlocked);
  }, [editor, inputBlocked]);

  // Close the picker on Escape or click-outside (mirrors AIChatPanel).
  useEffect(() => {
    if (!mentionOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setMentionOpen(false);
    };
    const onClick = (e: MouseEvent) => {
      const target = e.target as HTMLElement;
      if (!target.closest('[data-testid="resource-picker"]')) {
        setMentionOpen(false);
      }
    };
    document.addEventListener('keydown', onKey);
    document.addEventListener('mousedown', onClick);
    return () => {
      document.removeEventListener('keydown', onKey);
      document.removeEventListener('mousedown', onClick);
    };
  }, [mentionOpen]);

  const handleMentionSelect = useCallback((item: ResourceSearchResult) => {
    const ed = editorRef.current;
    if (ed) {
      (ed.commands as unknown as {
        insertResourceRef: (item: ResourceSearchResult) => boolean;
      }).insertResourceRef(item);
    }
    setMentionOpen(false);
    setMentionQuery('');
    ed?.commands.focus();
  }, []);

  return (
    <div
      {...rootProps}
      className="relative border border-ink-800 rounded-lg bg-ink-900/50 mx-4 mb-4"
    >
      {/* @-mention resource picker — absolutely-positioned overlay above the composer */}
      {mentionOpen && (
        <div className="absolute bottom-full left-0 right-0 z-20 flex justify-start px-2 pb-1">
          <ResourcePickerSuggestion
            items={mentionData.results}
            query={mentionQuery}
            loading={mentionLoading}
            counts={mentionData.counts}
            activeKind={mentionActiveKind}
            onKindChange={setMentionActiveKind}
            onSelect={handleMentionSelect}
            activeIndex={mentionActiveIndex}
          />
        </div>
      )}

      {/* Attachment chip strip */}
      <div className="px-3 pt-2">
        <ChatAttachmentPicker
          attachments={stagedAttachments}
          onChange={setStagedAttachments}
          disabled={inputBlocked}
        />
      </div>

      <EditorContent editor={editor} />

      <div className="flex items-center gap-2 px-2 pb-2 border-t border-ink-800/80 pt-2">
        <span className="text-[12px] text-ink-600 ml-1">⌘↩ to send</span>
        <div className="relative ml-auto">
          <button
            type="button"
            onClick={() => setPickerOpen((v) => !v)}
            className="inline-flex items-center gap-1 px-2 py-1 text-[12px] rounded bg-ink-800 text-ink-300 hover:bg-ink-700"
          >
            {selectedAgent ? (
              <>
                <span
                  className={`inline-flex items-center justify-center rounded-full w-3.5 h-3.5 text-[10px] font-semibold text-white ${selectedAgent.avatar_color ?? 'bg-ink-600'}`}
                >
                  {selectedAgent.name.slice(0, 1).toUpperCase()}
                </span>
                {selectedAgent.name}
              </>
            ) : (
              'No agent'
            )}
            <ChevronDown size={11} />
          </button>
          {pickerOpen && (
            <div className="absolute right-0 bottom-full mb-1 w-56 max-h-72 overflow-y-auto bg-ink-900 border border-ink-800 rounded shadow-lg z-10">
              <button
                onClick={() => { setAgentId(null); setPickerOpen(false); }}
                className="w-full flex items-center gap-2 px-2 py-1.5 text-[12px] text-ink-400 hover:bg-ink-800 text-left"
              >
                No agent (just comment)
              </button>
              <div className="border-t border-ink-800/60" />
              {agents.length === 0 && (
                <div className="px-2 py-2 text-[12px] text-ink-500 italic">No agents available</div>
              )}
              {agents.map((a) => (
                <button
                  key={a.id}
                  onClick={() => { setAgentId(a.id); setPickerOpen(false); }}
                  className="w-full flex items-center gap-2 px-2 py-1.5 text-[12px] text-ink-300 hover:bg-ink-800 text-left"
                >
                  <span className={`inline-flex items-center justify-center rounded-full w-4 h-4 text-[10px] text-white ${a.avatar_color ?? 'bg-ink-600'}`}>
                    {a.name.slice(0, 1).toUpperCase()}
                  </span>
                  {a.name}
                </button>
              ))}
            </div>
          )}
        </div>
        <button
          aria-label="send"
          onClick={() => void submit()}
          disabled={inputBlocked}
          className="inline-flex items-center gap-1 px-3 py-1 text-[12px] rounded bg-indigo-500 text-white hover:bg-indigo-600 disabled:opacity-40 disabled:cursor-not-allowed"
        >
          <Send size={11} /> {submitting ? 'Sending…' : 'Send'}
        </button>
      </div>

      {/* Drag-active overlay */}
      {isDragActive && (
        <div className="absolute inset-0 z-10 flex items-center justify-center pointer-events-none bg-blue-500/10 border-2 border-dashed border-blue-400 rounded-lg">
          <span className="text-sm font-medium text-blue-200">{t('chat.attachments.dropToUpload')}</span>
        </div>
      )}
    </div>
  );
};
