// frontend/components/AILibrary/agentStatus.ts
// Status derivation for the B1 agent gallery (spec 2026-08-02 §B1).
//
// The card's badge, its warning line and its primary button are all one
// decision. Keeping that decision here — pure, no React — is what makes it
// testable and keeps the three from drifting apart on the card.

import type { AILibraryAgent } from '../../types';
import type { AgentStatsItem } from '../../services/aiLibraryService';

export type AgentDerivedStatus =
  | { kind: 'running' }
  | { kind: 'needs_reply'; count: number }
  | { kind: 'fault'; detail: string }
  | { kind: 'idle' };

export type AgentGroup = 'writing' | 'art' | 'tools';

export const AGENT_GROUP_ORDER: AgentGroup[] = ['writing', 'art', 'tools'];

/** Group buckets that predate mig 400 (or come from a newer backend) land in tools. */
export function agentGroupOf(agent: AILibraryAgent): AgentGroup {
  const g = agent.agent_group;
  return AGENT_GROUP_ORDER.includes(g as AgentGroup) ? (g as AgentGroup) : 'tools';
}

// Used only when the row says "paused" but the stats request hasn't landed
// (or failed). A fault badge with no reason is precisely what this redesign
// set out to remove, so there is always *something* actionable to show.
const FALLBACK_FAULT_DETAIL: Record<string, string> = {
  budget: 'Monthly budget exceeded — raise the budget or resume the agent',
  manual: 'Paused by admin — resume from the agent’s Profile tab',
};

/**
 * Priority: fault > running > needs_reply > idle.
 *
 * A paused agent shows its pause even while runs are still draining — the
 * user's next step is "resume it", not "watch it". Running beats a pending
 * reply for the same reason: the agent is not blocked on the human yet.
 *
 * ``runningIds`` is the realtime feed and ``stats.running_count`` is the
 * polled one; either counts, because the poll lags a freshly started run.
 */
export function deriveAgentStatus(
  agent: AILibraryAgent,
  stats: AgentStatsItem | undefined,
  runningIds: Set<string>,
): AgentDerivedStatus {
  const fault = stats?.fault;
  if (fault) return { kind: 'fault', detail: fault.detail };
  if (agent.paused_reason) {
    return {
      kind: 'fault',
      detail: FALLBACK_FAULT_DETAIL[agent.paused_reason] ?? 'Agent is paused',
    };
  }
  if (runningIds.has(agent.id) || (stats?.running_count ?? 0) > 0) {
    return { kind: 'running' };
  }
  const waiting = stats?.needs_input_count ?? 0;
  if (waiting > 0) return { kind: 'needs_reply', count: waiting };
  return { kind: 'idle' };
}

export type PrimaryActionKey = 'chat' | 'viewRuns' | 'goReply' | 'fixGuide';

/** The one button that reflects what the agent needs from you right now. */
export function primaryAction(status: AgentDerivedStatus): { key: PrimaryActionKey } {
  switch (status.kind) {
    case 'running':
      return { key: 'viewRuns' };
    case 'needs_reply':
      return { key: 'goReply' };
    case 'fault':
      return { key: 'fixGuide' };
    default:
      return { key: 'chat' };
  }
}
