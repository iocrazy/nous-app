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
import { Send, ChevronDown, Clock } from 'lucide-react';
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
import type { AssetGridRow } from '../assets/AssetGridPicker';
import { useMentionAssetsTab } from '../chat/useMentionAssetsTab';
import { useMentionOutputsTab } from '../chat/useMentionOutputsTab';
import type { OutputMentionRow } from '../chat/outputMentionRows';
import {
  stageOutput,
  toOutputAttachment,
  toStagedOutput,
  type StagedOutputRef,
} from '../chat/stagedOutputs';
import {
  MAX_ASSET_REF_ATTACHMENTS,
  MAX_OUTPUT_REF_ATTACHMENTS,
} from '../chat/attachmentLimits';
import {
  stageAsset as stageAssetInto,
  toAssetAttachment,
  type StagedAssetRef,
} from '../chat/stagedResources';
import { useToast } from '../Toast';
import type {
  AssetRefAttachment,
  OutputRefAttachment,
  ResourceRefAttachment,
  ResourceSearchResult,
} from '../../types';
import { IssueCommentTriggerChip } from './IssueCommentTriggerChip';
import { isNoteDraft } from './isNoteDraft';
import { LaterPopover } from './LaterPopover';
import type { CommentTriggerPreview } from '../../services/issueMessageService';

/** Merged attachment payload the parent forwards to the backend: staged file
 *  uploads, collected resource references from @-mention chips, and staged
 *  library ASSETS (v2 Task 3).
 *
 *  All three arrive as `IssueMessagePost.attachments`, which is already
 *  `List[AttachmentRequest]` — `run_issue_reply_step` rebuilds them and calls
 *  the same `run_session_turn` the chat router does, so `asset_ref` is
 *  resolved (and capped) by the very same code. No backend change was needed
 *  to make this reach the agent. */
export type ComposerAttachment =
  | StagedAttachment
  | ResourceRefAttachment
  | AssetRefAttachment
  | OutputRefAttachment;

