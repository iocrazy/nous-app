/**
 * AIChatPanel — right-rail chat panel used by ScriptEditor + StoryboardWorkbench.
 *
 * As of U2 this talks to the unified AI Library framework:
 *   - Agents come from ``/api/v1/ai-library/agents`` (the same list the
 *     sidebar AI LIBRARY section renders).
 *   - Sessions + messages flow through ``/api/v1/ai-library/sessions``
 *     and every chat turn produces an agent_runs row, so the pulse /
 *     Runs tab / Usage dashboard / budget guard all work for chat too.
 *
 * The U1 backend does NOT stream yet — responses are delivered as a
 * single POST. We show a typing indicator during the in-flight request
 * instead of character-by-character streaming. Streaming SSE can be
 * added in a follow-up if the perceived latency is an issue.
 */

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Plus, Search, X } from 'lucide-react';
import type { Editor } from '@tiptap/core';

import { aiLibraryService } from '../services/aiLibraryService';
import type { ChatScriptContextInput } from '../services/aiLibraryService';
import type {
  AILibraryAgent,
  AIChatMessage,
  AIChatMessageAttachment,
  ChatSession,
  ChatToolCall,
  ResourceRefAttachment,
  ResourceSearchResult,
} from '../types';
import { AgentSelector } from './AgentSelector';
import { AgentIdentityHeader } from './agentActivity/AgentIdentityHeader';
import {
  ContextCapsule,
  type ContextCapsuleValue,
} from './agentActivity/ContextCapsule';
import {
  QuickActions,
  type QuickActionContext,
} from './agentActivity/QuickActions';
import { SessionList, type SessionItem } from './SessionList';
import { MessageBubble } from './chat/AIChatBubble';
import { questionFromChatMetadata, type TypedQuestion } from './Todolist/questionTypes';
import { ChatTrajectoryView } from './chat/ChatTrajectoryView';
import { chatRunId } from './chat/chatMessageMeta';
import { deliverSteer, InboxTargetEndedError } from '../services/agentInboxService';
import { TypingIndicator } from './chat/TypingIndicator';
import {
  AttachmentFailureBanner,
  type AttachmentFailure,
} from './chat/AttachmentFailureBanner';
import { ChatInput } from './chat/ChatInput';
import { CommitmentsPanel } from './CommitmentsPanel';
import { ChatAttachmentPicker, type StagedAttachment } from './ChatAttachmentPicker';
import {
  mergeAssetAttachments,
  mergeRefAttachments,
  stageAsset as stageAssetInto,
  stageResource as stageResourceInto,
  type AssetRefInsertItem,
  type StagedAssetRef,
  type StagedResourceRef,
} from './chat/stagedResources';
import type { ResourceRefInsertItem } from './chat/ChatInputResourceMention';
import { ResourcePickerSuggestion } from './chat/ResourcePickerSuggestion';
import type { AssetGridRow } from './assets/AssetGridPicker';
import { EmptyState } from './chat/EmptyState';
import { useToast } from './Toast';
import { useChatAttachmentUpload } from '../hooks/useChatAttachmentUpload';
import { useComposerDropzone } from '../hooks/useComposerDropzone';
import { useComposerPaste } from '../hooks/useComposerPaste';
import { useResourceSearch } from '../hooks/useResourceSearch';
import { useGlobalChatStore } from '../stores/globalChatStore';
import { useComposerResourceAttach } from '../hooks/useComposerResourceAttach';
import { useComposerAssetAttach } from '../hooks/useComposerAssetAttach';
import { useMentionAssetsTab } from './chat/useMentionAssetsTab';
import { useResourceProcessingFollowUps } from '../hooks/useResourceProcessingFollowUps';
import { providerErrorMessage } from '../utils/providerErrorMessage';

export interface AIChatPanelProps {
  /** String form of the project's BIGINT id, for display + session tagging.
   *  Optional when agentSlug is provided (agent-scoped mode, no project filter). */
  projectId?: string;
  contextType?: 'script' | 'storyboard';
  contextId?: string;
  onApplyContent?: (content: string) => void;
  onClose?: () => void;
  /** When provided, locks the panel to this agent (hides the AgentSelector) and
   *  scopes sessions to the agent only (no project filter). Existing callers that
   *  pass projectId but not agentSlug are completely unaffected. */
  agentSlug?: string;
  /** Controlled session-history overlay (FloatingChatWidget). When defined,
   *  the inline SESSIONS section is hidden and the session list renders as a
   *  left slide-over instead — opened by the widget's title-bar button.
   *  Callers that omit it (ChatPage) keep the inline list, unaffected. */
  sessionsOverlayOpen?: boolean;
  onSessionsOverlayClose?: () => void;
}

function formatTimestamp(isoString?: string | null): string {
  if (!isoString) return '';
  return new Date(isoString).toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
  });
}

/**
 * Extract the per-turn Skill / Delegate trace from a persisted assistant
 * message. Backend folds it into ``metadata_json.tool_calls``; older
 * rows (pre Step B) just won't have the field. Returns [] for any shape
 * we don't recognise so the renderer can drop in unconditionally.
 */
function extractToolCalls(msg: AIChatMessage): ChatToolCall[] {
  const meta = msg.metadata_json;
  if (!meta || typeof meta !== 'object') return [];
  const raw = (meta as Record<string, unknown>).tool_calls;
  if (!Array.isArray(raw)) return [];
  return raw.filter(
    (entry): entry is ChatToolCall =>
      typeof entry === 'object' &&
      entry !== null &&
      typeof (entry as ChatToolCall).name === 'string',
  );
}


/**
 * Extract the Plan Mode paused-for-approval state (Phase 4.5). Backend
 * folds it into ``metadata_json.awaiting_approval`` when a hook returned
 * await_approval; absent on the common ran-to-completion turn.
 */
function extractAwaitingApproval(
  msg: AIChatMessage,
): { approvalId: string | null; reason: string; hook?: string } | undefined {
  const meta = msg.metadata_json;
  if (!meta || typeof meta !== 'object') return undefined;
  const raw = (meta as Record<string, unknown>).awaiting_approval;
  if (!raw || typeof raw !== 'object') return undefined;
  const entry = raw as Record<string, unknown>;
  return {
    approvalId: typeof entry.approval_id === 'string' ? entry.approval_id : null,
    reason: typeof entry.reason === 'string' ? entry.reason : '',
    hook: typeof entry.hook === 'string' ? entry.hook : undefined,
  };
}

/**
 * Phase 2a: the typed question an assistant turn parked on. Backend folds it
 * into ``metadata_json.awaiting_input`` (Task 4) and stamps ``answered`` /
 * ``superseded`` there once resolved; absent on the common turn.
 */
function extractAwaitingInput(msg: AIChatMessage): TypedQuestion | undefined {
  return questionFromChatMetadata(msg.metadata_json) ?? undefined;
}

/** Validate the project id (digits-only) but KEEP it a string — project ids
 *  are Snowflake BIGINTs and Number() rounds them past 2^53. The backend
 *  parses the string as an exact int64. */
function parseProjectId(projectId: string | undefined): string | undefined {
  if (!projectId) return undefined;
  return /^\d+$/.test(projectId) ? projectId : undefined;
}

// Per-agent "last active session" memory: switching agents (or reopening the
// widget) resumes where you left off with that agent instead of always
// landing on the newest session. Plain localStorage map slug → session id.
const LAST_SESSION_KEY = 'ai_chat_last_session';

