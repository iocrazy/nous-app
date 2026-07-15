/**
 * Single source of truth for issue status + priority presentation.
 *
 * Before this, the same enums were re-declared in three places
 * (IssueStatusIcon, issuesService, IssuesPage) and had already drifted —
 * issuesService's STATUS_LABEL was missing `needs_followup`. Everything now
 * derives from these two config maps; IssueStatusIcon re-exports the labels
 * and orders so existing imports keep working.
 *
 * Each status carries its full visual contract (multica config/status.ts
 * pattern): the pie glyph shape, the icon text color, and the tinted
 * backgrounds a board column / list hover uses, so adding a status is a
 * one-line change here instead of a hunt across components.
 */

import type { IssueStatus, IssuePriority } from '../../services/issuesService';

/** Pie-glyph shape encoding workflow progress (drawn by IssueStatusIcon). */
export type StatusGlyph =
  | 'dashed'       // backlog — dotted ring, nothing started
  | 'circle'       // todo — empty ring
  | 'half'         // in_progress — half-filled pie
  | 'threeQuarter' // in_review — 3/4 pie
  | 'dot'          // needs_followup — ring with a center dot
  | 'alert'        // blocked — ring with a bang
  | 'check'        // done — filled circle + tick
  | 'cancel';      // cancelled — ring with a minus

export interface StatusMeta {
  label: string;
  glyph: StatusGlyph;
  /** Icon text color (Tailwind class, drives currentColor). */
  iconColor: string;
  /** Solid hue for the pipeline tick / spectrum segment. */
  tick: string;
  /** Board-column tinted background. */
  columnBg: string;
  /** List-row hover tint. */
  hoverBg: string;
}

export const STATUS_CONFIG: Record<IssueStatus, StatusMeta> = {
  backlog:        { label: 'Backlog',         glyph: 'dashed',       iconColor: 'text-ink-500',     tick: 'bg-ink-500',     columnBg: 'bg-ink-500/5',     hoverBg: 'hover:bg-ink-500/10' },
  todo:           { label: 'Todo',            glyph: 'circle',       iconColor: 'text-blue-400',    tick: 'bg-blue-400',    columnBg: 'bg-blue-500/5',    hoverBg: 'hover:bg-blue-500/10' },
  in_progress:    { label: 'In Progress',     glyph: 'half',         iconColor: 'text-amber-400',   tick: 'bg-amber-400',   columnBg: 'bg-amber-500/5',   hoverBg: 'hover:bg-amber-500/10' },
  in_review:      { label: 'In Review',       glyph: 'threeQuarter', iconColor: 'text-purple-400',  tick: 'bg-purple-400',  columnBg: 'bg-purple-500/5',  hoverBg: 'hover:bg-purple-500/10' },
  needs_followup: { label: 'Needs Follow-up', glyph: 'dot',          iconColor: 'text-orange-400',  tick: 'bg-orange-400',  columnBg: 'bg-orange-500/5',  hoverBg: 'hover:bg-orange-500/10' },
  blocked:        { label: 'Blocked',         glyph: 'alert',        iconColor: 'text-rose-400',    tick: 'bg-rose-400',    columnBg: 'bg-rose-500/5',    hoverBg: 'hover:bg-rose-500/10' },
  done:           { label: 'Done',            glyph: 'check',        iconColor: 'text-emerald-500', tick: 'bg-emerald-500', columnBg: 'bg-emerald-500/5', hoverBg: 'hover:bg-emerald-500/10' },
  cancelled:      { label: 'Cancelled',       glyph: 'cancel',       iconColor: 'text-ink-500',     tick: 'bg-ink-500',     columnBg: 'bg-ink-500/5',     hoverBg: 'hover:bg-ink-500/10' },
};

/** Workflow order for grouping and the pipeline; Blocked hangs off the side. */
export const STATUS_ORDER: IssueStatus[] = [
  'backlog', 'todo', 'in_progress', 'in_review', 'needs_followup', 'blocked', 'done', 'cancelled',
];

/** Flow stages shown in the pipeline (Blocked/cancelled are not flow stops). */
export const PIPELINE_ORDER: IssueStatus[] = [
  'backlog', 'todo', 'in_progress', 'in_review', 'done',
];

export const STATUS_LABEL: Record<IssueStatus, string> = Object.fromEntries(
  Object.entries(STATUS_CONFIG).map(([k, v]) => [k, v.label]),
) as Record<IssueStatus, string>;

/** Icon text color per status (kept for back-compat with STATUS_COLOR imports). */
export const STATUS_COLOR: Record<IssueStatus, string> = Object.fromEntries(
  Object.entries(STATUS_CONFIG).map(([k, v]) => [k, v.iconColor]),
) as Record<IssueStatus, string>;

export interface PriorityMeta {
  label: string;
  /** 1–4 signal bars; 4 renders as the urgent badge. */
  bars: number;
  color: string;
}

export const PRIORITY_CONFIG: Record<IssuePriority, PriorityMeta> = {
  critical: { label: 'Critical', bars: 4, color: 'text-rose-400' },
  high:     { label: 'High',     bars: 3, color: 'text-orange-400' },
  medium:   { label: 'Medium',   bars: 2, color: 'text-ink-400' },
  low:      { label: 'Low',      bars: 1, color: 'text-ink-500' },
};

export const PRIORITY_ORDER: IssuePriority[] = ['critical', 'high', 'medium', 'low'];

export const PRIORITY_LABEL: Record<IssuePriority, string> = Object.fromEntries(
  Object.entries(PRIORITY_CONFIG).map(([k, v]) => [k, v.label]),
) as Record<IssuePriority, string>;
