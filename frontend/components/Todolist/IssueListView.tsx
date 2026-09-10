/**
 * Paperclip-style flat issue list.
 *
 *  A8.3 — wired to real backend via UiIssue.
 *  A9.1 — column visibility picker (persisted per-team).
 *  A9.2 — filter popover (Quick filters + Status/Priority/Assignee/
 *         Creator/Project/Visibility multi-selects). Filtering is
 *         applied client-side against the already-fetched UiIssue list.
 */

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useParams } from 'react-router-dom';

import {
  Plus, Search, Columns, Filter, ArrowUpDown, RotateCw,
  Diamond, ChevronRight, ChevronDown, Check, X,
} from 'lucide-react';

/**
 * Paperclip-exact view-mode icons. lucide's LayoutList/LayoutGrid/Grid3x3
 * don't match paperclip's geometry — paperclip draws a "rows with
 * bullets" glyph for list and a clean 2x2 grid for board. These SVGs
 * are direct ports.
 */
const ListViewIcon: React.FC<{ size?: number }> = ({ size = 13 }) => (
  <svg width={size} height={size} viewBox="0 0 16 16" fill="currentColor" aria-hidden>
    <rect x="2" y="3.5" width="2.5" height="2.5" rx="0.6" />
    <rect x="6" y="3.75" width="8" height="2" rx="0.6" />
    <rect x="2" y="10" width="2.5" height="2.5" rx="0.6" />
    <rect x="6" y="10.25" width="8" height="2" rx="0.6" />
  </svg>
);

const BoardViewIcon: React.FC<{ size?: number }> = ({ size = 13 }) => (
  <svg width={size} height={size} viewBox="0 0 16 16" fill="currentColor" aria-hidden>
    <rect x="2" y="2" width="5.5" height="5.5" rx="0.8" />
    <rect x="8.5" y="2" width="5.5" height="5.5" rx="0.8" />
    <rect x="2" y="8.5" width="5.5" height="5.5" rx="0.8" />
    <rect x="8.5" y="8.5" width="5.5" height="5.5" rx="0.8" />
  </svg>
);
import type { UiIssue, AgentRef, ProjectRef } from './types';
import type { IssueStatus } from '../../services/issuesService';
import { IssueStatusIcon, STATUS_ORDER, STATUS_LABEL, PriorityIcon } from './IssueStatusIcon';
import { STATUS_CONFIG, PIPELINE_ORDER } from './issueConfig';
import { IssueBoardView } from './IssueBoardView';
import {
  IssueColumnPicker,
  loadVisibleColumns,
  saveVisibleColumns,
  type IssueColumnKey,
} from './IssueColumnPicker';
import {
  IssueFilterPopover,
  EMPTY_FILTERS,
  type IssueFilters,
  type QuickFilter,
} from './IssueFilterPopover';
import {
  IssueSortMenu,
  DEFAULT_SORT,
  compareIssues,
  SORT_LABEL_BY_KEY,
  type IssueSort,
} from './IssueSortMenu';
import { relativeTime } from '../../utils/taskDisplay';
import { originModule } from './issueOrigin';
import { runningChipLabel, needsReplyChip, queuedChip } from './issueChips';
import { buildAttentionItems } from './attentionItems';
import { PHASE_FALLBACK, PHASE_LABEL_KEY, PHASE_ORDER, PHASE_TONE, QUICK_PHASES, isIssueLive, issuePhase, type IssuePhase } from './issuePhase';
import {
  AttentionStrip,
  loadAttentionCollapsed,
  saveAttentionCollapsed,
} from './AttentionStrip';
import { useTaskManager } from '../../contexts/TaskManagerContext';
import { aiLibraryService } from '../../services/aiLibraryService';
import { fetchPendingSummary, type PendingSummaryEntry } from '../../services/agentInboxService';
import type { AILibraryApprovalRequest } from '../../types';
import {
  computeSubtaskCounts,
  dueBucket,
  readDueDate,
  type SubtaskCount,
} from './issueFlow';
import { SubtaskBar } from './SubtaskBar';
import {
  type IssueScope,
  scopeClientFilter,
  scopeCreatesInLabel,
  scopeIsReadOnly,
} from './issueScope';

export type IssueViewMode = 'list' | 'board';

/** Team-scope grouping mode — status pipeline order, or project ⊃ issue tree. */
export type IssueGroupMode = 'phase' | 'status' | 'project';

interface IssueListViewProps {
  /** Issue open in the split detail pane, if any — highlights its row. */
  selectedIssueId?: number | null;
  issues: UiIssue[];
  loading: boolean;
  error: string | null;
  viewMode: IssueViewMode;
  onViewModeChange: (mode: IssueViewMode) => void;
  onNewIssue: () => void;
  onRefresh: () => void;
  agents: AgentRef[];
  currentUserId?: string;
  /**
   * Which slice of issues this surface is showing. Every variant carries a
   * teamId (team is a hard boundary); `my`/`agent` narrow the already-fetched
   * team list client-side. Drives the "Creates in …" badge, the read-only lock
   * (agent scope), the Group-by toggle (team scope), and the project context
   * bar (project scope).
   */
  scope: IssueScope;
  /** Display names for the "Creates in …" badge (resolved by the caller). */
  teamName?: string;
  projectName?: string;
  /**
   * Current SOP stage for the project context bar (project scope only). Null /
   * undefined → no stage ring is drawn (graceful degradation).
   */
  projectStage?: { name: string; index: number; total: number } | null;
  /**
   * Create a project inline (team scope). When provided, the New Issue picker
   * and the Group-by-Project header expose a "+ New project" affordance.
   */
  onCreateProject?: (name: string) => Promise<{ id: string; name: string }>;
}

const AgentAvatar: React.FC<{ initials: string; color?: string; size?: number }> = ({ initials, color = 'bg-ink-600', size = 20 }) => (
  <span
    className={`inline-flex items-center justify-center rounded-full text-[10px] font-semibold text-ink-50 ${color}`}
    style={{ width: size, height: size }}
  >
    {initials}
  </span>
);

const Kbd: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <kbd className="font-mono text-[10px] leading-none px-1 py-0.5 rounded border border-ink-700 bg-ink-800/50 text-ink-400">
    {children}
  </kbd>
);