function readLastSessionMap(): Record<string, string> {
  try {
    const raw = localStorage.getItem(LAST_SESSION_KEY);
    const parsed = raw ? JSON.parse(raw) : {};
    return parsed && typeof parsed === 'object' ? parsed : {};
  } catch {
    return {};
  }
}

function rememberLastSession(agentSlug: string, sessionId: string): void {
  try {
    localStorage.setItem(
      LAST_SESSION_KEY,
      JSON.stringify({ ...readLastSessionMap(), [agentSlug]: sessionId }),
    );
  } catch {
    /* storage full/blocked — memory is a nicety, never fatal */
  }
}

/**
 * §5.3: turn a context capsule into the structured selection handle sent
 * alongside the chat turn. Ids only — no quoted_text, the selection text is
 * already folded into the outgoing message content by handleSend, so
 * repeating it here would just double the token spend for no benefit.
 *
 * Returns null when there's no capsule, or the capsule carries no id
 * (scene_id/element_id) to hand the agent — a bare text-only capsule isn't
 * worth a block.
 */
export function buildScriptContext(
  capsule: ContextCapsuleValue | null,
): ChatScriptContextInput | null {
  if (!capsule) return null;
  if (!capsule.sceneId && !capsule.elementId) return null;
  return {
    scene_id: capsule.sceneId ?? null,
    element_ids: capsule.elementId ? [capsule.elementId] : [],
    element_type: capsule.elementType ?? null,
    scene_label: capsule.sceneLabel ?? null,
    cross_scene: Boolean(capsule.crossScene),
  };
}