interface IssueReplyBoxProps {
  agents: AgentRef[];
  defaultAgentId?: string | null;
  /** Parent owns submission, returns rejection on error so we can keep content.
   *  Third arg carries staged attachments + resource refs (may be empty).
   *  Fourth carries the agents this one comment must not wake (subtractive —
   *  the server only drops from the set it computed itself). */
  onSubmit: (
    body: string,
    agentId: string | null,
    attachments: ComposerAttachment[],
    suppressAgentIds?: string[],
  ) => Promise<void>;
  disabled?: boolean;
  /** One line above the editor saying what a comment does right now (e.g.
   *  "the agent is running — this is picked up before its next step").
   *  Server phase decides it; the box only shows it. */
  hint?: string;
  /** The server's verdict on what a comment here would start. Null while it
   *  loads, or when the trigger-chip flag is off — the chip stays hidden. */
  triggerPreview?: CommentTriggerPreview | null;
  /** Resolved display name for triggerPreview.agent_id (the endpoint is a pure
   *  predicate and returns only the id). */
  triggerAgentName?: string;
  /** Fires when the draft crosses the `/note` boundary (either direction),
   *  carrying the draft body while it IS a note and null once it stops being
   *  one. The parent re-asks the server's preview endpoint with it — this
   *  callback is a refetch trigger, never a verdict (the chip renders only
   *  what the server returned). */
  onNoteBoundaryChange?: (noteBody: string | null) => void;
  /** Scope the @-mention resource picker to this team + personal resources. */
  teamId?: string;
  /** The issue this composer belongs to. Absent on surfaces that have no
   *  issue behind them — the "Later" affordance is then hidden rather than
   *  posting a wake-up at nothing (harness 2b-2 §5-2). */
  issueId?: number;
  /** A wake-up was armed; the page re-reads its schedules panel. */
  onScheduled?: () => void;
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
  hint,
  triggerPreview,
  triggerAgentName,
  onNoteBoundaryChange,
  teamId,
  issueId,
  onScheduled,
}) => {
  const { t } = useTranslation();
  const { addToast } = useToast();
  const [agentId, setAgentId] = useState<string | null>(defaultAgentId ?? null);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [laterOpen, setLaterOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [stagedAttachments, setStagedAttachments] = useState<StagedAttachment[]>([]);
  // Library ASSETS staged for this comment. Their own list beside the files,
  // exactly as in AIChatPanel: an asset resolves through a different backend
  // path and carries a different snapshot, and folding the two together would
  // make every consumer re-derive which kind it is holding.
  const [stagedAssets, setStagedAssets] = useState<StagedAssetRef[]>([]);
  // Output CITATIONS staged for this comment (3a Task 6). A third list for the
  // same reason assets got a second one, one step further out: a citation
  // carries no bytes at all and is validated against THIS ISSUE by a resolver
  // nothing else here answers to.
  const [stagedOutputs, setStagedOutputs] = useState<StagedOutputRef[]>([]);
  // Store WHICH agent the user skipped, not a bare flag: if the assignee changes
  // between the preview and the send, `suppressed` below stops matching and the
  // chip re-arms itself, so a skip aimed at agent A can never silently swallow
  // agent B's run. Per-comment and one-shot — reset on submit, never persisted.
  const [suppressedAgentId, setSuppressedAgentId] = useState<string | null>(null);
  const [draftEmpty, setDraftEmpty] = useState(true);
  const suppressed =
    suppressedAgentId != null && suppressedAgentId === triggerPreview?.agent_id;

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
    if (
      (!text
        && refs.length === 0
        && stagedAssets.length === 0
        && stagedOutputs.length === 0)
      || submitting
      || uploading
    ) {
      return;
    }
    setSubmitting(true);
    try {
      await onSubmit(
        text,
        agentId,
        [
          ...stagedAttachments,
          ...refs,
          ...stagedAssets.map(toAssetAttachment),
          ...stagedOutputs.map(toOutputAttachment),
        ],
        suppressed && suppressedAgentId ? [suppressedAgentId] : undefined,
      );
      editor.commands.clearContent(true);
      setStagedAttachments([]);
      // Cleared only on SUCCESS — the catch below keeps everything staged so a
      // rejected send can be retried without re-picking.
      setStagedAssets([]);
      // Same rule, and it matters more here: a refused citation rejects the
      // WHOLE comment, so a retry that had to re-pick every reference would
      // punish the writer for the server's one objection.
      setStagedOutputs([]);
      // One-shot: the next comment starts armed again.
      setSuppressedAgentId(null);
    } catch {
      // parent toasts; keep editor content AND chips so the user can retry
    } finally {
      setSubmitting(false);
    }
  }, [
    agentId,
    onSubmit,
    stagedAssets,
    stagedAttachments,
    stagedOutputs,
    submitting,
    suppressed,
    suppressedAgentId,
    uploading,
  ]);

  // Keep a stable ref to submit so the editor key-handler closure (created
  // once) always calls the latest version without re-binding the editor.
  const submitRef = useRef(submit);
  useEffect(() => { submitRef.current = submit; }, [submit]);

  // onPaste may change identity; forward through a ref so handlePaste is stable.
  const onPasteRef = useRef(onPaste);
  useEffect(() => { onPasteRef.current = onPaste; }, [onPaste]);

  // Same reason as `submitRef`: the editor's key handler closure is built once
  // by `useEditor`, so it must reach the LATEST routing callback rather than
  // the one that existed at mount — otherwise it would forever read
  // `mentionOpen === false` and route nothing.
  //
  // Seeded with a no-op rather than with `handleMentionKey`: that callback is
  // declared BELOW `useEditor` (it depends on the picker state the editor's
  // own handlers set up), and naming it here would be a temporal-dead-zone
  // ReferenceError on every render. The effect underneath it does the wiring.
  const mentionKeyRef = useRef<(key: 'ArrowUp' | 'ArrowDown' | 'Enter') => boolean>(
    () => false,
  );

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
        // The open Assets tab gets first refusal on ↑ / ↓ / ↵. It answers
        // false whenever it is not showing or nothing is highlighted, so every
        // branch below still runs in that case. Checked BEFORE ⌘↩ only for
        // the bare keys — a modifier means send, never navigate.
        if (
          !event.metaKey
          && !event.ctrlKey
          && (event.key === 'ArrowUp' || event.key === 'ArrowDown' || event.key === 'Enter')
          && mentionKeyRef.current(event.key)
        ) {
          event.preventDefault();
          return true;
        }
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

  // The parent's callback may change identity; route through a ref so the
  // editor-update listener below stays stable.
  const onNoteBoundaryChangeRef = useRef(onNoteBoundaryChange);
  useEffect(() => {
    onNoteBoundaryChangeRef.current = onNoteBoundaryChange;
  }, [onNoteBoundaryChange]);
  // Last note-ness we reported — boundary-triggered, not debounced: typing
  // WITHIN a note (or within a normal comment) never refetches, only the flip
  // does, and the flip is exactly when the server's verdict could change.
  const lastNoteRef = useRef(false);

  // Track whether anything is staged to send — the trigger chip only discloses
  // a wake that's actually imminent (mirrors multica's
  // shouldRenderComposerHandoffPreview: empty body → no preview row).
  // The same editor-update stream also watches the `/note` boundary: isNoteDraft
  // here only decides WHEN to re-ask the server, never what the chip says.
  useEffect(() => {
    if (!editor) return;
    const sync = () => {
      const text = editor.getText();
      setDraftEmpty(!text.trim() && collectRefs(editor).length === 0);
      // NOTE: staged assets are deliberately NOT counted here. `draftEmpty`
      // drives the trigger chip, whose question is "will ⌘↩ wake an agent",
      // and the editor-update stream this runs on does not fire when an asset
      // is staged. Reading a stale answer would be worse than a conservative
      // one; the SUBMIT guard above is what actually decides sendability.
      const note = isNoteDraft(text);
      if (note !== lastNoteRef.current) {
        lastNoteRef.current = note;
        onNoteBoundaryChangeRef.current?.(note ? text : null);
      }
    };
    sync();
    editor.on('update', sync);
    return () => {
      editor.off('update', sync);
    };
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

  // `useMentionAssetsTab` is declared BELOW — its `onSelect` closes the picker,
  // and closing resets the tab, so the two reference each other. The ref breaks
  // that cycle without making either one re-created on every render.
  const mentionAssetsReset = useRef<() => void>(() => {});
  // The Outputs tab's own reset, broken out of the same cycle for the same
  // reason. Closing the picker must forget BOTH tabs — leaving one of them
  // active would reopen the popover on a body the reader did not choose.
  const mentionOutputsReset = useRef<() => void>(() => {});

  /**
   * Close, and forget which tab was open.
   *
   * The reset belongs on CLOSE, not on open: the live query updates on every
   * keystroke, so resetting there would bounce the user off the Assets tab the
   * moment they typed the next character. Closing ends the mention session,
   * which is the only moment the choice stops meaning anything.
   */
  const closeMentionPicker = useCallback(() => {
    setMentionOpen(false);
    mentionAssetsReset.current();
    mentionOutputsReset.current();
  }, []);

  // Close the picker on Escape or click-outside (mirrors AIChatPanel).
  useEffect(() => {
    if (!mentionOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') closeMentionPicker();
    };
    const onClick = (e: MouseEvent) => {
      const target = e.target as HTMLElement;
      if (!target.closest('[data-testid="resource-picker"]')) {
        closeMentionPicker();
      }
    };
    document.addEventListener('keydown', onKey);
    document.addEventListener('mousedown', onClick);
    return () => {
      document.removeEventListener('keydown', onKey);
      document.removeEventListener('mousedown', onClick);
    };
  }, [mentionOpen, closeMentionPicker]);

  const handleMentionSelect = useCallback((item: ResourceSearchResult) => {
    const ed = editorRef.current;
    if (ed) {
      (ed.commands as unknown as {
        insertResourceRef: (item: ResourceSearchResult) => boolean;
      }).insertResourceRef(item);
    }
    closeMentionPicker();
    setMentionQuery('');
    ed?.commands.focus();
  }, [closeMentionPicker]);

  /**
   * Picking an asset STAGES it — it does not insert a tiptap node.
   *
   * The cap is enforced HERE as well as on the server. The server refuses
   * anything past `MAX_ASSET_REF_ATTACHMENTS` and reports it in
   * `attachment_failures`, but an issue reply runs asynchronously in a DBOS
   * workflow and this composer never sees that field — so without this check
   * the ninth pick would be a silent no-op, which the "typed result and echo"
   * rule forbids for any user-triggered path. The copy is the same sentence
   * the chat banner shows for the same refusal, from the same key.
   */
  const handleMentionAssetSelect = useCallback(
    (row: AssetGridRow) => {
      const next = stageAssetInto(stagedAssets, {
        id: row.id,
        name: row.name,
        asset_type: row.asset_type,
        // Staging never picks an outfit; the chip's loadout menu does, and
        // null is the backend's "use the default loadout".
        loadout_id: null,
        cover_file_id: row.cover_file_id,
        scope_id: row.scope_id ?? null,
      });
      if (next.length > MAX_ASSET_REF_ATTACHMENTS) {
        addToast(
          t('chat.attachmentFailureReason.attachment_limit_exceeded', {
            n: MAX_ASSET_REF_ATTACHMENTS,
          }),
          'error',
        );
        return;
      }
      setStagedAssets(next);
      closeMentionPicker();
      setMentionQuery('');
      editorRef.current?.commands.focus();
    },
    [stagedAssets, addToast, t, closeMentionPicker],
  );

  /**
   * Picking an output STAGES a citation, version and all.
   *
   * The cap is enforced here as well as on the server, and the two refusals
   * differ in kind from the asset one: past `MAX_OUTPUT_REF_ATTACHMENTS` the
   * server rejects the WHOLE comment (`output_ref_limit_exceeded`) rather than
   * dropping the extras, and an issue reply runs asynchronously in a DBOS
   * workflow this composer never hears back from. Without this check the ninth
   * pick would be a silent no-op that produces a comment the writer believes
   * was sent and that never posts.
   */
  const handleMentionOutputSelect = useCallback(
    (row: OutputMentionRow) => {
      const next = stageOutput(stagedOutputs, toStagedOutput(row));
      if (next === null) {
        addToast(
          t('outputs.citationLimit', 'A comment can reference at most {{n}} outputs', {
            n: MAX_OUTPUT_REF_ATTACHMENTS,
          }),
          'error',
        );
        return;
      }
      setStagedOutputs(next);
      closeMentionPicker();
      setMentionQuery('');
      editorRef.current?.commands.focus();
    },
    [stagedOutputs, addToast, t, closeMentionPicker],
  );

  // The Assets tab itself — state, transport and key routing shared with
  // AIChatPanel, so the two composers cannot drift about what mentioning an
  // asset searches or which keys the grid claims.
  const mentionAssets = useMentionAssetsTab({
    pickerOpen: mentionOpen,
    onSelect: handleMentionAssetSelect,
  });
  // The Outputs tab. Only this composer has one: citations are issue-scoped,
  // and the chat panel — which has no issue behind it — refuses the kind.
  const mentionOutputs = useMentionOutputsTab({
    pickerOpen: mentionOpen,
    issueId: issueId ?? null,
    query: mentionQuery,
    onSelect: handleMentionOutputSelect,
  });
  useEffect(() => {
    mentionAssetsReset.current = mentionAssets.reset;
  }, [mentionAssets.reset]);
  useEffect(() => {
    mentionOutputsReset.current = mentionOutputs.reset;
  }, [mentionOutputs.reset]);
  // Same reason as `submitRef`: the editor's key handler closure is built once.
  //
  // Chained, not replaced. Each tab answers false unless it is the one
  // showing, so the order is not a priority — it is two independent refusals,
  // and whichever tab is open claims the key. Dropping either from the chain
  // would leave that tab's arrows dead with nothing on screen saying why.
  const assetsKey = mentionAssets.handleKey;
  const outputsKey = mentionOutputs.handleKey;
  useEffect(() => {
    mentionKeyRef.current = (key) => assetsKey(key) || outputsKey(key);
  }, [assetsKey, outputsKey]);

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
            onKindChange={(kind) => {
              // Going back to a resource kind leaves BOTH extra tabs. Missing
              // one would light two tabs at once over a single set of arrow
              // keys.
              mentionAssets.deactivate();
              mentionOutputs.deactivate();
              setMentionActiveKind(kind);
            }}
            onSelect={handleMentionSelect}
            activeIndex={mentionActiveIndex}
            assets={{
              ...mentionAssets.assets,
              // Activating one extra tab leaves the other. The two are
              // different populations sharing one keyboard, so both lit would
              // mean two bodies on screen and Enter picking from whichever the
              // code reached first.
              onActivate: () => {
                mentionOutputs.deactivate();
                mentionAssets.assets.onActivate();
              },
            }}
            // Only where an issue backs the composer. Without one every
            // citation would come back `output_ref_unresolvable`, so offering
            // the tab would be offering a control that cannot work.
            outputs={
              issueId != null
                ? {
                    ...mentionOutputs.outputs,
                    onActivate: () => {
                      mentionAssets.deactivate();
                      mentionOutputs.outputs.onActivate();
                    },
                  }
                : undefined
            }
          />
        </div>
      )}

      {hint && (
        <div className="px-3 pt-2 text-[11px] text-ink-500" data-testid="reply-hint">
          {hint}
        </div>
      )}

      {/* Attachment chip strip. The staged ASSETS ride in the same row as the
          files, which also brings the v2 loadout menu here for free — the chip
          and its menu live in ChatAttachmentPicker, not in either host. */}
      <div className="px-3 pt-2">
        <ChatAttachmentPicker
          attachments={stagedAttachments}
          onChange={setStagedAttachments}
          assets={stagedAssets}
          onAssetsChange={setStagedAssets}
          outputs={stagedOutputs}
          onOutputsChange={setStagedOutputs}
          disabled={inputBlocked}
        />
      </div>

      <EditorContent editor={editor} />

      <div className="flex items-center gap-2 px-2 pb-2 border-t border-ink-800/80 pt-2">
        <span className="text-[12px] text-ink-600 ml-1">⌘↩ to send</span>
        {/* Beside "⌘↩ to send" for the same reason the trigger chip is: both
            say what will happen to the text now in the box. */}
        {issueId != null && (
          <div className="relative">
            <button
              type="button"
              data-testid="reply-later"
              onClick={() => setLaterOpen((v) => !v)}
              disabled={inputBlocked}
              className="inline-flex items-center gap-1 px-2 py-1 text-[12px] rounded text-ink-400 hover:bg-ink-800 hover:text-ink-200 disabled:opacity-40"
            >
              <Clock size={11} /> {t('later.button', 'Later')}
            </button>
            {laterOpen && (
              <LaterPopover
                issueId={issueId}
                text={editor?.getText().trim() ?? ''}
                onClose={() => setLaterOpen(false)}
                onScheduled={() => {
                  setLaterOpen(false);
                  onScheduled?.();
                }}
              />
            )}
          </div>
        )}
        {/* Sits beside "⌘↩ to send" on purpose: it describes what that keystroke
            is about to cost. */}
        {triggerPreview && (
          <IssueCommentTriggerChip
            preview={triggerPreview}
            agentName={triggerAgentName}
            suppressed={suppressed}
            draftEmpty={draftEmpty}
            onToggle={() =>
              setSuppressedAgentId((cur) =>
                cur === triggerPreview.agent_id ? null : triggerPreview.agent_id,
              )
            }
          />
        )}
        <div className="relative ml-auto">
          <button
            type="button"
            onClick={() => setPickerOpen((v) => !v)}
            className="inline-flex items-center gap-1 px-2 py-1 text-[12px] rounded bg-ink-800 text-ink-300 hover:bg-ink-700"
          >
            {selectedAgent ? (
              <>
                <span
                  className={`inline-flex items-center justify-center rounded-full w-3.5 h-3.5 text-[10px] font-semibold text-ink-50 ${selectedAgent.avatar_color ?? 'bg-ink-600'}`}
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
                  <span className={`inline-flex items-center justify-center rounded-full w-4 h-4 text-[10px] text-ink-50 ${a.avatar_color ?? 'bg-ink-600'}`}>
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