/** Circular stage-progress ring (current/total) for the project context bar. */
const StageRing: React.FC<{ current: number; total: number }> = ({ current, total }) => {
  const r = 9;
  const circ = 2 * Math.PI * r;
  const pct = total > 0 ? Math.min(1, Math.max(0, current / total)) : 0;
  return (
    <span
      className="relative inline-flex items-center justify-center shrink-0"
      style={{ width: 26, height: 26 }}
      title={`Stage ${current} of ${total}`}
      aria-label={`Stage ${current} of ${total}`}
    >
      <svg width={26} height={26} viewBox="0 0 26 26" className="-rotate-90">
        <circle cx="13" cy="13" r={r} fill="none" stroke="currentColor" strokeWidth="2.5" className="text-ink-800" />
        <circle
          cx="13" cy="13" r={r} fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"
          strokeDasharray={circ} strokeDashoffset={circ * (1 - pct)}
          style={{ color: 'var(--accent-text)' }}
        />
      </svg>
      <span className="absolute text-[9px] font-semibold tabular-nums text-ink-300">{current}</span>
    </span>
  );
};

const DUE_CLASS: Record<'normal' | 'soon' | 'overdue', string> = {
  normal: 'text-ink-500',
  soon: 'text-warn font-semibold',
  overdue: 'text-danger font-semibold',
};

/**
 * A `Date` that advances once a second while `active`, frozen otherwise.
 * Only live rows pay for a timer — a finished issue's elapsed time never
 * changes, so re-rendering it every second would be pure waste.
 */
function useTickingNow(active: boolean): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    if (!active) return;
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, [active]);
  return now;
}

interface IssueRowProps {
  issue: UiIssue;
  teamId: string;
  visibleCols: Set<IssueColumnKey>;
  parentLookup: Map<number, UiIssue>;
  /** Row highlighted as the one open in the split detail pane. */
  selected?: boolean;
  /** In Group-by-Project mode the project is the header, so the row pill is redundant. */
  hideProjectPill?: boolean;
  /** Sub-issue done/total for this row, or undefined when it has no children. */
  subtaskCount?: SubtaskCount;
  /** phase 2a §4: queued-comment count for this issue (inbox summary). */
  queued?: number;
}

const IssueRow: React.FC<IssueRowProps> = ({ issue, teamId, visibleCols, parentLookup, hideProjectPill, subtaskCount, selected = false, queued }) => {
  const moduleTag = originModule(issue.raw.origin_id, issue.raw.origin_kind);
  const initials = issue.assignee?.name.slice(0, 2).toUpperCase() ?? (issue.assignee_user_label?.slice(0, 2).toUpperCase() ?? '·');
  const parent = issue.parent_id ? parentLookup.get(issue.parent_id) : null;
  // Due date is pre-baked: the column lands with the workflow session, so this
  // is null (renders nothing) until then.
  const due = dueBucket(readDueDate(issue.raw));
  const { t } = useTranslation();
  // An agent is actively working this issue (see isIssueLive for the rule).
  // Surfaces as a pulse from data already on the row — no extra fetch. A1: the
  // chip also carries which turn it is on and how long it has been going —
  // "running" says an agent is assigned, a moving number says it is actually
  // getting somewhere.
  const isLive = isIssueLive(issue);
  const now = useTickingNow(isLive);
  const runningLabel = runningChipLabel(issue, now);
  const needsReply = needsReplyChip(issue);
  const phase = issuePhase(issue);
  // The one hover action that fits the phase: answer when it waits, steer when
  // it runs, resume when a person paused it. All land on the detail page — the
  // row only names the verb so the list reads as "what can I do here" instead
  // of "what is it".
  const rowAction =
    phase === 'waiting_input' ? t('issues.action.reply', 'Reply')
      : phase === 'running' ? t('issues.action.steer', 'Steer')
        : phase === 'paused' ? t('issues.action.resume', 'Resume')
          : null;
  const queuedLabel = queuedChip(queued, t);
  return (
    <Link
      to={`/team/${teamId}/todolist/${issue.identifier}`}
      data-phase={phase}
      aria-current={selected ? 'true' : undefined}
      className={`flex items-center gap-3 px-4 py-1.5 hover:bg-ink-800/30 transition group ${selected ? 'bg-ink-800/40 ring-1 ring-inset ring-[var(--accent-border)]' : ''}`}
    >
      {visibleCols.has('status') && <IssueStatusIcon status={issue.status} size={15} />}
      <span className="w-4 flex justify-center" title={issue.priority}>
        <PriorityIcon priority={issue.priority} />
      </span>
      {visibleCols.has('id') && (
        <span className="font-mono text-[12px] text-ink-500 w-16 shrink-0 uppercase tracking-wider">
          {issue.identifier}
        </span>
      )}
      <span className="flex-1 truncate text-[14px] text-ink-200 group-hover:text-ink-50">{issue.title}</span>
      {runningLabel && (
        <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[11px] text-agent bg-agent-soft shrink-0" title="An agent is working on this">
          <span className="w-1.5 h-1.5 rounded-full bg-agent animate-pulse" />
          {runningLabel}
        </span>
      )}
      {queuedLabel && (
        <span
          data-testid="queued-chip"
          className="inline-flex items-center px-1.5 py-0.5 rounded-full text-[11px] text-info bg-info-soft ring-1 ring-info-line shrink-0"
          title={t('issues.queuedTitle', 'Comments waiting for the agent to pick up')}
        >
          {queuedLabel}
        </span>
      )}
      {needsReply && (
        <span
          data-testid="needs-reply-chip"
          className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[11px] text-warn bg-warn-soft ring-1 ring-warn-line shrink-0"
          title={needsReply.question ?? undefined}
        >
          {t('issues.needsReplyChip', 'Needs your reply')}
        </span>
      )}
      {rowAction && (
        <span
          data-testid="row-action"
          className="hidden group-hover:inline-flex items-center px-1.5 py-0.5 rounded text-[11px] text-[var(--accent-text)] bg-[var(--accent-soft)] ring-1 ring-[var(--accent-border)] shrink-0"
        >
          {rowAction}
        </span>
      )}
      {visibleCols.has('parent') && parent && (
        <Link
          to={`/team/${teamId}/todolist/${parent.identifier}`}
          onClick={(e) => e.stopPropagation()}
          className="hidden lg:inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-ink-800/70 text-ink-400 text-[12px] hover:bg-ink-700"
          title={`Parent: ${parent.title}`}
        >
          ↳ {parent.identifier}
        </Link>
      )}
      {visibleCols.has('tags') && moduleTag && (
        <span
          className="hidden lg:inline-flex items-center gap-1.5 px-1.5 py-0.5 rounded-full bg-ink-800/60 ring-1 ring-ink-700 text-ink-400 text-[12px]"
          title={`Origin: ${moduleTag.label}`}
        >
          <span className={`w-1.5 h-1.5 rounded-full ${moduleTag.dotClass}`} />
          {moduleTag.label}
        </span>
      )}
      {visibleCols.has('project') && issue.project && !hideProjectPill && (
        <span
          className="hidden md:inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-ink-800 text-ink-400 text-[12px]"
          title={`Project: ${issue.project.name}`}
        >
          <span className={`w-1.5 h-1.5 rounded-full ${issue.project.color ?? 'bg-ink-500'}`} />
          {issue.project.name}
        </span>
      )}
      {visibleCols.has('subtasks') && subtaskCount && (
        <SubtaskBar count={subtaskCount} />
      )}
      {visibleCols.has('due') && due && (
        <span
          data-testid="issue-due"
          className={`hidden sm:inline text-[12px] tabular-nums whitespace-nowrap shrink-0 ${DUE_CLASS[due.kind]}`}
        >
          {due.label}
        </span>
      )}
      {visibleCols.has('assignee') && (issue.assignee || issue.assignee_user_label) && (
        <AgentAvatar
          initials={initials}
          color={issue.assignee?.avatar_color ?? 'bg-ink-600'}
        />
      )}
      {visibleCols.has('updated') && (
        <span className="text-[12px] text-ink-500 w-16 text-right shrink-0">
          {relativeTime(issue.last_activity_at)}
        </span>
      )}
    </Link>
  );
};

