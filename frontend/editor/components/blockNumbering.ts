/**
 * Continuous per-block numbering (spec: A1 — laper.ai-style document-order
 * numbering). Every block in the document gets ONE running number, scene
 * headings included: scene1's heading is block 1, its elements are 2..N,
 * scene2's heading is the next number, and so on. This replaces the old
 * dual-numbering scheme (a scene-position badge + a separate per-element
 * hover number).
 *
 * `sceneBlockBases` returns, for each scene, the cumulative number of blocks
 * consumed by all PRECEDING scenes. A scene at `bases[i]` renders its heading
 * as `bases[i] + 1` and its elements as `bases[i] + 2, bases[i] + 3, ...`
 * (one number for the heading, then one per element).
 */
import type { SceneDoc } from '../types';

export function sceneBlockBases(scenes: SceneDoc[]): number[] {
  const bases: number[] = [];
  let running = 0;
  for (const scene of scenes) {
    bases.push(running);
    running += 1 + scene.elements.length;
  }
  return bases;
}
