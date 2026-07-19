/**
 * Beats M3.5 — user custom templates (pure math).
 *
 * `deriveTemplateAnchors` reverse-computes percentage anchors from the beats a
 * user has already arranged on the timeline (relative to the current total), so
 * a hand-crafted sheet can be saved as a reusable template.
 * `instantiateCustomAnchors` is the inverse: it turns those stored anchors back
 * into `BeatInput` rows against a (possibly different) target total, carrying
 * the user's own title / summary / color verbatim (no i18n — custom beats are
 * free-form, beat_role null). The round-trip must land within one snap grid of
 * the original placement.
 */
import { describe, expect, it } from 'vitest';

import type { Beat } from '../sceneService';
import {
  deriveTemplateAnchors,
  instantiateCustomAnchors,
  type CustomTemplateAnchor,
} from '../beats/templates';

const beat = (over: Partial<Beat> & { id: string }): Beat => ({
  script_id: '1',
  title: 'Beat',
  summary: null,
  scene_ids: [],
  sort_order: 1000,
  start_sec: null,
  duration_sec: null,
  beat_role: null,
  color: null,
  ...over,
});

describe('deriveTemplateAnchors', () => {
  it('reverse-computes percentage anchors from arranged beats (1-decimal)', () => {
    const anchors = deriveTemplateAnchors(
      [
        beat({ id: 'a', title: 'Open', start_sec: 60, duration_sec: 120, color: '#b8b0a0' }),
        beat({ id: 'b', title: 'Turn', summary: 'The pivot', start_sec: 300, duration_sec: 60 }),
      ],
      600,
    );
    expect(anchors).toEqual([
      { title: 'Open', summary: null, pctStart: 10, pctEnd: 30, color: '#b8b0a0' },
      { title: 'Turn', summary: 'The pivot', pctStart: 50, pctEnd: 60, color: null },
    ]);
  });

  it('rounds non-grid ratios to one decimal', () => {
    const [a] = deriveTemplateAnchors(
      [beat({ id: 'a', start_sec: 65, duration_sec: 120 })],
      700,
    );
    // 65/700 = 9.2857% → 9.3; 185/700 = 26.4285% → 26.4
    expect(a.pctStart).toBe(9.3);
    expect(a.pctEnd).toBe(26.4);
  });

  it('skips unarranged beats (start_sec null) and sorts by start', () => {
    const anchors = deriveTemplateAnchors(
      [
        beat({ id: 'late', title: 'Late', start_sec: 300, duration_sec: 60 }),
        beat({ id: 'tray', title: 'Tray', start_sec: null }),
        beat({ id: 'early', title: 'Early', start_sec: 0, duration_sec: 60 }),
      ],
      600,
    );
    expect(anchors.map((a) => a.title)).toEqual(['Early', 'Late']);
  });

  it('returns [] for a non-positive total (cannot derive percentages)', () => {
    expect(deriveTemplateAnchors([beat({ id: 'a', start_sec: 0, duration_sec: 10 })], 0)).toEqual([]);
    expect(deriveTemplateAnchors([beat({ id: 'a', start_sec: 0, duration_sec: 10 })], -5)).toEqual([]);
  });

  it('clamps anchors that run past the total to 100%', () => {
    const [a] = deriveTemplateAnchors(
      [beat({ id: 'a', start_sec: 500, duration_sec: 400 })],
      600,
    );
    expect(a.pctStart).toBeCloseTo(83.3, 1);
    expect(a.pctEnd).toBe(100); // 900/600 = 150% → clamped
  });
});

describe('instantiateCustomAnchors', () => {
  const anchors: CustomTemplateAnchor[] = [
    { title: 'Open', summary: 'Cold open', pctStart: 10, pctEnd: 30, color: '#b8b0a0' },
    { title: 'Beat marker', pctStart: 50, pctEnd: 50 }, // point anchor
  ];

  it('carries the stored title/summary/color verbatim, beat_role null', () => {
    const rows = instantiateCustomAnchors(anchors, 600, 5);
    expect(rows).toHaveLength(2);
    expect(rows[0].title).toBe('Open');
    expect(rows[0].summary).toBe('Cold open');
    expect(rows[0].color).toBe('#b8b0a0');
    expect(rows[0].beat_role).toBeNull();
    // No stored summary → null (clears rather than leaks a template key).
    expect(rows[1].summary).toBeNull();
    expect(rows[1].color).toBeNull();
  });

  it('scales interval and point anchors against the target total', () => {
    const rows = instantiateCustomAnchors(anchors, 600, 5);
    expect(rows[0].start_sec).toBe(60); // 10% of 600
    expect(rows[0].duration_sec).toBe(120); // 20% of 600
    // Point anchor: duration = max(1% of total, granularity), snapped.
    expect(rows[1].start_sec).toBe(300); // 50% of 600
    expect(rows[1].duration_sec).toBe(5); // 1% of 600 = 6 → snap to 5
  });
});

describe('derive → instantiate round-trip', () => {
  it('recovers start/duration within one snap grid', () => {
    const total = 700;
    const granularity = 5;
    const originals = [
      beat({ id: 'a', title: 'A', start_sec: 65, duration_sec: 120 }),
      beat({ id: 'b', title: 'B', start_sec: 210, duration_sec: 95 }),
      beat({ id: 'c', title: 'C', start_sec: 480, duration_sec: 130 }),
    ];
    const anchors = deriveTemplateAnchors(originals, total);
    const rows = instantiateCustomAnchors(anchors, total, granularity);
    expect(rows).toHaveLength(3);
    for (let i = 0; i < originals.length; i += 1) {
      expect(Math.abs(rows[i].start_sec! - originals[i].start_sec!)).toBeLessThanOrEqual(granularity);
      expect(
        Math.abs((rows[i].duration_sec ?? 0) - (originals[i].duration_sec ?? 0)),
      ).toBeLessThanOrEqual(granularity);
      expect(rows[i].title).toBe(originals[i].title);
    }
  });
});
