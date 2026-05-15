/**
 * Linear/Paperclip-style task display helpers (A7).
 *
 * Centralises ID-prefix formatting, status icon/color mapping, group-by
 * partitioning, filtering, and sorting so TodolistPage / TaskListView /
 * TaskKanbanView / TaskDetailDrawer all share one source of truth.
 */

import type { TaskStatus, TaskType, UnifiedTask } from '../contexts/TaskManagerContext';

// ─── ID prefix ──────────────────────────────────────────

/** Map task_type → 2-letter prefix (Linear-style: DL-128, AI-572). */
const TASK_TYPE_PREFIX: Record<string, string> = {
  parse: 'PA',
  download: 'DL',
  upload: 'UP',
  transcode: 'TC',
  ai_pipeline: 'PI',
  ai_extract: 'EX',
  ai_transcription: 'TR',
  ai_summary: 'SU',
  ai_visual_analysis: 'VA',
  agent_task: 'AG',
};

/** Last 4 hex chars of a UUID. */
function shortHash(uuid: string | undefined): string {
  if (!uuid) return 'XXXX';
  const stripped = uuid.replace(/-/g, '');
  return stripped.slice(-4).toUpperCase();
}

export function taskIdLabel(task: UnifiedTask): string {
  const prefix = TASK_TYPE_PREFIX[task.task_type] ?? 'TK';
  return `${prefix}-${shortHash(task.id)}`;
}

// ─── Status icon ────────────────────────────────────────

export type StatusVisual = {
  /** Tailwind text color class for the dot/icon */
  color: string;
  /** Tailwind bg color class for kanban column header */
  bg: string;
  /** Border color */
  border: string;
  /** Display label */
  label: string;
  /** Higher-contrast bg for the drawer header pill */
  badgeBg: string;
  /** Bright text color for the drawer header pill */
  badgeText: string;
};

// `color` is for compact dots/icons (used in row + card displays — kept
// muted to avoid a wall of bright color). `badgeBg`/`badgeText` are the
// drawer header pill — needs higher contrast since it's the primary
// status indicator on the focus surface.
export const STATUS_VISUAL: Record<TaskStatus, StatusVisual> = {
  pending:    { color: 'text-zinc-400',    bg: 'bg-zinc-500/10',    border: 'border-zinc-500/30',    label: 'Pending',    badgeBg: 'bg-zinc-500/25',    badgeText: 'text-zinc-100' },
  processing: { color: 'text-blue-400',    bg: 'bg-blue-500/10',    border: 'border-blue-500/30',    label: 'Processing', badgeBg: 'bg-blue-500/25',    badgeText: 'text-blue-100' },
  completed:  { color: 'text-emerald-400', bg: 'bg-emerald-500/10', border: 'border-emerald-500/30', label: 'Done',       badgeBg: 'bg-emerald-500/25', badgeText: 'text-emerald-100' },
  failed:     { color: 'text-rose-400',    bg: 'bg-rose-500/10',    border: 'border-rose-500/30',    label: 'Failed',     badgeBg: 'bg-rose-500/25',    badgeText: 'text-rose-100' },
  cancelled:  { color: 'text-amber-400',   bg: 'bg-amber-500/10',   border: 'border-amber-500/30',   label: 'Cancelled',  badgeBg: 'bg-amber-500/25',   badgeText: 'text-amber-100' },
};

export function statusVisual(status: TaskStatus | undefined): StatusVisual {
  return STATUS_VISUAL[status ?? 'pending'] ?? STATUS_VISUAL.pending;
}

// ─── Group by ───────────────────────────────────────────

export type GroupBy = 'none' | 'status' | 'type' | 'flow' | 'date' | 'agent';

const STATUS_ORDER: TaskStatus[] = ['processing', 'pending', 'failed', 'completed', 'cancelled'];

export interface TaskGroup {
  key: string;
  label: string;
  tasks: UnifiedTask[];
}

function dateBucket(iso: string | undefined): string {
  if (!iso) return 'No date';
  const d = new Date(iso);
  const now = new Date();
  const diffDays = Math.floor((now.getTime() - d.getTime()) / 86_400_000);
  if (diffDays <= 0) return 'Today';
  if (diffDays === 1) return 'Yesterday';
  if (diffDays <= 7) return 'This week';
  if (diffDays <= 30) return 'This month';
  return 'Older';
}

const DATE_ORDER = ['Today', 'Yesterday', 'This week', 'This month', 'Older', 'No date'];

