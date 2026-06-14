/**
 * Paperclip-style status & priority icons (A8). Aligns with mediahub's
 * issues table enums (services/issuesService.ts).
 *
 * Status: backlog / todo / in_progress / in_review / blocked / done / cancelled
 * Priority: critical / high / medium / low
 */

import React from 'react';
import { Circle, CircleDashed, AlertCircle, CircleDot, CheckCircle2, MinusCircle, XCircle, HelpCircle } from 'lucide-react';
import type { IssueStatus, IssuePriority } from '../../services/issuesService';

export const STATUS_ORDER: IssueStatus[] = [
  'backlog', 'todo', 'in_progress', 'in_review', 'needs_followup', 'blocked', 'done', 'cancelled',
];

export const STATUS_LABEL: Record<IssueStatus, string> = {
  backlog: 'Backlog',
  todo: 'Todo',
  in_progress: 'In Progress',
  in_review: 'In Review',
  needs_followup: 'Needs Follow-up',
  blocked: 'Blocked',
  done: 'Done',
  cancelled: 'Cancelled',
};

export const STATUS_COLOR: Record<IssueStatus, string> = {
  backlog: 'text-ink-500',
  todo: 'text-blue-400',
  in_progress: 'text-amber-400',
  in_review: 'text-purple-400',
  needs_followup: 'text-orange-400',
  blocked: 'text-rose-400',
  done: 'text-emerald-500',
  cancelled: 'text-ink-500',
};

export const IssueStatusIcon: React.FC<{ status: IssueStatus; size?: number }> = ({ status, size = 14 }) => {
  const cls = STATUS_COLOR[status];
  switch (status) {
    case 'backlog':        return <CircleDashed size={size} className={cls} />;
    case 'todo':           return <Circle size={size} className={cls} />;
    case 'in_progress':    return <CircleDot size={size} className={cls} />;
    case 'in_review':      return <CircleDot size={size} className={cls} />;
    case 'needs_followup': return <HelpCircle size={size} className={cls} />;
    case 'blocked':        return <AlertCircle size={size} className={cls} />;
    case 'done':           return <CheckCircle2 size={size} className={cls} />;
    case 'cancelled':      return <MinusCircle size={size} className={cls} />;
    default:               return <XCircle size={size} className="text-ink-500" />;
  }
};

export const PRIORITY_LABEL: Record<IssuePriority, string> = {
  critical: 'Critical',
  high: 'High',
  medium: 'Medium',
  low: 'Low',
};

export const PRIORITY_ORDER: IssuePriority[] = ['critical', 'high', 'medium', 'low'];

export const PriorityIcon: React.FC<{ priority: IssuePriority }> = ({ priority }) => {
  switch (priority) {
    case 'critical': return <span className="text-rose-400 font-bold text-[11px]" title="Critical">⚠</span>;
    case 'high':     return <span className="text-orange-400 font-bold text-[11px]" title="High">↑</span>;
    case 'medium':   return <span className="text-ink-400 text-[11px]" title="Medium">—</span>;
    case 'low':      return <span className="text-ink-500 text-[11px]" title="Low">↓</span>;
    default:         return <span className="text-ink-700 text-[11px]">—</span>;
  }
};
