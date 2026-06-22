// frontend/components/TopicInspiration/sourceHealthSummary.test.ts
import { describe, it, expect } from 'vitest';
import { summarizeSourceHealth } from './sourceHealthSummary';
import type { SourceHealth } from '../../services/topicService';

function src(p: Partial<SourceHealth>): SourceHealth {
  return {
    id: '1',
    name: 'S',
    kind: 'rss',
    enabled: true,
    health: 'ok',
    consecutive_failures: 0,
    ...p,
  };
}

describe('summarizeSourceHealth', () => {
  it('counts enabled sources by status', () => {
    const s = summarizeSourceHealth([
      src({ id: '1', health: 'ok' }),
      src({ id: '2', health: 'degraded' }),
      src({ id: '3', health: 'dead' }),
    ]);
    expect(s).toMatchObject({ ok: 1, degraded: 1, dead: 1, total: 3, worst: 'dead' });
  });

  it('worst is the most severe enabled status', () => {
    expect(summarizeSourceHealth([src({ health: 'ok' }), src({ health: 'degraded' })]).worst).toBe(
      'degraded',
    );
  });

  it('excludes disabled sources — an off feed is not a fault', () => {
    const s = summarizeSourceHealth([
      src({ id: '1', health: 'ok', enabled: true }),
      src({ id: '2', health: 'dead', enabled: false }),
    ]);
    expect(s.total).toBe(1);
    expect(s.dead).toBe(0);
    expect(s.worst).toBe('ok');
  });

  it('empty list is all-ok', () => {
    expect(summarizeSourceHealth([])).toMatchObject({ total: 0, worst: 'ok' });
  });
});
