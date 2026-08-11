/**
 * shotSync — pure reconciliation logic between a storyboard canvas's shot
 * nodes and the `script_shots` rows they mirror (shot-nodes-on-canvas epic,
 * Task 4).
 *
 * Deliberately has ZERO imports from the store or any service layer — it
 * takes plain data in, returns plain data out, and the impure "fetch scenes/
 * shots, apply the result to the store" glue lives in the mount-orchestration
 * call site (`CanvasPage.tsx`) instead. This keeps the diffing logic — the
 * part with real edge cases (drift, deletion, in-flight generation) —
 * exhaustively unit-testable without mocking React Flow or Zustand.
 *
 * New node ids are deterministic (`shot-${shot.id}`) rather than going
 * through `factories.ts`'s counter+random-suffix scheme: that scheme exists
 * for hand-placed composer nodes where two nodes can otherwise collide, but
 * a reconciled node's identity IS the shot id, so reusing it as the node id
 * is both simpler and idempotent (calling reconcile twice against the same
 * input never produces two different ids for the same shot).
 */

import type { ShotNodeData, SmartNode } from './types';
import { SMART_NODE_DEFAULT_WIDTH } from './types';

export interface ShotSyncResult {
  /** Shots present in `script_shots` with no matching bound node yet —
   *  materialized as new nodes at a default grid position. */
  nodesToAdd: SmartNode<ShotNodeData>[];
  /** Node ids whose `shot_id` no longer resolves to a `script_shots` row
   *  (deleted elsewhere — shot list, another tab). The consumer sets
   *  `data.stale = true` on these; `reconcileShotNodes` itself never
   *  mutates a node, only reports which ones need it. */
  nodesToMarkStale: string[];
  /** Existing bound nodes whose mirror fields drifted from `script_shots`
   *  — id + the partial `data` patch to merge in (only the fields that
   *  actually changed, never a full-object replace). */
  nodesToPatch: Array<{ id: string; data: Partial<ShotNodeData> }>;
}

/** Horizontal gap between scene columns and vertical gap between stacked
 *  shot nodes within a column — same gutter reused for both axes. */
export const SHOT_SYNC_GUTTER = 40;

/**
 * Estimated bound-shot-node render height, for the default vertical stack
 * only. React Flow nodes auto-size from content (this codebase never tracks
 * a real per-type height the way `SMART_NODE_DEFAULT_WIDTH` tracks width),
 * so this is a deliberate eyeball estimate — header row + 3 vocabulary chips
 * + focal-length chip (wraps to ~2 rows at the 280px node width) + a 2-row
 * description textarea + a 16:9 frame slot + the Generate button. Only used
 * to space NEWLY added nodes; never reconciled against actual rendered
 * height, and never applied to a node that already has a position.
 */
export const SHOT_SYNC_NODE_HEIGHT_ESTIMATE = 360;

/** `readStr(shot.shot_type)`-style narrowing — `shots` entries come in
 *  typed as `[k: string]: unknown` (server JSON pass-through), so every
 *  field read narrows defensively instead of trusting the shape. */
function readStr(v: unknown): string | null {
  return typeof v === 'string' ? v : null;
}

/**
 * "1A" shot-code convention — mirrors `StoryboardView.tsx`'s
 * `` `${sceneIdx + 1}${String.fromCharCode(65 + shotIdx)}` `` so the canvas
 * never shows a different numbering scheme than the storyboard rail for the
 * same shot. `sceneNo` is the caller-computed 1-based scene rank (same
 * source as the rail); `shotIndexInScene` is 0-based, wrapping past Z the
 * same (unhandled, matching precedent) way the rail does for >26 shots in
 * one scene.
 */
export function computeShotLabel(sceneNo: number, shotIndexInScene: number): string {
  const letter = String.fromCharCode(65 + (shotIndexInScene % 26));
  return `${sceneNo}${letter}`;
}

function isInFlight(data: ShotNodeData): boolean {
  return data.shot_status === 'generating' && !!data.gen_task_id;
}

function buildNewShotNode(
  shot: { id: string; scene_id: string; [k: string]: unknown },
  label: string,
  position: { x: number; y: number },
): SmartNode<ShotNodeData> {
  return {
    id: `shot-${shot.id}`,
    type: 'shot',
    position,
    data: {
      // Legacy hand-placed-draft fields — a reconciled node is never a
      // draft, so these start empty (ShotNodeView's bound render never
      // shows them anyway).
      title: '',
      reference_resource_ids: [],
      notes: '',
      shot_id: shot.id,
      shot_label: label,
      shot_type: readStr(shot.shot_type),
      camera_angle: readStr(shot.camera_angle),
      camera_movement: readStr(shot.camera_movement),
      focal_length: readStr(shot.focal_length),
      description: readStr(shot.description),
      image_url: readStr(shot.image_url),
      shot_status: readStr(shot.status),
      gen_task_id: null,
      scene_id: shot.scene_id,
    },
  };
}

