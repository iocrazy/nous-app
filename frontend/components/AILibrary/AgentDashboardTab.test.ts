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

  it('groups in-flight states under plum (agent hue) and terminal-success under green', () => {
    // Chart legend cohesion — same hue for related states. Values are the
    // warm-paper-palette anchors (K1 remap): plum for in-flight/agent-owned,
    // green for terminal success, brick for failure.
    expect(STATUS_COLORS.in_progress).toBe('#7A5E8F');
    // Was `toMatch(/^#[7A]/i)` — a character class, not a literal, so it
    // only pinned "starts with # then 7 or A/a" and would have passed for
    // e.g. '#a1a1aa' (cancelled's gray) just as happily as any plum shade.
    // Pin the exact value: #AF9BBF is the dark-theme --agent anchor
    // (plum-400, index.css), so `assigned` genuinely sits in the plum
    // family rather than merely matching a loose leading-nibble pattern.
    expect(STATUS_COLORS.assigned).toBe('#AF9BBF');
    expect(STATUS_COLORS.done).toBe('#1E7A5B');
    expect(STATUS_COLORS.completed).toBe('#1E7A5B');
    expect(STATUS_COLORS.failed).toBe('#AD5147');
  });
});
