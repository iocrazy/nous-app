/**
 * Paperclip-style Chat tab — streamed timeline of issue messages,
 * agent runs, and system status changes (A8.3 wired to real backend).
 *
 * Consumes IssueMessage rows directly from issueMessageService. Author
 * resolution (uuid → display name + avatar color) happens here via the
 * agentsMap / userLabel passed in by the parent page.
 */

import React, { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router-dom';
import { Zap, ChevronRight, ChevronDown, Clock, Paperclip, GitFork } from 'lucide-react';
import type { IssueMessage, AgentLivenessState } from '../../services/issueMessageService';
import { OutputChipBody } from '../chat/OutputChipBody';
import { simulateAgentRunComplete, startedByWakeup } from '../../services/issueMessageService';
import type { ChildRunOrigin } from './childRunContext';
import { coalesceSystemStatus } from './coalesceSystemStatus';
import { groupAgentRuns, type RunGroupEntry } from './runGrouping';
import { formatTokens, formatCentsAsUsd } from '../../pages/usagePanelHelpers';
import type { AgentRef } from './types';
import { STATUS_LABEL, STATUS_COLOR, IssueStatusIcon } from './IssueStatusIcon';
import type { IssueStatus } from '../../services/issuesService';
import { relativeTime } from '../../utils/taskDisplay';
import { useToast } from '../Toast';
import { useElapsedSeconds } from '../../hooks/useElapsedSeconds';
import { CapabilityDeniedNotice } from '../agentActivity/CapabilityDeniedNotice';
import { TrajectoryRenderer } from '../agentActivity/TrajectoryRenderer';
import { useRunToolActivity } from '../agentActivity/useRunToolActivity';
import { ReplayScrubber } from '../agentActivity/ReplayScrubber';
import { replayTicks } from '../agentActivity/replayTicks';
import { isReplaying, useReplay } from './replayContext';
import { forkMarksFor, useRunForks } from '../agentActivity/useRunForks';
import { forkOrigin, timedOutTools } from '../agentActivity/runHeader';

// `finished` uses the semantic `info` token (K1 §2.3 — the convention for new
// code) rather than a success green: the whole point of the state is to be
// told apart at a glance from `running`, which already owns the green dot.
const LIVENESS_VISUAL: Record<AgentLivenessState, { dot: string; label: string; tooltip: string }> = {
  running:   { dot: 'bg-emerald-500', label: 'running',   tooltip: 'Agent is making progress' },
  silent:    { dot: 'bg-amber-400',   label: 'silent',    tooltip: 'No useful action recently — watching' },
  stuck:     { dot: 'bg-orange-500',  label: 'stuck',     tooltip: 'Stuck long enough to attempt continuation' },
  dead:      { dot: 'bg-rose-500',    label: 'dead',      tooltip: 'Marked dead by liveness scanner' },
  cancelled: { dot: 'bg-ink-500',    label: 'cancelled', tooltip: 'Cancelled by user / system' },
  finished:  { dot: 'bg-info',        label: 'finished',  tooltip: 'Run completed normally' },
};

const LivenessPill: React.FC<{ state: AgentLivenessState }> = ({ state }) => {
  const v = LIVENESS_VISUAL[state] ?? LIVENESS_VISUAL.running;
  return (
    <span
      className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-ink-900/60 ring-1 ring-ink-800 text-[12px] text-ink-300"
      title={v.tooltip}
    >
      <span className={`w-1.5 h-1.5 rounded-full ${v.dot}`} />
      {v.label}
    </span>
  );
};

interface IssueChatThreadProps {
  messages: IssueMessage[];
  agentsById: Record<string, AgentRef>;
  /** Display label for the current authenticated user (so own comments show "You"). */
  selfUserId?: string;
  /**
   * Live token-by-token text accumulating from the active agent turn.
   * When non-empty a transient streaming bubble is rendered at the bottom.
   * Cleared (set to '') by the parent when the `message` event arrives.
   */
  streamingText?: string;
  /**
   * Team whose route the run-group deep link is built under. Absent on
   * surfaces rendered outside a /team/:teamId route — the link is then
   * suppressed rather than pointing at a half-built URL.
   */
  teamId?: string;
  /**
   * `issues.ai_session_id` — the conversation the agent's turns were written
   * into. Null until the issue's first dispatch backfills one, so the
   * "Open conversation" affordance is conditional on it.
   */
  aiSessionId?: string | null;
}

const AgentAvatar: React.FC<{ initials: string; color?: string; size?: number }> = ({ initials, color = 'bg-ink-600', size = 22 }) => (
  <span
    className={`inline-flex items-center justify-center rounded-full text-[12px] font-semibold text-ink-50 ${color}`}
    style={{ width: size, height: size }}
  >
    {initials}
  </span>
);

function formatDuration(sec?: number | null): string {
  if (sec === null || sec === undefined) return '';
  if (sec < 60) return `${sec} second${sec === 1 ? '' : 's'}`;
  const min = Math.floor(sec / 60);
  return `${min} minute${min === 1 ? '' : 's'}`;
}

const SystemStatusEvent: React.FC<{ msg: IssueMessage; selfUserId?: string }> = ({ msg, selfUserId }) => {
  const from = msg.from_status as IssueStatus | undefined;
  const to = msg.to_status as IssueStatus | undefined;
  const isSelf = msg.author_user_id && msg.author_user_id === selfUserId;
  const author = isSelf ? 'You' : msg.author_user_id ? `User ${msg.author_user_id.slice(0, 6)}` : 'System';
  return (
    <div
      data-testid="system-status-row"
      className="text-center text-[11px] text-ink-500 italic my-2"
    >
      <span className="text-ink-400 not-italic font-medium">{author}</span>
      {' '}updated this task —{' '}
      <span className="not-italic">STATUS</span>{' '}
      {from && (
        <span className={`${STATUS_COLOR[from]} not-italic font-medium inline-flex items-center gap-0.5`}>
          <IssueStatusIcon status={from} size={10} />
          {STATUS_LABEL[from].toLowerCase()}
        </span>
      )}
      {' '}<span className="text-ink-600 not-italic">→</span>{' '}
      {to && (
        <span className={`${STATUS_COLOR[to]} not-italic font-medium inline-flex items-center gap-0.5`}>
          <IssueStatusIcon status={to} size={10} />
          {STATUS_LABEL[to].toLowerCase()}
        </span>
      )}
    </div>
  );
};

/**
 * A deliverable-upload line. Emitted as an authored comment (the issue_messages
 * system_status kind requires a status transition, which an upload has none of)
 * and identified by its meta marker.
 *
 * A2: promoted from a muted centered whisper to an ok-tinted card. A filed
 * deliverable is what the whole exchange was FOR — it read as less important
 * than a todo → in_progress flip, which is exactly backwards.
 */
const DeliverableFiledEvent: React.FC<{ msg: IssueMessage; agentsById: Record<string, AgentRef>; selfUserId?: string }> = ({ msg, agentsById, selfUserId }) => {
  const agent = msg.author_agent_id ? agentsById[msg.author_agent_id] : null;
  const isSelf = msg.author_user_id && msg.author_user_id === selfUserId;
  const who = agent?.name ?? (isSelf ? 'You' : msg.author_user_id ? `User ${msg.author_user_id.slice(0, 6)}` : 'Someone');
  const filename = (msg.meta?.filename as string | undefined) ?? '';
  return (
    <div
      data-testid="deliverable-filed-row"
      className="my-3 flex items-center gap-2 rounded-lg border border-ok-line bg-ok-soft px-3 py-2"
    >
      <Paperclip size={14} className="text-ok shrink-0" />
      <span className="text-[14px] text-ink-200 font-medium truncate">{filename}</span>
      <span className="text-[12px] text-ink-500 truncate">filed by {who}</span>
      <span className="ml-auto text-[12px] text-ink-500 shrink-0">{relativeTime(msg.created_at)}</span>
    </div>
  );
};

const AgentRunEvent: React.FC<{ msg: IssueMessage; agentsById: Record<string, AgentRef>; fromWakeup?: boolean }> = ({ msg, agentsById, fromWakeup }) => {
  const agent = msg.author_agent_id ? agentsById[msg.author_agent_id] : null;
  const initials = (agent?.name ?? '·').slice(0, 2).toUpperCase();
  const boardSignoff = (msg.meta?.board_signoff as string | undefined);
  // Liveness comes either inlined on the row (future), or via meta.status
  // emitted by the bridge trigger (mig 206). Display it as a pill so users
  // can see "agent is silent / stuck / dead" without leaving the chat.
  const liveness = (msg.liveness_state as AgentLivenessState | undefined)
    ?? (msg.meta?.liveness_state as AgentLivenessState | undefined)
    ?? null;
  const metaStatus = msg.meta?.status as string | undefined;
  const errorCode = msg.meta?.error_code as string | undefined;
  const isRunning = metaStatus === 'running';
  // Live tick for in-flight runs. Falls back to msg.created_at as the
  // timestamp origin because started_at is not yet surfaced on IssueMessage
  // (agent_runs.started_at exists in DB but isn't in the REST response).
  const elapsedLive = useElapsedSeconds(msg.created_at, { enabled: isRunning });
  const { addToast } = useToast();
  const [simulating, setSimulating] = useState(false);

  const onSimulate = async () => {
    if (!msg.agent_run_id || simulating) return;
    setSimulating(true);
    try {
      await simulateAgentRunComplete(msg.issue_id, msg.agent_run_id);
      addToast('Simulated agent finish — row should update shortly', 'success');
    } catch (err) {
      addToast(err instanceof Error ? err.message : 'Simulate failed', 'error');
    } finally {
      setSimulating(false);
    }
  };
  return (
    <div className="my-3" id={msg.agent_run_id ? `run-${msg.agent_run_id}` : undefined} data-testid="agent-run-row">
      <div className="flex items-center gap-2 mb-1.5">
        <AgentAvatar initials={initials} color={agent?.avatar_color} />
        <span className="text-xs font-medium text-ink-200">{agent?.name ?? 'Agent'}</span>
        {isRunning ? (
          <span className="text-[12px] text-ink-500">working for {formatDuration(elapsedLive)}</span>
        ) : msg.duration_seconds != null ? (
          <span className="text-[12px] text-ink-500">worked for {formatDuration(msg.duration_seconds)}</span>
        ) : null}
        {liveness && <LivenessPill state={liveness} />}
        {metaStatus && metaStatus !== 'completed' && (
          <span className="text-[12px] text-ink-500 italic">({metaStatus})</span>
        )}
        {isRunning && msg.agent_run_id && (
          <button
            onClick={onSimulate}
            disabled={simulating}
            className="inline-flex items-center gap-1 px-1.5 py-0.5 text-[12px] rounded bg-amber-500/10 text-warn ring-1 ring-amber-500/30 hover:bg-amber-500/20 disabled:opacity-50"
            title="Dev: simulate the agent finishing this run"
          >
            <Zap size={9} /> {simulating ? 'Sim…' : 'Simulate finish'}
          </button>
        )}
        {errorCode && (
          <span className="px-1.5 py-0.5 rounded bg-rose-500/15 text-rose-300 ring-1 ring-rose-500/30 text-[12px] font-mono" title="error_code">
            {errorCode}
          </span>
        )}
        <span className="ml-auto inline-flex items-center gap-2 text-[12px] text-ink-500">
          {boardSignoff && (
            <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-purple-500/10 text-purple-300 text-[12px]">
              Board <span className="font-mono text-[12px]">{boardSignoff}</span>
            </span>
          )}
          {relativeTime(msg.created_at)}
        </span>
      </div>
      <RunMetaLine msg={msg} />
      <RunTrajectory runId={msg.agent_run_id} isRunning={isRunning} startedByWakeup={fromWakeup} />
      {msg.body && (
        <div className="ml-7 rounded border border-ink-800/80 bg-ink-900/50 p-3 text-[14px] text-ink-300 leading-relaxed whitespace-pre-wrap break-words">
          {msg.body}
        </div>
      )}
    </div>
  );
};

/**
 * Runs a wake-up started (harness 2b-2 §5-2). A fired schedule posts an
 * ordinary comment and the run it triggers is the very next row, so the
 * marker is read off the row IMMEDIATELY before — reaching further back
 * would credit a wake-up for a turn some later comment actually started.
 */
export function wakeupRunIds(messages: IssueMessage[]): Set<string> {
  const ids = new Set<string>();
  for (let i = 1; i < messages.length; i += 1) {
    const row = messages[i];
    if (row.kind !== 'agent_run' || !row.agent_run_id) continue;
    if (startedByWakeup(messages[i - 1])) ids.add(String(row.agent_run_id));
  }
  return ids;
}

/**
 * What this run did, step by step — the shared TrajectoryRenderer over the
 * run's transcript (harness P4 seam C). Steps do not stack: the live step is
 * the one expanded block, finished steps are one-line summaries. Sourced from
 * `agent_run_transcript_events` — an IssueMessage carries only `agent_run_id`.
 * Renders once per run; a capability denial keeps its own notice above.
 */
export const RunTrajectory: React.FC<{ runId: string | null; isRunning: boolean; startedByWakeup?: boolean }> = ({ runId, isRunning, startedByWakeup: fromWakeup }) => {
  const { t } = useTranslation();
  const { events, denials } = useRunToolActivity(runId, isRunning);
  // Replay (harness 2b-1 §1): the scrubber sits on the issue's NEWEST run
  // only; in the past the trajectory is events[:seq], frozen (no live step).
  const replay = useReplay();
  const attached = !!replay && !!runId && replay.runId === runId;
  const replaying = isReplaying(replay, runId);
  const ticks = useMemo(() => (attached ? replayTicks(events) : []), [attached, events]);
  const shown = useMemo(
    () => (replaying ? events.filter((e) => e.seq <= (replay?.seq as number)) : events),
    [replaying, events, replay?.seq],
  );
  // Fork points (harness 2b-1 §2): runs forked from this one, drawn on the
  // step they branched at.
  const forks = useRunForks(runId, isRunning);
  const forkMarks = useMemo(() => forkMarksFor(events, forks), [events, forks]);
  // The run's own facts (harness 2b-1 §2/§3): where it was forked from and
  // how many tools timed out — read from its events, so they stay on the row
  // after the run ends (the Cockpit's live view is gone by then).
  const origin = useMemo(() => forkOrigin(events), [events]);
  const timedOut = useMemo(() => timedOutTools(events), [events]);
  if (events.length === 0 && denials.length === 0) return null;
  return (
    <div className="ml-7 mb-1.5 space-y-1.5" data-testid="run-trajectory" data-replay-seq={replaying ? replay?.seq : undefined}>
      {(origin || timedOut.count > 0 || fromWakeup) && (
        <div className="flex flex-wrap items-center gap-1.5" data-testid="run-header-chips">
          {origin && (
            <button
              type="button"
              data-testid="run-fork-chip"
              onClick={() => replay?.seekRun(origin.ofRunId, origin.atSeq)}
              disabled={!replay}
              className="inline-flex items-center gap-1 rounded-full border border-info-line bg-info-soft/40 px-2 py-0.5 text-[11px] text-info hover:bg-info-soft disabled:cursor-default"
              title={t('fork.chipSeq', 'Forked from run #{{run}} @ seq {{seq}}', { run: origin.ofRunId, seq: origin.atSeq })}
            >
              <GitFork size={10} />
              {t('fork.chipSeq', 'Forked from run #{{run}} @ seq {{seq}}', { run: origin.ofRunId.slice(-6), seq: origin.atSeq })}
            </button>
          )}
          {fromWakeup && (
            <span
              data-testid="run-wakeup-chip"
              className="inline-flex items-center gap-1 rounded-full border border-info-line bg-info-soft/40 px-2 py-0.5 text-[11px] text-info"
            >
              <Clock size={10} />
              {t('schedule.startedBy', 'Started By Wake-up')}
            </span>
          )}
          {timedOut.count > 0 && (
            <span
              data-testid="run-timeout-chip"
              className="inline-flex items-center rounded-full border border-danger/40 bg-danger/10 px-2 py-0.5 text-[11px] text-danger"
              title={timedOut.last ?? undefined}
            >
              {t('issueDetail.toolsTimedOut', '{{count}} timed out', { count: timedOut.count })}
            </span>
          )}
        </div>
      )}
      {attached && replay && (
        <ReplayScrubber
          ticks={ticks}
          seq={replay.seq}
          isRunning={isRunning}
          onSeek={replay.seek}
          onFork={replay.fork}
          loading={replay.loading}
        />
      )}
      {denials.length > 0 && <CapabilityDeniedNotice denials={denials} interactive={false} />}
      <TrajectoryRenderer events={shown} isRunning={isRunning && !replaying} forkMarks={forkMarks} runId={runId} />
    </div>
  );
};

/**
 * A run that is being replayed but has no row in this thread — the ORIGIN of
 * a fork lives in the issue's previous conversation (harness 2b-1 §2). It is
 * drawn above the thread with its own scrubber and fork marks, so the fork
 * chip and a `?run=&seq=` link to it have somewhere to land.
 */
export const DetachedRunPanel: React.FC<{
  runId: string;
  /** Set when the panel is showing a SUB-RUN rather than a fork origin
   *  (harness 2b-2 §5-1) — the header then says where the child came from. */
  origin?: ChildRunOrigin;
  onBack?: () => void;
}> = ({ runId, origin, onBack }) => {
  const { t } = useTranslation();
  const ref = React.useRef<HTMLDivElement>(null);
  useEffect(() => {
    ref.current?.scrollIntoView?.({ behavior: 'smooth', block: 'center' });
  }, [runId]);
  return (
    <div
      ref={ref}
      id={`run-${runId}`}
      data-testid="detached-run-panel"
      data-variant={origin ? 'child' : 'origin'}
      className={`mx-4 mt-3 rounded-lg border p-2 ${origin ? 'border-agent-line bg-agent-soft/30' : 'border-info-line bg-info-soft/30'}`}
    >
      {origin ? (
        <div className="mb-1 flex items-center gap-2 text-[12px] text-agent">
          <span data-testid="child-run-header">
            {t('subagent.panelTitle', 'Sub-run #{{run}} · from run #{{parent}} step {{step}} · {{mode}}', {
              run: runId.slice(-6),
              parent: (origin.parentRunId ?? '').slice(-6) || '—',
              step: origin.step,
              mode: origin.mode === 'async' ? t('subagent.modeAsync', 'Background') : t('subagent.modeSync', 'Foreground'),
            })}
          </span>
          <button type="button" data-testid="child-run-back" onClick={onBack} className="ml-auto underline decoration-dotted">
            {t('subagent.backToParent', 'Back To Parent')}
          </button>
        </div>
      ) : (
        <div className="mb-1 text-[12px] text-info">
          {t('replay.originRun', 'Original run #{{run}} (from an earlier conversation of this issue)', { run: runId.slice(-6) })}
        </div>
      )}
      <RunTrajectory runId={runId} isRunning={false} />
    </div>
  );
};

/**
 * What the turn actually cost. `meta` has carried model / prompt_tokens /
 * completion_tokens / cost_cents since the bridge trigger (mig 206) but none
 * of it ever reached the screen. Missing fields are skipped rather than shown
 * as zeros — an unpriced run should read as "no data", not "free".
 */
const RunMetaLine: React.FC<{ msg: IssueMessage }> = ({ msg }) => {
  const model = msg.meta?.model as string | undefined;
  const tokens = Number(msg.meta?.prompt_tokens ?? 0) + Number(msg.meta?.completion_tokens ?? 0);
  const cents = Number(msg.meta?.cost_cents ?? 0);
  const parts: string[] = [];
  if (model) parts.push(model);
  if (Number.isFinite(tokens) && tokens > 0) parts.push(`${formatTokens(tokens)} tok`);
  if (Number.isFinite(cents) && cents > 0) parts.push(formatCentsAsUsd(cents));
  const attempt = Number(msg.meta?.continuation_attempt ?? 0);
  if (Number.isFinite(attempt) && attempt > 0) parts.push(`continuation ${attempt}`);
  if (parts.length === 0) return null;
  return (
    <div data-testid="agent-run-meta" className="ml-7 mb-1 text-[11px] text-ink-500 font-mono">
      {parts.join(' · ')}
    </div>
  );
};

/**
 * A folded stretch of consecutive agent runs. Collapsed by default: one line
 * with the turn count and the summed cost, so a five-turn dispatch reads as
 * one event instead of five walls of text. Expanding renders the original
 * rows (runs plus the status flips between them) unchanged.
 */
const RunGroupCard: React.FC<{
  entry: RunGroupEntry;
  agentsById: Record<string, AgentRef>;
  selfUserId?: string;
  /** Session-view deep link, or null when this issue has no session / no team. */
  conversationHref?: string | null;
  /** Run ids a wake-up started — computed once for the whole thread. */
  wakeupRuns?: Set<string>;
}> = ({ entry, agentsById, selfUserId, conversationHref, wakeupRuns }) => {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(false);
  // Replay (harness 2b-1 §1): the scrubber lives on the newest run's row —
  // a collapsed group would hide it (and the only way back to Live).
  const replay = useReplay();
  const holdsReplayRun = !!replay?.runId && entry.runs.some((r) => r.agent_run_id === replay.runId);
  useEffect(() => {
    if (holdsReplayRun) setExpanded(true);
  }, [holdsReplayRun]);
  const agentId = entry.runs[0]?.author_agent_id;
  const agent = agentId ? agentsById[agentId] : null;
  const summary = [
    `${entry.runs.length} turns`,
    entry.totals.durationSeconds > 0 ? formatDuration(entry.totals.durationSeconds) : null,
    entry.totals.tokens > 0 ? `${formatTokens(entry.totals.tokens)} tok` : null,
    entry.totals.costCents > 0 ? formatCentsAsUsd(entry.totals.costCents) : null,
  ].filter(Boolean).join(' · ');

  return (
    <div
      data-testid="run-group-card"
      className="my-3 rounded-lg border border-agent-line bg-agent-soft"
    >
      {/* Toggle and deep link are SIBLINGS, not nested: an anchor inside a
          button is invalid interactive nesting and swallows the link's click. */}
      <div className="flex items-center">
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          aria-expanded={expanded}
          data-testid="run-group-toggle"
          className="flex-1 min-w-0 flex items-center gap-2 px-3 py-2 text-left hover:bg-ink-900/30 rounded-lg transition-colors"
        >
          {expanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
          <AgentAvatar initials={(agent?.name ?? '·').slice(0, 2).toUpperCase()} color={agent?.avatar_color} size={18} />
          <span className="text-xs font-medium text-ink-200">{agent?.name ?? 'Agent'} run</span>
          <span className="text-[12px] text-ink-500 truncate">· {summary}</span>
          {entry.anyRunning && (
            <span className="inline-flex items-center gap-1 text-[11px] text-ok">
              <span className="w-1.5 h-1.5 rounded-full bg-ok animate-pulse" />
              running
            </span>
          )}
          <span className="ml-auto text-[12px] text-ink-500 shrink-0">{relativeTime(entry.startedAt)}</span>
        </button>
        {conversationHref && (
          <Link
            to={conversationHref}
            data-testid="run-group-open-conversation"
            className="shrink-0 pl-2 pr-3 py-2 text-[12px] text-agent hover:underline whitespace-nowrap"
          >
            {t('issueDetail.openConversation', 'Open conversation')} →
          </Link>
        )}
      </div>
      {expanded && (
        <div data-testid="run-group-body" className="px-3 pb-2">
          {entry.items.map((m) => (
            m.kind === 'agent_run'
              ? <AgentRunEvent key={m.id} msg={m} agentsById={agentsById} fromWakeup={!!m.agent_run_id && !!wakeupRuns?.has(String(m.agent_run_id))} />
              : <SystemStatusEvent key={m.id} msg={m} selfUserId={selfUserId} />
          ))}
        </div>
      )}
    </div>
  );
};

const CommentEvent: React.FC<{ msg: IssueMessage; agentsById: Record<string, AgentRef>; selfUserId?: string }> = ({ msg, agentsById, selfUserId }) => {
  const agent = msg.author_agent_id ? agentsById[msg.author_agent_id] : null;
  const isSelf = msg.author_user_id && msg.author_user_id === selfUserId;
  const displayName = agent?.name ?? (isSelf ? 'You' : msg.author_user_id ? `User ${msg.author_user_id.slice(0, 6)}` : 'Anonymous');
  const initials = displayName.slice(0, 2).toUpperCase();
  const color = agent?.avatar_color ?? (isSelf ? 'bg-indigo-500' : 'bg-ink-600');
  return (
    <div
      data-testid="comment-row"
      className={`flex my-2 ${isSelf ? 'justify-end' : 'justify-start'}`}
    >
      <div
        data-testid="comment-bubble"
        className={`max-w-[80%] rounded-lg px-3 py-2 ${
          isSelf
            ? 'bg-blue-600/15 border border-blue-700/40 text-ink-100'
            : 'bg-ink-800 border border-ink-700 text-ink-200'
        }`}
      >
        <div className="flex items-center gap-2 mb-1.5">
          <AgentAvatar initials={initials} color={color} />
          <span className="text-xs font-medium">{displayName}</span>
          <span className="text-[12px] text-ink-500">commented · {relativeTime(msg.created_at)}</span>
        </div>
        {msg.body && (
          <div className="rounded text-[14px] leading-relaxed whitespace-pre-wrap break-words">
            {msg.body}
          </div>
        )}
        <CitationChips msg={msg} />
      </div>
    </div>
  );
};

/**
 * The citations this comment was posted with (3a Task 8b).
 *
 * The composer has drawn these chips since Task 6, but the thread dropped
 * them: scrolling back, "tighten this one" pointed at nothing. Same component
 * as the composer's — one chip, one reading of `@<title> v<n>` — minus the X:
 * a sent comment is a record, not a draft.
 */
const CitationChips: React.FC<{ msg: IssueMessage }> = ({ msg }) => {
  const cites = (msg.attachments ?? []).filter((a) => a.kind === 'output_ref');
  if (cites.length === 0) return null;
  return (
    <div className="mt-1.5 flex flex-wrap gap-1" data-testid="comment-citations">
      {cites.map((c, i) => (
        <OutputChipBody
          // The index is part of the key on purpose: two citations of the
          // same version are one string otherwise, and React reuses state
          // between children that claim the same key.
          key={`${c.ref_kind}:${c.ref_id}:${c.version}:${i}`}
          refKind={c.ref_kind}
          refId={c.ref_id}
          version={c.version}
          title={c.title}
        />
      ))}
    </div>
  );
};

/**
 * Collapsed run of consecutive system_status events. Renders a single
 * summary row ("N status updates"); clicking expands the original rows
 * inline, clicking again collapses them. Reuses the same muted ink
 * tokens as the individual SystemStatusEvent row so it blends in.
 */
const SystemStatusGroup: React.FC<{ messages: IssueMessage[]; selfUserId?: string }> = ({ messages, selfUserId }) => {
  const [expanded, setExpanded] = useState(false);
  const count = messages.length;
  return (
    <div data-testid="system-status-group" className="my-2">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
        data-testid="system-status-group-toggle"
        className="mx-auto flex items-center gap-1 px-2 py-0.5 rounded text-[11px] text-ink-500 italic hover:text-ink-300 hover:bg-ink-900/40 transition-colors"
      >
        {expanded ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
        <span className="not-italic font-medium">{count}</span> status updates
      </button>
      {expanded && (
        <div data-testid="system-status-group-body">
          {messages.map((m) => (
            <SystemStatusEvent key={m.id} msg={m} selfUserId={selfUserId} />
          ))}
        </div>
      )}
    </div>
  );
};

/** Blinking text cursor shown while the agent is streaming. */
const StreamingCursor: React.FC = () => (
  <span
    className="inline-block w-[2px] h-[1em] bg-ink-300 ml-0.5 align-middle animate-pulse"
    aria-hidden="true"
  />
);

/** Transient assistant bubble rendered while tokens are arriving. */
const StreamingBubble: React.FC<{ text: string }> = ({ text }) => (
  <div className="my-3">
    <div className="flex items-center gap-2 mb-1.5">
      <AgentAvatar initials="AI" color="bg-indigo-600" />
      <span className="text-xs font-medium text-ink-200">Agent</span>
      <span className="text-[12px] text-ink-500 italic">streaming…</span>
    </div>
    <div className="ml-7 rounded border border-ink-800/80 bg-ink-900/50 p-3 text-[14px] text-ink-300 leading-relaxed whitespace-pre-wrap break-words">
      {text}
      <StreamingCursor />
    </div>
  </div>
);

type RenderRow =
  | RunGroupEntry
  | { kind: 'single'; key: string; message: IssueMessage }
  | { kind: 'status_group'; key: string; messages: IssueMessage[] };

/**
 * Two grouping passes, in this order: agent runs fold first (they may span
 * status events), then whatever is left between the run cards goes through
 * the existing status coalescing. Running them the other way round would let
 * a status group wall off two runs that belong together.
 */
function buildTimeline(messages: IssueMessage[]): RenderRow[] {
  const rows: RenderRow[] = [];
  let pending: IssueMessage[] = [];
  const flush = () => {
    if (pending.length === 0) return;
    for (const item of coalesceSystemStatus(pending)) {
      rows.push(item.type === 'status_group'
        ? { kind: 'status_group', key: item.key, messages: item.messages }
        : { kind: 'single', key: item.key, message: item.message });
    }
    pending = [];
  };
  for (const entry of groupAgentRuns(messages)) {
    if (entry.kind === 'single') {
      pending.push(entry.message);
      continue;
    }
    flush();
    rows.push(entry);
  }
  flush();
  return rows;
}

export const IssueChatThread: React.FC<IssueChatThreadProps> = ({ messages, agentsById, selfUserId, streamingText, teamId, aiSessionId }) => {
  const hasStreaming = typeof streamingText === 'string' && streamingText.length > 0;
  // A2: the folded run card is the timeline's handle on the agent's own
  // conversation — without both halves of the route there is nothing to link to.
  const conversationHref = teamId && aiSessionId
    ? `/team/${teamId}/ai-library/sessions/${aiSessionId}`
    : null;

  if (messages.length === 0 && !hasStreaming) {
    return (
      <div className="text-sm text-ink-500 italic px-4 py-12 text-center">
        No activity yet. Reply below to start the conversation or dispatch the issue to an agent.
      </div>
    );
  }
  const renderItems = buildTimeline(messages);
  // Read off the flat list, before any grouping: adjacency is the signal, and
  // grouping is exactly what destroys it.
  const wakeupRuns = wakeupRunIds(messages);
  return (
    <div className="px-4 py-3">
      {renderItems.map((item) => {
        if (item.kind === 'status_group') {
          return <SystemStatusGroup key={item.key} messages={item.messages} selfUserId={selfUserId} />;
        }
        if (item.kind === 'run_group') {
          return <RunGroupCard key={item.key} entry={item} agentsById={agentsById} selfUserId={selfUserId} conversationHref={conversationHref} wakeupRuns={wakeupRuns} />;
        }
        const m = item.message;
        if (m.kind === 'system_status') return <SystemStatusEvent key={item.key} msg={m} selfUserId={selfUserId} />;
        if (m.kind === 'agent_run')     return <AgentRunEvent key={item.key} msg={m} agentsById={agentsById} fromWakeup={!!m.agent_run_id && wakeupRuns.has(String(m.agent_run_id))} />;
        if (m.meta?.deliverable_upload) return <DeliverableFiledEvent key={item.key} msg={m} agentsById={agentsById} selfUserId={selfUserId} />;
        return <CommentEvent key={item.key} msg={m} agentsById={agentsById} selfUserId={selfUserId} />;
      })}
      {hasStreaming && <StreamingBubble text={streamingText as string} />}
    </div>
  );
};
