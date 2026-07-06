/**
 * Scene/chapter → flow-graph projection (spec v3 §3.3, Phase B Task 1).
 *
 * Pure function: projects v2 `SceneDoc[]` + `ScriptChapter[]` onto the node/edge
 * shapes consumed by @xyflow/react. This is a CLEAN-ROOM mapper — it deliberately
 * does NOT import from `stores/scriptCanvasStore.ts` (the old node editor). The
 * chapter node data shape below is COPIED from that store's `ChapterNodeData`
 * (scriptCanvasStore.ts:15-25) and its parent→child edge semantics mirror
 * `mapChaptersToEdges` (scriptCanvasStore.ts:51-59), so the two stay in sync
 * without coupling this v2 surface to the legacy store.
 *
 * All ids are strings end-to-end (Snowflake bigint keys — see editor/types.ts).
 * Inputs are never mutated.
 */

import type { SceneDoc } from '../types';
import type { ScriptChapter } from '../../types';

/** Longest summary line rendered on a scene node before truncation. */
const SUMMARY_MAX = 60;
/** Vertical step between auto-laid scenes within a column. */
const SCENE_Y_STEP = 140;
/** Horizontal offset of a chapter's scene column from the chapter node. */
const SCENE_X_OFFSET = 320;
/** Horizontal step between chapter columns when a chapter has no position. */
const CHAPTER_FALLBACK_X_STEP = 360;

/**
 * Chapter node data — shape mirrored from `ChapterNodeData` in
 * `stores/scriptCanvasStore.ts:15-25` (copied, not imported, per the clean
 * boundary above). Keep the field set aligned if the store's shape changes.
 */
export interface ChapterNodeData {
  title: string;
  summary: string;
  content: string;
  chapterNumber: number;
  branchLabel?: string;
  branchType?: 'condition' | 'choice';
  isExpanded?: boolean;
  contentJson: Record<string, unknown> | null;
  [key: string]: unknown;
}

/** Scene node data: the source doc plus a pre-truncated first-action summary. */
export interface SceneNodeData {
  scene: SceneDoc;
  summary: string;
}

export interface FlowNode {
  id: string;
  type: 'chapterNode' | 'sceneNode';
  position: { x: number; y: number };
  data: unknown;
}

export interface FlowEdge {
  id: string;
  source: string;
  target: string;
}

export interface FlowGraph {
  nodes: FlowNode[];
  edges: FlowEdge[];
}

/** First non-empty `action` element text, truncated to 60 chars with ellipsis. */
export function sceneSummary(scene: SceneDoc): string {
  const action = scene.elements.find(
    (el) => el.type === 'action' && el.text.trim().length > 0,
  );
  if (!action) return '';
  const text = action.text.trim();
  return text.length > SUMMARY_MAX ? text.slice(0, SUMMARY_MAX) + '…' : text;
}

function chapterNodeData(ch: ScriptChapter): ChapterNodeData {
  return {
    title: ch.title ?? '',
    summary: ch.summary ?? '',
    content: ch.content ?? '',
    chapterNumber: ch.chapter_number ?? 0,
    branchLabel: ch.branch_label,
    branchType: ch.branch_type,
    contentJson: null,
  };
}

/**
 * Project scenes + chapters onto flow nodes/edges.
 *
 * - chapter node id = `ch-<id>`, type 'chapterNode', position from the row.
 * - scene node id = `sc-<id>`, type 'sceneNode'. Position uses the scene's
 *   persisted `position_x/y` when BOTH are non-null; otherwise scenes are
 *   auto-laid in a per-chapter column (y = index * 140 within the group;
 *   x = chapter.position_x + 320, or fallbackColumn * 360 + 320 when the
 *   chapter row is absent; chapterless scenes column from x=0).
 * - edges: `ch-X → sc-Y` for each scene's chapter_id, plus chapter→chapter
 *   edges from `parent_chapter_id`.
 */
export function mapToFlow(scenes: SceneDoc[], chapters: ScriptChapter[]): FlowGraph {
  const chapterById = new Map<string, ScriptChapter>(
    chapters.map((ch) => [String(ch.id), ch]),
  );

  const chapterNodes: FlowNode[] = chapters.map((ch) => ({
    id: `ch-${ch.id}`,
    type: 'chapterNode' as const,
    position: { x: ch.position_x, y: ch.position_y },
    data: chapterNodeData(ch),
  }));

  // Group scene indices by chapter key so auto-layout can place a whole column.
  // Key `''` collects chapterless (orphan) scenes.
  const groupIndex = new Map<string, number>();
  // Order of first appearance for chapter groups without a resolvable position.
  const fallbackColumnOrder = new Map<string, number>();

  const sceneNodes: FlowNode[] = scenes.map((scene) => {
    const key = scene.chapter_id == null ? '' : String(scene.chapter_id);
    const indexInGroup = groupIndex.get(key) ?? 0;
    groupIndex.set(key, indexInGroup + 1);

    const hasPersisted = scene.position_x != null && scene.position_y != null;
    let position: { x: number; y: number };
    if (hasPersisted) {
      position = { x: scene.position_x as number, y: scene.position_y as number };
    } else if (key === '') {
      // Chapterless column anchored at origin.
      position = { x: 0, y: indexInGroup * SCENE_Y_STEP };
    } else {
      const chapter = chapterById.get(key);
      let baseX: number;
      if (chapter) {
        baseX = chapter.position_x;
      } else {
        if (!fallbackColumnOrder.has(key)) {
          fallbackColumnOrder.set(key, fallbackColumnOrder.size);
        }
        baseX = (fallbackColumnOrder.get(key) as number) * CHAPTER_FALLBACK_X_STEP;
      }
      position = { x: baseX + SCENE_X_OFFSET, y: indexInGroup * SCENE_Y_STEP };
    }

    return {
      id: `sc-${scene.id}`,
      type: 'sceneNode' as const,
      position,
      data: { scene, summary: sceneSummary(scene) } satisfies SceneNodeData,
    };
  });

  const sceneEdges: FlowEdge[] = scenes
    .filter((scene) => scene.chapter_id != null)
    .map((scene) => ({
      id: `edge-ch-${scene.chapter_id}-sc-${scene.id}`,
      source: `ch-${scene.chapter_id}`,
      target: `sc-${scene.id}`,
    }));

  // Chapter→chapter DAG — mirrors mapChaptersToEdges (scriptCanvasStore.ts:51-59),
  // adjusted to the `ch-` id scheme used here.
  const chapterEdges: FlowEdge[] = chapters
    .filter((ch) => ch.parent_chapter_id)
    .map((ch) => ({
      id: `edge-ch-${ch.parent_chapter_id}-ch-${ch.id}`,
      source: `ch-${ch.parent_chapter_id}`,
      target: `ch-${ch.id}`,
    }));

  return {
    nodes: [...chapterNodes, ...sceneNodes],
    edges: [...sceneEdges, ...chapterEdges],
  };
}
