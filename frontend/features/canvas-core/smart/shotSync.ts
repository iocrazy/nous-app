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
  /**
   * Node ids that appear MORE THAN ONCE among `existing` (2026-08-12
   * production incident self-heal): the numeric-shot-id regression made
   * every mounted reconcile re-add its whole shot set — rows accumulated
   * dozens of copies of each deterministic `shot-{id}` node, and React
   * Flow renders a duplicated id permanently `visibility:hidden` (blank
   * canvas). Each offending id is listed ONCE; the consumer collapses the
   * copies down to the FIRST occurrence (`dedupeNodesById` in the store)
   * BEFORE applying adds/patches, then persists the healed set through the
   * normal save path — never a manual SQL fixup.
   */
  nodeIdsToDedupe: string[];
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
 * Id-shaped value → canonical string, or null. THE fix for the 2026-08-12
 * production incident: the shots REST router (unlike canvases/scenes) hands
 * bigint ids back as JSON *numbers*, and this module's original
 * `typeof shotId === 'string'` narrowing silently dropped every numeric id
 * from `boundNodesByShotId` — so each reconcile re-added its entire shot set
 * (duplicate node ids → React Flow keeps them all `visibility:hidden` →
 * blank canvas). `sceneService.toShot` now stringifies at the fetch
 * boundary, but rows PERSISTED during the regression carry numeric
 * `data.shot_id`/`data.scene_id` forever — so this module must normalize on
 * its own, or those rows would keep re-adding after the boundary fix.
 * Snowflake ids in this app are 53-bit (mig 050) so Number→String is exact;
 * anything non-id-shaped (objects, NaN, empty string) stays null.
 */
function readId(v: unknown): string | null {
  if (typeof v === 'string') return v.length > 0 ? v : null;
  if (typeof v === 'number' && Number.isFinite(v)) return String(v);
  return null;
}

