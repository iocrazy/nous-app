/**
 * Pin the STATUS_COLORS contract — both the dashboard chart and the
 * status badge styling read it. Centralising the map means a future
 * lifecycle state addition (e.g. waiting_for_other) needs exactly one
 * touchpoint.
 */

import { describe, expect, it } from 'vitest';

import { STATUS_COLORS } from './AgentDashboardTab';

describe('STATUS_COLORS', () => {
  it('covers every lifecycle state we ship', () => {
    // These are the lifecycle states from agent_tasks.lifecycle_status
    // CHECK constraint plus the agent_runs.status states. Missing one
    // would render as undefined gray in the chart.
    const required = [
      'queued',
      'assigned',
      'in_progress',
      'waiting_for_other',
      'blocked',
      'done',
      'failed',
      'cancelled',
      'completed',
    ];
    for (const s of required) {
      expect(STATUS_COLORS[s], `missing color for ${s}`).toMatch(/^#[0-9a-f]{6}$/i);
    }
  });

  it('groups in-flight states under indigo and terminal-success under emerald', () => {
    // Chart legend cohesion — same hue for related states.
    expect(STATUS_COLORS.in_progress).toBe('#6366f1');
    expect(STATUS_COLORS.assigned).toMatch(/^#[68]/);
    expect(STATUS_COLORS.done).toBe('#10b981');
    expect(STATUS_COLORS.completed).toBe('#10b981');
    expect(STATUS_COLORS.failed).toBe('#ef4444');
  });
});