/**
 * Diff a canvas's existing nodes against the current `script_shots` state
 * for its episode and report the three edits needed to bring the canvas
 * in sync. Never mutates `existing` — every result is a plain description
 * the caller applies through the store's own mutation methods.
 *
 * Rules (brief + Task 3 fix-round-2 in-flight-generation carry-over):
 *  - A shot with no matching bound node → `nodesToAdd` (new node, default
 *    grid position: column = scene index in `scenes`, row = shot index
 *    within its scene, both by the ORDER the caller passed them in).
 *  - A bound node (`type==='shot'` and `data.shot_id` set) whose shot_id
 *    isn't in `shots` anymore → `nodesToMarkStale` (shot deleted upstream).
 *  - A bound node whose mirror fields (label/params/description/image_url/
 *    status/scene_id) differ from the current `script_shots` row →
 *    `nodesToPatch` with ONLY the changed fields.
 *  - Unbound draft nodes (`shot_id === null`) and every non-shot node type
 *    are never inspected or touched — reconcile only ever reads/writes
 *    nodes it itself indexed into `boundNodesByShotId`.
 *  - A node mid-generation (`shot_status==='generating'` AND `gen_task_id`
 *    set) never gets `image_url`/`shot_status` in its patch, even if
 *    `script_shots` already shows a newer value — the shot node's own
 *    dispatch→poll chain (ShotNodeView's `handleGenerate`) owns settling
 *    those two fields, and `gen_task_id` is a frontend-only field that was
 *    never part of the mirror set to begin with (script_shots has no such
 *    column) so it's never a candidate for the patch regardless.
 *  - All ids are compared as strings throughout — no `Number()`/`parseInt`
 *    anywhere, so a Snowflake id past 2^53 never loses precision.
 */
export function reconcileShotNodes(
  existing: SmartNode[],
  scenes: Array<{ id: string; sceneNo: number }>,
  shots: Array<{ id: string; scene_id: string; [k: string]: unknown }>,
): ShotSyncResult {
  const nodesToAdd: SmartNode<ShotNodeData>[] = [];
  const nodesToMarkStale: string[] = [];
  const nodesToPatch: Array<{ id: string; data: Partial<ShotNodeData> }> = [];

  // Index existing BOUND shot nodes by shot_id. Everything else (unbound
  // drafts, every other node type) is deliberately left out of this map —
  // that's the entire mechanism behind "orphan nodes are untouched".
  const boundNodesByShotId = new Map<string, SmartNode<ShotNodeData>>();
  for (const node of existing) {
    if (node.type !== 'shot') continue;
    const data = node.data as ShotNodeData | undefined;
    const shotId = data?.shot_id;
    if (typeof shotId === 'string' && shotId.length > 0) {
      boundNodesByShotId.set(shotId, node as SmartNode<ShotNodeData>);
    }
  }

  const sceneIndexById = new Map<string, number>();
  const sceneNoById = new Map<string, number>();
  scenes.forEach((scene, idx) => {
    sceneIndexById.set(scene.id, idx);
    sceneNoById.set(scene.id, scene.sceneNo);
  });

  // Per-scene shot index, computed from the ORDER `shots` arrives in (the
  // caller concatenates `listShots(sceneId)` per scene, already
  // sort_order-sorted server-side) — never re-sorted here.
  const shotIndexInScene = new Map<string, number>();
  const perSceneCounter = new Map<string, number>();
  for (const shot of shots) {
    const n = perSceneCounter.get(shot.scene_id) ?? 0;
    shotIndexInScene.set(shot.id, n);
    perSceneCounter.set(shot.scene_id, n + 1);
  }

  const seenShotIds = new Set<string>();
  for (const shot of shots) {
    seenShotIds.add(shot.id);
    // Defensive fallback (not expected in practice — every shot's scene_id
    // should be one of `scenes`): an unresolvable scene lands in a trailing
    // extra column rather than colliding with column 0.
    const sceneIdx = sceneIndexById.get(shot.scene_id) ?? scenes.length;
    const sceneNo = sceneNoById.get(shot.scene_id) ?? sceneIdx + 1;
    const idxInScene = shotIndexInScene.get(shot.id) ?? 0;
    const label = computeShotLabel(sceneNo, idxInScene);

    const node = boundNodesByShotId.get(shot.id);
    if (!node) {
      nodesToAdd.push(
        buildNewShotNode(shot, label, {
          x: sceneIdx * (SMART_NODE_DEFAULT_WIDTH.shot + SHOT_SYNC_GUTTER),
          y: idxInScene * (SHOT_SYNC_NODE_HEIGHT_ESTIMATE + SHOT_SYNC_GUTTER),
        }),
      );
      continue;
    }

    // Existing bound node — position is NEVER touched here (only newly
    // added nodes get positioned); only a `data` mirror-field diff.
    const data = node.data;
    const inFlight = isInFlight(data);
    const mirror: Partial<Record<keyof ShotNodeData, unknown>> = {
      shot_label: label,
      shot_type: readStr(shot.shot_type),
      camera_angle: readStr(shot.camera_angle),
      camera_movement: readStr(shot.camera_movement),
      focal_length: readStr(shot.focal_length),
      description: readStr(shot.description),
      scene_id: shot.scene_id,
      // In-flight guard (Task 3 fix-round-2 carry-over): a generation in
      // progress owns these two fields until it settles — reconcile must
      // not race it with a possibly-stale-in-the-other-direction read.
      ...(inFlight
        ? {}
        : { image_url: readStr(shot.image_url), shot_status: readStr(shot.status) }),
    };

    const patch: Partial<ShotNodeData> = {};
    for (const key of Object.keys(mirror) as Array<keyof ShotNodeData>) {
      const next = mirror[key];
      if (data[key] !== next) {
        (patch as Record<string, unknown>)[key] = next;
      }
    }
    if (Object.keys(patch).length > 0) {
      nodesToPatch.push({ id: node.id, data: patch });
    }
  }

  // Shots that vanished from this load's list — every bound node whose
  // shot_id we indexed but never saw in `shots` gets flagged. Idempotent:
  // re-running reconcile against an already-stale node reports it again,
  // and re-applying `data.stale = true` is a no-op merge.
  for (const [shotId, node] of boundNodesByShotId) {
    if (!seenShotIds.has(shotId)) {
      nodesToMarkStale.push(node.id);
    }
  }

  return { nodesToAdd, nodesToMarkStale, nodesToPatch };
}
