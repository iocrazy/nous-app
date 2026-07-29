// features/canvas-core/smart/nodes/RunStatusBadge.tsx
//
// Run-status badge (P1-5 — Infinite's .node-run-status): a tinted pill with
// a status dot that BREATHES while the run is live (queued/running). This
// replaces two weaker signals at once: the bare 10px run_status text and
// the whole-card animate-pulse (Infinite only ever animates the dot).

import type { PromptNodeData } from '../types';

type RunStatus = PromptNodeData['run_status'];

const BADGE_TONE: Record<Exclude<RunStatus, 'idle'>, { pill: string; dot: string }> = {
  queued: { pill: 'bg-amber-500/15 text-warn', dot: 'bg-amber-400' },
  running: { pill: 'bg-indigo-500/15 text-indigo-600 dark:text-indigo-300', dot: 'bg-indigo-400' },
  succeeded: { pill: 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-400', dot: 'bg-emerald-400' },
  failed: { pill: 'bg-rose-500/15 text-rose-600 dark:text-rose-400', dot: 'bg-rose-400' },
  blocked: { pill: 'bg-canvas-line/40 text-canvas-muted', dot: 'bg-canvas-muted' },
};

export function RunStatusBadge({ status }: { status: RunStatus }) {
  // Idle nodes carry no badge — Infinite's nodes are silent until they run.
  if (status === 'idle') return null;
  const tone = BADGE_TONE[status];
  const live = status === 'queued' || status === 'running';
  return (
    <span
      data-testid="run-status-badge"
      className={`inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wide ${tone.pill}`}
    >
      <span
        data-testid="run-status-dot"
        className={`h-1.5 w-1.5 rounded-full ${tone.dot} ${live ? 'mh-status-dot--pulse' : ''}`}
      />
      {status}
    </span>
  );
}
