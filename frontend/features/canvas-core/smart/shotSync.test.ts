import { describe, expect, it } from 'vitest';

import {
  computeShotLabel,
  reconcileShotNodes,
  SHOT_SYNC_GUTTER,
  SHOT_SYNC_NODE_HEIGHT_ESTIMATE,
} from './shotSync';
import { SMART_NODE_DEFAULT_WIDTH } from './types';
import type { ShotNodeData, SmartNode } from './types';

// Two scenes, matching the `{id, sceneNo}` shape `reconcileShotNodes` takes
// (sceneNo is the caller-computed 1-based rank — same source EP naming and
// StoryboardView use, not necessarily the raw `sort_order` column).
const SCENE_1 = { id: 'scene-1', sceneNo: 1 };
const SCENE_2 = { id: 'scene-2', sceneNo: 2 };

function shot(id: string, overrides: Record<string, unknown> = {}) {
  return {
    id,
    scene_id: SCENE_1.id,
    shot_type: 'MEDIUM',
    camera_angle: 'EYE_LEVEL',
    camera_movement: 'STATIC',
    focal_length: '35mm',
    description: 'Base description',
    image_url: null,
    status: 'empty',
    ...overrides,
  };
}

const BASE_DATA: ShotNodeData = {
  title: '',
  reference_resource_ids: [],
  notes: '',
  shot_id: null,
  shot_label: null,
  shot_type: null,
  camera_angle: null,
  camera_movement: null,
  focal_length: null,
  description: null,
  image_url: null,
  shot_status: null,
  gen_task_id: null,
  scene_id: null,
};

function boundNode(
  nodeId: string,
  shotId: string,
  overrides: Partial<ShotNodeData> = {},
): SmartNode<ShotNodeData> {
  return {
    id: nodeId,
    type: 'shot',
    position: { x: 999, y: 999 },
    data: { ...BASE_DATA, shot_id: shotId, ...overrides },
  };
}

function unboundNode(nodeId: string): SmartNode<ShotNodeData> {
  return {
    id: nodeId,
    type: 'shot',
    position: { x: 0, y: 0 },
    data: { ...BASE_DATA, title: 'Draft shot' },
  };
}

describe('computeShotLabel', () => {
  it('formats "<sceneNo><letter>" — 0-based shot index to A/B/C…', () => {
    expect(computeShotLabel(1, 0)).toBe('1A');
    expect(computeShotLabel(1, 1)).toBe('1B');
    expect(computeShotLabel(3, 2)).toBe('3C');
  });
});

