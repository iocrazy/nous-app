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
import type {
  AILibraryAgent,
  AIChatMessage,
  ChatSession,
  ChatToolCall,
  ResourceRefAttachment,
  ResourceSearchResult,
} from '../types';
import { AgentSelector } from './AgentSelector';
import { SessionList, type SessionItem } from './SessionList';
import { MessageBubble } from './chat/AIChatBubble';
import { TypingIndicator } from './chat/TypingIndicator';
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

/** Coerce the string project id to a BIGINT-compatible number when possible. */
function parseProjectId(projectId: string | undefined): number | undefined {
  if (!projectId) return undefined;
  const n = Number(projectId);
  return Number.isFinite(n) && n > 0 ? n : undefined;
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

  // Agent-scoped mode: when agentSlug prop is provided the panel locks to that
  // agent (no selector) and scopes sessions without a project filter. When absent,
  // the user-selected agent drives everything — identical to the existing behavior.
  const lockedAgent = agentSlug ?? null;
  const effectiveAgentSlug = lockedAgent ?? selectedAgentSlug;

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
  ): Promise<void> {
    try {
      const data = await aiLibraryService.getChatSession(sessionId);
      if (isCancelled()) return;
      setMessages(data.messages ?? []);
    } catch (err) {
      console.error('[AIChatPanel] getChatSession failed:', err);
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
    async (text: string, refAttachments: ResourceRefAttachment[] = []) => {
      if (!activeSessionId || sending) return;
      setSending(true);

      // Optimistic user bubble — replaced by the authoritative row after
      // the server responds and we reload the message list. Staged image
      // attachments render immediately via their local preview data URL.
      const tempUser: AIChatMessage = {
        id: `tmp-user-${Date.now()}`,
        session_id: activeSessionId,
        role: 'user',
        content: text,
        attachments: stagedAttachments.length > 0
          ? stagedAttachments.map((a) => ({
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

      try {
        // O3: pass plan_mode only when non-default. Backend swaps in
        // plan-prompt instructions for prompt_user / dry_run.
        // B: send staged attachments + resource_ref attachments alongside.
        // Clear staged on success — failed sends keep them so user can retry.
        const opts: Parameters<typeof aiLibraryService.sendChatMessage>[2] = {};
        if (planMode !== 'auto') opts.plan_mode = planMode;
        const allAttachments = [
          ...stagedAttachments.map((a) => ({
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
        await aiLibraryService.sendChatMessage(activeSessionId, text, opts);
        setStagedAttachments([]);
        // Refetch full history so IDs + timestamps are server-authoritative.
        await loadSessionMessages(activeSessionId);
      } catch (err) {
        console.error('[AIChatPanel] sendChatMessage failed:', err);
        const msg = err instanceof Error ? err.message : String(err);
        addToast(`Send failed: ${msg}`, 'error');
        // Roll back the optimistic bubble — the server didn't accept it.
        setMessages((prev) => prev.filter((m) => m.id !== tempUser.id));
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
     addToast, planMode, stagedAttachments],
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

  const hasMessages = messages.length > 0 || sending;

  return (
    <div className="relative w-full h-full flex-1 flex flex-col bg-ink-900 overflow-hidden min-h-0">
      {/* Header */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-ink-800 flex-shrink-0">
        <span className="text-sm font-medium text-ink-200 flex-1">AI Chat</span>

        {!lockedAgent && (
          <AgentSelector
            agents={agentOptions}
            selectedId={selectedAgentSlug}
            onSelect={setSelectedAgentSlug}
          />
        )}

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
                          ? 'bg-indigo-500/10 text-indigo-400'
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
                onApply={
                  msg.role === 'assistant' && onApplyContent
                    ? () => onApplyContent(msg.content)
                    : undefined
                }
              />
            ))}

            {sending && (
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
                    ? 'bg-blue-600 text-white'
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