/**
 * "1A" shot-code convention — originally mirrored the pre-redesign
 * `frontend/editor/storyboard/StoryboardView.tsx`'s letter-suffix formula so
 * the canvas and the rail agreed on a shot's code. That file (and its
 * `ShotCard.tsx`) retired in Task 6 (shot-nodes-on-canvas epic, #1797); the
 * current rail — `EpisodeSceneBoard.tsx`'s scene columns — displays its own
 * `SHOT {scene}-{shot}` numeric badge instead (`projects.sceneBoard.
 * shotBadge` i18n key), so this label is now canvas-shot-node-only, no
 * longer required to match a second live surface. `sceneNo` is the
 * caller-computed 1-based scene rank; `shotIndexInScene` is 0-based,
 * wrapping past Z unhandled (kept from the original formula, never hit in
 * practice).
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
    // `readId` (not raw interpolation): a numeric id would interpolate to
    // the same string anyway, but routing through the normalizer keeps the
    // node id and `data.shot_id` provably the same canonical value.
    id: `shot-${readId(shot.id)}`,
    type: 'shot',
    position,
    data: {
      // Legacy hand-placed-draft fields — a reconciled node is never a
      // draft, so these start empty (ShotNodeView's bound render never
      // shows them anyway).
      title: '',
      reference_resource_ids: [],
      notes: '',
      shot_id: readId(shot.id) as string,
      shot_label: label,
      shot_type: readStr(shot.shot_type),
      camera_angle: readStr(shot.camera_angle),
      camera_movement: readStr(shot.camera_movement),
      focal_length: readStr(shot.focal_length),
      description: readStr(shot.description),
      image_url: readStr(shot.image_url),
      shot_status: readStr(shot.status),
      gen_task_id: null,
      scene_id: readId(shot.scene_id) as string,
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

  // Duplicate-node-id sweep over ALL existing nodes (2026-08-12 self-heal —
  // see `ShotSyncResult.nodeIdsToDedupe`). Runs before/independently of the
  // shot indexing below so a poisoned row heals even for ids the current
  // shot list no longer contains.
  const idCounts = new Map<string, number>();
  for (const node of existing) {
    if (typeof node.id !== 'string') continue;
    idCounts.set(node.id, (idCounts.get(node.id) ?? 0) + 1);
  }
  const nodeIdsToDedupe = [...idCounts.entries()]
    .filter(([, count]) => count > 1)
    .map(([id]) => id);

  // Index existing BOUND shot nodes by shot_id. Everything else (unbound
  // drafts, every other node type) is deliberately left out of this map —
  // that's the entire mechanism behind "orphan nodes are untouched".
  // `readId` (not a `typeof === 'string'` narrow): rows persisted during
  // the numeric-shot-id regression carry `data.shot_id` as a number — they
  // MUST still index here or every reconcile would re-add their shots
  // (the exact production failure this narrowing originally caused).
  const boundNodesByShotId = new Map<string, SmartNode<ShotNodeData>>();
  for (const node of existing) {
    if (node.type !== 'shot') continue;
    const data = node.data as ShotNodeData | undefined;
    const shotId = readId(data?.shot_id);
    if (shotId !== null && !boundNodesByShotId.has(shotId)) {
      // First occurrence wins — the same survivor `dedupeNodesById` keeps.
      boundNodesByShotId.set(shotId, node as SmartNode<ShotNodeData>);
    }
  }

  const sceneIndexById = new Map<string, number>();
  const sceneNoById = new Map<string, number>();
  scenes.forEach((scene, idx) => {
    const key = readId(scene.id);
    if (key === null) return;
    sceneIndexById.set(key, idx);
    sceneNoById.set(key, scene.sceneNo);
  });

  // Per-scene shot index, computed from the ORDER `shots` arrives in (the
  // caller concatenates `listShots(sceneId)` per scene, already
  // sort_order-sorted server-side) — never re-sorted here. All keys go
  // through `readId` so a numeric `scene_id`/`id` (see `readId`'s doc
  // comment) still buckets/joins correctly against the string-keyed maps.
  const shotIndexInScene = new Map<string, number>();
  const perSceneCounter = new Map<string, number>();
  for (const shot of shots) {
    const sceneKey = readId(shot.scene_id) ?? '';
    const shotKey = readId(shot.id);
    if (shotKey === null) continue;
    const n = perSceneCounter.get(sceneKey) ?? 0;
    shotIndexInScene.set(shotKey, n);
    perSceneCounter.set(sceneKey, n + 1);
  }

  const seenShotIds = new Set<string>();
  for (const shot of shots) {
    const shotKey = readId(shot.id);
    if (shotKey === null) continue;
    const sceneKey = readId(shot.scene_id) ?? '';
    seenShotIds.add(shotKey);
    // Defensive fallback (not expected in practice — every shot's scene_id
    // should be one of `scenes`): an unresolvable scene lands in a trailing
    // extra column rather than colliding with column 0.
    const sceneIdx = sceneIndexById.get(sceneKey) ?? scenes.length;
    const sceneNo = sceneNoById.get(sceneKey) ?? sceneIdx + 1;
    const idxInScene = shotIndexInScene.get(shotKey) ?? 0;
    const label = computeShotLabel(sceneNo, idxInScene);

    const node = boundNodesByShotId.get(shotKey);
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
      scene_id: sceneKey,
      // Type-normalization patch for regression-era rows: a persisted
      // NUMERIC `shot_id` drifts to its canonical string form here (the
      // value is identical, only the JSON type changes), so healed rows
      // converge to strings end-to-end after one reconcile+save cycle.
      shot_id: shotKey,
      // In-flight guard (Task 3 fix-round-2 carry-over): a generation in
      // progress owns these two fields until it settles — reconcile must
      // not race it with a possibly-stale-in-the-other-direction read.
      ...(inFlight
        ? {}
        : { image_url: readStr(shot.image_url), shot_status: readStr(shot.status) }),
      // Reverse transition (final review Important 3): the shot survived
      // this pass (it's in `shots`, we're inside this loop at all) — if the
      // node is still carrying a PREVIOUS pass's `stale: true` (e.g. an
      // agent-deleted shot's node went grey, then an Undo restored the
      // `script_shots` row), clear it here so the card comes back editable
      // and doSave's stale-filter stops excluding it. Only written when the
      // node actually has `stale === true` — never turns an untouched
      // node's absent `stale` field into an explicit `false`, which would
      // make every reconcile pass emit a no-op-shaped patch for every node.
      ...(data.stale === true ? { stale: false } : {}),
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

  return { nodesToAdd, nodesToMarkStale, nodesToPatch, nodeIdsToDedupe };
}
