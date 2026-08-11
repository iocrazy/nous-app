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
import { TypingIndicator } from './chat/TypingIndicator';
import { AttachmentFailureBanner } from './chat/AttachmentFailureBanner';
import { ChatInput } from './chat/ChatInput';
import { CommitmentsPanel } from './CommitmentsPanel';
import { ChatAttachmentPicker, type StagedAttachment } from './ChatAttachmentPicker';
import { ResourcePickerSuggestion } from './chat/ResourcePickerSuggestion';
import { EmptyState } from './chat/EmptyState';
import { useToast } from './Toast';
import { useChatAttachmentUpload } from '../hooks/useChatAttachmentUpload';
import { useComposerDropzone } from '../hooks/useComposerDropzone';
import { useComposerPaste } from '../hooks/useComposerPaste';
import { useResourceSearch } from '../hooks/useResourceSearch';
import { useGlobalChatStore } from '../stores/globalChatStore';

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

/** 已知 provider 错误码 → i18n 文案;未知码返回 null 走原有兜底。 */
export function providerErrorMessage(
  code: unknown,
  t: (key: string, fallback: string) => string,
): string | null {
  const KNOWN = [
    'provider_rate_limit',
    'provider_unreachable',
    'provider_auth',
    'provider_bad_model',
    'task_timeout',
  ];
  if (typeof code !== 'string' || !KNOWN.includes(code)) return null;
  const key = code
    .split('_')
    .map((w, i) => (i === 0 ? w : w[0].toUpperCase() + w.slice(1)))
    .join('');
  return t(`errors.provider.${key}`, code);
}

/** Persisted assistant message metadata_json.run_id (BIGINT snowflake, kept
 *  as a string end-to-end — see the file-wide 2^53 precision caveat). */
