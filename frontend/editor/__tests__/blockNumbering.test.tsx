/**
 * Unit test for the continuous per-block numbering helper (spec: A1 continuous
 * numbering — every block in the document, scene headings included, gets one
 * running number 1..N). `sceneBlockBases` returns the cumulative block-index
 * base for each scene: scene i's heading number is `bases[i] + 1`, and its
 * elements are `bases[i] + 2`, `bases[i] + 3`, ... Each scene consumes
 * `1 + elements.length` numbers (1 for the heading, one per element).
 */

import { describe, expect, it } from 'vitest';
import { sceneBlockBases } from '../components/blockNumbering';
import type { SceneDoc, ScriptElement } from '../types';

function el(id: string): ScriptElement {
  return { id, type: 'action', text: '' };
}

function makeScene(id: string, elementCount: number): SceneDoc {
  return {
    id,
    script_id: 's1',
    chapter_id: null,
    heading_int_ext: 'INT',
    location_text: 'Studio',
    time_of_day: 'NIGHT',
    content_version: 1,
    elements: Array.from({ length: elementCount }, (_, i) => el(`${id}-el${i}`)),
    sort_order: 0,
  };
}

describe('sceneBlockBases', () => {
  it('returns cumulative offsets: [2, 1, 0] elements → [0, 3, 5]', () => {
    const scenes = [makeScene('s1', 2), makeScene('s2', 1), makeScene('s3', 0)];
    expect(sceneBlockBases(scenes)).toEqual([0, 3, 5]);
  });

  it('returns an empty array for no scenes', () => {
    expect(sceneBlockBases([])).toEqual([]);
  });

  it('a single scene always starts at base 0', () => {
    const scenes = [makeScene('s1', 4)];
    expect(sceneBlockBases(scenes)).toEqual([0]);
  });

  it('does not mutate the input scenes array', () => {
    const scenes = [makeScene('s1', 2), makeScene('s2', 0)];
    const snapshot = JSON.parse(JSON.stringify(scenes));
    sceneBlockBases(scenes);
    expect(scenes).toEqual(snapshot);
  });
});
