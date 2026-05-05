/**
 * Paperclip-style status icons. Match the rendering of paperclip's
 * `StatusIcon.tsx` — a small ring/circle that fills based on state.
 */

import React from 'react';
import { Circle, CircleDashed, AlertCircle, CircleDot, CheckCircle2, XCircle, MinusCircle } from 'lucide-react';
import type { IssueStatus, IssuePriority } from './types';

export const STATUS_ORDER: IssueStatus[] = [
  'backlog', 'todo', 'in_progress', 'in_review', 'blocked', 'done', 'cancelled',
];

export const STATUS_LABEL: Record<IssueStatus, string> = {
  backlog: 'Backlog',
  todo: 'Todo',
  in_progress: 'In Progress',
  in_review: 'In Review',
  blocked: 'Blocked',
  done: 'Done',
  cancelled: 'Cancelled',
};

export const STATUS_COLOR: Record<IssueStatus, string> = {
  backlog: 'text-zinc-500',
  todo: 'text-blue-400',
  in_progress: 'text-amber-400',
  in_review: 'text-purple-400',
  blocked: 'text-rose-400',
  done: 'text-emerald-500',
  cancelled: 'text-zinc-500',
};

export const IssueStatusIcon: React.FC<{ status: IssueStatus; size?: number }> = ({ status, size = 14 }) => {
  const cls = STATUS_COLOR[status];
  switch (status) {
    case 'backlog':
      return <CircleDashed size={size} className={cls} />;
    case 'todo':
      return <Circle size={size} className={cls} />;
    case 'in_progress':
      return <CircleDot size={size} className={cls} />;
    case 'in_review':
      return <CircleDot size={size} className={cls} />;
    case 'blocked':
      return <AlertCircle size={size} className={cls} />;
    case 'done':
      return <CheckCircle2 size={size} className={cls} />;
    case 'cancelled':
      return <MinusCircle size={size} className={cls} />;
    default:
      return <XCircle size={size} className="text-zinc-500" />;
  }
};

export const PRIORITY_LABEL: Record<IssuePriority, string> = {
  no_priority: 'No priority',
  urgent: 'Urgent',
  high: 'High',
  medium: 'Medium',
  low: 'Low',
};

export const PriorityIcon: React.FC<{ priority: IssuePriority; size?: number }> = ({ priority, size = 12 }) => {
  switch (priority) {
    case 'urgent':
      return <span className={`inline-flex items-center justify-center text-[${size}px] text-rose-400 font-bold`} title="Urgent">⚠</span>;
    case 'high':
      return <span className="text-orange-400 font-bold text-[10px]">↑</span>;
    case 'medium':
      return <span className="text-zinc-400 text-[10px]">—</span>;
    case 'low':
      return <span className="text-zinc-500 text-[10px]">↓</span>;
    case 'no_priority':
    default:
      return <span className="text-zinc-700 text-[10px]">—</span>;
  }
};
