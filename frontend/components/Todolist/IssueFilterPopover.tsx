/**
 * Filter popover (A9.2, paperclip-style).
 *
 * Quick filters (All / Active / Backlog / Done) are derived from the
 * statuses set — clicking one sets the preset and the highlighted chip
 * reflects the current state. The remaining sections are independent
 * multi-selects: Status / Priority / Assignee / Creator / Project /
 * Visibility (live-runs-only, hide-routine).
 *
 * Filtering is applied client-side in IssueListView's useMemo. The list
 * endpoint already fetches up to 200 rows per team, so we don't need a
 * round-trip to refilter — just project the current filter against the
 * already-loaded issues.
 */

import React, { useEffect, useRef } from 'react';
import { X, Check, Search } from 'lucide-react';
import type { IssueStatus, IssuePriority } from '../../services/issuesService';
import { STATUS_LABEL, STATUS_ORDER, PRIORITY_LABEL, PRIORITY_ORDER, IssueStatusIcon, PriorityIcon } from './IssueStatusIcon';
import type { AgentRef, ProjectRef } from './types';

export type QuickFilter = 'all' | 'active' | 'backlog' | 'done';

export interface IssueFilters {
  statuses: Set<IssueStatus>;
  priorities: Set<IssuePriority>;
  assigneeMe: boolean;
  assigneeNone: boolean;
  assigneeAgents: Set<string>;
  creatorsAgents: Set<string>;
  creatorMe: boolean;
  projects: Set<number>;
  liveRunsOnly: boolean;
  hideRoutine: boolean;
  creatorSearch: string;
}

export const EMPTY_FILTERS: IssueFilters = {
  statuses: new Set<IssueStatus>(),
  priorities: new Set<IssuePriority>(),
  assigneeMe: false,
  assigneeNone: false,
  assigneeAgents: new Set<string>(),
  creatorsAgents: new Set<string>(),
  creatorMe: false,
  projects: new Set<number>(),
  liveRunsOnly: false,
  hideRoutine: false,
  creatorSearch: '',
};

const QUICK_PRESETS: Record<Exclude<QuickFilter, 'all'>, IssueStatus[]> = {
  active: ['todo', 'in_progress'],
  backlog: ['backlog'],
  done: ['done'],
};

export function detectQuick(statuses: Set<IssueStatus>): QuickFilter {
  if (statuses.size === 0) return 'all';
  for (const [name, set] of Object.entries(QUICK_PRESETS) as Array<[keyof typeof QUICK_PRESETS, IssueStatus[]]>) {
    if (statuses.size === set.length && set.every((s) => statuses.has(s))) return name;
  }
  return 'all'; // unrecognised custom set — show All as inactive baseline
}

interface IssueFilterPopoverProps {
  filters: IssueFilters;
  onChange: (next: IssueFilters) => void;
  onClose: () => void;
  agents: AgentRef[];
  projects: ProjectRef[];
}

