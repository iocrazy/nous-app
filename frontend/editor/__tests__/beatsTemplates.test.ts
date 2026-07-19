/**
 * Beats M3 — methodology template registry + instantiation (pure math).
 *
 * `instantiateTemplate` turns a percentage-anchored template into concrete
 * `BeatInput` rows for a given target total: point anchors (pctStart == pctEnd)
 * become a minimum-length instant, interval anchors span their percentage
 * window, and everything snaps to the arrangement grid. The 60s / 110-min pair
 * is the issue's verification baseline (short-video vs feature runtime).
 */
import { describe, expect, it } from 'vitest';

import { BEAT_COLORS } from '../beats/beatColors';
import {
  BEAT_TEMPLATES,
  getTemplate,
  instantiateTemplate,
} from '../beats/templates';

/** Identity translator — the geometry under test is translation-independent. */
const id = (k: string) => k;

describe('BEAT_TEMPLATES registry', () => {
  it('ships the three methodologies with the expected beat counts', () => {
    expect(BEAT_TEMPLATES.map((t) => t.key)).toEqual([
      'save_the_cat',
      'five_beats',
      'kishotenketsu',
    ]);
    expect(getTemplate('save_the_cat')?.beats).toHaveLength(15);
    expect(getTemplate('five_beats')?.beats).toHaveLength(5);
    expect(getTemplate('kishotenketsu')?.beats).toHaveLength(4);
  });

  it('keeps every anchor in-range and well-formed (end >= start, within 0–100)', () => {
    // Note: canonical beat-sheet order interleaves overlapping anchors (Save the
    // Cat lists Theme Stated at 4.5% BEFORE Setup 1–9%), so starts are NOT
    // globally monotonic — only each beat's own window is ordered.
    for (const tpl of BEAT_TEMPLATES) {
      for (const b of tpl.beats) {
        expect(b.pctStart).toBeGreaterThanOrEqual(0);
        expect(b.pctEnd).toBeLessThanOrEqual(100);
        expect(b.pctEnd).toBeGreaterThanOrEqual(b.pctStart);
      }
    }
  });

  it('returns undefined for an unknown key', () => {
    expect(getTemplate('nope')).toBeUndefined();
  });
});

describe('instantiateTemplate — save the cat', () => {
  const tpl = getTemplate('save_the_cat')!;

  it('produces 15 beats with the midpoint at 30s for a 60s total (issue baseline)', () => {
    const beats = instantiateTemplate(tpl, 60, 1, id);
    expect(beats).toHaveLength(15);

    const midpoint = beats.find((b) => b.beat_role === 'save_the_cat.midpoint');
    expect(midpoint?.start_sec).toBe(30); // 50% of 60s

    // Every beat carries a namespaced role, a title, a summary, a grid-aligned
    // start, and a duration at least the snap granularity.
    for (const b of beats) {
      expect(b.beat_role?.startsWith('save_the_cat.')).toBe(true);
      expect(typeof b.title).toBe('string');
      expect(typeof b.summary).toBe('string');
      expect(b.start_sec).toBeGreaterThanOrEqual(0);
      expect(b.start_sec!).toBeLessThanOrEqual(60);
      expect(b.duration_sec ?? 0).toBeGreaterThanOrEqual(1);
    }
    // Beats are emitted in canonical beat-sheet order (Theme Stated is beat 2).
    expect(beats[1].beat_role).toBe('save_the_cat.theme_stated');
    // Colors cycle the M1 Morandi palette.
    expect(beats[0].color).toBe(BEAT_COLORS[0]);
  });

  it('scales anchors to a 110-minute feature (6600s) on the 5s grid', () => {
    const beats = instantiateTemplate(tpl, 6600, 5, id);
    const midpoint = beats.find((b) => b.beat_role === 'save_the_cat.midpoint');
    expect(midpoint?.start_sec).toBe(3300); // 50% of 6600

    const catalyst = beats.find((b) => b.beat_role === 'save_the_cat.catalyst');
    expect(catalyst?.start_sec).toBe(725); // 11% of 6600 = 726 → snap to 5

    const finalImage = beats.find((b) => b.beat_role === 'save_the_cat.final_image');
    expect(finalImage?.start_sec).toBe(6600); // 100% → timeline end

    // A point anchor's duration = max(1% of total, granularity), snapped.
    expect(midpoint?.duration_sec).toBe(65); // 1% of 6600 = 66 → snap to 5
    // An interval anchor spans its percentage window: fun & games 27–50%.
    const funGames = beats.find((b) => b.beat_role === 'save_the_cat.fun_and_games');
    expect(funGames?.start_sec).toBe(1780); // 27% of 6600 = 1782 → 1780
    expect(funGames?.duration_sec).toBe(1520); // 23% of 6600 = 1518 → 1520
  });
});

describe('instantiateTemplate — five beats & kishotenketsu', () => {
  it('five beats resolves to 5 rows starting at the inciting incident', () => {
    const beats = instantiateTemplate(getTemplate('five_beats')!, 600, 5, id);
    expect(beats).toHaveLength(5);
    expect(beats[0].beat_role).toBe('five_beats.inciting_incident');
    expect(beats[0].start_sec).toBe(65); // 11% of 600 = 66 → 65
  });

  it('kishotenketsu splits the timeline into quarters', () => {
    const beats = instantiateTemplate(getTemplate('kishotenketsu')!, 400, 5, id);
    expect(beats.map((b) => b.start_sec)).toEqual([0, 100, 200, 300]);
    expect(beats.map((b) => b.beat_role)).toEqual([
      'kishotenketsu.ki',
      'kishotenketsu.sho',
      'kishotenketsu.ten',
      'kishotenketsu.ketsu',
    ]);
  });
});
