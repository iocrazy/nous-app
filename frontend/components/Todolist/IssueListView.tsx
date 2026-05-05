/**
 * Paperclip-style flat issue list.
 *
 *  A8.3 — wired to real backend via UiIssue.
 *  A9.1 — column visibility picker (persisted per-team).
 *  A9.2 — filter popover (Quick filters + Status/Priority/Assignee/
 *         Creator/Project/Visibility multi-selects). Filtering is
 *         applied client-side against the already-fetched UiIssue list.
 */

import React, { useEffect, useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { Plus, Search, Columns, Filter, ArrowUpDown, RotateCw } from 'lucide-react';

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

export type IssueViewMode = 'list' | 'board';

interface IssueListViewProps {
  issues: UiIssue[];
  loading: boolean;
  error: string | null;
  viewMode: IssueViewMode;
  onViewModeChange: (mode: IssueViewMode) => void;
  onNewIssue: () => void;
  onRefresh: () => void;
  agents: AgentRef[];
  currentUserId?: string;
}

const AgentAvatar: React.FC<{ initials: string; color?: string; size?: number }> = ({ initials, color = 'bg-zinc-600', size = 20 }) => (
  <span
    className={`inline-flex items-center justify-center rounded-full text-[10px] font-semibold text-white ${color}`}
    style={{ width: size, height: size }}
  >
    {initials}
  </span>
);

interface IssueRowProps {
  issue: UiIssue;
  teamId: string;
  visibleCols: Set<IssueColumnKey>;
  parentLookup: Map<number, UiIssue>;
}

const IssueRow: React.FC<IssueRowProps> = ({ issue, teamId, visibleCols, parentLookup }) => {
  const initials = issue.assignee?.name.slice(0, 2).toUpperCase() ?? (issue.assignee_user_label?.slice(0, 2).toUpperCase() ?? '·');
  const parent = issue.parent_id ? parentLookup.get(issue.parent_id) : null;
  return (
    <Link
      to={`/team/${teamId}/todolist/${issue.identifier}`}
      className="flex items-center gap-3 px-4 py-1.5 hover:bg-zinc-800/30 transition group"
    >
      {visibleCols.has('status') && <IssueStatusIcon status={issue.status} size={15} />}
      <span className="w-4 flex justify-center" title={issue.priority}>
        <PriorityIcon priority={issue.priority} />
      </span>
      {visibleCols.has('id') && (
        <span className="font-mono text-[12px] text-zinc-500 w-16 shrink-0 uppercase tracking-wider">
          {issue.identifier}
        </span>
      )}
      <span className="flex-1 truncate text-[14px] text-zinc-200 group-hover:text-white">{issue.title}</span>
      {visibleCols.has('parent') && parent && (
        <Link
          to={`/team/${teamId}/todolist/${parent.identifier}`}
          onClick={(e) => e.stopPropagation()}
          className="hidden lg:inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-zinc-800/70 text-zinc-400 text-[12px] hover:bg-zinc-700"
          title={`Parent: ${parent.title}`}
        >
          ↳ {parent.identifier}
        </Link>
      )}
      {visibleCols.has('tags') && (
        <span className="hidden lg:inline-flex items-center gap-1 text-[12px] text-zinc-600 italic">
          {/* tags schema not in place yet */}
          —
        </span>
      )}
      {visibleCols.has('project') && issue.project && (
        <span
          className="hidden md:inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-zinc-800 text-zinc-400 text-[12px]"
          title={`Project: ${issue.project.name}`}
        >
          <span className={`w-1.5 h-1.5 rounded-full ${issue.project.color ?? 'bg-zinc-500'}`} />
          {issue.project.name}
        </span>
      )}
      {visibleCols.has('assignee') && (issue.assignee || issue.assignee_user_label) && (
        <AgentAvatar
          initials={initials}
          color={issue.assignee?.avatar_color ?? 'bg-zinc-600'}
        />
      )}
      {visibleCols.has('updated') && (
        <span className="text-[12px] text-zinc-500 w-16 text-right shrink-0">
          {relativeTime(issue.last_activity_at)}
        </span>
      )}
    </Link>
  );
};

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
    if (filters.liveRunsOnly) {
      const live = !!i.raw.dbos_workflow_id && i.status !== 'done' && i.status !== 'cancelled';
      if (!live) return false;
    }
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

export const IssueListView: React.FC<IssueListViewProps> = ({ issues, loading, error, viewMode, onViewModeChange, onNewIssue, onRefresh, agents, currentUserId }) => {
  const { teamId } = useParams<{ teamId: string }>();
  const [search, setSearch] = useState('');
  const [filters, setFilters] = useState<IssueFilters>(EMPTY_FILTERS);
  const [columnPickerOpen, setColumnPickerOpen] = useState(false);
  const [filterOpen, setFilterOpen] = useState(false);
  const [sortOpen, setSortOpen] = useState(false);
  const [sort, setSort] = useState<IssueSort>(DEFAULT_SORT);

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
    } else if (q === 'active') {
      setFilters((prev) => ({ ...prev, statuses: new Set(['todo', 'in_progress']) }));
    } else if (q === 'backlog') {
      setFilters((prev) => ({ ...prev, statuses: new Set(['backlog']) }));
    } else {
      setFilters((prev) => ({ ...prev, statuses: new Set(['done']) }));
    }
  };

  // Derive Project list from currently-loaded issues so filter popover
  // doesn't depend on a separate /projects fetch we don't have.
  const projectsList: ProjectRef[] = useMemo(() => {
    const seen = new Map<number, ProjectRef>();
    for (const i of issues) {
      if (i.project && !seen.has(i.project.id)) seen.set(i.project.id, i.project);
    }
    return Array.from(seen.values());
  }, [issues]);

  const filteredByPanel = useMemo(() => applyFilters(issues, filters, currentUserId), [issues, filters, currentUserId]);

  const filtered = useMemo(() => {
    const q = search.toLowerCase().trim();
    if (!q) return filteredByPanel;
    return filteredByPanel.filter((i) => {
      const hay = `${i.identifier} ${i.title} ${i.description ?? ''} ${i.assignee?.name ?? i.assignee_user_label ?? ''}`.toLowerCase();
      return hay.includes(q);
    });
  }, [filteredByPanel, search]);

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

  const filterCount = activeFilterCount(filters);

  return (
    <div className="flex flex-col h-[calc(100vh-5rem)] -mx-4 sm:-mx-8 -mb-28 sm:-mb-8 bg-zinc-950">
      <div className="flex items-center gap-2 px-4 py-3 sticky top-0 z-10 bg-zinc-950">
        <button
          type="button"
          onClick={onNewIssue}
          className="inline-flex items-center gap-1 px-2.5 py-1.5 text-[13px] rounded bg-indigo-500/15 text-indigo-300 ring-1 ring-indigo-500/30 hover:bg-indigo-500/25"
        >
          <Plus size={13} /> New Issue
        </button>
        <div className="relative flex-1 max-w-md">
          <Search size={13} className="absolute left-2 top-1/2 -translate-y-1/2 text-zinc-500" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search issues…"
            className="w-full pl-7 pr-2 py-1.5 text-[13px] bg-zinc-900/80 border border-zinc-800 rounded focus:outline-none focus:ring-1 focus:ring-indigo-500/40 text-zinc-200 placeholder-zinc-600"
          />
        </div>
        <div className="ml-auto flex items-center gap-1">
          <div className="inline-flex rounded border border-zinc-800 bg-zinc-900/80 overflow-hidden">
            <button
              type="button"
              onClick={() => onViewModeChange('list')}
              className={`p-1.5 transition ${
                viewMode === 'list'
                  ? 'bg-zinc-800 text-zinc-100'
                  : 'text-zinc-400 hover:text-zinc-200'
              }`}
              title="List view"
            >
              <ListViewIcon />
            </button>
            <button
              type="button"
              onClick={() => onViewModeChange('board')}
              className={`p-1.5 transition border-l border-zinc-800 ${
                viewMode === 'board'
                  ? 'bg-zinc-800 text-zinc-100'
                  : 'text-zinc-400 hover:text-zinc-200'
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
                  ? 'bg-indigo-500/15 border-indigo-500/40 text-indigo-200'
                  : 'bg-zinc-900/80 border-zinc-800 text-zinc-400 hover:text-zinc-200'
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
                  ? 'bg-zinc-800 border-zinc-700 text-zinc-100'
                  : 'bg-zinc-900/80 border-zinc-800 text-zinc-400 hover:text-zinc-200'
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
                  ? 'bg-zinc-800 border-zinc-700 text-zinc-100'
                  : 'bg-zinc-900/80 border-zinc-800 text-zinc-400 hover:text-zinc-200'
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
            className="p-1.5 rounded border border-zinc-800 bg-zinc-900/80 text-zinc-400 hover:text-zinc-200 disabled:opacity-50"
            title="Refresh"
          >
            <RotateCw size={13} className={loading ? 'animate-spin' : ''} />
          </button>
          <span className="text-[12px] text-zinc-500 pl-2 pr-1">
            {filtered.length} issue{filtered.length === 1 ? '' : 's'}
          </span>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-1.5 px-4 py-1.5 text-[12px]">
        <span className="text-zinc-600 mr-1">Quick:</span>
        {(['all', 'active', 'backlog', 'done'] as QuickFilter[]).map((q) => {
          const matches = (() => {
            if (q === 'all') return filters.statuses.size === 0;
            if (q === 'active') return filters.statuses.size === 2 && filters.statuses.has('todo') && filters.statuses.has('in_progress');
            if (q === 'backlog') return filters.statuses.size === 1 && filters.statuses.has('backlog');
            return filters.statuses.size === 1 && filters.statuses.has('done');
          })();
          return (
            <button
              key={q}
              type="button"
              onClick={() => setQuick(q)}
              className={`inline-flex items-center gap-1 px-2 py-0.5 rounded transition ${
                matches
                  ? 'bg-indigo-500/20 text-indigo-200 ring-1 ring-indigo-500/40'
                  : 'text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800'
              }`}
            >
              {q === 'all' ? 'All' : q === 'active' ? 'Active' : q === 'backlog' ? 'Backlog' : 'Done'}
            </button>
          );
        })}
        {filters.statuses.size > 0 && (
          <span className="text-zinc-600 ml-3">
            ·  {Array.from(filters.statuses).map((s) => STATUS_LABEL[s]).join(', ')}
          </span>
        )}
        {filterCount > 0 && (
          <button
            type="button"
            onClick={() => setFilters(EMPTY_FILTERS)}
            className="ml-auto text-[12px] text-zinc-500 hover:text-zinc-200"
          >
            Reset filters ({filterCount})
          </button>
        )}
      </div>

      <div className="flex-1 overflow-y-auto">
        {error && (
          <div className="px-4 py-3 mx-4 my-3 rounded bg-rose-500/10 text-rose-300 text-xs ring-1 ring-rose-500/30">
            {error}
          </div>
        )}
        {loading && issues.length === 0 ? (
          <div className="flex items-center justify-center py-24 text-sm text-zinc-500">
            Loading issues…
          </div>
        ) : viewMode === 'board' ? (
          <IssueBoardView issues={flatSorted} />
        ) : grouped.length === 0 ? (
          <div className="flex items-center justify-center py-24 text-sm text-zinc-500 italic">
            No issues match the current filters.
          </div>
        ) : (
          grouped.map((g) => (
            <div key={g.status}>
              <div className="flex items-center gap-2 px-4 pt-3 pb-1">
                <IssueStatusIcon status={g.status} size={12} />
                <span className="text-[12px] font-semibold text-zinc-300 uppercase tracking-wider">
                  {STATUS_LABEL[g.status]}
                </span>
                <span className="text-[12px] text-zinc-500 ml-auto">{g.items.length}</span>
              </div>
              {g.items.map((issue) => (
                <IssueRow
                  key={issue.id}
                  issue={issue}
                  teamId={teamId ?? ''}
                  visibleCols={visibleCols}
                  parentLookup={parentLookup}
                />
              ))}
            </div>
          ))
        )}
      </div>
    </div>
  );
};