export const IssueFilterPopover: React.FC<IssueFilterPopoverProps> = ({ filters, onChange, onClose, agents, projects }) => {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const onDocClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('mousedown', onDocClick);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDocClick);
      document.removeEventListener('keydown', onKey);
    };
  }, [onClose]);

  const update = (patch: Partial<IssueFilters>) => onChange({ ...filters, ...patch });
  const toggleSetItem = <T,>(key: keyof IssueFilters, item: T) => {
    const set = new Set(filters[key] as Set<T>);
    if (set.has(item)) set.delete(item);
    else set.add(item);
    update({ [key]: set } as unknown as Partial<IssueFilters>);
  };

  const setQuick = (q: QuickFilter) => {
    if (q === 'all') {
      update({ statuses: new Set() });
    } else {
      update({ statuses: new Set(QUICK_PRESETS[q]) });
    }
  };
  const activeQuick = detectQuick(filters.statuses);

  const filteredAgents = filters.creatorSearch
    ? agents.filter((a) => a.name.toLowerCase().includes(filters.creatorSearch.toLowerCase()))
    : agents;

  const Pill: React.FC<{ active: boolean; onClick: () => void; children: React.ReactNode; title?: string }> = ({ active, onClick, children, title }) => (
    <button
      type="button"
      title={title}
      onClick={onClick}
      className={`inline-flex items-center gap-1 px-2 py-0.5 rounded transition text-[11px] ${
        active
          ? 'bg-indigo-500/20 text-indigo-200 ring-1 ring-indigo-500/40'
          : 'text-ink-400 hover:text-ink-200 hover:bg-ink-800/80 ring-1 ring-ink-800'
      }`}
    >
      {children}
    </button>
  );

  const CheckRow: React.FC<{ checked: boolean; onClick: () => void; label: React.ReactNode }> = ({ checked, onClick, label }) => (
    <button
      type="button"
      onClick={onClick}
      className="w-full flex items-center gap-2 px-2 py-1 text-left hover:bg-ink-900/60 rounded transition"
    >
      <span className={`inline-flex items-center justify-center w-3.5 h-3.5 rounded border ${
        checked ? 'bg-indigo-500/80 border-indigo-400 text-white' : 'border-ink-700 bg-transparent'
      }`}>
        {checked && <Check size={10} strokeWidth={3} />}
      </span>
      <span className="text-[11px] text-ink-300 flex-1 inline-flex items-center gap-1.5">{label}</span>
    </button>
  );

  return (
    <div
      ref={ref}
      className="absolute right-0 top-full mt-1 w-[42rem] bg-ink-950 border border-ink-800 rounded-lg shadow-2xl z-30"
    >
      <header className="flex items-center justify-between px-3 py-2 border-b border-ink-800">
        <h3 className="text-sm font-semibold text-ink-200">Filters</h3>
        <button onClick={onClose} className="p-1 text-ink-500 hover:text-ink-300 rounded hover:bg-ink-800">
          <X size={12} />
        </button>
      </header>

      <div className="px-3 py-2 border-b border-ink-800/60">
        <div className="text-[10px] uppercase tracking-wider text-ink-500 mb-1.5">Quick filters</div>
        <div className="flex items-center gap-1.5">
          {(['all', 'active', 'backlog', 'done'] as QuickFilter[]).map((q) => (
            <Pill key={q} active={activeQuick === q} onClick={() => setQuick(q)}>
              {q === 'all' ? 'All' : q === 'active' ? 'Active' : q === 'backlog' ? 'Backlog' : 'Done'}
            </Pill>
          ))}
        </div>
      </div>

      <div className="grid grid-cols-3 gap-3 px-3 py-3">
        <section>
          <div className="text-[10px] uppercase tracking-wider text-ink-500 mb-1">Status</div>
          <div className="space-y-0.5">
            {STATUS_ORDER.map((s) => (
              <CheckRow
                key={s}
                checked={filters.statuses.has(s)}
                onClick={() => toggleSetItem<IssueStatus>('statuses', s)}
                label={
                  <>
                    <IssueStatusIcon status={s} size={11} />
                    {STATUS_LABEL[s]}
                  </>
                }
              />
            ))}
          </div>
        </section>

        <section>
          <div className="text-[10px] uppercase tracking-wider text-ink-500 mb-1">Assignee</div>
          <div className="space-y-0.5">
            <CheckRow
              checked={filters.assigneeNone}
              onClick={() => update({ assigneeNone: !filters.assigneeNone })}
              label={<span className="text-ink-400">No assignee</span>}
            />
            <CheckRow
              checked={filters.assigneeMe}
              onClick={() => update({ assigneeMe: !filters.assigneeMe })}
              label={<span><span className="font-medium">Me</span></span>}
            />
            {agents.map((a) => (
              <CheckRow
                key={a.id}
                checked={filters.assigneeAgents.has(a.id)}
                onClick={() => toggleSetItem<string>('assigneeAgents', a.id)}
                label={
                  <>
                    <span className={`w-3.5 h-3.5 rounded-full inline-flex items-center justify-center text-[8px] font-bold text-ink-50 ${a.avatar_color ?? 'bg-ink-600'}`}>
                      {a.name.slice(0, 1).toUpperCase()}
                    </span>
                    {a.name}
                  </>
                }
              />
            ))}
          </div>
        </section>

        <section>
          <div className="text-[10px] uppercase tracking-wider text-ink-500 mb-1">Visibility</div>
          <div className="space-y-0.5">
            <CheckRow
              checked={filters.liveRunsOnly}
              onClick={() => update({ liveRunsOnly: !filters.liveRunsOnly })}
              label={<>Live runs only</>}
            />
            <CheckRow
              checked={filters.hideRoutine}
              onClick={() => update({ hideRoutine: !filters.hideRoutine })}
              label={<>Hide routine runs</>}
            />
          </div>
          <div className="text-[10px] uppercase tracking-wider text-ink-500 mt-3 mb-1">Creator</div>
          <div className="relative mb-1">
            <Search size={11} className="absolute left-2 top-1/2 -translate-y-1/2 text-ink-500" />
            <input
              type="text"
              value={filters.creatorSearch}
              onChange={(e) => update({ creatorSearch: e.target.value })}
              placeholder="Search creators…"
              className="w-full pl-6 pr-2 py-1 text-[11px] bg-ink-900 border border-ink-800 rounded focus:outline-none focus:ring-1 focus:ring-indigo-500/40 text-ink-200 placeholder-ink-600"
            />
          </div>
          <div className="space-y-0.5 max-h-32 overflow-y-auto">
            <CheckRow
              checked={filters.creatorMe}
              onClick={() => update({ creatorMe: !filters.creatorMe })}
              label={<span><span className="font-medium">Me</span></span>}
            />
            {filteredAgents.map((a) => (
              <CheckRow
                key={a.id}
                checked={filters.creatorsAgents.has(a.id)}
                onClick={() => toggleSetItem<string>('creatorsAgents', a.id)}
                label={
                  <>
                    <span className={`w-3.5 h-3.5 rounded-full inline-flex items-center justify-center text-[8px] font-bold text-ink-50 ${a.avatar_color ?? 'bg-ink-600'}`}>
                      {a.name.slice(0, 1).toUpperCase()}
                    </span>
                    {a.name}
                  </>
                }
              />
            ))}
          </div>
        </section>

        <section>
          <div className="text-[10px] uppercase tracking-wider text-ink-500 mb-1">Priority</div>
          <div className="space-y-0.5">
            {PRIORITY_ORDER.map((p) => (
              <CheckRow
                key={p}
                checked={filters.priorities.has(p)}
                onClick={() => toggleSetItem<IssuePriority>('priorities', p)}
                label={
                  <>
                    <PriorityIcon priority={p} />
                    {PRIORITY_LABEL[p]}
                  </>
                }
              />
            ))}
          </div>
        </section>

        <section className="col-span-2">
          <div className="text-[10px] uppercase tracking-wider text-ink-500 mb-1">Project</div>
          {projects.length === 0 ? (
            <div className="text-[11px] text-ink-600 italic px-2 py-1">No projects on visible issues.</div>
          ) : (
            <div className="space-y-0.5 max-h-24 overflow-y-auto">
              {projects.map((p) => (
                <CheckRow
                  key={p.id}
                  checked={filters.projects.has(p.id)}
                  onClick={() => toggleSetItem<number>('projects', p.id)}
                  label={
                    <>
                      <span className={`w-1.5 h-1.5 rounded-full ${p.color ?? 'bg-ink-500'}`} />
                      {p.name}
                    </>
                  }
                />
              ))}
            </div>
          )}
        </section>
      </div>

      <footer className="flex items-center justify-end gap-2 px-3 py-2 border-t border-ink-800 text-[11px]">
        <button
          type="button"
          onClick={() => onChange(EMPTY_FILTERS)}
          className="text-ink-300 hover:text-ink-50"
        >
          Reset
        </button>
      </footer>
    </div>
  );
};
