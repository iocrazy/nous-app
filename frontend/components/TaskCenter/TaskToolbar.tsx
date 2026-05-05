/**
 * Linear/Paperclip-style toolbar for the Task Center (A7).
 *
 * Holds: search input, filter dropdowns, sort dropdown, group-by
 * selector, and view-mode toggle (list / kanban). Stateless — parent
 * owns the filter state and passes it down.
 */

import React, { useEffect, useRef, useState } from 'react';
import {
  Search, ListFilter, ArrowDownNarrowWide, LayoutList, LayoutGrid,
  RefreshCw, Calendar, Layers, Info, Check,
} from 'lucide-react';
import type { TaskStatus, TaskType } from '../../contexts/TaskManagerContext';
import type { GroupBy, SortBy } from '../../utils/taskDisplay';

export type ViewMode = 'list' | 'kanban';

interface TaskToolbarProps {
  search: string;
  onSearchChange: (v: string) => void;
  statusFilter: Set<TaskStatus>;
  onToggleStatus: (s: TaskStatus) => void;
  typeFilter: Set<TaskType>;
  onToggleType: (t: TaskType) => void;
  groupBy: GroupBy;
  onGroupByChange: (g: GroupBy) => void;
  sortBy: SortBy;
  onSortByChange: (s: SortBy) => void;
  viewMode: ViewMode;
  onViewModeChange: (v: ViewMode) => void;
  onRefresh: () => void;
  isRefreshing: boolean;
  totalCount: number;
}

const STATUS_OPTIONS: TaskStatus[] = ['pending', 'processing', 'completed', 'failed', 'cancelled'];
const TYPE_OPTIONS: TaskType[] = ['parse', 'download', 'upload', 'transcode', 'ai_pipeline', 'ai_extract', 'ai_transcription', 'ai_summary'];
const GROUP_OPTIONS: { value: GroupBy; label: string }[] = [
  { value: 'none', label: 'None' },
  { value: 'status', label: 'Status' },
  { value: 'type', label: 'Type' },
  { value: 'flow', label: 'Flow' },
  { value: 'date', label: 'Date' },
  { value: 'agent', label: 'Agent' },
];