export function groupTasks(tasks: UnifiedTask[], by: GroupBy): TaskGroup[] {
  // 'none' = no grouping; render a single bucket so callers don't have
  // to special-case ungrouped output. The label is empty so list views
  // can detect it and skip rendering a header row.
  if (by === 'none') {
    return tasks.length === 0 ? [] : [{ key: '__all__', label: '', tasks }];
  }
  const map = new Map<string, TaskGroup>();
  const ensure = (key: string, label: string) => {
    let g = map.get(key);
    if (!g) {
      g = { key, label, tasks: [] };
      map.set(key, g);
    }
    return g;
  };
  for (const t of tasks) {
    if (by === 'status') {
      ensure(t.status, statusVisual(t.status).label).tasks.push(t);
    } else if (by === 'type') {
      ensure(t.task_type, t.task_type).tasks.push(t);
    } else if (by === 'flow') {
      // Prefer the task_tracking.flow_id column; metadata fallback covers
      // legacy rows that wrote flow into metadata before the column wired.
      const metaFlow = (t.metadata as Record<string, unknown> | undefined)?.['flow_id'] as string | undefined;
      const flowId = t.flow_id ?? metaFlow;
      const key = flowId ?? '__standalone__';
      const label = flowId ? `Flow ${flowId.slice(0, 8)}` : 'Standalone';
      ensure(key, label).tasks.push(t);
    } else if (by === 'date') {
      const bucket = dateBucket(t.created_at);
      ensure(bucket, bucket).tasks.push(t);
    } else if (by === 'agent') {
      const agentId = (t.metadata as Record<string, unknown> | undefined)?.['agent_id'] as string | undefined;
      const key = agentId ?? '__no_agent__';
      const label = agentId ? `Agent ${agentId.slice(0, 8)}` : 'No agent';
      ensure(key, label).tasks.push(t);
    }
  }
  const groups = Array.from(map.values());
  // Stable ordering per dimension
  if (by === 'status') {
    groups.sort((a, b) => STATUS_ORDER.indexOf(a.key as TaskStatus) - STATUS_ORDER.indexOf(b.key as TaskStatus));
  } else if (by === 'date') {
    groups.sort((a, b) => DATE_ORDER.indexOf(a.label) - DATE_ORDER.indexOf(b.label));
  } else {
    groups.sort((a, b) => a.label.localeCompare(b.label));
  }
  return groups;
}

// ─── Filter ─────────────────────────────────────────────

export interface TaskFilter {
  search?: string;
  statuses?: Set<TaskStatus>;
  types?: Set<TaskType>;
}

export function filterTasks(tasks: UnifiedTask[], filter: TaskFilter): UnifiedTask[] {
  const q = filter.search?.toLowerCase().trim() ?? '';
  return tasks.filter((t) => {
    if (filter.statuses && filter.statuses.size > 0 && !filter.statuses.has(t.status)) return false;
    if (filter.types && filter.types.size > 0 && !filter.types.has(t.task_type)) return false;
    if (q) {
      const haystack = [t.title, t.subtitle, taskIdLabel(t), t.error_msg, t.task_type]
        .filter(Boolean)
        .join(' ')
        .toLowerCase();
      if (!haystack.includes(q)) return false;
    }
    return true;
  });
}

// ─── Sort ───────────────────────────────────────────────

export type SortBy = 'created_desc' | 'created_asc' | 'updated_desc' | 'title_asc';

export function sortTasks(tasks: UnifiedTask[], by: SortBy): UnifiedTask[] {
  const arr = [...tasks];
  switch (by) {
    case 'created_asc':
      return arr.sort((a, b) => (a.created_at ?? '').localeCompare(b.created_at ?? ''));
    case 'updated_desc':
      return arr.sort((a, b) => (b.updated_at ?? b.created_at ?? '').localeCompare(a.updated_at ?? a.created_at ?? ''));
    case 'title_asc':
      return arr.sort((a, b) => (a.title ?? '').localeCompare(b.title ?? ''));
    case 'created_desc':
    default:
      return arr.sort((a, b) => (b.created_at ?? '').localeCompare(a.created_at ?? ''));
  }
}

// ─── Time formatting ────────────────────────────────────

export function relativeTime(iso: string | undefined): string {
  if (!iso) return '';
  const d = new Date(iso);
  const diff = Date.now() - d.getTime();
  const sec = Math.floor(diff / 1000);
  if (sec < 60) return `${sec}s ago`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const day = Math.floor(hr / 24);
  if (day < 7) return `${day}d ago`;
  const week = Math.floor(day / 7);
  if (week < 4) return `${week}w ago`;
  const mo = Math.floor(day / 30);
  return `${mo}mo ago`;
}