function extractRunId(msg: AIChatMessage): string | null {
  const meta = msg.metadata_json;
  if (!meta || typeof meta !== 'object') return null;
  const raw = (meta as Record<string, unknown>).run_id;
  return typeof raw === 'string' && raw ? raw : null;
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
  useEffect(() => {
    setAttachmentFailureCount(undefined);
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

  // --- Resource @-mention picker state ---
  // Editor ref so we can call insertResourceRef when user picks an item.
  const chatEditorRef = useRef<Editor | null>(null);
  const [mentionPickerOpen, setMentionPickerOpen] = useState(false);
  const [mentionQuery, setMentionQuery] = useState('');
  const [mentionActiveKind, setMentionActiveKind] = useState<
    '' | 'video' | 'image' | 'doc' | 'audio' | 'pdf'
  >('');
  const [mentionActiveIndex, setMentionActiveIndex] = useState(0);
  const { data: mentionSearchData, loading: mentionLoading } = useResourceSearch(
    mentionQuery,
    mentionActiveKind,
  );

  const handleMentionRequest = useCallback((query: string) => {
    setMentionQuery(query);
    setMentionActiveIndex(0);
    setMentionPickerOpen(true);
  }, []);

  // Item 1: close picker on Escape or click-outside
  useEffect(() => {
    if (!mentionPickerOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setMentionPickerOpen(false);
    };
    const onClick = (e: MouseEvent) => {
      const target = e.target as HTMLElement;
      if (!target.closest('[data-testid="resource-picker"]')) {
        setMentionPickerOpen(false);
      }
    };
    document.addEventListener('keydown', onKey);
    document.addEventListener('mousedown', onClick);
    return () => {
      document.removeEventListener('keydown', onKey);
      document.removeEventListener('mousedown', onClick);
    };
  }, [mentionPickerOpen]);

  const handleMentionSelect = useCallback(
    (item: ResourceSearchResult) => {
      if (chatEditorRef.current) {
        (chatEditorRef.current.commands as unknown as {
          insertResourceRef: (item: ResourceSearchResult) => boolean;
        }).insertResourceRef(item);
      }
      setMentionPickerOpen(false);
      setMentionQuery('');
    },
    [],
  );

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
    async (rawText: string, refAttachments: ResourceRefAttachment[] = []) => {
      if (!activeSessionId || sending) return;

      // Fold the context capsule into the outgoing message and clear it — one
      // selection travels with one turn, exactly like the blockquote it
      // replaced, but the user could see and drop it first.
      const capsule = contextCapsule;
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

      // Optimistic user bubble — replaced by the authoritative row after
      // the server responds and we reload the message list. Staged image
      // attachments render immediately via their local preview data URL.
      const sentAttachments = stagedAttachments;
      const tempUser: AIChatMessage = {
        id: `tmp-user-${Date.now()}`,
        session_id: activeSessionId,
        role: 'user',
        content: text,
        attachments: sentAttachments.length > 0
          ? sentAttachments.map((a) => ({
              kind: a.kind,
              resource_id: a.resource_id,
              mime: a.mime,
              alt_text: a.filename,
              preview_data_url: a.preview_data_url,
            }))
          : undefined,
        created_at: new Date().toISOString(),
      };
      setMessages((prev) => [...prev, tempUser]);
      // Clear the composer chips optimistically — the attachments now live
      // in the message bubble. Restored on failure so the user can retry.
      setStagedAttachments([]);

      // Streaming assistant bubble — created on the first delta, grown in
      // place, then replaced by the authoritative row on history reload.
      const tempAssistantId = `tmp-assistant-${Date.now()}`;

      try {
        // O3: pass plan_mode only when non-default. Backend swaps in
        // plan-prompt instructions for prompt_user / dry_run.
        // B: send staged attachments + resource_ref attachments alongside.
        const opts: Parameters<typeof aiLibraryService.streamChatMessage>[2] = {};
        if (planMode !== 'auto') opts.plan_mode = planMode;
        const allAttachments = [
          ...sentAttachments.map((a) => ({
            kind: a.kind,
            url: a.url,
            mime: a.mime ?? undefined,
            alt_text: a.filename,
            resource_id: a.resource_id,
          })),
          ...refAttachments.map((r) => ({
            kind: r.kind,
            url: '',            // resource_ref resolves by id, not URL
            resource_id: r.resource_id,
            mime: r.mime,
            alt_text: r.name,
          })),
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
            throw new Error(
              mapped ??
                (typeof evt.data?.error === 'string' ? evt.data.error : 'stream error'),
            );
          } else if (evt.type === 'done') {
            // G2: backend resolves attachments best-effort and reports
            // per-attachment failures instead of failing the whole turn —
            // surface the count so a silent no-op isn't the user's only
            // signal that "the file didn't make it".
            const failures = evt.data?.attachment_failures;
            if (Array.isArray(failures) && failures.length > 0) {
              setAttachmentFailureCount(failures.length);
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
        } else {
          const landed = serverMsgs
            .slice(-3)
            .some((m) => m.role === 'user' && m.content === text);
          if (!landed) setStagedAttachments(sentAttachments);
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
     addToast, planMode, stagedAttachments, contextCapsule, t],
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
                runId={msg.role === 'assistant' ? extractRunId(msg) : undefined}
                onApply={
                  msg.role === 'assistant' && onApplyContent
                    ? () => onApplyContent(msg.content)
                    : undefined
                }
              />
            ))}

            <AttachmentFailureBanner count={attachmentFailureCount} />

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
        <div ref={messagesEndRef} />
      </div>

      {/* Composer area: chip strip + plan mode bar + chat input.
          Wrapped in a single relative div so the drag-drop overlay can
          cover the whole composer region. */}
      <div {...dropzoneRootProps} className="relative">
        {/* B: Attachment chip strip — only render when staged or actively
            uploading. The picker button itself lives next to ChatInput. */}
        {activeSessionId && effectiveAgentSlug && stagedAttachments.length > 0 && (
          <div className="flex items-center gap-2 px-3 py-1.5 border-t border-ink-800 bg-ink-900/30">
            <ChatAttachmentPicker
              attachments={stagedAttachments}
              onChange={setStagedAttachments}
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
              onKindChange={setMentionActiveKind}
              onSelect={handleMentionSelect}
              activeIndex={mentionActiveIndex}
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
          {activeSessionId && effectiveAgentSlug && stagedAttachments.length === 0 && (
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
              onSend={handleSend}
              onPaste={composerOnPaste}
              disabled={sending || !activeSessionId || !effectiveAgentSlug}
              placeholder={
                !effectiveAgentSlug
                  ? t('chat.placeholderNoAgent', 'Select an agent to start')
                  : !activeSessionId
                    ? t('chat.placeholderNoSession', 'Create a session first')
                    : t('chat.placeholder', 'Type a message...')
              }
              onMentionRequest={handleMentionRequest}
              editorRef={chatEditorRef}
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