const GroupByPicker: React.FC<{
  value: GroupBy;
  onChange: (g: GroupBy) => void;
}> = ({ value, onChange }) => {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDocClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', onDocClick);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDocClick);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);
  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className={`p-1.5 rounded border transition ${
          open || value !== 'none'
            ? 'bg-zinc-800 border-zinc-700 text-zinc-100'
            : 'bg-zinc-900 border-zinc-800 text-zinc-400 hover:text-zinc-200'
        }`}
        title={`Group by: ${GROUP_OPTIONS.find((o) => o.value === value)?.label ?? 'None'}`}
      >
        <Layers size={13} />
      </button>
      {open && (
        <div className="absolute right-0 top-full mt-1 w-44 bg-zinc-950 border border-zinc-800 rounded-lg shadow-2xl z-30 py-1">
          {GROUP_OPTIONS.map((o) => {
            const active = o.value === value;
            return (
              <button
                key={o.value}
                type="button"
                onClick={() => { onChange(o.value); setOpen(false); }}
                className={`w-full flex items-center gap-2 px-3 py-1.5 text-left text-xs transition ${
                  active ? 'bg-zinc-900 text-zinc-100' : 'text-zinc-300 hover:bg-zinc-900/60'
                }`}
              >
                <span className="flex-1">{o.label}</span>
                {active && <Check size={11} className="text-emerald-400" />}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
};
const SORT_OPTIONS: { value: SortBy; label: string }[] = [
  { value: 'created_desc', label: 'Newest first' },
  { value: 'created_asc', label: 'Oldest first' },
  { value: 'updated_desc', label: 'Recently updated' },
  { value: 'title_asc', label: 'Title A→Z' },
];

const Pill: React.FC<{ active: boolean; onClick: () => void; children: React.ReactNode }> = ({ active, onClick, children }) => (
  <button
    type="button"
    onClick={onClick}
    className={`px-2 py-0.5 text-[11px] rounded transition ${
      active
        ? 'bg-indigo-500/20 text-indigo-300 ring-1 ring-indigo-500/40'
        : 'text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800'
    }`}
  >
    {children}
  </button>
);

export const TaskToolbar: React.FC<TaskToolbarProps> = ({
  search, onSearchChange,
  statusFilter, onToggleStatus,
  typeFilter, onToggleType,
  groupBy, onGroupByChange,
  sortBy, onSortByChange,
  viewMode, onViewModeChange,
  onRefresh, isRefreshing, totalCount,
}) => {
  return (
    <div className="space-y-2 px-4 py-3 border-b border-zinc-800 bg-zinc-950/40 sticky top-0 z-10">
      <div className="flex items-center gap-3">
        {/* Tasks are auto-created by parser/AI/workforce. The user-facing
           "create something" path is the Schedules feature — link there
           rather than show a disabled "+ New" stub. */}
        <a
          href="../dashboard/schedules"
          className="inline-flex items-center gap-1 px-2.5 py-1.5 text-xs rounded bg-indigo-500/15 text-indigo-300 ring-1 ring-indigo-500/30 hover:bg-indigo-500/25 hover:text-indigo-200"
          title="Create a recurring schedule that fires tasks. Tasks themselves are auto-created by parser, AI pipelines, and the Workforce."
        >
          <Calendar size={13} /> Schedule
        </a>
        <span
          className="text-zinc-500 hover:text-zinc-300 cursor-help"
          title="Tasks in this list are created automatically when you submit a URL to the parser, kick off an AI pipeline, or when an agent dispatches work. To run a task on a recurring cadence, use Schedules."
        >
          <Info size={12} />
        </span>

        <div className="relative flex-1 max-w-md">
          <Search size={13} className="absolute left-2 top-1/2 -translate-y-1/2 text-zinc-500" />
          <input
            type="text"
            value={search}
            onChange={(e) => onSearchChange(e.target.value)}
            placeholder="Search tasks…"
            className="w-full pl-7 pr-2 py-1 text-xs bg-zinc-900 border border-zinc-800 rounded focus:outline-none focus:ring-1 focus:ring-indigo-500/40 text-zinc-200 placeholder-zinc-600"
          />
        </div>

        <div className="flex items-center gap-1">
          <select
            value={sortBy}
            onChange={(e) => onSortByChange(e.target.value as SortBy)}
            className="text-xs bg-zinc-900 border border-zinc-800 rounded px-2 py-1 text-zinc-300"
            title="Sort"
          >
            {SORT_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
          <GroupByPicker value={groupBy} onChange={onGroupByChange} />
        </div>

        <div className="flex items-center bg-zinc-900 border border-zinc-800 rounded p-0.5">
          <button
            type="button"
            onClick={() => onViewModeChange('list')}
            className={`p-1.5 rounded transition ${viewMode === 'list' ? 'bg-zinc-800 text-zinc-100' : 'text-zinc-500 hover:text-zinc-300'}`}
            title="List view"
          >
            <LayoutList size={13} />
          </button>
          <button
            type="button"
            onClick={() => onViewModeChange('kanban')}
            className={`p-1.5 rounded transition ${viewMode === 'kanban' ? 'bg-zinc-800 text-zinc-100' : 'text-zinc-500 hover:text-zinc-300'}`}
            title="Kanban view"
          >
            <LayoutGrid size={13} />
          </button>
        </div>

        <button
          type="button"
          onClick={onRefresh}
          disabled={isRefreshing}
          className="p-1.5 text-zinc-400 hover:text-zinc-200 rounded hover:bg-zinc-800"
          title="Refresh"
        >
          <RefreshCw size={13} className={isRefreshing ? 'animate-spin' : ''} />
        </button>

        <span className="text-[11px] text-zinc-500 ml-auto">
          <Layers size={11} className="inline mr-1" />
          {totalCount} task{totalCount === 1 ? '' : 's'}
        </span>
      </div>

      <div className="flex flex-wrap items-center gap-1.5 text-[11px]">
        <span className="text-zinc-600 inline-flex items-center gap-1 mr-1">
          <ListFilter size={11} /> Status:
        </span>
        {STATUS_OPTIONS.map((s) => (
          <Pill key={s} active={statusFilter.has(s)} onClick={() => onToggleStatus(s)}>{s}</Pill>
        ))}
        <span className="text-zinc-600 inline-flex items-center gap-1 mx-2">
          <ArrowDownNarrowWide size={11} /> Type:
        </span>
        {TYPE_OPTIONS.map((t) => (
          <Pill key={t} active={typeFilter.has(t)} onClick={() => onToggleType(t)}>{t}</Pill>
        ))}
      </div>
    </div>
  );
};
