/**
 * Rail entity derivations (Task 8.5) — laper-style left-rail information
 * architecture. The left rail is not just a scene list: it surfaces the script's
 * Characters and Locations too. Both are derived purely on the client from the
 * loaded scenes, in first-seen order, with a scene-appearance count and the id
 * of the first scene they appear in (so clicking a row can scroll there).
 */
import type { SceneDoc } from './types';

export interface RailCharacter {
  name: string;
  /** Number of distinct scenes the character is cued in. */
  sceneCount: number;
  firstSceneId: string;
}

export interface RailLocation {
  name: string;
  /** Number of distinct scenes at this location. */
  sceneCount: number;
  firstSceneId: string;
}

interface Aggregate {
  name: string;
  scenes: Set<string>;
  firstSceneId: string;
}

function collect(
  scenes: SceneDoc[],
  keyOf: (scene: SceneDoc) => { key: string; name: string }[],
): { name: string; sceneCount: number; firstSceneId: string }[] {
  const order: string[] = [];
  const byKey = new Map<string, Aggregate>();
  for (const scene of scenes) {
    for (const { key, name } of keyOf(scene)) {
      let rec = byKey.get(key);
      if (!rec) {
        rec = { name, scenes: new Set(), firstSceneId: scene.id };
        byKey.set(key, rec);
        order.push(key);
      }
      rec.scenes.add(scene.id);
    }
  }
  return order.map((key) => {
    const rec = byKey.get(key)!;
    return { name: rec.name, sceneCount: rec.scenes.size, firstSceneId: rec.firstSceneId };
  });
}

export function deriveRailCharacters(scenes: SceneDoc[]): RailCharacter[] {
  return collect(scenes, (scene) => {
    const seen = new Map<string, string>(); // key -> first display name in this scene
    for (const el of scene.elements) {
      if (el.type !== 'character') continue;
      const name = el.text.trim();
      if (!name) continue;
      const key = name.toUpperCase();
      if (!seen.has(key)) seen.set(key, name);
    }
    return [...seen.entries()].map(([key, name]) => ({ key, name }));
  });
}

export function deriveRailLocations(scenes: SceneDoc[]): RailLocation[] {
  return collect(scenes, (scene) => {
    const name = (scene.location_text ?? '').trim();
    if (!name) return [];
    return [{ key: name.toUpperCase(), name }];
  });
}
