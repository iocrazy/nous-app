/**
 * NodesView — the scene/chapter flow projection surface (Phase B Task 2-3).
 *
 * A scene-mode adapter over the shared `CanvasEngine` (canvas-kit): it projects
 * the mapper's node/edge graph, and wires the scene-specific persistence.
 * Positions are debounced per lane (`sc-` → updateSceneMeta, `ch-` →
 * updateChapterPosition) through `useGroupDragPersist`; the engine owns the
 * feel (snap grid, box-select, alignment guides, minimap, fit-view / zoom).
 *
 * The edges are a read-only projection, so the engine runs with
 * `allowConnect={false}` (no connection handlers attached at all). Scene nodes
 * are draggable; a drag-stop persists via the engine's `onNodeDragStop` /
 * `onSelectionDragStop` hooks. Double-clicking a scene node jumps back to the
 * script view through `onOpenScene`. Chapter nodes carry an inline action bar
 * (Expand / Branch / Convert) — dispatched + polled through a context so the
 * node components don't thread callbacks through React Flow's node `data`.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  applyNodeChanges,
  type Edge,
  type Node,
  type NodeChange,
  type NodePositionChange,
} from '@xyflow/react';
import { useTranslation } from 'react-i18next';
import {
  mapToFlow,
  SCENE_NODE_WIDTH,
  CHAPTER_NODE_WIDTH,
  NODE_HEIGHT_FALLBACK,
} from './sceneNodeMapper';
import { SceneFlowNode } from './SceneFlowNode';
import { ChapterActionsNode, ChapterActionContext } from './ChapterActionsNode';
import { CanvasEngine } from '../../canvas-kit/CanvasEngine';
import { useGroupDragPersist } from '../../canvas-kit/useGroupDragPersist';
import { useConvertPoll } from '../useConvertPoll';
import { updateSceneMeta, updateChapterPosition, listShots } from '../sceneService';
import type { SceneDoc } from '../types';
import type { ScriptChapter } from '../../types';

/** Debounce window for persisting a scene's dragged coordinates. */
const DRAG_PERSIST_MS = 500;
/** Length of the `sc-` / `ch-` id prefixes the mapper emits. */
const ID_PREFIX_LEN = 3;

const NODE_TYPES = { sceneNode: SceneFlowNode, chapterNode: ChapterActionsNode };

/** Content equality for the sceneId → cover-url maps (identical size + pairs). */
function sameCovers(a: Map<string, string>, b: Map<string, string>): boolean {
  if (a.size !== b.size) return false;
  for (const [k, v] of a) if (b.get(k) !== v) return false;
  return true;
}

/** Node box for guide + snap math: measured size when known, else per-type fallback. */
function sceneNodeMeasure(node: Node): { width: number; height: number } {
  const fallbackWidth =
    node.type === 'chapterNode' ? CHAPTER_NODE_WIDTH : SCENE_NODE_WIDTH;
  return {
    width: node.measured?.width ?? fallbackWidth,
    height: node.measured?.height ?? NODE_HEIGHT_FALLBACK,
  };
}

export interface NodesViewProps {
  scenes: SceneDoc[];
  chapters: ScriptChapter[];
  onOpenScene: (sceneId: string) => void;
  /** Owning script — chapter actions dispatch against it. */
  scriptId: string;
  /** Refresh scenes+chapters after a chapter action settles. */
  onReload: () => void | Promise<void>;
  /** Switch the shell back to the Script view (shown in the empty state). */
  onBackToScript?: () => void;
}

