/**
 * Completeness guard for issueConfig. The bug this file prevents: a status
 * map that silently drops an enum member (issuesService's old STATUS_LABEL
 * was missing `needs_followup`, which typechecked as long as callers didn't
 * index it). Here we assert every IssueStatus / IssuePriority is covered.
 */

import { describe, it, expect } from 'vitest';
import {
  STATUS_CONFIG, STATUS_ORDER, STATUS_LABEL, STATUS_COLOR, PIPELINE_ORDER,
  PRIORITY_CONFIG, PRIORITY_ORDER, PRIORITY_LABEL,
} from './issueConfig';

const ALL_STATUSES = [
  'backlog', 'todo', 'in_progress', 'in_review', 'needs_followup', 'blocked', 'done', 'cancelled',
] as const;
const ALL_PRIORITIES = ['critical', 'high', 'medium', 'low'] as const;

describe('issueConfig completeness', () => {
  it('covers every status in config, order, label and color', () => {
    for (const s of ALL_STATUSES) {
      expect(STATUS_CONFIG[s], `STATUS_CONFIG missing ${s}`).toBeTruthy();
      expect(STATUS_CONFIG[s].label).toBeTruthy();
      expect(STATUS_CONFIG[s].glyph).toBeTruthy();
      expect(STATUS_LABEL[s]).toBe(STATUS_CONFIG[s].label);
      expect(STATUS_COLOR[s]).toBe(STATUS_CONFIG[s].iconColor);
      expect(STATUS_ORDER).toContain(s);
    }
    expect(STATUS_ORDER).toHaveLength(ALL_STATUSES.length);
  });

  it('needs_followup is present (the drift this guards against)', () => {
    expect(STATUS_ORDER).toContain('needs_followup');
    expect(STATUS_CONFIG.needs_followup.label).toBe('Needs Follow-up');
  });

  it('pipeline order is a subset of the status order', () => {
    for (const s of PIPELINE_ORDER) expect(STATUS_ORDER).toContain(s);
    // Blocked is an incident, not a flow stop — it must not be in the pipeline.
    expect(PIPELINE_ORDER).not.toContain('blocked');
  });

  it('covers every priority with distinct bar levels', () => {
    for (const p of ALL_PRIORITIES) {
      expect(PRIORITY_CONFIG[p], `PRIORITY_CONFIG missing ${p}`).toBeTruthy();
      expect(PRIORITY_LABEL[p]).toBe(PRIORITY_CONFIG[p].label);
      expect(PRIORITY_ORDER).toContain(p);
    }
    const bars = ALL_PRIORITIES.map((p) => PRIORITY_CONFIG[p].bars);
    expect(new Set(bars).size).toBe(4);
    expect(Math.max(...bars)).toBe(4);
    expect(Math.min(...bars)).toBe(1);
  });
});
