import type { UnifiedTask, TaskStatus } from '../../contexts/TaskManagerContext';

// agent_runs is the universal record for every agent turn (chat + issue +
// scheduled). The Task Center merges those runs in client-side (the two-table
// split — agent_runs vs task_tracking — stays intact on the backend). This
// module maps an agent_runs row to the UnifiedTask shape the panel renders.

/** Subset of public.agent_runs the Task Center reads. */
export interface AgentRunRow {
  id: string;
  user_id: string;
  status: 'running' | 'completed' | 'failed' | 'cancelled' | 'heartbeat_lost';
  trigger: string;
  input_summary: string | null;
  output_summary: string | null;
  error_message: string | null;
  started_at: string | null;
  ended_at: string | null;
  created_at: string;
}

/** agent_runs has no "queued" state — a run is executing or terminal. */
function mapStatus(status: AgentRunRow['status']): TaskStatus {
  switch (status) {
    case 'running':        return 'processing';
    case 'completed':      return 'completed';
    case 'cancelled':      return 'cancelled';
    case 'failed':
    case 'heartbeat_lost': return 'failed';
  }
}

export function agentRunToTask(run: AgentRunRow): UnifiedTask {
  const input = run.input_summary?.trim();
  const status = mapStatus(run.status);
  return {
    id: run.id,
    user_id: run.user_id,
    task_type: 'agent',
    status,
    title: input || `Agent · ${run.trigger}`,
    // While running, the trigger ("chat" / "issue") is the most useful hint;
    // once done, surface the agent's own output summary.
    subtitle: status === 'completed' ? run.output_summary || undefined : run.trigger,
    progress: 0,
    error_msg: run.error_message || undefined,
    metadata: {},
    created_at: run.created_at,
    started_at: run.started_at || undefined,
    completed_at: run.ended_at || undefined,
  };
}
