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
  /** `detailKey` is set only for frontend-authored fallbacks; a served
   *  `fault.detail` has no key and is rendered as-is. */
  | { kind: 'fault'; detail: string; detailKey?: string }
  /** The last run was cut short by something that is not the agent's fault.
   *  Informational, never red — see INTERRUPTION_DETAIL. */
  | { kind: 'interrupted'; detail: string; detailKey: string }
  | { kind: 'idle' };

export type AgentGroup = 'writing' | 'art' | 'tools';

export const AGENT_GROUP_ORDER: AgentGroup[] = ['writing', 'art', 'tools'];

/**
 * Avatar tint per group — semantic tokens, never raw hues (K1 palette).
 * Shared so the gallery card and the detail header tint the same agent
 * identically; they used to be two separate maps waiting to drift.
 */
export const GROUP_AVATAR: Record<AgentGroup, string> = {
  writing: 'bg-agent-soft text-agent',
  art: 'bg-info-soft text-info',
  tools: 'bg-ok-soft text-ok',
};

/** Group buckets that predate mig 400 (or come from a newer backend) land in tools. */
export function agentGroupOf(agent: AILibraryAgent): AgentGroup {
  const g = agent.agent_group;
  return AGENT_GROUP_ORDER.includes(g as AgentGroup) ? (g as AgentGroup) : 'tools';
}

// Used only when the row says "paused" but the stats request hasn't landed
// (or failed). A fault badge with no reason is precisely what this redesign
// set out to remove, so there is always *something* actionable to show.
//
// These are the only fault strings the frontend authors itself (a served
// `fault.detail` arrives already worded), so they carry an i18n key next to
// the English default — this module is not a component and cannot call `t`.
const FALLBACK_FAULT_DETAIL: Record<string, { key: string; text: string }> = {
  budget: {
    key: 'aiLibrary.fault.budget',
    text: 'Monthly budget exceeded — raise the budget or resume the agent',
  },
  manual: {
    key: 'aiLibrary.fault.manual',
    text: 'Paused by admin — resume from the agent’s Profile tab',
  },
};

const FALLBACK_FAULT_GENERIC = {
  key: 'aiLibrary.fault.paused',
  text: 'Agent is paused',
};

// Copy for a run that ended through no fault of the agent's. Authored here
// rather than served (unlike a `fault.detail`) precisely so it can be
// translated — the endpoint sends a bare reason code.
//
// The wording has a job: the previous behaviour showed the raw backend
// string "Backend restarted while this run was in flight; no heartbeat for
// >2 minutes." under a red Fault badge, and users reasonably concluded their
// agent was broken. It must read as "that one run stopped", not "this agent
// is unwell", and must say the agent is still usable.
const INTERRUPTION_DETAIL: Record<string, { key: string; text: string }> = {
  restart: {
    key: 'aiLibrary.interruption.restart',
    text: 'The last run stopped early because the service restarted. The agent is fine — run it again.',
  },
};

/**
 * Priority: fault > running > needs_reply > interrupted > idle.
 *
 * A paused agent shows its pause even while runs are still draining — the
 * user's next step is "resume it", not "watch it". Running beats a pending
 * reply for the same reason: the agent is not blocked on the human yet.
 *
 * ``interrupted`` sits second-to-last on purpose: it describes a run that is
 * already over, so it must never mask a live run or a reply the agent is
 * waiting on. It outranks only ``idle``, which it refines.
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
    const fb = FALLBACK_FAULT_DETAIL[agent.paused_reason] ?? FALLBACK_FAULT_GENERIC;
    return {
      kind: 'fault',
      detailKey: fb.key,
      detail: fb.text,
    };
  }
  if (runningIds.has(agent.id) || (stats?.running_count ?? 0) > 0) {
    return { kind: 'running' };
  }
  const waiting = stats?.needs_input_count ?? 0;
  if (waiting > 0) return { kind: 'needs_reply', count: waiting };
  const interruption = stats?.interrupted_reason
    ? INTERRUPTION_DETAIL[stats.interrupted_reason]
    : undefined;
  if (interruption) {
    return {
      kind: 'interrupted',
      detailKey: interruption.key,
      detail: interruption.text,
    };
  }
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
    case 'interrupted':
      // The interrupted run is the open question ("did my Analyze finish?"),
      // and the runs list is where it gets answered. Not 'fixGuide' — there
      // is nothing to fix.
      return { key: 'viewRuns' };
    default:
      return { key: 'chat' };
  }
}
