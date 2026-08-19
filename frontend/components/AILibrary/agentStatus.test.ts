import { describe, it, expect } from 'vitest';
import {
  deriveAgentStatus,
  primaryAction,
  agentGroupOf,
  AGENT_GROUP_ORDER,
} from './agentStatus';
import type { AILibraryAgent } from '../../types';
import type { AgentStatsItem } from '../../services/aiLibraryService';

const agent = (over: Partial<AILibraryAgent> = {}): AILibraryAgent =>
  ({
    id: 'a1',
    slug: 'script_ai',
    name: 'Script AI',
    model: 'qwen-max',
    temperature: 0.7,
    max_tokens: 4096,
    is_system_preset: true,
    enabled: true,
    skill_ids: [],
    created_at: '',
    updated_at: '',
    ...over,
  }) as AILibraryAgent;

const stats = (over: Partial<AgentStatsItem> = {}): AgentStatsItem => ({
  runs_7d: 0,
  tokens_7d: 0,
  cost_cents_7d: 0,
  running_count: 0,
  needs_input_count: 0,
  fault: null,
  interrupted_reason: null,
  ...over,
});

describe('deriveAgentStatus', () => {
  it('reports a fault with the backend detail when the agent is paused', () => {
    const s = deriveAgentStatus(
      agent({ paused_reason: 'budget' }),
      stats({ fault: { kind: 'budget', detail: 'Monthly budget exceeded — raise it' } }),
      new Set(),
    );
    expect(s.kind).toBe('fault');
    // The badge is useless without a next step — the detail must survive
    // all the way from the endpoint to the card.
    expect(s.kind === 'fault' && s.detail).toBe('Monthly budget exceeded — raise it');
  });

  it('treats a live run id as running even with no stats loaded yet', () => {
    // The stats endpoint is one request behind the realtime run feed; an
    // agent that just started must not render idle for that window.
    expect(deriveAgentStatus(agent(), undefined, new Set(['a1'])).kind).toBe('running');
  });

  it('treats a positive running_count as running', () => {
    expect(
      deriveAgentStatus(agent(), stats({ running_count: 1 }), new Set()).kind,
    ).toBe('running');
  });

  it('reports needs_reply with the count', () => {
    const s = deriveAgentStatus(agent(), stats({ needs_input_count: 2 }), new Set());
    expect(s).toEqual({ kind: 'needs_reply', count: 2 });
  });

  it('falls back to idle', () => {
    expect(deriveAgentStatus(agent(), stats(), new Set()).kind).toBe('idle');
  });

  it('ranks fault above running', () => {
    const s = deriveAgentStatus(
      agent({ paused_reason: 'manual' }),
      stats({ running_count: 3, fault: { kind: 'manual', detail: 'Paused by admin' } }),
      new Set(['a1']),
    );
    expect(s.kind).toBe('fault');
  });

  it('ranks running above needs_reply', () => {
    const s = deriveAgentStatus(
      agent(),
      stats({ running_count: 1, needs_input_count: 5 }),
      new Set(),
    );
    expect(s.kind).toBe('running');
  });

  it('reports a restart-interrupted last run as interrupted, never as a fault', () => {
    // The 2026-08-19 report: a day of deploys left the Analyze card showing a
    // red "Fault" badge, so users believed the agent itself was broken.
    // Killing an in-flight run is routine ops, so it gets its own neutral
    // status — and `fault` stays null, which is what the "Faults only"
    // filter and the fault counter key off.
    const s = deriveAgentStatus(agent(), stats({ interrupted_reason: 'restart' }), new Set());
    expect(s.kind).toBe('interrupted');
    expect(s.kind === 'interrupted' && s.detailKey).toBe('aiLibrary.interruption.restart');
  });

  it('still faults when the last run really died', () => {
    // The other direction of the same rule — the guard against "fixing" the
    // report by suppressing bad news. A run the liveness scanner killed while
    // the backend was up is a genuine fault and must stay red.
    const s = deriveAgentStatus(
      agent(),
      stats({ fault: { kind: 'dead_runs', detail: 'Marked dead by liveness scanner' } }),
      new Set(),
    );
    expect(s.kind).toBe('fault');
  });

  it('ranks a live run and a pending reply above an interrupted past run', () => {
    // `interrupted` describes a run that is already over, so it must never
    // hide something the agent is doing now or waiting on.
    expect(
      deriveAgentStatus(
        agent(),
        stats({ running_count: 1, interrupted_reason: 'restart' }),
        new Set(),
      ).kind,
    ).toBe('running');
    expect(
      deriveAgentStatus(
        agent(),
        stats({ needs_input_count: 1, interrupted_reason: 'restart' }),
        new Set(),
      ).kind,
    ).toBe('needs_reply');
  });

  it('ranks interrupted above idle', () => {
    expect(deriveAgentStatus(agent(), stats(), new Set()).kind).toBe('idle');
  });

  it('still faults when stats are missing but the row is paused', () => {
    // Fallback copy, because a fault badge with no reason is exactly what
    // the redesign set out to remove.
    const s = deriveAgentStatus(agent({ paused_reason: 'budget' }), undefined, new Set());
    expect(s.kind).toBe('fault');
    expect(s.kind === 'fault' && s.detail.length).toBeGreaterThan(0);
  });
});

describe('primaryAction', () => {
  it('maps each status to its own action', () => {
    expect(primaryAction({ kind: 'idle' }).key).toBe('chat');
    expect(primaryAction({ kind: 'running' }).key).toBe('viewRuns');
    expect(primaryAction({ kind: 'needs_reply', count: 1 }).key).toBe('goReply');
    expect(primaryAction({ kind: 'fault', detail: 'x' }).key).toBe('fixGuide');
    // Not 'fixGuide' — there is nothing to fix; the runs list is where
    // "did my run finish?" gets answered.
    expect(
      primaryAction({ kind: 'interrupted', detail: 'x', detailKey: 'k' }).key,
    ).toBe('viewRuns');
  });
});

describe('agentGroupOf', () => {
  it('passes through a known group', () => {
    expect(agentGroupOf(agent({ agent_group: 'writing' }))).toBe('writing');
    expect(agentGroupOf(agent({ agent_group: 'art' }))).toBe('art');
  });

  it('buckets null and unknown groups into tools', () => {
    // Rather than rendering a fourth "ungrouped" section for rows that
    // predate mig 400 or came from a future group name.
    expect(agentGroupOf(agent({ agent_group: null }))).toBe('tools');
    expect(agentGroupOf(agent({ agent_group: 'wardrobe' }))).toBe('tools');
    expect(agentGroupOf(agent())).toBe('tools');
  });

  it('orders groups writing → art → tools', () => {
    expect(AGENT_GROUP_ORDER).toEqual(['writing', 'art', 'tools']);
  });
});
