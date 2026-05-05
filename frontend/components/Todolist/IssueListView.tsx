/**
 * Paperclip-style flat issue list (A8.3 wired to real backend via UiIssue).
 */

import React, { useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { Plus, Search, ListFilter } from 'lucide-react';
import type { UiIssue } from './types';
import type { IssueStatus } from '../../services/issuesService';
import { IssueStatusIcon, STATUS_ORDER, STATUS_LABEL, PriorityIcon } from './IssueStatusIcon';
import { relativeTime } from '../../utils/taskDisplay';

interface IssueListViewProps {
  issues: UiIssue[];
  loading: boolean;
  error: string | null;
  onNewIssue: () => void;
  onRefresh: () => void;
}

const AgentAvatar: React.FC<{ initials: string; color?: string; size?: number }> = ({ initials, color = 'bg-zinc-600', size = 20 }) => (
  <span
    className={`inline-flex items-center justify-center rounded-full text-[10px] font-semibold text-white ${color}`}
    style={{ width: size, height: size }}
  >
    {initials}
  </span>
);

const IssueRow: React.FC<{ issue: UiIssue; teamId: string }> = ({ issue, teamId }) => {
  const initials = issue.assignee?.name.slice(0, 2).toUpperCase() ?? (issue.assignee_user_label?.slice(0, 2).toUpperCase() ?? '·');
  return (
    <Link
      to={`/team/${teamId}/todolist/${issue.identifier}`}
      className="flex items-center gap-3 px-4 py-2 border-b border-zinc-900/60 hover:bg-zinc-800/30 transition text-xs group"
    >
      <IssueStatusIcon status={issue.status} size={14} />
      <span className="w-4 flex justify-center" title={issue.priority}>
        <PriorityIcon priority={issue.priority} />
      </span>
      <span className="font-mono text-[10px] text-zinc-500 w-14 shrink-0 uppercase tracking-wider">
        {issue.identifier}
      </span>
      <span className="flex-1 truncate text-zinc-200 group-hover:text-white">{issue.title}</span>
      {issue.project && (
        <span
          className="hidden md:inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-zinc-800 text-zinc-400 text-[10px]"
          title={`Project: ${issue.project.name}`}
        >
          <span className={`w-1.5 h-1.5 rounded-full ${issue.project.color ?? 'bg-zinc-500'}`} />
          {issue.project.name}
        </span>
      )}
      {(issue.assignee || issue.assignee_user_label) && (
        <AgentAvatar
          initials={initials}
          color={issue.assignee?.avatar_color ?? 'bg-zinc-600'}
        />
      )}
      <span className="text-[10px] text-zinc-500 w-14 text-right shrink-0">
        {relativeTime(issue.last_activity_at)}
      </span>
    </Link>
  );
};

export const IssueListView: React.FC<IssueListViewProps> = ({ issues, loading, error, onNewIssue, onRefresh }) => {
  const { teamId } = useParams<{ teamId: string }>();
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState<Set<IssueStatus>>(new Set());

  const toggleStatus = (s: IssueStatus) => {
    setStatusFilter((prev) => {
      const next = new Set(prev);
      if (next.has(s)) next.delete(s);
      else next.add(s);
      return next;
    });
  };

  const filtered = useMemo(() => {
    const q = search.toLowerCase().trim();
    return issues.filter((i) => {
      if (statusFilter.size > 0 && !statusFilter.has(i.status)) return false;
      if (q) {
        const hay = `${i.identifier} ${i.title} ${i.description ?? ''} ${i.assignee?.name ?? i.assignee_user_label ?? ''}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
  }, [issues, search, statusFilter]);

  const grouped = useMemo(() => {
    const map = new Map<IssueStatus, UiIssue[]>();
    for (const i of filtered) {
      if (!map.has(i.status)) map.set(i.status, []);
      map.get(i.status)!.push(i);
    }
    return STATUS_ORDER.filter((s) => map.has(s)).map((s) => ({ status: s, items: map.get(s)! }));
  }, [filtered]);

  return (
    <div className="flex flex-col h-[calc(100vh-5rem)] -mx-4 sm:-mx-8 -mb-28 sm:-mb-8 bg-zinc-950 border-t border-zinc-800/80">
      <div className="flex items-center gap-3 px-4 py-3 border-b border-zinc-800 bg-zinc-950/40 sticky top-0 z-10">
        <button
          type="button"
          onClick={onNewIssue}
          className="inline-flex items-center gap-1 px-2.5 py-1.5 text-xs rounded bg-indigo-500/15 text-indigo-300 ring-1 ring-indigo-500/30 hover:bg-indigo-500/25"
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
            className="w-full pl-7 pr-2 py-1 text-xs bg-zinc-900 border border-zinc-800 rounded focus:outline-none focus:ring-1 focus:ring-indigo-500/40 text-zinc-200 placeholder-zinc-600"
          />
        </div>
        <button
          type="button"
          onClick={onRefresh}
          disabled={loading}
          className="text-[11px] px-2 py-1 rounded text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800 disabled:opacity-50"
          title="Refresh"
        >
          {loading ? '…' : '↻'}
        </button>
        <span className="text-[11px] text-zinc-500 ml-auto">
          {filtered.length} issue{filtered.length === 1 ? '' : 's'}
        </span>
      </div>

      <div className="flex flex-wrap items-center gap-1.5 px-4 py-2 text-[11px] border-b border-zinc-800/60">
        <span className="text-zinc-600 inline-flex items-center gap-1 mr-1">
          <ListFilter size={11} /> Status:
        </span>
        {STATUS_ORDER.map((s) => (
          <button
            key={s}
            type="button"
            onClick={() => toggleStatus(s)}
            className={`inline-flex items-center gap-1 px-2 py-0.5 rounded transition ${
              statusFilter.has(s)
                ? 'bg-indigo-500/20 text-indigo-200 ring-1 ring-indigo-500/40'
                : 'text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800'
            }`}
          >
            <IssueStatusIcon status={s} size={10} />
            {STATUS_LABEL[s]}
          </button>
        ))}
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
        ) : grouped.length === 0 ? (
          <div className="flex items-center justify-center py-24 text-sm text-zinc-500 italic">
            No issues match the current filters.
          </div>
        ) : (
          grouped.map((g) => (
            <div key={g.status} className="border-b border-zinc-800/80 last:border-b-0">
              <div className="flex items-center gap-2 px-4 py-1.5 bg-zinc-900/40 sticky top-0">
                <IssueStatusIcon status={g.status} size={11} />
                <span className="text-[11px] font-medium text-zinc-300 uppercase tracking-wider">
                  {STATUS_LABEL[g.status]}
                </span>
                <span className="text-[10px] text-zinc-500 ml-auto">{g.items.length}</span>
              </div>
              {g.items.map((issue) => (
                <IssueRow key={issue.id} issue={issue} teamId={teamId ?? ''} />
              ))}
            </div>
          ))
        )}
      </div>
    </div>
  );
};
