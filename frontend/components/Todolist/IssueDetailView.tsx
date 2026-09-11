/**
 * Paperclip-style issue detail view (A8.3 wired to real backend).
 *
 * Fetches messages on mount, subscribes to Realtime issue_messages
 * inserts so new comments / status changes / agent runs stream in
 * without manual refresh.
 *
 * Agent replies now stream token-by-token over the /ws/issue/{id}
 * WebSocket (replaces polling from commit 3453a990).
 */

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import {
  ChevronLeft, MoreHorizontal, AlignLeft, Paperclip, FileText, Plus,
  MessageSquare, Link2, Bot,
} from 'lucide-react';
import type { UiIssue, AgentRef } from './types';
import type { CommentTriggerPreview, IssueMessage } from '../../services/issueMessageService';
import { toIssueAttachmentPayload } from './composerAttachmentPayload';
import { IssueStatusIcon, PriorityIcon } from './IssueStatusIcon';
import { formatElapsed } from './formatElapsed';
import { blocksFor, issueBlockContext } from './issueBlocks';
import './blocks';
import { useIssueProgress } from './useIssueProgress';
import { isIssueLive } from './issuePhase';
import { DetachedRunPanel, IssueChatThread } from './IssueChatThread';
import { focusTrajectoryStep } from './focusTrajectoryStep';
import { IssueRelatedTab } from './IssueRelatedTab';
import { IssueReplyBox, type ComposerAttachment } from './IssueReplyBox';
import { AgentNotDispatchedError, getCommentTriggerPreview, listIssueMessages, postIssueMessage } from '../../services/issueMessageService';
import { NeedsInputCard } from './NeedsInputCard';
import { questionFromMarker } from './questionTypes';
import { dispatchIssue, getDispatchPreview, type DispatchPreview } from '../../services/issuesService';
import { DispatchConfirmDialog } from './DispatchConfirmDialog';
import type { SubtaskCount } from './issueFlow';
import { RunPipelineMenu } from './RunPipelineMenu';
import { openIssueChatSocket } from '../../services/issueChatSocket';
import { getSupabaseClient } from '../../supabaseClient';
import { useToast } from '../Toast';
import { aiLibraryService } from '../../services/aiLibraryService';
import { selectRunCost, selectRunView } from '../TaskCenter/runView';
import { ChildRunContext, type ChildRunOrigin, type ChildRunState } from './childRunContext';
import { ReplayContext, type ReplayState } from './replayContext';
import { ForkRunDialog } from './ForkRunDialog';
import { forkErrorText } from './forkErrors';
import { replyErrorText } from './outputRefErrors';

interface IssueDetailViewProps {
  issue: UiIssue;
  agents: AgentRef[];
  agentsById: Record<string, AgentRef>;
  selfUserId?: string;
  /** Opens the New Issue dialog in "sub-issue" mode (parent_id pre-set). */
  onCreateSubIssue: (parentId: number) => void;
  /** Called after a successful dispatch so the parent can re-fetch the issue row. */
  onIssueDispatched?: () => void;
  /** Sub-issue done/total for THIS issue, or undefined when it has no children. */
  subtaskCount?: SubtaskCount;
}

// Chat + Activity were separate tabs over the SAME messages array (Activity
// just filtered kind='system_status'). IssueChatThread already interleaves
// comments, agent runs and status events in one thread, so they're unified
// into a single Timeline — the "one thread with your teammates + agents"
// model. Related stays its own tab (sub-issues / parent).
type DetailTab = 'timeline' | 'related';

/**
 * Header chip shown while an agent is working the issue. The pulsing amber dot
 * + live elapsed timer ("the ticking number = it's alive") is the low-cost,
 * high-signal cue from the multica analysis. Only the timer re-renders each
 * second; it reads started_at off the issue row (no extra fetch).
 */
const AgentWorkingBadge: React.FC<{ startedAt: string | null; agentName?: string }> = ({ startedAt, agentName }) => {
  const [nowMs, setNowMs] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNowMs(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);
  const startMs = startedAt ? new Date(startedAt).getTime() : NaN;
  const elapsed = Number.isFinite(startMs) ? Math.max(0, Math.floor((nowMs - startMs) / 1000)) : null;
  return (
    <span
      className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[12px] text-warn bg-amber-500/10 ring-1 ring-amber-500/30"
      title={agentName ? `${agentName} is working on this issue` : 'An agent is working on this issue'}
    >
      <span className="w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse" />
      {agentName ? `${agentName} is working` : 'Agent working'}
      {elapsed != null && <span className="tabular-nums text-amber-400/80">· {formatElapsed(elapsed)}</span>}
    </span>
  );
};

