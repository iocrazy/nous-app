// frontend/components/TopicInspiration/hotspotRanking.test.ts
import { describe, it, expect } from 'vitest';
import { blendedScore, topHotspots, partitionBySignal } from './hotspotRanking';
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
  it('cross-source confirmation adds a bounded bump, single-source unchanged', () => {
    const base = blendedScore({ score: 0.6, heat: 0.6 }); // 0.6
    expect(blendedScore({ score: 0.6, heat: 0.6, source_count: 1 })).toBe(base);
    // 5 platforms → full cross factor → +0.10
    expect(blendedScore({ score: 0.6, heat: 0.6, source_count: 5 })).toBeCloseTo(0.7);
    // clamps at 1.0
    expect(blendedScore({ score: 1, heat: 1, source_count: 9 })).toBe(1);
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
  it('multi-source topics lead even with a lower blended score', () => {
    const list = [
      h({ id: 'single', score: 0.9, heat: 0.9 }), // blended 0.9, 1 source
      h({ id: 'multi', score: 0.5, heat: 0.5, source_count: 2 }), // ~0.625
    ];
    // cross-source confirmed item floats to the top of Current Hotspots
    expect(topHotspots(list, 2).map((x) => x.id)).toEqual(['multi', 'single']);
  });
});

describe('partitionBySignal', () => {
  it('folds scored-but-low items, keeps unscored visible', () => {
    const list = [
      h({ id: 'good', score: 0.8, heat: 0.4 }), // blended 0.6 -> main
      h({ id: 'noise', score: 0.1, heat: 0.05 }), // blended ~0.075 -> low
      h({ id: 'pending', score: null, heat: null }), // unscored -> main
      h({ id: 'hot', score: 0.1, heat: 0.9 }), // blended 0.5 -> main (genuinely hot)
    ];
    const { main, low } = partitionBySignal(list);
    expect(main.map((x) => x.id)).toEqual(['good', 'pending', 'hot']);
    expect(low.map((x) => x.id)).toEqual(['noise']);
  });

  it('threshold is configurable', () => {
    const list = [h({ id: 'x', score: 0.4, heat: 0.4 })]; // blended 0.4
    expect(partitionBySignal(list, 0.5).low.map((x) => x.id)).toEqual(['x']);
    expect(partitionBySignal(list, 0.3).low).toEqual([]);
  });
});