describe('reconcileShotNodes', () => {
  it('fresh canvas: every shot onboards as a new node at a default grid position', () => {
    const shots = [shot('s1'), shot('s2', { scene_id: SCENE_2.id })];
    const result = reconcileShotNodes([], [SCENE_1, SCENE_2], shots);

    expect(result.nodesToMarkStale).toEqual([]);
    expect(result.nodesToPatch).toEqual([]);
    expect(result.nodesToAdd).toHaveLength(2);

    const [n1, n2] = result.nodesToAdd;
    expect(n1.id).toBe('shot-s1');
    expect(n1.type).toBe('shot');
    expect(n1.data.shot_id).toBe('s1');
    expect(n1.data.shot_label).toBe('1A');
    expect(n1.data.scene_id).toBe(SCENE_1.id);
    expect(n1.data.shot_type).toBe('MEDIUM');
    expect(n1.data.gen_task_id).toBeNull();
    // Column 0 (scene index 0), row 0 (first shot in that scene).
    expect(n1.position).toEqual({ x: 0, y: 0 });

    // Second shot lands in scene 2's column (index 1).
    expect(n2.id).toBe('shot-s2');
    expect(n2.position).toEqual({
      x: 1 * (SMART_NODE_DEFAULT_WIDTH.shot + SHOT_SYNC_GUTTER),
      y: 0,
    });
  });

  it('stacks a second shot in the SAME scene vertically below the first', () => {
    const shots = [shot('s1'), shot('s2')];
    const result = reconcileShotNodes([], [SCENE_1], shots);
    expect(result.nodesToAdd[0].position).toEqual({ x: 0, y: 0 });
    expect(result.nodesToAdd[1].position).toEqual({
      x: 0,
      y: 1 * (SHOT_SYNC_NODE_HEIGHT_ESTIMATE + SHOT_SYNC_GUTTER),
    });
    expect(result.nodesToAdd[1].data.shot_label).toBe('1B');
  });

  it('partial onboarding: only the missing shot produces a new node; an already-identical bound node produces no patch', () => {
    const s1 = shot('s1');
    const existingNode = boundNode('node-s1', 's1', {
      shot_label: '1A',
      shot_type: 'MEDIUM',
      camera_angle: 'EYE_LEVEL',
      camera_movement: 'STATIC',
      focal_length: '35mm',
      description: 'Base description',
      image_url: null,
      shot_status: 'empty',
      scene_id: SCENE_1.id,
    });
    const s2 = shot('s2');

    const result = reconcileShotNodes([existingNode], [SCENE_1], [s1, s2]);

    expect(result.nodesToAdd).toHaveLength(1);
    expect(result.nodesToAdd[0].data.shot_id).toBe('s2');
    expect(result.nodesToPatch).toEqual([]);
    expect(result.nodesToMarkStale).toEqual([]);
  });

  it('deleted shot: the bound node with no matching shot is reported stale, not touched otherwise', () => {
    const survivor = shot('s1');
    const existingNodes = [
      boundNode('node-s1', 's1'),
      boundNode('node-gone', 'deleted-shot-id'),
    ];

    const result = reconcileShotNodes(existingNodes, [SCENE_1], [survivor]);

    expect(result.nodesToMarkStale).toEqual(['node-gone']);
    // The surviving shot still gets diffed normally (it has drift here since
    // `boundNode` defaults to null mirror fields) — but the point of this
    // test is the STALE node never appears in add/patch.
    expect(result.nodesToAdd).toEqual([]);
    expect(result.nodesToPatch.some((p) => p.id === 'node-gone')).toBe(false);
  });

  it('mirror drift: only the fields that actually changed appear in the patch', () => {
    const driftedShot = shot('s1', { shot_type: 'CLOSE_UP', description: 'New description' });
    const existingNode = boundNode('node-s1', 's1', {
      shot_label: '1A',
      shot_type: 'MEDIUM', // stale — shot now says CLOSE_UP
      camera_angle: 'EYE_LEVEL', // matches
      camera_movement: 'STATIC', // matches
      focal_length: '35mm', // matches
      description: 'Old description', // stale — shot now says New description
      image_url: null,
      shot_status: 'empty',
      scene_id: SCENE_1.id,
    });

    const result = reconcileShotNodes([existingNode], [SCENE_1], [driftedShot]);

    expect(result.nodesToPatch).toHaveLength(1);
    expect(result.nodesToPatch[0].id).toBe('node-s1');
    expect(result.nodesToPatch[0].data).toEqual({
      shot_type: 'CLOSE_UP',
      description: 'New description',
    });
  });

  it('orphan nodes are never touched: unbound draft + a non-shot node produce no add/patch/stale', () => {
    const nonShotNode: SmartNode = { id: 'prompt-1', type: 'prompt', position: { x: 0, y: 0 }, data: {} };
    const existing = [unboundNode('draft-1'), nonShotNode];

    const result = reconcileShotNodes(existing, [SCENE_1], [shot('s1')]);

    // The one real shot still onboards normally...
    expect(result.nodesToAdd).toHaveLength(1);
    // ...but neither orphan node appears anywhere in the result.
    expect(result.nodesToMarkStale).toEqual([]);
    expect(result.nodesToPatch.some((p) => p.id === 'draft-1' || p.id === 'prompt-1')).toBe(
      false,
    );
  });

  it('preserves a Snowflake id past 2^53 exactly — no Number() precision loss', () => {
    const bigId = '9007199254740997'; // 2^53 + 5, unsafe as a JS number
    const result = reconcileShotNodes([], [SCENE_1], [shot(bigId)]);

    expect(result.nodesToAdd).toHaveLength(1);
    expect(result.nodesToAdd[0].data.shot_id).toBe(bigId);
    expect(result.nodesToAdd[0].id).toBe(`shot-${bigId}`);
    // String equality, never Number(bigId) — a numeric round-trip would
    // silently collapse this to 9007199254740996 or ...998.
    expect(typeof result.nodesToAdd[0].data.shot_id).toBe('string');
  });

  it('in-flight generation guard: a generating node keeps its own image_url/shot_status/gen_task_id even when script_shots already moved on', () => {
    const settledShot = shot('s1', {
      shot_type: 'CLOSE_UP', // drifted — should still patch (not generation-owned)
      status: 'done',
      image_url: 'https://cdn/settled.png',
    });
    const inFlightNode = boundNode('node-s1', 's1', {
      shot_label: '1A',
      shot_type: 'MEDIUM', // stale, non-generation field — must still update
      camera_angle: 'EYE_LEVEL',
      camera_movement: 'STATIC',
      focal_length: '35mm',
      description: 'Base description',
      scene_id: SCENE_1.id,
      image_url: null, // local: dispatch in flight, no frame yet
      shot_status: 'generating',
      gen_task_id: 'task-abc',
    });

    const result = reconcileShotNodes([inFlightNode], [SCENE_1], [settledShot]);

    expect(result.nodesToPatch).toHaveLength(1);
    const patch = result.nodesToPatch[0].data;
    expect(patch).toEqual({ shot_type: 'CLOSE_UP' });
    expect(patch).not.toHaveProperty('image_url');
    expect(patch).not.toHaveProperty('shot_status');
    expect(patch).not.toHaveProperty('gen_task_id');
  });

  it('a settled (non-generating) node DOES pick up image_url/shot_status drift', () => {
    const settledShot = shot('s1', { status: 'done', image_url: 'https://cdn/done.png' });
    const settledNode = boundNode('node-s1', 's1', {
      shot_label: '1A',
      shot_type: 'MEDIUM',
      camera_angle: 'EYE_LEVEL',
      camera_movement: 'STATIC',
      focal_length: '35mm',
      description: 'Base description',
      scene_id: SCENE_1.id,
      image_url: null,
      shot_status: 'empty',
      gen_task_id: null,
    });

    const result = reconcileShotNodes([settledNode], [SCENE_1], [settledShot]);

    expect(result.nodesToPatch).toEqual([
      { id: 'node-s1', data: { image_url: 'https://cdn/done.png', shot_status: 'done' } },
    ]);
  });

  it('never repositions an existing bound node — patch entries never carry a position', () => {
    const existingNode = boundNode('node-s1', 's1', { shot_type: 'CLOSE_UP' }); // force a drift patch
    const result = reconcileShotNodes([existingNode], [SCENE_1], [shot('s1')]);
    expect(result.nodesToPatch).toHaveLength(1);
    expect(result.nodesToPatch[0]).not.toHaveProperty('position');
    expect(result.nodesToPatch[0].data).not.toHaveProperty('position');
  });

  // Final review Important 3 — stale reverse transition: a node previously
  // flagged `stale: true` (its shot vanished from `script_shots`) whose shot
  // comes BACK (e.g. an Undo of the deletion that caused the stale flag)
  // must have `stale: false` patched back in on the pass that sees it, or
  // the card stays permanently grey and excluded from doSave's payload even
  // though the shot is alive again.
  describe('stale reverse transition (final review Important 3)', () => {
    it('a stale node whose shot is present again gets patched back to stale: false', () => {
      const revivedShot = shot('s1');
      const staleNode = boundNode('node-s1', 's1', {
        shot_label: '1A',
        shot_type: 'MEDIUM',
        camera_angle: 'EYE_LEVEL',
        camera_movement: 'STATIC',
        focal_length: '35mm',
        description: 'Base description',
        image_url: null,
        shot_status: 'empty',
        scene_id: SCENE_1.id,
        stale: true,
      });

      const result = reconcileShotNodes([staleNode], [SCENE_1], [revivedShot]);

      expect(result.nodesToMarkStale).toEqual([]);
      expect(result.nodesToPatch).toEqual([{ id: 'node-s1', data: { stale: false } }]);
    });

    it('a revived stale node with OTHER mirror drift too gets both in one patch', () => {
      const revivedShot = shot('s1', { shot_type: 'CLOSE_UP' });
      const staleNode = boundNode('node-s1', 's1', {
        shot_label: '1A',
        shot_type: 'MEDIUM', // drifted
        camera_angle: 'EYE_LEVEL',
        camera_movement: 'STATIC',
        focal_length: '35mm',
        description: 'Base description',
        image_url: null,
        shot_status: 'empty',
        scene_id: SCENE_1.id,
        stale: true,
      });

      const result = reconcileShotNodes([staleNode], [SCENE_1], [revivedShot]);

      expect(result.nodesToPatch).toEqual([
        { id: 'node-s1', data: { shot_type: 'CLOSE_UP', stale: false } },
      ]);
    });

    it('a node with no stale field never gets a no-op stale patch (healthy node stays untouched)', () => {
      // Regression guard for the "don't full-patch every node" caveat: a
      // node that was NEVER stale (data.stale is undefined, not false) must
      // not have `stale` appear in its patch just because reconcile ran.
      const s1 = shot('s1');
      const healthyNode = boundNode('node-s1', 's1', {
        shot_label: '1A',
        shot_type: 'MEDIUM',
        camera_angle: 'EYE_LEVEL',
        camera_movement: 'STATIC',
        focal_length: '35mm',
        description: 'Base description',
        image_url: null,
        shot_status: 'empty',
        scene_id: SCENE_1.id,
        // stale intentionally omitted (undefined).
      });

      const result = reconcileShotNodes([healthyNode], [SCENE_1], [s1]);

      expect(result.nodesToPatch).toEqual([]);
    });

    it('a node still genuinely gone stays in nodesToMarkStale, not nodesToPatch — reverse transition only fires when the shot is found', () => {
      const survivor = shot('s1');
      const staleNode = boundNode('node-gone', 'deleted-shot-id', { stale: true });

      const result = reconcileShotNodes([staleNode], [SCENE_1], [survivor]);

      expect(result.nodesToMarkStale).toEqual(['node-gone']);
      expect(result.nodesToPatch.some((p) => p.id === 'node-gone')).toBe(false);
    });
  });
});
