// frontend/components/TopicInspiration/hotspotRanking.test.ts
import { describe, it, expect } from 'vitest';
import { blendedScore, topHotspots } from './hotspotRanking';
import type { Hotspot } from '../../services/topicService';

function h(p: Partial<Hotspot>): Hotspot {
  return { id: '1', title: 'T', tags: [], ...p };
}

describe('blendedScore', () => {
  it('averages score and heat when both present', () => {
    expect(blendedScore({ score: 0.8, heat: 0.4 })).toBeCloseTo(0.6);
  });
  it('falls back to the present one', () => {
    expect(blendedScore({ score: 0.9, heat: null })).toBe(0.9);
    expect(blendedScore({ score: null, heat: 0.7 })).toBe(0.7);
  });
  it('is 0 when neither present', () => {
    expect(blendedScore({ score: null, heat: null })).toBe(0);
  });
  it('a hot item out-ranks a high-LLM-score-but-cold item', () => {
    const cold = blendedScore({ score: 0.85, heat: 0.0 });
    const hot = blendedScore({ score: 0.6, heat: 0.9 });
    expect(hot).toBeGreaterThan(cold);
  });
});

describe('topHotspots', () => {
  it('orders by blended score and caps to n', () => {
    const list = [
      h({ id: 'a', score: 0.9, heat: 0.1 }), // 0.5
      h({ id: 'b', score: 0.5, heat: 0.9 }), // 0.7
      h({ id: 'c', score: null, heat: null }), // excluded
      h({ id: 'd', score: 0.6, heat: 0.6 }), // 0.6
    ];
    const top = topHotspots(list, 2);
    expect(top.map((x) => x.id)).toEqual(['b', 'd']);
  });
});
