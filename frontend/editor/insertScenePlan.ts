/**
 * Planning half of the toolbar "Scene" insert (laper semantics, user
 * 2026-07-21): the new heading lands AT the caret.
 *
 *  - Caret mid-scene → SPLIT: the caret's element and everything below it move
 *    into the new scene, which slots in right after the source.
 *  - Caret on the scene's FIRST element → the whole scene sits "below" the
 *    caret, so a new EMPTY scene slots in ABOVE it (no elements move).
 *  - No element caret (scene-level focus, or no focus at all) → plain insert
 *    after the anchor scene (or at the tail with no anchor).
 *
 * Pure so the decision table is unit-testable; EditorShell's handler performs
 * the create/move/ops calls this plan prescribes.
 */
import type { SceneDoc, ScriptElement } from './types';

export interface InsertScenePlan {
  /** Scene the new one is placed relative to; null → leave at the tail. */
  anchorSceneId: string | null;
  /** Place BEFORE the anchor (first-line case) instead of after. */
  placeBefore: boolean;
  /** Elements to move into the new scene (empty → no split). */
  tail: ScriptElement[];
}

export function planInsertScene(
  scenes: SceneDoc[],
  cursor: { sceneId: string; elementId?: string | null; field?: string } | null,
  activeSceneId: string | null,
): InsertScenePlan {
  const anchorId = cursor?.sceneId ?? activeSceneId;
  const anchorScene = anchorId ? scenes.find((s) => s.id === anchorId) : undefined;
  if (!anchorScene) return { anchorSceneId: null, placeBefore: false, tail: [] };

  const splitIndex =
    cursor?.field === 'element' && cursor.elementId
      ? anchorScene.elements.findIndex((e) => e.id === cursor.elementId)
      : -1;

  if (splitIndex === 0) {
    return { anchorSceneId: anchorScene.id, placeBefore: true, tail: [] };
  }
  if (splitIndex > 0) {
    return {
      anchorSceneId: anchorScene.id,
      placeBefore: false,
      tail: anchorScene.elements.slice(splitIndex),
    };
  }
  return { anchorSceneId: anchorScene.id, placeBefore: false, tail: [] };
}