export function AIChatPanel({
  projectId,
  contextType,
  contextId,
  onApplyContent,
  onClose,
  agentSlug,
  sessionsOverlayOpen,
  onSessionsOverlayClose,
}: AIChatPanelProps): React.ReactElement {
  const { t } = useTranslation();
  const { addToast } = useToast();

  const [agents, setAgents] = useState<AILibraryAgent[]>([]);
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  // Session-history search + scope (slide-over only). Search runs
  // server-side (SQL ILIKE over the whole history); scope 'all' switches to
  // the cross-agent list where every row carries an agent badge. Both reset
  // when the overlay closes so it reopens fresh.
  const [sessionSearch, setSessionSearch] = useState('');
  const [sessionScope, setSessionScope] = useState<'current' | 'all'>('current');
  // Server-fetched rows for the overlay (search active or scope=all).
  // null = show the panel's own per-agent session list untouched.
  const [overlaySessions, setOverlaySessions] = useState<ChatSession[] | null>(null);
  useEffect(() => {
    if (!sessionsOverlayOpen) {
      setSessionSearch('');
      setSessionScope('current');
      setOverlaySessions(null);
    }
  }, [sessionsOverlayOpen]);
  const [messages, setMessages] = useState<AIChatMessage[]>([]);
  const [selectedAgentSlug, setSelectedAgentSlug] = useState<string | null>(null);
  // T6: count of attachments the backend couldn't resolve for the most
  // recently completed turn (from the stream's 'done' event). Ephemeral —
  // not persisted on the message row — so it's cleared on the next send
  // and whenever the active session changes, rather than tied to a
  // specific message id.
  const [attachmentFailureCount, setAttachmentFailureCount] = useState<number | undefined>(
    undefined,
  );
  // P5: the failures themselves, so the banner can name the REASON. Kept
  // beside the count rather than replacing it — a turn can report failures
  // whose reason this build has no string for, and the count is still true.
  const [attachmentFailures, setAttachmentFailures] = useState<
    AttachmentFailure[] | undefined
  >(undefined);
  useEffect(() => {
    setAttachmentFailureCount(undefined);
    setAttachmentFailures(undefined);
  }, [activeSessionId]);

  // Agent-scoped mode: when agentSlug prop is provided the panel locks to that
  // agent (no selector) and scopes sessions without a project filter. When absent,
  // the user-selected agent drives everything — identical to the existing behavior.
  const lockedAgent = agentSlug ?? null;
  const effectiveAgentSlug = lockedAgent ?? selectedAgentSlug;

  // AI Library sidebar / Sessions page "open chat with this agent": consume
  // the one-shot request from globalChatStore (nonce keyed so re-clicking the
  // same agent re-fires). With a sessionId, seed the per-agent last-session
  // memory FIRST so the agent-switch effect resumes straight into that
  // session; when the agent is already selected that effect won't re-run, so
  // switch the session directly. Ignored in locked mode — an embedded,
  // agent-locked panel must not be hijacked by the global sidebar.
  const chatRequest = useGlobalChatStore((s) => s.chatRequest);
  useEffect(() => {
    if (!chatRequest || lockedAgent) return;
    const { agentSlug: reqSlug, sessionId: reqSession } = chatRequest;
    if (reqSession) rememberLastSession(reqSlug, reqSession);
    if (reqSession && selectedAgentSlug === reqSlug) {
      setActiveSessionId(reqSession);
      setMessages([]);
      void loadSessionMessages(reqSession);
    }
    setSelectedAgentSlug(reqSlug);
    useGlobalChatStore.getState().consumeChatRequest();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chatRequest, lockedAgent]);

  // Script editor "select text → AI chat": a staged selection is injected into
  // the composer as a quoted reference (blockquote tagged with its source
  // scene), then the input is focused so the user can instruct the AI about it.
  // The composer editor mounts a frame or two after the panel opens, so retry
  // over a few frames until chatEditorRef is live; only then consume the quote.
  //
  // A7 (design §D): the selection is now held as a CAPSULE above the composer
  // instead of being spliced into it as a blockquote. Splicing meant the
  // user's instruction and the quoted script became one blob they had to edit
  // around; as a capsule it stays labelled, stays removable, and keeps its
  // scene/element ids attached until send.
  const pendingQuote = useGlobalChatStore((s) => s.pendingQuote);
  const [contextCapsule, setContextCapsule] = useState<ContextCapsuleValue | null>(null);
  useEffect(() => {
    if (!pendingQuote) return;
    setContextCapsule({
      text: pendingQuote.text,
      sceneLabel: pendingQuote.sceneLabel,
      sceneId: pendingQuote.sceneId,
      elementId: pendingQuote.elementId,
      elementType: pendingQuote.elementType,
      crossScene: pendingQuote.crossScene,
    });
    useGlobalChatStore.getState().consumePendingQuote();
    // The composer mounts a frame or two after the panel opens, so retry over
    // a few frames before giving up on focusing it.
    let raf = 0;
    let tries = 0;
    const focusComposer = () => {
      const editor = chatEditorRef.current;
      if (!editor) {
        if (tries++ > 30) return; // give up quietly rather than loop forever
        raf = requestAnimationFrame(focusComposer);
        return;
      }
      editor.chain().focus('end').run();
    };
    focusComposer();
    return () => {
      if (raf) cancelAnimationFrame(raf);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingQuote]);

  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  // T10: Chat (bubbles) | Trajectory (runs, step by step) over the same messages.
  const [viewMode, setViewMode] = useState<'chat' | 'trajectory'>('chat');
  // O3: plan-mode toggle. 'auto' = normal execute; 'prompt_user' = LLM
  // emits a plan first; 'dry_run' = plan without ever executing.
  // Persisted in localStorage so the user's choice survives reloads.
  const [planMode, setPlanMode] = useState<'auto' | 'prompt_user' | 'dry_run'>(
    () => {
      try {
        const v = localStorage.getItem('ai_chat_plan_mode');
        if (v === 'auto' || v === 'prompt_user' || v === 'dry_run') return v;
      } catch { /* ignore */ }
      return 'auto';
    },
  );
  const handlePlanModeChange = useCallback(
    (next: 'auto' | 'prompt_user' | 'dry_run') => {
      setPlanMode(next);
      try { localStorage.setItem('ai_chat_plan_mode', next); } catch { /* ignore */ }
    },
    [],
  );

  // B: staged attachments (uploaded but not yet sent). Cleared on send.
  const [stagedAttachments, setStagedAttachments] = useState<StagedAttachment[]>([]);
  // Library assets picked from the @ menu or "Send to Agent". They share the
  // attachment row with the uploaded files above rather than sitting inside
  // the sentence, and are cleared on send just the same.
  const [stagedResources, setStagedResources] = useState<StagedResourceRef[]>([]);
  // Functional update so the identity stays stable — the pendingResource
  // effect in useComposerResourceAttach must not re-fire because the list
  // it just wrote to changed.
  const stageResource = useCallback((item: ResourceRefInsertItem) => {
    setStagedResources((prev) => stageResourceInto(prev, item));
  }, []);
  // Library ASSETS (P5) — their own list beside the resources above. Same
  // functional-update rule, and for the same reason: the pendingAsset effect
  // must not re-fire because the list it just wrote to changed identity.
  const [stagedAssets, setStagedAssets] = useState<StagedAssetRef[]>([]);
  const stageAsset = useCallback((item: AssetRefInsertItem) => {
    setStagedAssets((prev) => stageAssetInto(prev, item));
  }, []);

  // --- Resource @-mention picker state ---
  // Editor ref so we can call insertResourceRef when user picks an item.
  const chatEditorRef = useRef<Editor | null>(null);

  // Put the caret in the composer. Used by the "Send to Agent" channel,
  // where the user was over in the library and has no caret here yet.
  //
  // Still needs the frame retry the staging path no longer does: the panel
  // and the tiptap editor mount a beat apart (RECON#20), and this is the
  // one action that genuinely needs the editor to exist. Frames are only
  // cancelled on unmount — a second Send to Agent must not cancel the
  // first one's pending focus.
  const focusFrames = useRef<Set<number>>(new Set());
  useEffect(() => () => {
    focusFrames.current.forEach((h) => cancelAnimationFrame(h));
    focusFrames.current.clear();
  }, []);
  const focusComposer = useCallback(() => {
    let tries = 0;
    const attempt = () => {
      const editor = chatEditorRef.current;
      if (!editor) {
        if (tries++ > 30) return; // give up quietly rather than loop forever
        const handle = requestAnimationFrame(() => {
          focusFrames.current.delete(handle);
          attempt();
        });
        focusFrames.current.add(handle);
        return;
      }
      editor.chain().focus('end').run();
    };
    attempt();
  }, []);
  const [mentionPickerOpen, setMentionPickerOpen] = useState(false);
  const [mentionQuery, setMentionQuery] = useState('');
  const [mentionActiveKind, setMentionActiveKind] = useState<
    '' | 'video' | 'image' | 'doc' | 'audio' | 'pdf'
  >('');
  const [mentionActiveIndex, setMentionActiveIndex] = useState(0);
  // P5: the picker's sixth tab. Library ASSETS are a different population from
  // the five resource kinds, so the tab's whole state, transport and key
  // routing live in `useMentionAssetsTab`, shared with the issue reply box —
  // the two composers must not drift about what an asset mention searches.
  //
  // Declared BELOW (its `onSelect` closes the picker, and closing resets the
  // tab); this ref breaks that cycle.
  const mentionAssetsReset = useRef<() => void>(() => {});
  const { data: mentionSearchData, loading: mentionLoading } = useResourceSearch(
    mentionQuery,
    mentionActiveKind,
  );

  const handleMentionRequest = useCallback((query: string) => {
    setMentionQuery(query);
    setMentionActiveIndex(0);
    setMentionPickerOpen(true);
  }, []);

  /**
   * Close, and forget which tab was open.
   *
   * The reset belongs on CLOSE, not on open: `handleMentionRequest` fires on
   * every keystroke of the live query, so resetting there would bounce the
   * user off the Assets tab the moment they typed the next character. Closing
   * ends the mention session, which is the only moment the choice stops
   * meaning anything.
   */
  const closeMentionPicker = useCallback(() => {
    setMentionPickerOpen(false);
    // The next `@` opens a fresh session; a count carried over from the last
    // one would badge a number for a search this session never ran.
    mentionAssetsReset.current();
  }, []);

  // Item 1: close picker on Escape or click-outside
  useEffect(() => {
    if (!mentionPickerOpen) return;
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
  }, [mentionPickerOpen, closeMentionPicker]);

  // Resource → composer, for both entry points: the @ picker below and the
  // library context menu's "Send to Agent" (which arrives on the
  // globalChatStore channel). Attaching also tops up whatever AI processing
  // the asset is missing, and reports what that cost (spec F1/F3).
  const notifyProcessing = useCallback(
    (message: string, type: 'info' | 'error') => addToast(message, type),
    [addToast],
  );
  const { attachResource } = useComposerResourceAttach({
    stageResource,
    focusComposer,
    selectedAgentSlug,
    setSelectedAgentSlug,
    lockedAgent,
    notify: notifyProcessing,
    t,
  });
  // Asset → composer, from the asset sheet's sidebar and the shelf card's
  // action menu (both via `utils/sendAssetToAgent`). Its own hook, so each
  // one-shot channel keeps exactly one effect watching it.
  useComposerAssetAttach({
    stageAsset,
    focusComposer,
    selectedAgentSlug,
    setSelectedAgentSlug,
    lockedAgent,
  });
  // The rest of the F1 chain, watched from the Task Center: a transcription
  // started here has no summary until the transcript lands, and a
  // transcription REFUSED because an audio extraction held the slot has to
  // be asked for again once that finishes. Both charge points, so both
  // report through the same toast.
  useResourceProcessingFollowUps({ notify: notifyProcessing, t });

  /** Delete the "@query" the user typed to open the picker. Once the pick is
   *  staged above the composer that text is a placeholder for nothing, and
   *  leaving it behind sends "@clip.mp4" as message body. */
  const dropMentionTrigger = useCallback(() => {
    (chatEditorRef.current?.commands as unknown as {
      removeMentionTrigger?: () => boolean;
    } | undefined)?.removeMentionTrigger?.();
  }, []);

  const handleMentionSelect = useCallback(
    (item: ResourceSearchResult) => {
      attachResource(item);
      dropMentionTrigger();
      closeMentionPicker();
      setMentionQuery('');
    },
    [attachResource, dropMentionTrigger, closeMentionPicker],
  );

  /**
   * Picking an asset STAGES it — it does not insert a tiptap node.
   *
   * An asset is not a span of the sentence: the backend resolves it into a
   * consistency prompt plus a primary image, which is a turn-level attachment,
   * not a word. Same holding area, same removal affordance and same send path
   * as the sheet's "Send To Agent" (P5 Task 5), so the two entry points cannot
   * disagree about what was attached.
   */
  const handleMentionAssetSelect = useCallback(
    (row: AssetGridRow) => {
      stageAsset({
        id: row.id,
        name: row.name,
        asset_type: row.asset_type,
        // Staging never picks an outfit — the chip's loadout menu does, once
        // the asset is in the row. null is the backend's "use the default
        // loadout", not a missing value.
        loadout_id: null,
        cover_file_id: row.cover_file_id,
        scope_id: row.scope_id ?? null,
      });
      dropMentionTrigger();
      closeMentionPicker();
      setMentionQuery('');
      focusComposer();
    },
    [stageAsset, dropMentionTrigger, closeMentionPicker, focusComposer],
  );

  // The Assets tab — state, transport and ↑/↓/↵ routing, shared with
  // Todolist/IssueReplyBox.
  const mentionAssets = useMentionAssetsTab({
    pickerOpen: mentionPickerOpen,
    onSelect: handleMentionAssetSelect,
  });
  useEffect(() => {
    mentionAssetsReset.current = mentionAssets.reset;
  }, [mentionAssets.reset]);

  // Paste + drag-drop upload hooks — all three funnel files into handleFiles
  // which reuses the same validation/upload pipeline as the picker button.
  const { handleFiles, uploading } = useChatAttachmentUpload({
    attachments: stagedAttachments,
    onChange: setStagedAttachments,
  });
  // Block send while any pasted/dropped file is still uploading — otherwise
  // hitting Enter mid-upload silently drops the in-flight chips.
  const composerDisabled = sending || !activeSessionId || !effectiveAgentSlug || uploading;
  const { rootProps: dropzoneRootProps, isDragActive } = useComposerDropzone({
    onFiles: handleFiles,
    disabled: composerDisabled,
  });
  const { onPaste: composerOnPaste } = useComposerPaste({
    onFiles: handleFiles,
    disabled: composerDisabled,
  });

  const messagesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, sending]);

  const numericProjectId = useMemo(() => parseProjectId(projectId), [projectId]);

  // Load the agent list for the selector. Sessions are loaded lazily per
  // agent selection so switching agents doesn't drag in noise from others.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const list = await aiLibraryService.listAgents();
        if (cancelled) return;
        // Only enabled agents are selectable; the sidebar already surfaces
        // disabled ones visually, but chat requires an executable agent.
        const enabled = list.filter((a) => a.enabled);
        setAgents(enabled);
        // Only auto-select the first agent when NOT in locked-agent mode.
        // When lockedAgent is set, effectiveAgentSlug = lockedAgent already.
        if (enabled.length > 0 && !lockedAgent) {
          setSelectedAgentSlug((prev) => prev ?? enabled[0].slug);
        }
      } catch (err) {
        console.error('[AIChatPanel] listAgents failed:', err);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // When the effective agent changes, (re)load that agent's sessions. In
  // locked-agent mode the project filter is omitted so the user sees all their
  // personal sessions with that agent; in the normal project-scoped mode the
  // existing behaviour (filtered by project) is preserved.
  // If there are no sessions, auto-create one so the input isn't permanently
  // disabled on first open.
  useEffect(() => {
    if (!effectiveAgentSlug) return;
    let cancelled = false;
    void (async () => {
      try {
        const list = await aiLibraryService.listChatSessions(
          effectiveAgentSlug,
          lockedAgent ? undefined : numericProjectId,
        );
        if (cancelled) return;
        setSessions(list);
        if (list.length > 0) {
          // Resume this agent's last active session when it still exists;
          // otherwise fall back to the newest one.
          const rememberedId = readLastSessionMap()[effectiveAgentSlug];
          const resume =
            list.find((s) => s.id === rememberedId) ?? list[0];
          setActiveSessionId(resume.id);
          await loadSessionMessages(resume.id, () => cancelled);
        } else {
          const sessionPayload = lockedAgent
            ? { title: t('chat.newConversation', 'New conversation') }
            : {
                title: t('chat.newConversation', 'New conversation'),
                project_id: numericProjectId,
                context_type: contextType,
                context_id: contextId,
              };
          const created = await aiLibraryService.createChatSession(
            effectiveAgentSlug,
            sessionPayload,
          );
          if (cancelled) return;
          setSessions([created]);
          setActiveSessionId(created.id);
          setMessages([]);
        }
      } catch (err) {
        console.error('[AIChatPanel] session load failed:', err);
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [effectiveAgentSlug, numericProjectId]);

  async function loadSessionMessages(
    sessionId: string,
    isCancelled: () => boolean = () => false,
  ): Promise<AIChatMessage[] | null> {
    try {
      const data = await aiLibraryService.getChatSession(sessionId);
      if (isCancelled()) return null;
      const msgs = data.messages ?? [];
      setMessages(msgs);
      return msgs;
    } catch (err) {
      console.error('[AIChatPanel] getChatSession failed:', err);
      return null;
    }
  }

  const handleSelectSession = useCallback(
    async (sessionId: string) => {
      setActiveSessionId(sessionId);
      if (effectiveAgentSlug) rememberLastSession(effectiveAgentSlug, sessionId);
      setMessages([]);
      await loadSessionMessages(sessionId);
    },
    [effectiveAgentSlug],
  );

  const handleNewSession = useCallback(async () => {
    if (!effectiveAgentSlug) return;
    try {
      const sessionPayload = lockedAgent
        ? { title: t('chat.newConversation', 'New conversation') }
        : {
            title: t('chat.newConversation', 'New conversation'),
            project_id: numericProjectId,
            context_type: contextType,
            context_id: contextId,
          };
      const created = await aiLibraryService.createChatSession(
        effectiveAgentSlug,
        sessionPayload,
      );
      setSessions((prev) => [created, ...prev]);
      setActiveSessionId(created.id);
      rememberLastSession(effectiveAgentSlug, created.id);
      setMessages([]);
    } catch (err) {
      console.error('[AIChatPanel] createChatSession failed:', err);
      const msg = err instanceof Error ? err.message : String(err);
      addToast(`Failed to create session: ${msg}`, 'error');
    }
  }, [effectiveAgentSlug, lockedAgent, numericProjectId, contextType, contextId, t, addToast]);

  const handleRenameSession = useCallback(
    async (sessionId: string, title: string) => {
      try {
        const updated = await aiLibraryService.updateChatSession(sessionId, { title });
        const applyTitle = (list: ChatSession[]) =>
          list.map((s) =>
            s.id === sessionId ? { ...s, title: updated.title ?? title } : s,
          );
        setSessions(applyTitle);
        setOverlaySessions((prev) => (prev ? applyTitle(prev) : prev));
      } catch (err) {
        console.error('[AIChatPanel] updateChatSession failed:', err);
        const msg = err instanceof Error ? err.message : String(err);
        addToast(`Rename failed: ${msg}`, 'error');
      }
    },
    [addToast],
  );

  const handleDeleteSession = useCallback(
    async (sessionId: string) => {
      try {
        await aiLibraryService.deleteChatSession(sessionId);
        const next = sessions.filter((s) => s.id !== sessionId);
        setSessions(next);
        setOverlaySessions((prev) =>
          prev ? prev.filter((s) => s.id !== sessionId) : prev,
        );
        if (activeSessionId === sessionId) {
          if (next.length > 0) {
            setActiveSessionId(next[0].id);
            await loadSessionMessages(next[0].id);
          } else {
            setActiveSessionId(null);
            setMessages([]);
          }
        }
      } catch (err) {
        console.error('[AIChatPanel] deleteChatSession failed:', err);
      }
    },
    [sessions, activeSessionId],
  );

  const handleSend = useCallback(
    async (
      rawText: string,
      refAttachments: ResourceRefAttachment[] = [],
      // Phase 2a: set when the text answers the assistant's parked question
      // (QuestionCard in the bubble) — sent as `answer_to`.
      extra: { answerTo?: string } = {},
    ) => {
      // Answering a parked question: the body IS the answer (the backend
      // compares it to the option labels verbatim), so no capsule fold and
      // no staged attachments ride along; failures are thrown to the card,
      // never swallowed into a toast.
      const answering = !!extra.answerTo;
      if (!activeSessionId || sending) {
        if (answering) throw new Error(t('question.busy', 'Another message is still being sent.'));
        return;
      }

      // Fold the context capsule into the outgoing message and clear it — one
      // selection travels with one turn, exactly like the blockquote it
      // replaced, but the user could see and drop it first.
      const capsule = answering ? null : contextCapsule;
      const text = capsule
        ? `${
            capsule.sceneLabel
              ? t('chat.selectionFrom', 'Selection from {{scene}}', {
                  scene: capsule.sceneLabel,
                })
              : t('chat.selection', 'Selection')
          }:\n${capsule.text}\n\n${rawText}`
        : rawText;
      if (capsule) setContextCapsule(null);

      setSending(true);
      setAttachmentFailureCount(undefined);
      setAttachmentFailures(undefined);

      // Optimistic user bubble — replaced by the authoritative row after
      // the server responds and we reload the message list. Staged image
      // attachments render immediately via their local preview data URL.
      const sentAttachments = answering ? [] : stagedAttachments;
      // Union of both sources, deduped by resource id: chips still sitting
      // in a restored draft AND everything staged in the attachment row.
      const sentResources = answering ? [] : stagedResources;
      const sentRefs = mergeRefAttachments(refAttachments, sentResources);
      // Assets have one source (the staged row — there is no inline asset
      // node), but the dedup still matters: two entries for one asset would
      // be resolved, rendered and billed twice.
      const sentAssets = answering ? [] : stagedAssets;
      const sentAssetRefs = mergeAssetAttachments(sentAssets);
      // Both halves, in the shape the reducer will persist. The refs used to
      // be left out here, so a just-sent turn showed no chip until the
      // history reload put one there — the optimistic bubble and the
      // authoritative one have to agree, or the chips visibly pop in.
      const optimisticAttachments: AIChatMessageAttachment[] = [
        ...sentAttachments.map((a) => ({
          kind: a.kind,
          resource_id: a.resource_id,
          mime: a.mime,
          alt_text: a.filename,
          preview_data_url: a.preview_data_url,
        })),
        ...sentRefs.map((r) => ({
          kind: r.kind,
          resource_id: r.resource_id,
          mime: r.mime,
          alt_text: r.name,
        })),
        // The asset half of the optimistic bubble. `name`, not `alt_text`:
        // that is the key the asset attachment carries on the wire and the
        // one the reducer persists, so the optimistic chip and the reloaded
        // one read the same field rather than agreeing by luck.
        ...sentAssetRefs.map((r) => ({
          kind: r.kind,
          asset_id: r.asset_id,
          loadout_id: r.loadout_id,
          name: r.name,
        })),
      ];
      const tempUser: AIChatMessage = {
        id: `tmp-user-${Date.now()}`,
        session_id: activeSessionId,
        role: 'user',
        content: text,
        attachments:
          optimisticAttachments.length > 0 ? optimisticAttachments : undefined,
        created_at: new Date().toISOString(),
      };
      setMessages((prev) => [...prev, tempUser]);
      // Clear the composer chips optimistically — the attachments now live
      // in the message bubble. Restored on failure so the user can retry.
      setStagedAttachments([]);
      setStagedResources([]);
      setStagedAssets([]);

      // Streaming assistant bubble — created on the first delta, grown in
      // place, then replaced by the authoritative row on history reload.
      const tempAssistantId = `tmp-assistant-${Date.now()}`;

      try {
        // O3: pass plan_mode only when non-default. Backend swaps in
        // plan-prompt instructions for prompt_user / dry_run.
        // B: send staged attachments + resource_ref attachments alongside.
        const opts: Parameters<typeof aiLibraryService.streamChatMessage>[2] = {};
        if (planMode !== 'auto') opts.plan_mode = planMode;
        if (extra.answerTo) opts.answer_to = extra.answerTo;
        const allAttachments = [
          ...sentAttachments.map((a) => ({
            kind: a.kind,
            url: a.url,
            mime: a.mime ?? undefined,
            alt_text: a.filename,
            resource_id: a.resource_id,
          })),
          ...sentRefs.map((r) => ({
            kind: r.kind,
            url: '',            // resource_ref resolves by id, not URL
            resource_id: r.resource_id,
            mime: r.mime,
            alt_text: r.name,
          })),
          // asset_ref: already the exact wire shape (`toAssetAttachment`
          // builds it), so it goes out unmapped. Reshaping it here would be a
          // second place for the contract to drift from the schema.
          ...sentAssetRefs,
        ];
        if (allAttachments.length > 0) {
          opts.attachments = allAttachments;
        }
        // §5.3: the same capsule snapshot read above for the text fold also
        // carries the structured handle — one read, two uses.
        const scriptContext = buildScriptContext(capsule);
        if (scriptContext) opts.script_context = scriptContext;
        // SSE streaming instead of the buffered /chat call: a long AI reply
        // no longer sits behind one giant request that proxies love to kill
        // — deltas render as they arrive and keep the connection alive.
        let streamed = '';
        for await (const evt of aiLibraryService.streamChatMessage(
          activeSessionId, text, opts,
        )) {
          if (evt.type === 'delta') {
            const chunk = typeof evt.data?.text === 'string' ? evt.data.text : '';
            if (!chunk) continue;
            const isFirst = streamed === '';
            streamed += chunk;
            const content = streamed;
            if (isFirst) {
              setMessages((prev) => [...prev, {
                id: tempAssistantId,
                session_id: activeSessionId,
                role: 'assistant' as const,
                content,
                created_at: new Date().toISOString(),
              }]);
            } else {
              setMessages((prev) => prev.map((m) =>
                m.id === tempAssistantId ? { ...m, content } : m,
              ));
            }
          } else if (evt.type === 'error') {
            const mapped = providerErrorMessage(evt.data?.code, (k, f) => t(k, f));
            const streamErr = new Error(
              mapped ??
                (typeof evt.data?.error === 'string' ? evt.data.error : 'stream error'),
            ) as Error & { code?: string; status?: number };
            // Typed 4xx from the answer channel (Task 4: `{error, code,
            // status}`) — the card maps `code` to copy.
            if (typeof evt.data?.code === 'string') streamErr.code = evt.data.code;
            if (typeof evt.data?.status === 'number') streamErr.status = evt.data.status;
            throw streamErr;
          } else if (evt.type === 'done') {
            // G2: backend resolves attachments best-effort and reports
            // per-attachment failures instead of failing the whole turn —
            // surface the count so a silent no-op isn't the user's only
            // signal that "the file didn't make it".
            const failures = evt.data?.attachment_failures;
            if (Array.isArray(failures) && failures.length > 0) {
              setAttachmentFailureCount(failures.length);
              setAttachmentFailures(failures as AttachmentFailure[]);
            }
          }
          // Unknown event types are no-ops (forward-compat per the
          // backend contract).
        }
        // Refetch full history so IDs + timestamps + tokens are
        // server-authoritative (also swaps out both temp bubbles).
        await loadSessionMessages(activeSessionId);
      } catch (err) {
        console.error('[AIChatPanel] chat stream failed:', err);
        const msg = err instanceof Error ? err.message : String(err);
        if (answering) {
          // The card shows the typed rejection inline; keep the optimistic
          // bubbles honest by reloading, then hand the error back.
          setSending(false);
          await loadSessionMessages(activeSessionId);
          throw err;
        }
        addToast(`Send failed: ${msg}`, 'error');
        // "Send failed" here often means the CONNECTION died mid-turn
        // (proxy timeout on a long AI reply), not that the message was
        // rejected — the backend persists the user turn BEFORE calling
        // the model. Reload server history instead of blindly rolling
        // back: if the turn landed, the bubble (with attachments) stays;
        // only restore the composer chips when it truly never arrived.
        const serverMsgs = await loadSessionMessages(activeSessionId);
        if (serverMsgs === null) {
          // History fetch also failed (offline?) — fall back to rollback.
          setMessages((prev) => prev.filter(
            (m) => m.id !== tempUser.id && m.id !== tempAssistantId,
          ));
          setStagedAttachments(sentAttachments);
          setStagedResources(sentResources);
          setStagedAssets(sentAssets);
        } else {
          const landed = serverMsgs
            .slice(-3)
            .some((m) => m.role === 'user' && m.content === text);
          if (!landed) {
            setStagedAttachments(sentAttachments);
            setStagedResources(sentResources);
            setStagedAssets(sentAssets);
          }
        }
      } finally {
        setSending(false);
      }

      // Update the session list ordering so this session bubbles to top.
      if (effectiveAgentSlug) {
        void aiLibraryService
          .listChatSessions(effectiveAgentSlug, lockedAgent ? undefined : numericProjectId)
          .then(setSessions)
          .catch((err) => console.error('[AIChatPanel] refresh sessions failed:', err));
      }
    },
    // C3 fix: include planMode + stagedAttachments so the closure
    // doesn't capture stale values when the user changes mode or
    // adds/removes attachments between renders.
    [activeSessionId, sending, effectiveAgentSlug, lockedAgent, numericProjectId,
     addToast, planMode, stagedAttachments, stagedResources, stagedAssets,
     contextCapsule, t],
  );

  // Phase 2a chat answer surface: the newest assistant message is the only
  // answerable one, and issue-context sessions answer on the issue thread
  // (the chat endpoint 409s `use_issue_thread`).
  const lastAssistantMessageId = useMemo(
    () => [...messages].reverse().find((m) => m.role === 'assistant')?.id ?? null,
    [messages],
  );
  const issueContextSession = useMemo(
    () => sessions.find((s) => s.id === activeSessionId)?.context_type === 'issue',
    [sessions, activeSessionId],
  );

  const handleSuggest = useCallback(
    (suggestion: string) => {
      void handleSend(suggestion, []);
    },
    [handleSend],
  );

  // Overlay data: when scope=all or a search term is active, fetch from the
  // server (debounced) into overlaySessions — the panel's own per-agent
  // `sessions` state is never clobbered by browsing the history.
  useEffect(() => {
    if (!sessionsOverlayOpen) return;
    const q = sessionSearch.trim();
    if (sessionScope === 'current' && !q) {
      setOverlaySessions(null);
      return;
    }
    let cancelled = false;
    const timer = setTimeout(() => {
      void (async () => {
        try {
          const list =
            sessionScope === 'all'
              ? await aiLibraryService.listAllChatSessions(q || undefined)
              : effectiveAgentSlug
                ? await aiLibraryService.listChatSessions(
                    effectiveAgentSlug,
                    lockedAgent ? undefined : numericProjectId,
                    50,
                    q,
                  )
                : [];
          if (!cancelled) setOverlaySessions(list);
        } catch (err) {
          console.error('[AIChatPanel] overlay session fetch failed:', err);
        }
      })();
    }, 250);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [
    sessionsOverlayOpen,
    sessionScope,
    sessionSearch,
    effectiveAgentSlug,
    lockedAgent,
    numericProjectId,
  ]);

  // Selecting a session that belongs to ANOTHER agent (scope=all) jumps the
  // panel to that agent; the remembered-session map makes the agent-switch
  // effect resume exactly the row that was clicked.
  const handleOverlaySelect = useCallback(
    (sessionId: string) => {
      const picked = overlaySessions?.find((s) => s.id === sessionId);
      const slug = picked?.agent_slug;
      if (slug && slug !== effectiveAgentSlug && !lockedAgent) {
        rememberLastSession(slug, sessionId);
        setSelectedAgentSlug(slug);
      } else {
        void handleSelectSession(sessionId);
      }
      onSessionsOverlayClose?.();
    },
    [
      overlaySessions,
      effectiveAgentSlug,
      lockedAgent,
      handleSelectSession,
      onSessionsOverlayClose,
    ],
  );

  const sessionItems: SessionItem[] = useMemo(
    () =>
      (sessions || []).filter(Boolean).map((s) => ({
        id: s.id,
        title: s.title ?? t('chat.untitled', 'Untitled'),
        message_count: s.message_count ?? 0,
        updated_at: s.updated_at ?? '',
      })),
    [sessions, t],
  );

  // Rows shown inside the slide-over. Agent badge only in the 'all' scope,
  // where sessions from other agents appear.
  const overlayItems: SessionItem[] = useMemo(() => {
    if (overlaySessions == null) return sessionItems;
    return overlaySessions.filter(Boolean).map((s) => ({
      id: s.id,
      title: s.title ?? t('chat.untitled', 'Untitled'),
      message_count: s.message_count ?? 0,
      updated_at: s.updated_at ?? '',
      agent_slug: sessionScope === 'all' ? (s.agent_slug ?? undefined) : undefined,
    }));
  }, [overlaySessions, sessionItems, sessionScope, t]);

  // AgentOption wants {id, name, description?}. We thread agent.slug as
  // id because selection downstream uses slug — it's the session FK.
  const agentOptions = useMemo(
    () =>
      agents.map((a) => ({
        id: a.slug,
        name: a.name,
        description: a.description ?? undefined,
      })),
    [agents],
  );

  /** The agent the header identifies. Null until the roster loads. */
  const activeAgent = useMemo(
    () => agents.find((a) => a.slug === effectiveAgentSlug) ?? null,
    [agents, effectiveAgentSlug],
  );

  /** Quick actions follow what is on screen — see QuickActions' docstring. */
  const quickActionContext: QuickActionContext = contextCapsule
    ? 'selection'
    : contextType === 'script' || contextType === 'storyboard'
      ? 'script'
      : 'none';

  const handleQuickAction = useCallback((prompt: string) => {
    // Seed the composer rather than sending: the user keeps the last word
    // before an agent with write tools touches their script.
    chatEditorRef.current?.chain().focus('end').insertContent(prompt).run();
  }, []);

  // T10 (harness P4 §1-③): while a turn is streaming the composer stays
  // open — a message now is a STEER, delivered to the conversation's inbox
  // and read by the running agent before its next step. Never a second
  // turn queued behind this one.
  const steerMode = sending && !!activeSessionId && !!effectiveAgentSlug;
  const handleSteer = useCallback(
    async (rawText: string) => {
      if (!activeSessionId) return;
      const text = rawText.trim();
      if (!text) return;
      const tempId = `tmp-steer-${Date.now()}`;
      setMessages((prev) => [
        ...prev,
        {
          id: tempId,
          session_id: activeSessionId,
          role: 'user' as const,
          content: text,
          created_at: new Date().toISOString(),
          metadata_json: { inbox_steer: true },
        } as AIChatMessage,
      ]);
      try {
        await deliverSteer('conversation', activeSessionId, text);
        addToast(t('chat.steerSent', 'Sent to the running agent — picked up before its next step'), 'success');
      } catch (err) {
        setMessages((prev) => prev.filter((m) => m.id !== tempId));
        if (err instanceof InboxTargetEndedError) {
          addToast(t('chat.steerEnded', 'The conversation is no longer running — send it as a new message'), 'error');
          return;
        }
        console.error('[AIChatPanel] steer failed:', err);
        addToast(`Steer failed: ${err instanceof Error ? err.message : String(err)}`, 'error');
      }
    },
    [activeSessionId, addToast, t],
  );

  const hasMessages = messages.length > 0 || sending;

  return (
    <div className="relative w-full h-full flex-1 flex flex-col bg-ink-900 overflow-hidden min-h-0">
      {/* Header */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-ink-800 flex-shrink-0">
        {/* Identity, not a generic "AI Chat" title: with write tools in play,
            WHO is answering determines what happens to the user's script. */}
        <AgentIdentityHeader
          name={activeAgent?.name}
          description={activeAgent?.description}
          icon={activeAgent?.icon}
          action={
            !lockedAgent ? (
              <AgentSelector
                agents={agentOptions}
                selectedId={selectedAgentSlug}
                onSelect={setSelectedAgentSlug}
              />
            ) : undefined
          }
        />

        <button
          type="button"
          onClick={handleNewSession}
          className="p-1 rounded hover:bg-ink-800 text-ink-500 hover:text-ink-300 transition-colors"
          title={t('chat.newSession', 'New session')}
        >
          <Plus size={15} />
        </button>

        {onClose && (
          <button
            type="button"
            onClick={onClose}
            className="p-1 rounded hover:bg-ink-800 text-ink-500 hover:text-ink-300 transition-colors"
            title={t('common.close', 'Close')}
          >
            <X size={15} />
          </button>
        )}
      </div>

      {/* O4: Pending followups — hidden when empty so it doesn't take
          space on the common case */}
      <CommitmentsPanel
        status="pending"
        limit={20}
        className="max-h-48 overflow-hidden flex-shrink-0"
        hideWhenEmpty
      />

      {/* Session list — inline by default; when the host widget controls a
          history overlay (sessionsOverlayOpen defined), render a left
          slide-over instead (Laper-style Chat History). */}
      {sessionsOverlayOpen === undefined ? (
        <SessionList
          sessions={sessionItems}
          activeSessionId={activeSessionId}
          onSelect={handleSelectSession}
          onNew={handleNewSession}
          onDelete={handleDeleteSession}
          onRename={handleRenameSession}
        />
      ) : (
        <>
          {sessionsOverlayOpen && (
            <button
              type="button"
              aria-label="Close session history"
              onClick={onSessionsOverlayClose}
              className="absolute inset-0 z-10 cursor-default bg-black/30"
            />
          )}
          <div
            aria-hidden={!sessionsOverlayOpen}
            className={`absolute inset-y-0 left-0 z-20 flex w-64 flex-col border-r border-ink-800 bg-ink-900 shadow-2xl transition-transform duration-200 ease-out ${
              sessionsOverlayOpen
                ? 'translate-x-0'
                : '-translate-x-full pointer-events-none'
            }`}
          >
            <div className="flex items-center justify-between border-b border-ink-800 px-3 py-2">
              <span className="text-sm font-medium text-ink-200">
                Chat History
              </span>
              {/* Scope toggle: this agent's sessions vs everything the
                  caller owns across agents (rows then carry agent badges). */}
              {!lockedAgent && (
                <div className="flex items-center gap-0.5 rounded-md bg-ink-800 p-0.5">
                  {(['current', 'all'] as const).map((scope) => (
                    <button
                      key={scope}
                      type="button"
                      onClick={() => setSessionScope(scope)}
                      className={`rounded px-1.5 py-0.5 text-[10px] font-medium transition-colors ${
                        sessionScope === scope
                          ? 'bg-[var(--accent-soft)] text-[var(--accent-text)]'
                          : 'text-ink-500 hover:text-ink-300'
                      }`}
                    >
                      {scope === 'current' ? 'Current' : 'All'}
                    </button>
                  ))}
                </div>
              )}
            </div>
            <div className="border-b border-ink-800 px-2 py-1.5">
              <div className="flex items-center gap-1.5 rounded-md bg-ink-800 px-2 py-1">
                <Search size={13} className="flex-shrink-0 text-ink-500" />
                <input
                  type="text"
                  value={sessionSearch}
                  onChange={(e) => setSessionSearch(e.target.value)}
                  placeholder="Search sessions..."
                  className="w-full bg-transparent text-xs text-ink-200 placeholder-ink-500 focus:outline-none"
                />
                {sessionSearch && (
                  <button
                    type="button"
                    aria-label="Clear search"
                    onClick={() => setSessionSearch('')}
                    className="flex-shrink-0 text-ink-500 hover:text-ink-300"
                  >
                    <X size={12} />
                  </button>
                )}
              </div>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto">
              <SessionList
                sessions={overlayItems}
                activeSessionId={activeSessionId}
                onSelect={handleOverlaySelect}
                onNew={() => {
                  handleNewSession();
                  onSessionsOverlayClose?.();
                }}
                onDelete={handleDeleteSession}
                onRename={handleRenameSession}
              />
            </div>
          </div>
        </>
      )}

      {/* Messages area */}
      <div className="flex-1 overflow-y-auto px-3 py-3 min-h-0">
        {loading ? (
          <div className="flex items-center justify-center h-full">
            <TypingIndicator show />
          </div>
        ) : !hasMessages ? (
          <EmptyState onSuggest={handleSuggest} />
        ) : (
          <>
            {/* T10: Chat | Trajectory over the same messages (no refetch on flip). */}
            <div className="flex justify-end mb-2">
              <div className="inline-flex rounded border border-ink-800 bg-ink-900/80 overflow-hidden text-[10px]" data-testid="chat-view-toggle">
                {(['chat', 'trajectory'] as const).map((m) => (
                  <button
                    key={m}
                    type="button"
                    onClick={() => setViewMode(m)}
                    aria-pressed={viewMode === m}
                    className={`px-2 py-0.5 transition ${
                      viewMode === m ? 'bg-[var(--accent-soft)] text-[var(--accent-text)]' : 'text-ink-500 hover:text-ink-300'
                    }`}
                  >
                    {m === 'chat' ? t('chat.view.chat', 'Chat') : t('chat.view.trajectory', 'Trajectory')}
                  </button>
                ))}
              </div>
            </div>
            {viewMode === 'trajectory' ? (
              <ChatTrajectoryView messages={messages} isRunning={sending} />
            ) : (
            <>
            {messages.map((msg) => (
              <MessageBubble
                key={msg.id}
                role={msg.role === 'system' ? 'assistant' : msg.role}
                content={msg.content}
                attachments={msg.attachments ?? undefined}
                tokens={
                  msg.prompt_tokens != null && msg.completion_tokens != null
                    ? (msg.prompt_tokens ?? 0) + (msg.completion_tokens ?? 0)
                    : undefined
                }
                timestamp={formatTimestamp(msg.created_at)}
                toolCalls={
                  msg.role === 'assistant' ? extractToolCalls(msg) : undefined
                }
                awaitingApproval={
                  msg.role === 'assistant'
                    ? extractAwaitingApproval(msg)
                    : undefined
                }
                awaitingInput={
                  msg.role === 'assistant' && !issueContextSession
                    ? extractAwaitingInput(msg)
                    : undefined
                }
                // Only the NEWEST assistant message is answerable (the
                // backend's latest_assistant_open_question rule); older
                // cards render read-only.
                awaitingInputDisabled={msg.id !== lastAssistantMessageId}
                onAnswerQuestion={(value, answerTo) => handleSend(value, [], { answerTo })}
                runId={msg.role === 'assistant' ? chatRunId(msg) : undefined}
                onApply={
                  msg.role === 'assistant' && onApplyContent
                    ? () => onApplyContent(msg.content)
                    : undefined
                }
              />
            ))}

            <AttachmentFailureBanner
              count={attachmentFailureCount}
              failures={attachmentFailures}
            />

            {/* Typing dots only until the first streamed delta arrives —
                after that the growing assistant bubble is the indicator. */}
            {sending && !messages.some((m) => m.id.startsWith('tmp-assistant')) && (
              <div className="flex justify-start mb-3">
                <div className="max-w-[85%] rounded-xl bg-ink-800 text-ink-200 text-sm leading-relaxed overflow-hidden">
                  <TypingIndicator show />
                </div>
              </div>
            )}
            </>
            )}
          </>
        )}
        <div ref={messagesEndRef} />
      </div>

      {/* Composer area: chip strip + plan mode bar + chat input.
          Wrapped in a single relative div so the drag-drop overlay can
          cover the whole composer region. */}
      <div {...dropzoneRootProps} className="relative">
        {/* B: Attachment chip strip — files and library assets share one
            wrapping row. Only rendered when something is staged; the picker
            button itself lives next to ChatInput. */}
        {activeSessionId && effectiveAgentSlug
          && (stagedAttachments.length > 0
            || stagedResources.length > 0
            || stagedAssets.length > 0) && (
          <div className="flex items-center gap-2 px-3 py-1.5 border-t border-ink-800 bg-ink-900/30">
            <ChatAttachmentPicker
              attachments={stagedAttachments}
              onChange={setStagedAttachments}
              resources={stagedResources}
              onResourcesChange={setStagedResources}
              assets={stagedAssets}
              onAssetsChange={setStagedAssets}
              disabled={sending}
            />
          </div>
        )}

        {/* O3: PlanMode toggle bar */}
        {activeSessionId && effectiveAgentSlug && (
          <div className="flex items-center gap-2 px-3 py-1.5 border-t border-ink-800 text-xs text-ink-400 bg-ink-900/50">
            <span className="font-medium text-ink-500">{t('chat.planMode.label')}</span>
            {(['auto', 'prompt_user', 'dry_run'] as const).map((m) => (
              <button
                key={m}
                type="button"
                onClick={() => handlePlanModeChange(m)}
                className={`px-2 py-0.5 rounded transition-colors ${
                  planMode === m
                    ? 'bg-[var(--accent-soft)] text-[var(--accent-text)]'
                    : 'text-ink-500 hover:text-ink-300 hover:bg-ink-800'
                }`}
                title={
                  m === 'auto'
                    ? t('chat.planMode.auto_tooltip')
                    : m === 'prompt_user'
                      ? t('chat.planMode.promptUser_tooltip')
                      : t('chat.planMode.dryRun_tooltip')
                }
              >
                {m === 'auto' ? t('chat.planMode.execute') : m === 'prompt_user' ? t('chat.planMode.planFirst') : t('chat.planMode.dryRun')}
              </button>
            ))}
            {planMode !== 'auto' && (
              <span className="ml-auto text-amber-400 text-[10px] uppercase tracking-wide">
                ⚠ {planMode === 'dry_run' ? t('chat.planMode.warning_dryRun') : t('chat.planMode.warning_planning')}
              </span>
            )}
          </div>
        )}

        {/* @-mention resource picker — absolutely-positioned overlay above the composer */}
        {mentionPickerOpen && (
          <div className="absolute bottom-full left-0 right-0 z-20 flex justify-start px-2 pb-1">
            <ResourcePickerSuggestion
              items={mentionSearchData.results}
              query={mentionQuery}
              loading={mentionLoading}
              counts={mentionSearchData.counts}
              activeKind={mentionActiveKind}
              onKindChange={(kind) => {
                mentionAssets.deactivate();
                setMentionActiveKind(kind);
              }}
              onSelect={handleMentionSelect}
              activeIndex={mentionActiveIndex}
              assets={mentionAssets.assets}
            />
          </div>
        )}

        {/* A7 §D: the injected selection sits above the composer as a closable
            capsule, with quick actions matched to what is on screen. */}
        {contextCapsule && (
          <ContextCapsule
            value={contextCapsule}
            onDismiss={() => setContextCapsule(null)}
          />
        )}
        {activeSessionId && effectiveAgentSlug && (
          <QuickActions context={quickActionContext} onPick={handleQuickAction} />
        )}

        {/* Chat input + B: attachment picker (when no staged chips above) */}
        <div className="flex items-end gap-1 bg-ink-900 border-t border-ink-700/50">
          {activeSessionId && effectiveAgentSlug
            && stagedAttachments.length === 0
            && stagedResources.length === 0
            && stagedAssets.length === 0 && (
            <div className="pl-2 pb-2">
              <ChatAttachmentPicker
                attachments={[]}
                onChange={setStagedAttachments}
                disabled={sending}
              />
            </div>
          )}
          <div className="flex-1 min-w-0">
            <ChatInput
              onSend={steerMode ? (text) => void handleSteer(text) : handleSend}
              onPaste={composerOnPaste}
              disabled={(sending && !steerMode) || !activeSessionId || !effectiveAgentSlug}
              placeholder={
                !effectiveAgentSlug
                  ? t('chat.placeholderNoAgent', 'Select an agent to start')
                  : !activeSessionId
                    ? t('chat.placeholderNoSession', 'Create a session first')
                    : steerMode
                      ? t('chat.steerPlaceholder', 'Steer the running agent — read before its next step…')
                      : t('chat.placeholder', 'Type a message...')
              }
              onMentionRequest={handleMentionRequest}
              onMentionKey={mentionAssets.handleKey}
              editorRef={chatEditorRef}
              hasAttachments={
                stagedAttachments.length > 0
                || stagedResources.length > 0
                || stagedAssets.length > 0
              }
            />
          </div>
        </div>

        {/* Drag-active overlay — pointer-events-none so drop fires on the
            wrapper (the div with the dropzone handlers) not on this overlay. */}
        {isDragActive && (
          <div className="absolute inset-0 z-10 flex items-center justify-center pointer-events-none bg-blue-500/10 border-2 border-dashed border-blue-400 rounded-lg">
            <span className="text-sm font-medium text-blue-200">{t('chat.attachments.dropToUpload')}</span>
          </div>
        )}
      </div>
    </div>
  );
}

export default AIChatPanel;