export const IssueDetailView: React.FC<IssueDetailViewProps> = ({ issue, agents, agentsById, selfUserId, onCreateSubIssue, onIssueDispatched, subtaskCount }) => {
  const { t } = useTranslation();
  const { teamId } = useParams<{ teamId: string }>();
  const [tab, setTab] = useState<DetailTab>('timeline');
  const [messages, setMessages] = useState<IssueMessage[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [dispatching, setDispatching] = useState(false);
  // Bumped after a pipeline run is started so the active-run strip re-fetches.
  const [pipelineRefresh, setPipelineRefresh] = useState(0);
  // Only a numeric team id is a valid pipeline scope (personal-team snowflake).
  const pipelineTeamId = teamId && /^\d+$/.test(teamId) ? teamId : null;
  // A pipeline child issue should not itself be a pipeline parent.
  const isPipelineChild = issue.raw.origin_kind === 'pipeline';
  // Link to the owning project's workspace (plain project link, same shape the
  // Group-by-Project header uses). Only numeric team ids are valid routes.
  const projectPath =
    issue.project && teamId ? `/team/${teamId}/projects/${issue.project.id}` : null;
  // Run-confirm gate: dispatch always goes through a confirm dialog that
  // renders the server's dispatch-preview verdict.
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [preview, setPreview] = useState<DispatchPreview | null>(null);
  // What a COMMENT would start — a different predicate from `preview` above
  // (the comment path has no terminal-status / already-running guard, so it
  // wakes where a dispatch would be blocked). Server-owned on purpose: deriving
  // it here from issue.assignee_agent_id would be right today and silently wrong
  // the day the rule grows a branch.
  const [triggerPreview, setTriggerPreview] = useState<CommentTriggerPreview | null>(null);
  // The composer's draft body WHILE it is a /note command, else null. Set by
  // IssueReplyBox exactly when the draft crosses the note boundary (either
  // direction) — a refetch key, not a verdict: the server's preview endpoint
  // reads the body and decides, we only forward it.
  const [noteDraftBody, setNoteDraftBody] = useState<string | null>(null);
  const [isAgentWorking, setIsAgentWorking] = useState(false);
  const [streamingText, setStreamingText] = useState('');
  const { addToast } = useToast();

  // issue.rollup (harness P4): phase / current run / budget / children, derived
  // server-side from the runs. Polls while live, nudged by agent_runs Realtime.
  const { progress, loaded: progressLoaded, refresh: refreshProgress } = useIssueProgress(issue.id, issue.raw.ai_session_id);
  const phase = progress?.phase ?? null;

  // ── Replay (harness 2b-1 §1) ───────────────────────────────────────────
  // The scrubber attaches to the issue's newest run: the live one, else the
  // last run that posted to the thread. Position lives in `?run&seq` so a
  // link opens the page as of that step.
  const [searchParams, setSearchParams] = useSearchParams();
  const [replayPos, setReplayPos] = useState<{ runId: string; seq: number; view: ReplayState['view']; cost: ReplayState['cost'] } | null>(null);
  const [replayLoading, setReplayLoading] = useState(false);
  const latestRunId = useMemo(() => {
    if (progress?.current_run?.id) return String(progress.current_run.id);
    for (let i = messages.length - 1; i >= 0; i -= 1) {
      const m = messages[i];
      if (m.kind === 'agent_run' && m.agent_run_id) return String(m.agent_run_id);
    }
    return null;
  }, [progress?.current_run?.id, messages]);
  const seekReplay = useCallback(
    async (runId: string, seq: number | null) => {
      if (seq == null) {
        setReplayPos(null);
        setSearchParams((prev) => { const n = new URLSearchParams(prev); n.delete('run'); n.delete('seq'); return n; }, { replace: true });
        return;
      }
      setReplayLoading(true);
      try {
        const at = await aiLibraryService.getRunViewAt(runId, seq);
        const view = selectRunView({ view: at.view });
        if (!view) {
          // A row that predates the folded view: nothing to freeze on — say so
          // instead of a panel of dashes wearing an "as of" badge.
          addToast(t('replay.unavailable', 'Replay is not available for that step.'), 'error');
          return;
        }
        setReplayPos({ runId, seq, view, cost: selectRunCost({ cost: at.cost }) });
        setSearchParams((prev) => { const n = new URLSearchParams(prev); n.set('run', runId); n.set('seq', String(seq)); return n; }, { replace: true });
      } catch (err) {
        console.error('[IssueDetailView] replay seek failed', err);
        addToast(err instanceof Error ? err.message : 'Replay failed', 'error');
      } finally {
        setReplayLoading(false);
      }
    },
    [setSearchParams, addToast, t],
  );
  // Deep link, honoured once after the rollup has loaded, in two shapes:
  //
  //   `?run=&seq=`  the replay link — names WHICH run, so it wins outright.
  //   `?step=`      what every lineage / Generated-card link carries (3a Task
  //                 3b). It names no run, so it cannot be seeked: it opens the
  //                 step node itself. Read at all only since Task 8b — before
  //                 that the anchor was built, sent, and dropped on arrival,
  //                 landing every provenance click at the top of the issue.
  //
  // Any run of the issue may be named: the newest one (scrubber on its row) or
  // an older one, e.g. a fork's origin (drawn in the detached panel).
  const deepLinked = useRef(false);
  const stepFocus = useRef<(() => void) | null>(null);
  // The step anchor is spent only once it has actually OPENED a step. Latching
  // on "we started looking" is what made this dead under StrictMode, which
  // runs every effect mount → cleanup → mount: pass one started the search,
  // the cleanup cancelled it, and pass two was turned away by the latch.
  const stepOpened = useRef(false);
  useEffect(() => () => stepFocus.current?.(), []);
  useEffect(() => {
    if (!progressLoaded) return;
    const run = searchParams.get('run');
    const seq = Number(searchParams.get('seq'));
    if (run && Number.isFinite(seq) && seq > 0) {
      if (deepLinked.current) return;
      deepLinked.current = true;
      void seekReplay(run, seq);
      return;
    }
    if (stepOpened.current) return;
    const raw = searchParams.get('step');
    if (raw === null) return;
    const step = Number(raw);
    // `turn` is the other half of a step's identity (the trajectory keys its
    // nodes by the pair). Optional: links built before the builder sent it —
    // and every row with no recorded turn — carry the step alone.
    const rawTurn = searchParams.get('turn');
    const turn = rawTurn === null ? null : Number(rawTurn);
    // Steps and turns are 0-based, so the guard is `>= 0`, not truthiness.
    if (Number.isInteger(step) && step >= 0) {
      // Cancel any search already in flight before starting another: a
      // re-run that just overwrote the ref would orphan a timer that keeps
      // unfolding groups in a tree nobody is looking at.
      stepFocus.current?.();
      stepFocus.current = focusTrajectoryStep(
        {
          step,
          turn: turn !== null && Number.isInteger(turn) && turn >= 0 ? turn : null,
        },
        () => {
          stepOpened.current = true;
        },
      );
    }
  }, [progressLoaded, searchParams, seekReplay]);
  // A STRICTLY NEWER run appeared while replaying the previously-newest one:
  // that position (and its URL) is stale. A replay of an older run (fork
  // chip / link) survives, and so does the position when `latestRunId` merely
  // flips back to an older row (current_run goes null at run end).
  const prevLatest = useRef<string | null>(null);
  useEffect(() => {
    const before = prevLatest.current;
    prevLatest.current = latestRunId;
    const newer = (a: string, b: string) => (a.length === b.length ? a > b : a.length > b.length); // snowflakes: monotonic
    if (replayPos && before && latestRunId && newer(latestRunId, before) && replayPos.runId === before) {
      setReplayPos(null);
      setSearchParams((prev) => { const n = new URLSearchParams(prev); n.delete('run'); n.delete('seq'); return n; }, { replace: true });
    }
  }, [replayPos, latestRunId, setSearchParams]);
  // The run being replayed is not in this thread (a fork's origin lives in
  // the issue's previous conversation): draw it above the thread.
  const detachedRunId = useMemo(() => {
    if (!replayPos) return null;
    const inThread = messages.some((m) => m.kind === 'agent_run' && m.agent_run_id && String(m.agent_run_id) === replayPos.runId);
    return inThread || replayPos.runId === progress?.current_run?.id ? null : replayPos.runId;
  }, [replayPos, messages, progress?.current_run?.id]);
  // ── Sub-runs (harness 2b-2 §5-1) ───────────────────────────────────────
  // A sub-agent card opens its child run in a panel above the thread. The
  // page owns the state because the panel is a sibling of the thread, not a
  // descendant of the card that asked for it.
  const [childRun, setChildRun] = useState<ChildRunOrigin | null>(null);
  // Bumped when a wake-up is armed or cancelled, so the Schedules block
  // re-reads instead of showing a list that is already out of date.
  const [schedulesRefresh, setSchedulesRefresh] = useState(0);
  const childRunState = useMemo<ChildRunState>(
    () => ({ current: childRun, open: setChildRun, close: () => setChildRun(null) }),
    [childRun],
  );

  // ── Fork (harness 2b-1 §2) ─────────────────────────────────────────────
  const [forkAt, setForkAt] = useState<{ runId: string; seq: number; label: string } | null>(null);
  const [forkPending, setForkPending] = useState(false);
  const [forkError, setForkError] = useState<string | null>(null);
  // `refresh` (the thread re-read) is declared further down; reach it by ref.
  const refreshMessagesRef = useRef<() => Promise<void>>(async () => undefined);
  const confirmFork = useCallback(
    async (steer: string | undefined) => {
      if (!forkAt || forkPending) return;
      setForkPending(true);
      setForkError(null);
      try {
        await aiLibraryService.forkRun(forkAt.runId, { at_seq: forkAt.seq, steer });
        setForkAt(null);
        setReplayPos(null);
        setSearchParams((prev) => { const n = new URLSearchParams(prev); n.delete('run'); n.delete('seq'); return n; }, { replace: true });
        addToast(t('fork.started', 'Forked — the new run is starting.'), 'success');
        void refreshProgress();
        void refreshMessagesRef.current();
      } catch (err) {
        console.error('[IssueDetailView] fork failed', err);
        setForkError(forkErrorText(err, t));
      } finally {
        setForkPending(false);
      }
    },
    [forkAt, forkPending, setSearchParams, addToast, t, refreshProgress],
  );
  // The scrubber attaches to the run being replayed (a fork chip can point it
  // at an older run of the same issue); at rest, the newest run.
  const attachedRunId = replayPos?.runId ?? latestRunId;
  const replay = useMemo<ReplayState | null>(
    () =>
      attachedRunId
        ? {
            runId: attachedRunId,
            seq: replayPos && replayPos.runId === attachedRunId ? replayPos.seq : null,
            view: replayPos && replayPos.runId === attachedRunId ? replayPos.view : null,
            cost: replayPos && replayPos.runId === attachedRunId ? replayPos.cost : null,
            loading: replayLoading,
            seek: (seq) => void seekReplay(attachedRunId, seq),
            seekRun: (runId, seq) => void seekReplay(runId, seq),
            fork: (seq, label) => {
              setForkError(null);
              setForkAt({ runId: attachedRunId, seq, label });
            },
          }
        : null,
    [attachedRunId, replayPos, replayLoading, seekReplay],
  );

  // Stable ref so the WS event handler always reads the latest messages
  // without needing to re-open the socket.
  const messagesRef = useRef<IssueMessage[]>([]);
  useEffect(() => { messagesRef.current = messages; }, [messages]);

  // ── Initial + post-action fetch ─────────────────────────────────────────
  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const list = await listIssueMessages(issue.id);
      setMessages(list.messages);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load messages');
    } finally {
      setLoading(false);
    }
  }, [issue.id]);

  useEffect(() => { void refresh(); }, [refresh]);

  // ── Comment trigger disclosure ──────────────────────────────────────────
  // Re-asks whenever the assignee changes OR the draft crosses the /note
  // boundary: those are the only inputs to today's predicate, and a stale
  // verdict would name the wrong agent — or the wrong state — on the chip.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const p = await getCommentTriggerPreview(issue.id, noteDraftBody);
        if (!cancelled) setTriggerPreview(p);
      } catch (err) {
        // Non-fatal: no chip is better than a wrong chip, and the composer
        // stays fully usable. Never silently swallow — this endpoint failing
        // means the disclosure is off, which is worth seeing in the logs.
        console.error('[IssueDetailView] comment-trigger-preview failed', err);
        if (!cancelled) setTriggerPreview(null);
      }
    })();
    return () => { cancelled = true; };
    // raw.assignee_agent_id, not the mapped `assignee` ref: the raw column is
    // the predicate's actual input, so keying on it re-asks exactly when the
    // server's answer could change. noteDraftBody changes only at the note
    // boundary (IssueReplyBox is boundary-triggered), so typing doesn't spam
    // the endpoint.
  }, [issue.id, issue.raw.assignee_agent_id, noteDraftBody]);

  // ── WebSocket: open on mount / issue.id change, close on unmount ────────
  useEffect(() => {
    let ws: WebSocket | null = null;
    let cancelled = false;

    openIssueChatSocket(issue.id, (event) => {
      if (event.type === 'chunk') {
        setStreamingText((prev) => prev + event.delta);
      } else if (event.type === 'message') {
        // Push the finalized message (dedupe by id).
        setMessages((prev) => {
          if (prev.some((m) => m.id === event.message.id)) return prev;
          return [...prev, event.message];
        });
        setStreamingText('');
        setIsAgentWorking(false);
      } else if (event.type === 'status') {
        if (event.phase === 'running') {
          setIsAgentWorking(true);
          setStreamingText('');
        } else if (event.phase === 'done') {
          setIsAgentWorking(false);
          setStreamingText('');
        }
      }
    })
      .then((socket) => {
        if (cancelled) {
          socket.close();
          return;
        }
        ws = socket;
        ws.onclose = () => {
          setStreamingText('');
          setIsAgentWorking(false);
        };
      })
      .catch((err) => {
        // Ticket acquisition failure — not fatal; initial refresh() still shows messages.
        console.debug('[IssueDetailView/WS] socket open failed:', err);
      });

    return () => {
      cancelled = true;
      ws?.close();
    };
  }, [issue.id]);

  // ── Realtime: INSERT + UPDATE on issue_messages ──────────────────────────
  // REPLICA IDENTITY FULL on issue_messages (mig 205) means the UPDATE
  // payload includes the full row, not just changed columns.
  useEffect(() => {
    const supa = getSupabaseClient();
    if (!supa) return;
    const handle = (event: 'INSERT' | 'UPDATE') => (payload: { new?: unknown; old?: unknown }) => {
      const row = (payload.new ?? payload.old) as IssueMessage | undefined;
      if (!row) return;
      setMessages((prev) => {
        if (event === 'UPDATE') {
          let touched = false;
          const next = prev.map((m) => {
            if (m.id !== row.id) return m;
            touched = true;
            return row;
          });
          return touched ? next : prev;
        }
        // INSERT: dedupe by id (POST response may have already added the row).
        if (prev.some((m) => m.id === row.id)) return prev;
        return [...prev, row];
      });
    };
    const channel = supa
      .channel(`issue-messages-${issue.id}`)
      .on(
        'postgres_changes',
        { event: 'INSERT', schema: 'public', table: 'issue_messages', filter: `issue_id=eq.${issue.id}` },
        handle('INSERT'),
      )
      .on(
        'postgres_changes',
        { event: 'UPDATE', schema: 'public', table: 'issue_messages', filter: `issue_id=eq.${issue.id}` },
        handle('UPDATE'),
      )
      .subscribe();
    return () => { void supa.removeChannel(channel); };
  }, [issue.id]);

  const handleReply = async (
    body: string,
    agentId: string | null,
    attachments: ComposerAttachment[] = [],
    suppressAgentIds?: string[],
  ) => {
    try {
      const attachmentPayload = toIssueAttachmentPayload(attachments);
      const res = await postIssueMessage(issue.id, {
        body,
        agent_id: agentId ?? undefined,
        attachments: attachmentPayload,
        // Omit the key entirely when nothing is suppressed — an empty array
        // would read as "an explicit empty exclusion list".
        suppress_agent_ids: suppressAgentIds?.length ? suppressAgentIds : undefined,
      });
      // harness P4 §1-③: a running root run took the comment as a steer. Say
      // so — the thread shows the comment either way, but "no new turn" is
      // the news, not a silent no-op.
      if (res?.diverted_to_inbox) {
        addToast(t('issueDetail.divertedToast', 'Sent to the running agent — picked up before its next step'), 'success');
        void refreshProgress();
      }
      // Spec-1b: for issues with an assigned agent the backend writes to
      // ai_messages (not issue_messages) and returns an optimistic comment
      // whose id does NOT match the real ai_messages row.  Appending the
      // optimistic row would leave a phantom entry that never reconciles.
      // Refetching via GET replaces the list with the canonical thread
      // (human reply + any already-completed agent reply) and naturally
      // discards the optimistic id.
      await refresh();
    } catch (err) {
      // A refused CITATION gets its own sentence: the server's message names a
      // kind and a snowflake, and it is the one refusal that rejects the whole
      // comment rather than degrading it (3a Task 6). Every other failure
      // keeps its own words.
      addToast(replyErrorText(err, t), 'error');
      throw err; // signal failure to keep textarea content
    }
  };

  // The agent stopped and asked something. `agent_outcome` is set by
  // route_finish_outcome's needs_input branch; the literal string match is
  // deliberate — an EMPTY_OUTPUT stall parks at the same status but nothing
  // was actually asked, so it must not render a question card.
  const execState = issue.raw.execution_state as Record<string, unknown> | null;
  const isAskingUser =
    issue.status === 'needs_followup' && execState?.agent_outcome === 'needs_input';
  const agentQuestion = (execState?.outcome_reason as string | null | undefined) ?? null;

  // Same contract as TaskCenter's handleAnswerNeedsInput: a POST can succeed
  // while starting no turn at all, and `agent_run` is null on every path, so
  // `agent_dispatched` is the only way to tell. Throwing the typed error lets
  // the card show "saved but nothing started" instead of silently pretending
  // the agent picked it up.
  const handleAnswerQuestion = useCallback(
    async (body: string, answerTo?: string) => {
      const res = await postIssueMessage(
        issue.id,
        answerTo ? { body, answer_to: answerTo } : { body },
      );
      await refresh();
      if (!res.agent_dispatched) throw new AgentNotDispatchedError();
      onIssueDispatched?.();
    },
    [issue.id, refresh, onIssueDispatched],
  );
  // Phase 2a: the typed question the issue was parked with (buttons), or
  // null for a plain needs_input park (textarea). Parsed once per row.
  const typedQuestion = questionFromMarker(execState);

  const handleDispatch = async () => {
    if (!issue?.id) return;
    setDispatching(true);
    try {
      await dispatchIssue(issue.id);
      await refresh();
      onIssueDispatched?.();
      setConfirmOpen(false);
      addToast('Agent dispatched', 'success');
    } catch (e) {
      console.error('[IssueDetailView] dispatch failed', e);
      addToast(e instanceof Error ? e.message : 'Dispatch failed', 'error');
    } finally {
      setDispatching(false);
    }
  };

  /** Open the confirm gate and ask the server what a dispatch would start. */
  const openDispatchConfirm = () => {
    if (!issue?.id) return;
    setPreview(null);
    setConfirmOpen(true);
    getDispatchPreview(issue.id)
      .then(setPreview)
      .catch((e) => {
        console.error('[IssueDetailView] dispatch preview failed', e);
        addToast(e instanceof Error ? e.message : 'Could not check dispatch', 'error');
        setConfirmOpen(false);
      });
  };

  const blockCtx = useMemo(
    () =>
      issueBlockContext(
        issue as unknown as Record<string, unknown>,
        progress,
        {
          agentsById,
          projectPath,
          subtaskCount: subtaskCount ?? null,
          assigneeName: issue.assignee?.name ?? issue.assignee_user_label,
          refreshKey: pipelineRefresh,
          schedulesRefreshKey: schedulesRefresh,
          teamId,
          onIssueChanged: () => {
            void refreshProgress();
            onIssueDispatched?.();
          },
          onAnswerQuestion: (value: string, answerTo: string) =>
            handleAnswerQuestion(value, answerTo),
        },
      ),
    [issue, progress, agentsById, projectPath, subtaskCount, pipelineRefresh, schedulesRefresh, teamId, refreshProgress, onIssueDispatched, handleAnswerQuestion],
  );
  const cockpitBlocks = blocksFor('cockpit', blockCtx);
  useEffect(() => {
    refreshMessagesRef.current = refresh;
  }, [refresh]);
  const contextBlocks = blocksFor('context', blockCtx);
  const agentLive = isAgentWorking || phase === 'running';

  return (
    <div className={`flex flex-col bg-ink-950 border-t border-ink-800/80 h-full min-h-0`}>
      <div className="flex items-center gap-2 px-4 py-2.5 border-b border-ink-800/80 text-[13px] text-ink-500">
        <Link to={`/team/${teamId}/todolist`} className="inline-flex items-center gap-1 hover:text-ink-300">
          <ChevronLeft size={13} />
          Issues
        </Link>
        <span className="text-ink-600">/</span>
        <span className="text-ink-300 truncate">{issue.title}</span>
      </div>

      <div className="flex-1 overflow-y-auto">
        {/* A2: two columns from md up — conversation left, progress rail right.
            Below md the rail stacks under the article (single column).

            ONE child-run provider wraps BOTH columns (3a T8c 缺陷 2). It used
            to sit twice inside the article — around the cockpit and around the
            thread — so the rail's blocks rendered outside every provider,
            `useChildRun()` handed them null, and the «Open Run» button in the
            dialog they open was permanently disabled with "The run panel is
            not open here" — on the very page that holds the panel. The panel
            is page state, so its context belongs at the page level, not once
            per consumer. */}
        <ChildRunContext.Provider value={childRunState}>
        <div className="max-w-5xl mx-auto px-4 sm:px-8 py-6 md:grid md:grid-cols-[1.5fr_1fr] md:gap-6 md:items-start">
        <div className="min-w-0">
          <div className="flex items-center gap-2 mb-3">
            <IssueStatusIcon status={issue.status} size={15} />
            <span className="font-mono text-[12px] text-ink-500 uppercase tracking-wider">{issue.identifier}</span>
            <span title={issue.priority}>
              <PriorityIcon priority={issue.priority} />
            </span>
            {(agentLive || isIssueLive(issue)) && (
              <AgentWorkingBadge startedAt={issue.raw.started_at} agentName={issue.assignee?.name} />
            )}
            <div className="ml-auto flex items-center gap-1">
              <button className="p-1.5 text-ink-500 hover:text-ink-300 hover:bg-ink-800 rounded" title="Properties">
                <AlignLeft size={14} />
              </button>
              <button className="p-1.5 text-ink-500 hover:text-ink-300 hover:bg-ink-800 rounded" title="More">
                <MoreHorizontal size={14} />
              </button>
            </div>
          </div>

          <h1 className="text-xl font-semibold text-ink-100 leading-tight">{issue.title}</h1>
          {issue.description && (
            <p className="mt-2 text-[14px] text-ink-400 leading-relaxed whitespace-pre-wrap">{issue.description}</p>
          )}

          {/* Zone: cockpit — registered blocks (issueBlocks.ts); today one block
              reading the rollup through runView selectors. */}
          <ReplayContext.Provider value={replay}>
            {cockpitBlocks.map((b) => (
              <b.component key={b.id} ctx={blockCtx} />
            ))}
          </ReplayContext.Provider>
          {forkAt && (
            <ForkRunDialog
              stepLabel={forkAt.label}
              pending={forkPending}
              error={forkError}
              onConfirm={(steer) => void confirmFork(steer)}
              onCancel={() => { if (!forkPending) setForkAt(null); }}
            />
          )}

          <div className="grid grid-cols-1 sm:grid-cols-3 gap-2 mt-5">
            <button
              onClick={() => onCreateSubIssue(issue.id)}
              className="inline-flex items-center justify-center gap-1.5 px-3 py-2 text-[13px] rounded border border-ink-800 bg-ink-900/50 text-ink-300 hover:bg-ink-800/60"
            >
              <Plus size={13} /> New Sub-issue
            </button>
            <button
              className="inline-flex items-center justify-center gap-1.5 px-3 py-2 text-[13px] rounded border border-ink-800 bg-ink-900/50 text-ink-500 cursor-not-allowed"
              disabled
              title="Attachments backend not in place yet"
            >
              <Paperclip size={13} /> Upload attachment
            </button>
            <button
              className="inline-flex items-center justify-center gap-1.5 px-3 py-2 text-[13px] rounded border border-ink-800 bg-ink-900/50 text-ink-500 cursor-not-allowed"
              disabled
              title="Documents backend not in place yet"
            >
              <FileText size={13} /> New document
            </button>
            {issue.assignee && ['backlog', 'todo'].includes(issue.status) && (
              <button
                onClick={openDispatchConfirm}
                disabled={dispatching}
                className="btn-tint-indigo inline-flex items-center justify-center gap-1.5 px-3 py-2 text-[13px] rounded disabled:opacity-50 disabled:cursor-not-allowed"
                title={`Dispatch to ${issue.assignee.name}`}
              >
                <Bot size={13} /> {dispatching ? 'Dispatching…' : 'Dispatch to Agent'}
              </button>
            )}
            {!isPipelineChild && pipelineTeamId && (
              <RunPipelineMenu
                issueId={issue.id}
                teamId={pipelineTeamId}
                onRan={() => setPipelineRefresh((v) => v + 1)}
              />
            )}
          </div>

          {isAskingUser && (
            <NeedsInputCard
              question={agentQuestion}
              agentName={issue.assignee?.name}
              onSubmit={handleAnswerQuestion}
              typed={typedQuestion}
              onAnswer={(value, answerTo) => handleAnswerQuestion(value, answerTo)}
            />
          )}

          <div className="flex items-center border-b border-ink-800/80 mt-6">
            {(['timeline', 'related'] as DetailTab[]).map((t) => {
              const label = t === 'timeline' ? 'Timeline' : 'Related work';
              const Icon = t === 'timeline' ? MessageSquare : Link2;
              const active = tab === t;
              return (
                <button
                  key={t}
                  onClick={() => setTab(t)}
                  className={`inline-flex items-center gap-1.5 px-3 py-2 text-[13px] font-medium border-b-2 transition ${
                    active
                      ? 'border-indigo-400 text-ink-100'
                      : 'border-transparent text-ink-500 hover:text-ink-300'
                  }`}
                >
                  <Icon size={13} />
                  {label}
                </button>
              );
            })}
            <span className="ml-auto text-[12px] text-ink-600 pr-2">{messages.length} message{messages.length === 1 ? '' : 's'}</span>
          </div>

          {tab === 'timeline' && (
            <>
              {error && (
                <div className="mt-4 mx-2 px-3 py-2 rounded bg-rose-500/10 text-rose-300 text-[13px] ring-1 ring-rose-500/30">
                  {error}
                </div>
              )}
              {loading && messages.length === 0
                ? <div className="text-[14px] text-ink-500 italic px-4 py-12 text-center">Loading messages…</div>
                : (
                  <ReplayContext.Provider value={replay}>
                    {detachedRunId && <DetachedRunPanel runId={detachedRunId} />}
                    {childRun && (
                      <DetachedRunPanel
                        runId={childRun.childRunId}
                        origin={childRun}
                        onBack={() => setChildRun(null)}
                      />
                    )}
                    <IssueChatThread
                      messages={messages}
                      agentsById={agentsById}
                      selfUserId={selfUserId}
                      streamingText={streamingText}
                      teamId={teamId}
                      aiSessionId={issue.raw.ai_session_id}
                    />
                  </ReplayContext.Provider>
                )}
              {agentLive && (
                <div className="flex items-center gap-2 px-4 py-2.5 text-[13px] text-ink-400">
                  <span
                    className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse"
                    style={{ boxShadow: '0 0 6px 1px rgba(16,185,129,0.45)' }}
                  />
                  <span className="text-ink-400">Agent is working…</span>
                </div>
              )}
            </>
          )}
          {tab === 'related' && (
            <IssueRelatedTab issue={issue} />
          )}
        </div>

        <aside className="mt-6 md:mt-0 md:sticky md:top-6 space-y-3" data-testid="issue-context-rail">
          {/* Zone: context — registered blocks, matched per issue (origin kind,
              project, children …) and ordered. No branches here. */}
          {contextBlocks.map((b) => (
            <b.component key={b.id} ctx={blockCtx} />
          ))}
        </aside>
        </div>
        </ChildRunContext.Provider>
      </div>

      {/* Composer keeps the article column's width — it belongs to the
          conversation, not the rail. */}
      <div className="w-full max-w-5xl mx-auto px-4 sm:px-8 md:grid md:grid-cols-[1.5fr_1fr] md:gap-6">
        <div className="min-w-0">
        <IssueReplyBox
          agents={agents}
          defaultAgentId={issue.assignee?.id ?? null}
          onSubmit={handleReply}
          hint={
            phase === 'running'
              ? t('issueDetail.steerHint', 'The agent is running — your comment is picked up before its next step.')
              : phase === 'idle' && issue.assignee
                ? t('issueDetail.replyHint', "Your comment starts the agent's next turn.")
                : undefined
          }
          triggerPreview={triggerPreview}
          onNoteBoundaryChange={setNoteDraftBody}
          triggerAgentName={
            triggerPreview?.agent_id
              ? agentsById?.[triggerPreview.agent_id]?.name
              : undefined
          }
          teamId={teamId}
          issueId={Number(issue.id)}
          onScheduled={() => setSchedulesRefresh((n) => n + 1)}
        />
        </div>
      </div>

      {confirmOpen && (
        <DispatchConfirmDialog
          preview={preview}
          agentName={
            (preview?.agent_id ? agentsById[preview.agent_id]?.name : undefined)
            ?? issue.assignee?.name
          }
          confirming={dispatching}
          onConfirm={handleDispatch}
          onClose={() => setConfirmOpen(false)}
        />
      )}
    </div>
  );
};
