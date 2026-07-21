import { describe, expect, it } from 'vitest';
import { planInsertScene } from '../insertScenePlan';
import type { SceneDoc } from '../types';

const scene = (id: string, elementIds: string[]): SceneDoc => ({
  id,
  script_id: '1',
  chapter_id: null,
  heading_int_ext: 'INT',
  location_text: '',
  time_of_day: 'DAY',
  content_version: 1,
  sort_order: 0,
  elements: elementIds.map((eid) => ({ id: eid, type: 'action', text: `t-${eid}` })),
});

const SCENES = [scene('s1', ['a', 'b', 'c']), scene('s2', ['d'])];

describe('planInsertScene', () => {
  it('splits at a mid-scene caret: the caret element and everything below move', () => {
    const plan = planInsertScene(SCENES, { sceneId: 's1', elementId: 'b', field: 'element' }, null);
    expect(plan.anchorSceneId).toBe('s1');
    expect(plan.placeBefore).toBe(false);
    expect(plan.tail.map((e) => e.id)).toEqual(['b', 'c']);
  });

  it('caret on the FIRST element inserts an empty scene ABOVE instead of splitting', () => {
    const plan = planInsertScene(SCENES, { sceneId: 's1', elementId: 'a', field: 'element' }, null);
    expect(plan.anchorSceneId).toBe('s1');
    expect(plan.placeBefore).toBe(true);
    expect(plan.tail).toEqual([]);
  });

  it('scene-level focus (no element caret) is a plain insert-after', () => {
    const plan = planInsertScene(SCENES, { sceneId: 's2', field: 'heading' }, null);
    expect(plan).toEqual({ anchorSceneId: 's2', placeBefore: false, tail: [] });
  });

  it('no cursor falls back to the active scene, insert-after', () => {
    const plan = planInsertScene(SCENES, null, 's2');
    expect(plan).toEqual({ anchorSceneId: 's2', placeBefore: false, tail: [] });
  });

  it('no cursor and no active scene leaves the new scene at the tail', () => {
    expect(planInsertScene(SCENES, null, null)).toEqual({
      anchorSceneId: null,
      placeBefore: false,
      tail: [],
    });
  });

  it('a stale caret element id (not in the scene) degrades to insert-after', () => {
    const plan = planInsertScene(SCENES, { sceneId: 's1', elementId: 'gone', field: 'element' }, null);
    expect(plan).toEqual({ anchorSceneId: 's1', placeBefore: false, tail: [] });
  });
});