/** Has a one-shot wake-up still waiting to fire (harness 2b-2 §5-2). Absent
 *  on rows read from anything but the list endpoint, so a missing field means
 *  "not known here", which for this chip is the same as none. */
const hasWakeup = (issue: UiIssue): boolean => (issue.raw.pending_wakeups ?? 0) > 0;

function applyFilters(issues: UiIssue[], filters: IssueFilters, currentUserId: string | undefined): UiIssue[] {
  const assigneeActive = filters.assigneeMe || filters.assigneeNone || filters.assigneeAgents.size > 0;
  const creatorActive = filters.creatorMe || filters.creatorsAgents.size > 0;

  return issues.filter((i) => {
    if (filters.statuses.size > 0 && !filters.statuses.has(i.status)) return false;
    if (filters.priorities.size > 0 && !filters.priorities.has(i.priority)) return false;
    if (filters.projects.size > 0) {
      if (i.raw.project_id == null || !filters.projects.has(i.raw.project_id)) return false;
    }
    if (assigneeActive) {
      let match = false;
      if (filters.assigneeMe && currentUserId && i.raw.assignee_user_id === currentUserId) match = true;
      if (!match && filters.assigneeNone && !i.raw.assignee_user_id && !i.raw.assignee_agent_id) match = true;
      if (!match && i.raw.assignee_agent_id && filters.assigneeAgents.has(i.raw.assignee_agent_id)) match = true;
      if (!match) return false;
    }
    if (creatorActive) {
      let match = false;
      if (filters.creatorMe && currentUserId && i.raw.created_by_user_id === currentUserId) match = true;
      if (!match && i.raw.created_by_agent_id && filters.creatorsAgents.has(i.raw.created_by_agent_id)) match = true;
      if (!match) return false;
    }
    if (filters.liveRunsOnly && !isIssueLive(i)) return false;
    if (filters.hideRoutine && i.raw.origin_kind === 'routine') return false;
    return true;
  });
}

function activeFilterCount(filters: IssueFilters): number {
  let n = 0;
  if (filters.statuses.size > 0) n += 1;
  if (filters.priorities.size > 0) n += 1;
  if (filters.assigneeMe || filters.assigneeNone || filters.assigneeAgents.size > 0) n += 1;
  if (filters.creatorMe || filters.creatorsAgents.size > 0) n += 1;
  if (filters.projects.size > 0) n += 1;
  if (filters.liveRunsOnly) n += 1;
  if (filters.hideRoutine) n += 1;
  return n;
}

/**
 * Workflow pipeline — the signature element. Status capsules chained in flow
 * order (Backlog → Todo → In Progress → In Review → Done) with connectors
 * that read as the direction issues move; Blocked hangs off the side because
 * it's an incident, not a flow stop. Counts are always global (the pipeline
 * is the map, the list is the filtered territory); clicking a capsule filters
 * to that status.
 */