export function NodesView({
  scenes,
  chapters,
  onOpenScene,
  scriptId,
  onReload,
  onBackToScript,
}: NodesViewProps) {
  const { t } = useTranslation();
  const [shotCovers, setShotCovers] = useState<Map<string, string>>(() => new Map());
  const graph = useMemo(
    () => mapToFlow(scenes, chapters, shotCovers),
    [scenes, chapters, shotCovers],
  );
  const [nodes, setNodes] = useState<Node[]>(() => graph.nodes as Node[]);
  const { startPoll } = useConvertPoll(scriptId);

  // Re-seed when the projection changes (scene added / removed / reparented).
  // Drag-in-progress positions are transient client state; a structural change
  // to the underlying scenes/chapters is the authoritative reset point.
  useEffect(() => {
    setNodes(graph.nodes as Node[]);
  }, [graph]);

  // Stable signature of the scene id set. The cover fetch keys off THIS, not the
  // `scenes` array identity, so a persist-then-reload (same ids, fresh array)
  // does not re-pull every scene's shots (F4). Ids are Snowflake numbers → no
  // commas, so a join is a safe set key.
  const sceneIdSig = useMemo(() => scenes.map((s) => String(s.id)).join(','), [scenes]);

  // Load each scene's first shot cover and build a sceneId → cover-url map the
  // mapper threads onto the cards. Best-effort: a scene whose shots fail to load
  // just shows no cover (console.error, no toast). The map is committed via a
  // content-diff (sameCovers): an unchanged result bails so the canvas never
  // re-seeds needlessly (zero regression), while a scene whose cover was removed
  // clears — the once-shown-then-emptied stale-cover case (F3).
  useEffect(() => {
    const ids = sceneIdSig ? sceneIdSig.split(',') : [];
    if (ids.length === 0) return;
    let cancelled = false;
    (async () => {
      const entries = await Promise.all(
        ids.map(async (sceneId): Promise<[string, string | null]> => {
          try {
            const shots = await listShots(sceneId);
            const withImage = shots.find((shot) => !!shot.image_url);
            return [sceneId, withImage?.image_url ?? null];
          } catch (err) {
            // Guard the log too, not just the setState below: a rejection
            // that lands after unmount would console.error into a torn-down
            // vitest worker ("Closing rpc while onUserConsoleLog was
            // pending") and fail CI as an unhandled teardown error (#1260
            // class).
            if (!cancelled) {
              console.error('[NodesView] failed to load shot covers for scene', sceneId, err);
            }
            return [sceneId, null];
          }
        }),
      );
      if (cancelled) return;
      const map = new Map<string, string>();
      for (const [sceneId, url] of entries) {
        if (url) map.set(sceneId, url);
      }
      setShotCovers((prev) => (sameCovers(prev, map) ? prev : map));
    })();
    return () => {
      cancelled = true;
    };
  }, [sceneIdSig]);

  // Latest node set, read synchronously by the group-drag branch to flush every
  // selected node without re-binding the callback on every position change.
  const nodesRef = useRef<Node[]>(nodes);
  useEffect(() => {
    nodesRef.current = nodes;
  }, [nodes]);

  // True between onNodeDragStart and onNodeDragStop. A pointer drag settles
  // through the drag-stop handlers; this flag lets onNodesChange tell a drag's
  // final position change (dragging===false, emitted just before drag-stop)
  // apart from a keyboard a11y move (also dragging===false, but no drag event).
  const pointerDraggingRef = useRef(false);

  // Debounce-persist every scene/chapter node through its own lane, keyed by the
  // `sc-`/`ch-` id prefix: scene → updateSceneMeta, chapter → updateChapterPosition.
  // The shared hook owns the per-id timer map + unmount cleanup.
  const persistPositions = useGroupDragPersist({
    lanes: {
      'sc-': (id, pos) => updateSceneMeta(id, { position_x: pos.x, position_y: pos.y }),
      'ch-': (id, pos) => updateChapterPosition(id, { position_x: pos.x, position_y: pos.y }),
    },
    debounceMs: DRAG_PERSIST_MS,
  });

  const onNodesChange = useCallback(
    (changes: NodeChange[]) => {
      setNodes((nds) => applyNodeChanges(changes, nds));
      // Persist xyflow's built-in a11y keyboard move (arrow keys): it emits a
      // `position` change with dragging===false and has no drag-stop event to
      // hang persistence on. Only settled (non-drag) moves of SELECTED scene
      // nodes qualify — mid-drag frames (dragging===true) and drag-end frames
      // (guarded by pointerDraggingRef) are handled by the drag-stop lane, so we
      // never double-write or re-add a custom nudge (F1).
      if (pointerDraggingRef.current) return;
      const byId = new Map(nodesRef.current.map((n) => [String(n.id), n]));
      const moved: Node[] = [];
      for (const c of changes) {
        if (c.type !== 'position') continue;
        const pos = c as NodePositionChange;
        if (pos.dragging || !pos.position) continue;
        const node = byId.get(String(pos.id));
        if (node?.selected && node.type === 'sceneNode') {
          moved.push({ ...node, position: pos.position });
        }
      }
      if (moved.length > 0) persistPositions(moved);
    },
    [persistPositions],
  );

  // Raise the drag flag before any position change reaches onNodesChange, so its
  // keyboard-move persistence never mistakes a drag frame for an arrow nudge.
  const onNodeDragStart = useCallback(() => {
    pointerDraggingRef.current = true;
  }, []);

  // A settled node drag. The engine hands us whether the drop was a group move
  // and any one-time alignment snap it computed; we own persistence.
  const onNodeDragStop = useCallback(
    (node: Node, ctx: { isGroupDrop: boolean; snappedPosition: { x: number; y: number } | null }) => {
      pointerDraggingRef.current = false;
      // Group drop: React Flow moved the whole selection — persist every selected
      // node through its own lane, not just this one.
      if (ctx.isGroupDrop) {
        persistPositions(nodesRef.current.filter((n) => n.selected));
        return;
      }
      // Solo drop: apply the engine's one-time alignment snap (wins over the 8px
      // grid) to local state, then persist the final coordinate.
      const finalPosition = ctx.snappedPosition ?? node.position;
      if (ctx.snappedPosition) {
        setNodes((nds) =>
          nds.map((n) =>
            String(n.id) === String(node.id) ? { ...n, position: finalPosition } : n,
          ),
        );
      }
      persistPositions([{ ...node, position: finalPosition }]);
    },
    [persistPositions],
  );

  const onSelectionDragStop = useCallback(
    (dragged: Node[]) => {
      pointerDraggingRef.current = false;
      persistPositions(dragged);
    },
    [persistPositions],
  );

  // Keyboard select-all / clear flips the `selected` flag across the controlled
  // node set. Arrow-key nudging is owned by xyflow's built-in a11y move (persisted
  // via onNodesChange); zoom / fit-view are owned by the engine's shortcut layer.
  const selectAll = useCallback(
    () => setNodes((nds) => nds.map((n) => (n.selected ? n : { ...n, selected: true }))),
    [],
  );
  const clearSelection = useCallback(
    () => setNodes((nds) => nds.map((n) => (n.selected ? { ...n, selected: false } : n))),
    [],
  );

  const onNodeDoubleClick = useCallback(
    (_evt: React.MouseEvent, node: Node) => {
      if (node.type !== 'sceneNode') return;
      onOpenScene(node.id.slice(ID_PREFIX_LEN));
    },
    [onOpenScene],
  );

  const noopEdgesChange = useCallback(() => {}, []);

  const actionContext = useMemo(
    () => ({ scriptId, chapters, startPoll, onReload }),
    [scriptId, chapters, startPoll, onReload],
  );

  const minimapConfig = useMemo(
    () => ({
      nodeColor: (node: Node) =>
        node.type === 'chapterNode' ? 'var(--ink)' : 'var(--ink-faint)',
      maskColor: 'var(--minimap-mask)',
      ariaLabel: t('editor.nodesMinimapLabel'),
    }),
    [t],
  );

  // Nothing to project: a script with neither scenes nor chapters would render a
  // bare xyflow canvas (a blank "invalid canvas" surface). Show an explicit
  // empty state that points back to writing instead — and skip mounting the
  // engine entirely so an empty view pays none of its init cost.
  const isEmpty = scenes.length === 0 && chapters.length === 0;

  return (
    <div
      className="mh-nodes-view"
      data-testid="nodes-view"
      aria-label={t('editor.nodesViewLabel')}
    >
      {isEmpty ? (
        <div className="mh-nodes-empty" data-testid="nodes-empty">
          <div className="mh-nodes-empty-title">{t('editor.nodesEmptyTitle')}</div>
          <p className="mh-nodes-empty-sub">{t('editor.nodesEmptySub')}</p>
          {onBackToScript && (
            <button
              type="button"
              className="mh-nodes-empty-btn"
              data-testid="nodes-empty-back"
              onClick={onBackToScript}
            >
              {t('editor.nodesEmptyAction')}
            </button>
          )}
        </div>
      ) : (
        <ChapterActionContext.Provider value={actionContext}>
          <CanvasEngine
            nodeTypes={NODE_TYPES}
            nodes={nodes}
            edges={graph.edges as Edge[]}
            fitView
            nodeMeasure={sceneNodeMeasure}
            onNodesChange={onNodesChange}
            onEdgesChange={noopEdgesChange}
            onNodeDragStart={onNodeDragStart}
            onNodeDragStop={onNodeDragStop}
            onSelectionDragStop={onSelectionDragStop}
            onNodeDoubleClick={onNodeDoubleClick}
            onSelectAll={selectAll}
            onClearSelection={clearSelection}
            allowConnect={false}
            minimap={minimapConfig}
            controls={{ showInteractive: false }}
          />
        </ChapterActionContext.Provider>
      )}
    </div>
  );
}
