/**
 * Active content-relay strip (W2b).
 *
 * Shows the running (or most recent terminal) pipeline run on a parent issue:
 * "Pipeline: <name> — step 2/5 · <agent>". A halted run shows its reason in
 * rose. Rendered under the issue title in the detail view; renders nothing when
 * the issue has never had a run.
 */

import React, { useEffect, useState } from 'react';
import { GitBranch } from 'lucide-react';

import {
  listIssuePipelineRuns,
  type PipelineRun,
} from '../../services/pipelinesService';
import type { AgentRef } from './types';

interface Props {
  issueId: number | string;
  agentsById: Record<string, AgentRef>;
  /** Bumped by the parent to force a re-fetch after a run is started. */
  refreshKey?: number;
}

export const PipelineRunStrip: React.FC<Props> = ({ issueId, agentsById, refreshKey }) => {
  const [run, setRun] = useState<PipelineRun | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const runs = await listIssuePipelineRuns(issueId);
        // Prefer a running relay; otherwise show the newest (list is newest-first).
        const active = runs.find((r) => r.status === 'running') ?? runs[0] ?? null;
        if (!cancelled) setRun(active);
      } catch (err) {
        console.error('[PipelineRunStrip] load failed', err);
        if (!cancelled) setRun(null);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [issueId, refreshKey]);

  if (!run) return null;

  const total = run.total_steps ?? '?';
  const agentName = run.current_agent_id
    ? agentsById[run.current_agent_id]?.name ?? 'agent'
    : null;

  const statusTint =
    run.status === 'running'
      ? 'border-cyan-500/30 bg-cyan-500/10 text-cyan-300'
      : run.status === 'completed'
        ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300'
        : run.status === 'halted'
          ? 'border-rose-500/30 bg-rose-500/10 text-rose-300'
          : 'border-ink-700 bg-ink-800/40 text-ink-400';

  return (
    <div
      data-testid="pipeline-run-strip"
      className={`mt-3 flex items-center gap-2 px-3 py-2 rounded border text-[13px] ${statusTint}`}
    >
      <GitBranch size={13} className="shrink-0" />
      <span className="font-medium">{run.pipeline_name ?? 'Pipeline'}</span>
      {run.status === 'running' ? (
        <span className="text-ink-300">
          — step {run.current_step}/{total}
          {agentName && <span className="text-ink-400"> · {agentName}</span>}
        </span>
      ) : run.status === 'completed' ? (
        <span className="text-ink-300">— complete ({total} steps)</span>
      ) : run.status === 'halted' ? (
        <span className="text-ink-300">
          — halted{run.halted_reason ? `: ${run.halted_reason}` : ''}
        </span>
      ) : (
        <span className="text-ink-300">— cancelled</span>
      )}
    </div>
  );
};