const IssuePipeline: React.FC<{
  issues: UiIssue[];
  activeStatus: IssueStatus | null;
  onPick: (s: IssueStatus) => void;
}> = ({ issues, activeStatus, onPick }) => {
  const countOf = (s: IssueStatus) => issues.reduce((n, i) => (i.status === s ? n + 1 : n), 0);
  const capsule = (s: IssueStatus, blocked = false) => {
    const n = countOf(s);
    const on = activeStatus === s;
    const base = on
      ? 'border-[var(--accent-border)] bg-[var(--accent-soft)] text-[var(--accent-text)]'
      : blocked
        // Blocked hangs off the chain as an incident — it must read as such on
        // white too, where the old /5 tint vanished entirely.
        ? 'border-rose-500/40 bg-rose-500/10 text-ink-200 hover:border-rose-500/60 hover:text-ink-50'
        : 'border-line-strong bg-island text-ink-300 hover:border-[var(--accent-border)] hover:text-ink-100';
    return (
      <button
        key={s}
        type="button"
        onClick={() => onPick(s)}
        title={`${STATUS_CONFIG[s].label} · ${n}`}
        className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full border transition shrink-0 ${base} ${n === 0 ? 'opacity-45' : ''}`}
      >
        <IssueStatusIcon status={s} size={12} />
        <span className="hidden xl:inline">{STATUS_CONFIG[s].label}</span>
        <span className="text-ink-500 tabular-nums">{n}</span>
      </button>
    );
  };
  // Sits right next to the Quick chips: pushing it to the far edge (justify-end)
  // read fine at the mockup's 1280px but strands it across a void on a wide
  // screen. It stays a compact chain that scrolls if the viewport is narrow.
  return (
    <div className="flex items-center min-w-0 overflow-x-auto">
      {PIPELINE_ORDER.map((s, i) => (
        <React.Fragment key={s}>
          {capsule(s)}
          {i < PIPELINE_ORDER.length - 1 && <span className="w-3.5 h-px bg-line-strong shrink-0" />}
        </React.Fragment>
      ))}
      <span className="w-3 shrink-0" />
      {capsule('blocked', true)}
    </div>
  );
};

export const IssueListView: React.FC<IssueListViewProps> = ({ issues, loading, error, viewMode, onViewModeChange, onNewIssue, onRefresh, agents, currentUserId, scope, teamName, projectName, projectStage, onCreateProject, selectedIssueId = null }) => {
  const { t } = useTranslation();
  const { teamId } = useParams<{ teamId: string }>();
  // Sub-issue done/total per parent, aggregated from the FULL unfiltered list so
  // display filters never undercount a parent's children (see issueFlow.ts).
  const subtaskCounts = useMemo(() => computeSubtaskCounts(issues), [issues]);
  const [search, setSearch] = useState('');
  const [filters, setFilters] = useState<IssueFilters>(EMPTY_FILTERS);
  const [columnPickerOpen, setColumnPickerOpen] = useState(false);
  const [filterOpen, setFilterOpen] = useState(false);
  const [sortOpen, setSortOpen] = useState(false);
  const [sort, setSort] = useState<IssueSort>(DEFAULT_SORT);
  // Phase first (harness P4): the list answers "what needs me / what is
  // moving" before "what column is it in". Status / Project stay selectable.
  const [groupMode, setGroupMode] = useState<IssueGroupMode>('phase');
  const [phaseFilter, setPhaseFilter] = useState<IssuePhase | null>(null);
  const [scheduledOnly, setScheduledOnly] = useState(false);
  const [collapsedProjects, setCollapsedProjects] = useState<Set<string>>(new Set());
  const [groupNewProjectOpen, setGroupNewProjectOpen] = useState(false);
  const [groupNewProjectName, setGroupNewProjectName] = useState('');
  const [groupProjectBusy, setGroupProjectBusy] = useState(false);
  const searchRef = useRef<HTMLInputElement>(null);

  const readOnly = scopeIsReadOnly(scope);
  const showProjectGroup = scope.type === 'team';
  const projectGrouped = showProjectGroup && groupMode === 'project';
  const phaseGrouped = groupMode === 'phase';

  // ── A1「等我的」横条 ────────────────────────────────────────────────
  // Questions come from the TaskManager feed (already polled app-wide);
  // approvals are fetched here on the same 60s cadence as the TopBar panel;
  // in_review issues are already in `scopedIssues`, no fetch at all.
  const { needsInputItems } = useTaskManager();
  const [approvals, setApprovals] = useState<AILibraryApprovalRequest[]>([]);
  // Phase 2a §4: queued-comment counts per issue, one grouped query on the
  // same 60s cadence as approvals. Feeds the row chip, the board card and the
  // "queued" attention card for paused issues.
  const [pendingSummary, setPendingSummary] = useState<Record<string, PendingSummaryEntry>>({});
  const attentionScopeKey = `${scope.type}:${teamId ?? 'none'}`;
  const [attentionCollapsed, setAttentionCollapsed] = useState(() =>
    loadAttentionCollapsed(attentionScopeKey));

  const refreshApprovals = useCallback(async () => {
    try {
      const res = await aiLibraryService.listApprovalRequests();
      setApprovals(res.items ?? []);
    } catch (err) {
      // Non-fatal: the other two attention sources still render.
      console.error('[IssueListView] approval requests load failed', err);
    }
    try {
      setPendingSummary(await fetchPendingSummary());
    } catch (err) {
      // Non-fatal: rows render without the queued chip.
      console.error('[IssueListView] pending summary load failed', err);
    }
  }, []);

  useEffect(() => {
    void refreshApprovals();
    const id = setInterval(() => { void refreshApprovals(); }, 60_000);
    return () => clearInterval(id);
  }, [refreshApprovals]);

  // Keyboard: `C` opens New Issue, `/` focuses search. Guarded against typing
  // contexts (inputs/textarea/contenteditable), IME composition, modifier
  // combos, and already-handled events so it never hijacks real input.
  // Read-only (agent) scope suppresses `C` — there is no New Issue there.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || e.metaKey || e.ctrlKey || e.altKey || e.isComposing) return;
      const el = document.activeElement as HTMLElement | null;
      const typing = !!el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.isContentEditable);
      if (typing) return;
      if (e.key === '/') {
        e.preventDefault();
        searchRef.current?.focus();
      } else if ((e.key === 'c' || e.key === 'C') && !readOnly) {
        e.preventDefault();
        onNewIssue();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onNewIssue, readOnly]);

  const colScopeKey = teamId ?? 'global';
  const [visibleCols, setVisibleCols] = useState<Set<IssueColumnKey>>(() => loadVisibleColumns(colScopeKey));

  useEffect(() => {
    saveVisibleColumns(colScopeKey, visibleCols);
  }, [colScopeKey, visibleCols]);

  useEffect(() => {
    setVisibleCols(loadVisibleColumns(colScopeKey));
  }, [colScopeKey]);

  const setQuick = (q: QuickFilter) => {
    if (q === 'all') {
      setFilters((prev) => ({ ...prev, statuses: new Set() }));
    } else {
      // 'active' — the one multi-status preset the pipeline chips can't
      // express in a single click (per-status filtering is theirs).
      setFilters((prev) => ({ ...prev, statuses: new Set(['todo', 'in_progress', 'in_review']) }));
    }
  };

  // Scope projection first: `my`/`agent` narrow the fetched team list to the
  // rows this surface owns before any panel filter or search runs. `team`/
  // `project` pass through (the backend already scoped them). The pipeline
  // counts, project list, and issue count all read from this scoped universe.
  const scopedIssues = useMemo(
    () => issues.filter(scopeClientFilter(scope)),
    [issues, scope],
  );

  // Derive Project list from currently-loaded issues so filter popover
  // doesn't depend on a separate /projects fetch we don't have.
  const projectsList: ProjectRef[] = useMemo(() => {
    const seen = new Map<number, ProjectRef>();
    for (const i of scopedIssues) {
      if (i.project && !seen.has(i.project.id)) seen.set(i.project.id, i.project);
    }
    return Array.from(seen.values());
  }, [scopedIssues]);

  const filteredByPanel = useMemo(() => applyFilters(scopedIssues, filters, currentUserId), [scopedIssues, filters, currentUserId]);

  // Attention items are built off the SCOPED list (not the filtered one): a
  // display filter hides rows, it does not mean the work stopped waiting.
  const attentionItems = useMemo(
    () => buildAttentionItems(
      needsInputItems,
      approvals,
      scopedIssues.filter((i) => i.status === 'in_review'),
      scopedIssues.filter((i) => issuePhase(i) === 'paused'),
      pendingSummary,
      t,
    ),
    [needsInputItems, approvals, scopedIssues, pendingSummary, t],
  );

  // The needs-input feed carries no identifier, and the detail route is keyed
  // by identifier — so resolve through the list we already have, and degrade
  // to a non-clickable card when the issue isn't in this scope.
  const issueLinkFor = useCallback((issueId: number): string | null => {
    const match = issues.find((i) => i.id === issueId);
    return match && teamId ? `/team/${teamId}/todolist/${match.identifier}` : null;
  }, [issues, teamId]);

  const handleAttentionToggle = useCallback((next: boolean) => {
    setAttentionCollapsed(next);
    saveAttentionCollapsed(attentionScopeKey, next);
  }, [attentionScopeKey]);

  const handleApprove = useCallback(async (id: string) => {
    try {
      await aiLibraryService.approveRequest(id);
    } catch (err) {
      console.error('[IssueListView] approve failed', err);
    }
    await refreshApprovals();
  }, [refreshApprovals]);

  const handleReject = useCallback(async (id: string) => {
    try {
      await aiLibraryService.rejectRequest(id);
    } catch (err) {
      console.error('[IssueListView] reject failed', err);
    }
    await refreshApprovals();
  }, [refreshApprovals]);

  const filtered = useMemo(() => {
    const q = search.toLowerCase().trim();
    const byPhase = phaseFilter ? filteredByPanel.filter((i) => issuePhase(i) === phaseFilter) : filteredByPanel;
    const byWakeup = scheduledOnly ? byPhase.filter(hasWakeup) : byPhase;
    if (!q) return byWakeup;
    return byWakeup.filter((i) => {
      const hay = `${i.identifier} ${i.title} ${i.description ?? ''} ${i.assignee?.name ?? i.assignee_user_label ?? ''}`.toLowerCase();
      return hay.includes(q);
    });
  }, [filteredByPanel, search, phaseFilter, scheduledOnly]);

  // harness 2b-2 §5-2: how many issues have a wake-up waiting to fire. Same
  // rule as the phase counts — off the scoped list, not the filtered one.
  const scheduledCount = useMemo(() => scopedIssues.filter(hasWakeup).length, [scopedIssues]);

  // Phase counts for the Quick chips — off the scoped list, so a chip's count
  // never shrinks because of the filter it is about to apply.
  const phaseCounts = useMemo(() => {
    const counts: Record<IssuePhase, number> = { waiting_input: 0, running: 0, paused: 0, blocked: 0, idle: 0, done: 0 };
    for (const i of scopedIssues) counts[issuePhase(i)] += 1;
    return counts;
  }, [scopedIssues]);

  // Group: Phase — the default. Same priority as the server rollup.
  const phaseGroups = useMemo(() => {
    const map = new Map<IssuePhase, UiIssue[]>();
    for (const i of filtered) {
      const ph = issuePhase(i);
      if (!map.has(ph)) map.set(ph, []);
      map.get(ph)!.push(i);
    }
    return PHASE_ORDER.filter((ph) => map.has(ph)).map((ph) => ({
      phase: ph,
      items: map.get(ph)!.slice().sort((a, b) => compareIssues(a, b, sort)),
    }));
  }, [filtered, sort]);

  // For sort keys that are NOT status-related, keep the status grouping
  // but sort within each group. For 'workflow'/'status' keys, the
  // grouping order itself encodes the sort, so we just need direction.
  const grouped = useMemo(() => {
    const map = new Map<IssueStatus, UiIssue[]>();
    for (const i of filtered) {
      if (!map.has(i.status)) map.set(i.status, []);
      map.get(i.status)!.push(i);
    }
    let orderedStatuses = STATUS_ORDER.filter((s) => map.has(s));
    if (sort.key === 'workflow' || sort.key === 'status') {
      if (sort.dir === 'desc') orderedStatuses = orderedStatuses.slice().reverse();
    }
    return orderedStatuses.map((s) => {
      const items = map.get(s)!.slice().sort((a, b) => compareIssues(a, b, sort));
      return { status: s, items };
    });
  }, [filtered, sort]);

  // Flat-sorted list for the Board view (no grouping there).
  const flatSorted = useMemo(
    () => filtered.slice().sort((a, b) => compareIssues(a, b, sort)),
    [filtered, sort],
  );

  const parentLookup = useMemo(() => {
    const m = new Map<number, UiIssue>();
    for (const i of issues) m.set(i.id, i);
    return m;
  }, [issues]);

  // Project ⊃ issue tree (team scope, Group: Project). Every project that owns a
  // visible issue becomes a group; rows with no project fall into "No project".
  // Sorted within a group by the active sort; groups by name (No project last).
  const projectGroups = useMemo(() => {
    const NONE = '__none__';
    const map = new Map<string, { key: string; project?: ProjectRef; items: UiIssue[] }>();
    for (const i of filtered) {
      const key = i.project ? String(i.project.id) : NONE;
      if (!map.has(key)) map.set(key, { key, project: i.project, items: [] });
      map.get(key)!.items.push(i);
    }
    const groups = Array.from(map.values());
    groups.forEach((g) => g.items.sort((a, b) => compareIssues(a, b, sort)));
    groups.sort((a, b) => {
      if (a.key === NONE) return 1;
      if (b.key === NONE) return -1;
      return (a.project?.name ?? '').localeCompare(b.project?.name ?? '');
    });
    return groups;
  }, [filtered, sort]);

  // Live (agent-running) count for the project context bar.
  const liveCount = useMemo(
    () => filtered.reduce((n, i) => (isIssueLive(i) ? n + 1 : n), 0),
    [filtered],
  );

  const createsInLabel = scopeCreatesInLabel(scope, { teamName, projectName });

  const filterCount = activeFilterCount(filters);

  const toggleProjectCollapse = (key: string) =>
    setCollapsedProjects((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  const commitGroupNewProject = async () => {
    const name = groupNewProjectName.trim();
    if (!name || groupProjectBusy || !onCreateProject) return;
    setGroupProjectBusy(true);
    try {
      await onCreateProject(name);
      setGroupNewProjectOpen(false);
      setGroupNewProjectName('');
    } catch {
      // Parent surfaces the error via toast; keep the input open to retry.
    } finally {
      setGroupProjectBusy(false);
    }
  };

  // Pipeline capsule ↔ status filter: a single-status filter lights its
  // capsule; clicking toggles that status as the sole filter.
  const pipelineActive: IssueStatus | null =
    filters.statuses.size === 1 ? Array.from(filters.statuses)[0] : null;
  const pickStatus = (s: IssueStatus) =>
    setFilters((prev) => {
      const only = prev.statuses.size === 1 && prev.statuses.has(s);
      return { ...prev, statuses: only ? new Set<IssueStatus>() : new Set<IssueStatus>([s]) };
    });

  return (
    <div className={`flex flex-col bg-ink-950 h-full min-h-0`}>
      <div className="flex items-center gap-2 px-4 py-3 sticky top-0 z-10 bg-ink-950">
        {!readOnly && (
          <button
            type="button"
            onClick={onNewIssue}
            className="inline-flex items-center gap-1.5 px-2.5 py-1.5 text-[13px] rounded border transition"
            style={{ background: 'var(--accent-soft)', color: 'var(--accent-text)', borderColor: 'var(--accent-border)' }}
          >
            <Plus size={13} /> New Issue
            <Kbd>C</Kbd>
          </button>
        )}
        <div className="relative flex-1 max-w-md">
          <Search size={13} className="absolute left-2 top-1/2 -translate-y-1/2 text-ink-500" />
          <input
            ref={searchRef}
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search issues…"
            className="w-full pl-7 pr-8 py-1.5 text-[13px] bg-ink-900/80 border border-ink-800 rounded focus:outline-none focus:ring-1 focus:ring-indigo-500/40 text-ink-200 placeholder-ink-600"
          />
          <span className="absolute right-2 top-1/2 -translate-y-1/2 pointer-events-none">
            <Kbd>/</Kbd>
          </span>
        </div>
        <span
          data-testid="creates-in-badge"
          className={`hidden sm:inline-flex items-center gap-1.5 px-2 py-1 text-[12px] rounded shrink-0 ${
            readOnly
              ? 'bg-warn-soft text-warn ring-1 ring-warn-line'
              : 'bg-ink-900/80 text-ink-400 ring-1 ring-ink-800'
          }`}
          title={readOnly ? 'This is a filtered, read-only view' : 'Where a new issue will be created'}
        >
          {!readOnly && <Diamond size={11} className="text-ink-500" />}
          {createsInLabel}
        </span>
        <div className="ml-auto flex items-center gap-1">
          <div className="inline-flex rounded border border-ink-800 bg-ink-900/80 overflow-hidden">
            <button
              type="button"
              onClick={() => onViewModeChange('list')}
              className={`p-1.5 transition ${
                viewMode === 'list'
                  ? 'bg-indigo-600 text-white'
                  : 'text-ink-400 hover:text-ink-200'
              }`}
              title="List view"
            >
              <ListViewIcon />
            </button>
            <button
              type="button"
              onClick={() => onViewModeChange('board')}
              className={`p-1.5 transition border-l border-ink-800 ${
                viewMode === 'board'
                  ? 'bg-indigo-600 text-white'
                  : 'text-ink-400 hover:text-ink-200'
              }`}
              title="Board view"
            >
              <BoardViewIcon />
            </button>
          </div>
          <div className="relative">
            <button
              type="button"
              onClick={() => { setFilterOpen((v) => !v); setColumnPickerOpen(false); setSortOpen(false); }}
              className={`p-1.5 rounded border transition inline-flex items-center gap-1 ${
                filterOpen || filterCount > 0
                  // Theme-flipping accent — a hard-coded indigo-200 here read as
                  // pale-on-pale in light theme (same defect the btn-tint fix cured).
                  ? 'bg-[var(--accent-soft)] border-[var(--accent-border)] text-[var(--accent-text)]'
                  : 'bg-ink-900/80 border-ink-800 text-ink-400 hover:text-ink-200'
              }`}
              title="Filters"
            >
              <Filter size={13} />
              {filterCount > 0 && <span className="text-[11px] font-medium">{filterCount}</span>}
            </button>
            {filterOpen && (
              <IssueFilterPopover
                filters={filters}
                onChange={setFilters}
                onClose={() => setFilterOpen(false)}
                agents={agents}
                projects={projectsList}
              />
            )}
          </div>
          <div className="relative">
            <button
              type="button"
              onClick={() => { setSortOpen((v) => !v); setFilterOpen(false); setColumnPickerOpen(false); }}
              className={`p-1.5 rounded border transition inline-flex items-center gap-1 ${
                sortOpen
                  // Same theme-flipping accent as the Filters button above —
                  // half-migrated leftover, matched to that idiom.
                  ? 'bg-[var(--accent-soft)] border-[var(--accent-border)] text-[var(--accent-text)]'
                  : 'bg-ink-900/80 border-ink-800 text-ink-400 hover:text-ink-200'
              }`}
              title={`Sort: ${SORT_LABEL_BY_KEY[sort.key]} ${sort.dir === 'asc' ? '↑' : '↓'}`}
            >
              <ArrowUpDown size={13} />
            </button>
            {sortOpen && (
              <IssueSortMenu
                sort={sort}
                onChange={(s) => setSort(s)}
                onClose={() => setSortOpen(false)}
              />
            )}
          </div>
          <div className="relative">
            <button
              type="button"
              onClick={() => { setColumnPickerOpen((v) => !v); setFilterOpen(false); setSortOpen(false); }}
              className={`p-1.5 rounded border transition ${
                columnPickerOpen
                  // Same theme-flipping accent as the Filters button above —
                  // half-migrated leftover, matched to that idiom.
                  ? 'bg-[var(--accent-soft)] border-[var(--accent-border)] text-[var(--accent-text)]'
                  : 'bg-ink-900/80 border-ink-800 text-ink-400 hover:text-ink-200'
              }`}
              title="Column visibility"
            >
              <Columns size={13} />
            </button>
            {columnPickerOpen && (
              <IssueColumnPicker
                visible={visibleCols}
                onChange={setVisibleCols}
                onClose={() => setColumnPickerOpen(false)}
              />
            )}
          </div>
          <button
            type="button"
            onClick={onRefresh}
            disabled={loading}
            className="p-1.5 rounded border border-ink-800 bg-ink-900/80 text-ink-400 hover:text-ink-200 disabled:opacity-50"
            title="Refresh"
          >
            <RotateCw size={13} className={loading ? 'animate-spin' : ''} />
          </button>
          <span className="text-[12px] text-ink-500 pl-2 pr-1">
            {filtered.length} issue{filtered.length === 1 ? '' : 's'}
          </span>
        </div>
      </div>

      <div className="flex items-center gap-2 px-4 py-1.5 text-[12px] border-b border-line">
        <div className="flex items-center gap-1.5 shrink-0">
          <span className="text-ink-600">Quick:</span>
          {(['all', 'active'] as QuickFilter[]).map((q) => {
            const matches = q === 'all'
              ? filters.statuses.size === 0
              : filters.statuses.size === 3
                && filters.statuses.has('todo')
                && filters.statuses.has('in_progress')
                && filters.statuses.has('in_review');
            return (
              <button
                key={q}
                type="button"
                onClick={() => { setPhaseFilter(null); setQuick(q); }}
                className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full transition ${
                  matches && !phaseFilter
                    ? 'bg-[var(--accent-soft)] text-[var(--accent-text)] ring-1 ring-[var(--accent-border)]'
                    : 'text-ink-400 hover:text-ink-200 hover:bg-ink-800'
                }`}
              >
                {q === 'all' ? 'All' : 'Active'}
              </button>
            );
          })}
          {/* Phase chips (harness P4): the four a person acts on. Count off the
              scoped list; click toggles the phase as the sole filter. */}
          {QUICK_PHASES.map((ph) => {
            const count = phaseCounts[ph];
            const active = phaseFilter === ph;
            return (
              <button
                key={ph}
                type="button"
                data-testid={`quick-phase-${ph}`}
                onClick={() => setPhaseFilter(active ? null : ph)}
                disabled={count === 0 && !active}
                className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full transition disabled:opacity-40 ${
                  active
                    ? `ring-1 ${PHASE_TONE[ph]}`
                    : 'text-ink-400 hover:text-ink-200 hover:bg-ink-800'
                }`}
              >
                {t(PHASE_LABEL_KEY[ph], PHASE_FALLBACK[ph])}
                <span className="tabular-nums text-ink-500">{count}</span>
              </button>
            );
          })}
          {/* Not a phase — an issue can be idle AND have a wake-up armed — so
              it sits beside the phase chips rather than among them. */}
          <button
            type="button"
            data-testid="quick-scheduled"
            onClick={() => setScheduledOnly((v) => !v)}
            disabled={scheduledCount === 0 && !scheduledOnly}
            className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full transition disabled:opacity-40 ${
              scheduledOnly ? `ring-1 ${PHASE_TONE.paused}` : 'text-ink-400 hover:text-ink-200 hover:bg-ink-800'
            }`}
          >
            {t('issues.quick.scheduled', 'Scheduled')}
            <span className="tabular-nums text-ink-500">{scheduledCount}</span>
          </button>
        </div>
        <IssuePipeline issues={scopedIssues} activeStatus={pipelineActive} onPick={pickStatus} />
        <div className="ml-auto flex items-center gap-2 shrink-0">
          <div className="inline-flex items-center gap-1" title="Group issues by phase, status or project" data-testid="group-toggle">
            <span className="text-ink-600">Group:</span>
            <div className="inline-flex rounded border border-ink-800 bg-ink-900/80 overflow-hidden">
              {(showProjectGroup ? (['phase', 'status', 'project'] as IssueGroupMode[]) : (['phase', 'status'] as IssueGroupMode[])).map((m) => (
                <button
                  key={m}
                  type="button"
                  onClick={() => setGroupMode(m)}
                  className={`px-2 py-0.5 transition ${
                    groupMode === m
                      ? 'bg-[var(--accent-soft)] text-[var(--accent-text)]'
                      : 'text-ink-400 hover:text-ink-200'
                  }`}
                >
                  {m === 'phase' ? 'Phase' : m === 'status' ? 'Status' : 'Project'}
                </button>
              ))}
            </div>
          </div>
          {filterCount > 0 && (
            <button
              type="button"
              onClick={() => setFilters(EMPTY_FILTERS)}
              className="text-ink-500 hover:text-ink-200"
            >
              Reset ({filterCount})
            </button>
          )}
        </div>
      </div>

      <AttentionStrip
        items={attentionItems}
        collapsed={attentionCollapsed}
        onToggle={handleAttentionToggle}
        onApprove={handleApprove}
        onReject={handleReject}
        teamId={teamId}
        issueLinkFor={issueLinkFor}
      />

      {scope.type === 'project' && (
        <div
          data-testid="project-context-bar"
          className="flex items-center gap-3 px-4 py-2 border-b border-line bg-ink-900/40"
        >
          {projectStage && projectStage.total > 0 && (
            <StageRing current={projectStage.index} total={projectStage.total} />
          )}
          <div className="min-w-0 flex items-baseline gap-2">
            <span className="text-[13px] font-semibold text-ink-100 truncate">{projectName ?? 'Project'}</span>
            <span className="text-[12px] text-ink-500 truncate">
              {projectStage ? `${projectStage.name} · ` : ''}
              {filtered.length} issue{filtered.length === 1 ? '' : 's'}
              {liveCount > 0 && ` · ${liveCount} running`}
            </span>
          </div>
        </div>
      )}

      <div className="flex-1 overflow-y-auto">
        {error && (
          <div className="px-4 py-3 mx-4 my-3 rounded bg-rose-500/10 text-rose-300 text-xs ring-1 ring-rose-500/30">
            {error}
          </div>
        )}
        {loading && issues.length === 0 ? (
          <div className="flex items-center justify-center py-24 text-sm text-ink-500">
            Loading issues…
          </div>
        ) : viewMode === 'board' ? (
          <IssueBoardView issues={flatSorted} pendingSummary={pendingSummary} />
        ) : projectGrouped ? (
          <div>
            {onCreateProject && (
              <div className="flex items-center gap-2 px-4 pt-3 pb-1">
                {groupNewProjectOpen ? (
                  <div className="flex items-center gap-2 flex-1 max-w-sm">
                    <input
                      type="text"
                      autoFocus
                      value={groupNewProjectName}
                      onChange={(e) => setGroupNewProjectName(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter' && !e.nativeEvent.isComposing) {
                          e.preventDefault();
                          void commitGroupNewProject();
                        } else if (e.key === 'Escape') {
                          e.preventDefault();
                          setGroupNewProjectOpen(false);
                          setGroupNewProjectName('');
                        }
                      }}
                      placeholder="New project name…"
                      className="flex-1 h-7 px-2 text-[12px] bg-ink-900 border border-ink-800 rounded focus:outline-none focus:ring-1 focus:ring-indigo-500/40 text-ink-200 placeholder-ink-600"
                    />
                    <button
                      type="button"
                      onClick={() => void commitGroupNewProject()}
                      disabled={!groupNewProjectName.trim() || groupProjectBusy}
                      className="inline-flex items-center gap-1 px-2 h-7 text-[12px] rounded bg-indigo-500 text-white hover:bg-indigo-600 disabled:opacity-40"
                    >
                      <Check size={11} /> {groupProjectBusy ? 'Creating…' : 'Add'}
                    </button>
                    <button
                      type="button"
                      onClick={() => { setGroupNewProjectOpen(false); setGroupNewProjectName(''); }}
                      className="p-1 text-ink-500 hover:text-ink-300 rounded hover:bg-ink-800"
                      title="Cancel"
                    >
                      <X size={12} />
                    </button>
                  </div>
                ) : (
                  <button
                    type="button"
                    onClick={() => setGroupNewProjectOpen(true)}
                    className="inline-flex items-center gap-1 px-2 py-0.5 text-[12px] rounded text-ink-400 hover:text-ink-100 hover:bg-ink-800 ring-1 ring-ink-800"
                  >
                    <Plus size={12} /> New project
                  </button>
                )}
              </div>
            )}
            {projectGroups.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-24 gap-2 text-center">
                <span className="text-[13px] text-ink-300">No issues match your filters</span>
              </div>
            ) : (
              projectGroups.map((g) => {
                const collapsed = collapsedProjects.has(g.key);
                return (
                  <div key={g.key} data-testid="project-group">
                    <button
                      type="button"
                      onClick={() => toggleProjectCollapse(g.key)}
                      className="w-full flex items-center gap-2 px-4 pt-3 pb-1 text-left group/pg"
                    >
                      {collapsed ? (
                        <ChevronRight size={13} className="text-ink-600 shrink-0" />
                      ) : (
                        <ChevronDown size={13} className="text-ink-600 shrink-0" />
                      )}
                      <Diamond
                        size={11}
                        className="shrink-0"
                        style={{ color: g.project ? 'var(--accent-text)' : undefined }}
                      />
                      <span className="text-[12px] font-semibold text-ink-200 truncate">
                        {g.project ? g.project.name : 'No project'}
                      </span>
                      <span className="text-[12px] text-ink-500 tabular-nums shrink-0">
                        {g.items.length} issue{g.items.length === 1 ? '' : 's'}
                      </span>
                      {g.project && (
                        <Link
                          to={`/team/${teamId ?? ''}/projects/${g.project.id}`}
                          onClick={(e) => e.stopPropagation()}
                          className="opacity-0 group-hover/pg:opacity-100 transition text-[11px] text-ink-500 hover:text-[var(--accent-text)] shrink-0"
                          title="Open project"
                        >
                          Open ↗
                        </Link>
                      )}
                      <span className="flex-1 h-px bg-line ml-1" />
                    </button>
                    {!collapsed && (
                      <div className="ml-[1.15rem] border-l border-line">
                        {g.items.map((issue) => (
                          <IssueRow
                            key={issue.id}
                            issue={issue}
                            teamId={teamId ?? ''}
                            visibleCols={visibleCols}
                            parentLookup={parentLookup}
                            hideProjectPill
                            subtaskCount={subtaskCounts.get(issue.id)}
                            selected={selectedIssueId === issue.id}
                            queued={pendingSummary[String(issue.id)]?.count}
                          />
                        ))}
                      </div>
                    )}
                  </div>
                );
              })
            )}
          </div>
        ) : phaseGrouped && phaseGroups.length > 0 ? (
          phaseGroups.map((g) => (
            <div key={g.phase} data-testid="phase-group" data-phase={g.phase}>
              <div className="flex items-center gap-2 px-4 pt-3 pb-1 sticky top-0 z-[5] bg-ink-950 group/gh">
                <span className={`inline-flex items-center gap-1.5 px-1.5 py-0.5 rounded-full text-[11px] font-semibold uppercase tracking-wider ring-1 ${PHASE_TONE[g.phase]}`}>
                  {g.phase === 'running' && <span className="w-1.5 h-1.5 rounded-full bg-ok animate-pulse" />}
                  {t(PHASE_LABEL_KEY[g.phase], PHASE_FALLBACK[g.phase])}
                </span>
                <span className="text-[12px] text-ink-500 tabular-nums">{g.items.length}</span>
                <span className="flex-1 h-px bg-line ml-1" />
              </div>
              {g.items.map((issue) => (
                <IssueRow
                  key={issue.id}
                  issue={issue}
                  teamId={teamId ?? ''}
                  visibleCols={visibleCols}
                  parentLookup={parentLookup}
                  subtaskCount={subtaskCounts.get(issue.id)}
                  selected={selectedIssueId === issue.id}
                  queued={pendingSummary[String(issue.id)]?.count}
                />
              ))}
            </div>
          ))
        ) : grouped.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-24 gap-2 text-center">
            <span className="w-9 h-9 rounded-xl border border-dashed border-line-strong grid place-items-center text-ink-500">
              <Plus size={16} />
            </span>
            <span className="text-[13px] text-ink-300">No issues match your filters</span>
            {!readOnly && (
              <span className="text-[12px] text-ink-500 inline-flex items-center gap-1">
                Press <Kbd>C</Kbd> to create one, or adjust filters
              </span>
            )}
          </div>
        ) : (
          grouped.map((g) => (
            <div key={g.status}>
              <div className="flex items-center gap-2 px-4 pt-3 pb-1 sticky top-0 z-[5] bg-ink-950 group/gh">
                <IssueStatusIcon status={g.status} size={12} />
                <span className="text-[12px] font-semibold text-ink-300 uppercase tracking-wider">
                  {STATUS_LABEL[g.status]}
                </span>
                <span className="text-[12px] text-ink-500 tabular-nums">{g.items.length}</span>
                <span className="flex-1 h-px bg-line ml-1" />
                {!readOnly && (
                  <button
                    type="button"
                    onClick={onNewIssue}
                    title="New issue"
                    className="opacity-0 group-hover/gh:opacity-100 transition w-5 h-5 grid place-items-center rounded text-ink-600 hover:text-ink-300 hover:bg-ink-800"
                  >
                    <Plus size={12} />
                  </button>
                )}
              </div>
              {g.items.map((issue) => (
                <IssueRow
                  key={issue.id}
                  issue={issue}
                  teamId={teamId ?? ''}
                  visibleCols={visibleCols}
                  parentLookup={parentLookup}
                  subtaskCount={subtaskCounts.get(issue.id)}
                  selected={selectedIssueId === issue.id}
                  queued={pendingSummary[String(issue.id)]?.count}
                />
              ))}
            </div>
          ))
        )}
      </div>
    </div>
  );
};
