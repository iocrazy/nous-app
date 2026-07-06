/**
 * NodesView — the scene/chapter flow projection surface (Phase B Task 2-3).
 *
 * Renders the mapper's node/edge graph in a controlled @xyflow/react canvas.
 * Scene nodes are draggable; a drag-stop persists the new coordinates via
 * `updateSceneMeta` (debounced 500ms per scene so a flurry of small moves
 * collapses into one write). Double-clicking a scene node jumps back to the
 * script view through `onOpenScene`. Chapter nodes carry an inline action bar
 * (Expand / Branch / Convert) — dispatched + polled through a context so the
 * node components don't thread callbacks through React Flow's node `data`.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ReactFlow,
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  SelectionMode,
  applyNodeChanges,
  type Edge,
  type Node,
  type NodeChange,
  type ReactFlowInstance,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { useTranslation } from 'react-i18next';
import {
  mapToFlow,
  SCENE_NODE_WIDTH,
  CHAPTER_NODE_WIDTH,
  NODE_HEIGHT_FALLBACK,
} from './sceneNodeMapper';
import { SceneFlowNode } from './SceneFlowNode';
import { ChapterActionsNode, ChapterActionContext } from './ChapterActionsNode';
import { GuideOverlay } from './GuideOverlay';
import { computeAlignmentGuides, type AlignmentGuides, type Rect } from './alignmentGuides';
import { useCanvasShortcuts } from './useCanvasShortcuts';
import { useConvertPoll } from '../useConvertPoll';
import { updateSceneMeta, updateChapterPosition, listShots } from '../sceneService';
import type { SceneDoc } from '../types';
import type { ScriptChapter } from '../../types';

/** Debounce window for persisting a scene's dragged coordinates. */
const DRAG_PERSIST_MS = 500;
/** Length of the `sc-` / `ch-` id prefixes the mapper emits. */
const ID_PREFIX_LEN = 3;
/** Snap-to-grid step, px. Gentle 8px lattice — no user toggle (YAGNI). */
const SNAP_GRID: [number, number] = [8, 8];
/** Stable empty-guides object so clearing never allocates a new render key. */
const NO_GUIDES: AlignmentGuides = {};

const NODE_TYPES = { sceneNode: SceneFlowNode, chapterNode: ChapterActionsNode };

/** Bounding rect of a node in flow space, using measured size when available. */
function nodeRect(node: Node): Rect {
  const fallbackWidth =
    node.type === 'chapterNode' ? CHAPTER_NODE_WIDTH : SCENE_NODE_WIDTH;
  return {
    x: node.position.x,
    y: node.position.y,
    width: node.measured?.width ?? fallbackWidth,
    height: node.measured?.height ?? NODE_HEIGHT_FALLBACK,
  };
}

/**
 * Modifier that toggles add-to-selection. Cmd on Apple platforms, Ctrl
 * elsewhere — matches the OS conventions React Flow's own defaults follow.
 * `navigator.platform` is empty under jsdom, which resolves to Control.
 */
function detectMultiSelectKey(): 'Meta' | 'Control' {
  const platform = (typeof navigator !== 'undefined' && navigator.platform) || '';
  return /Mac|iPhone|iPad|iPod/i.test(platform) ? 'Meta' : 'Control';
}

const MULTI_SELECT_KEY = detectMultiSelectKey();

export interface NodesViewProps {
  scenes: SceneDoc[];
  chapters: ScriptChapter[];
  onOpenScene: (sceneId: string) => void;
  /** Owning script — chapter actions dispatch against it. */
  scriptId: string;
  /** Refresh scenes+chapters after a chapter action settles. */
  onReload: () => void | Promise<void>;
}

export function NodesView({ scenes, chapters, onOpenScene, scriptId, onReload }: NodesViewProps) {
  const { t } = useTranslation();
  const [shotCovers, setShotCovers] = useState<Map<string, string>>(() => new Map());
  const graph = useMemo(
    () => mapToFlow(scenes, chapters, shotCovers),
    [scenes, chapters, shotCovers],
  );
  const [nodes, setNodes] = useState<Node[]>(() => graph.nodes as Node[]);
  const [guides, setGuides] = useState<AlignmentGuides>(NO_GUIDES);
  const { startPoll } = useConvertPoll(scriptId);

  // Canvas container (keyboard target) + live flow instance (zoom / fit).
  const containerRef = useRef<HTMLDivElement>(null);
  const instanceRef = useRef<ReactFlowInstance | null>(null);

  // Re-seed when the projection changes (scene added / removed / reparented).
  // Drag-in-progress positions are transient client state; a structural change
  // to the underlying scenes/chapters is the authoritative reset point.
  useEffect(() => {
    setNodes(graph.nodes as Node[]);
  }, [graph]);

  // Load each scene's first shot cover, once per scene set, and build a
  // sceneId → cover-url map the mapper threads onto the cards. Best-effort: a
  // scene whose shots fail to load just shows no cover (console.error, no toast),
  // and the map is only committed when at least one cover exists so the no-image
  // path never re-seeds the canvas (zero regression).
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const entries = await Promise.all(
        scenes.map(async (s): Promise<[string, string | null]> => {
          const sceneId = String(s.id);
          try {
            const shots = await listShots(sceneId);
            const withImage = shots.find((shot) => !!shot.image_url);
            return [sceneId, withImage?.image_url ?? null];
          } catch (err) {
            console.error('[NodesView] failed to load shot covers for scene', sceneId, err);
            return [sceneId, null];
          }
        }),
      );
      if (cancelled) return;
      const map = new Map<string, string>();
      for (const [sceneId, url] of entries) {
        if (url) map.set(sceneId, url);
      }
      if (map.size > 0) setShotCovers(map);
    })();
    return () => {
      cancelled = true;
    };
  }, [scenes]);

  const onNodesChange = useCallback(
    (changes: NodeChange[]) => setNodes((nds) => applyNodeChanges(changes, nds)),
    [],
  );

  // Latest node set, read synchronously by the single-drag handler to learn the
  // current selection without re-binding the callback on every position change.
  const nodesRef = useRef<Node[]>(nodes);
  useEffect(() => {
    nodesRef.current = nodes;
  }, [nodes]);

  // One pending coordinate write per scene id; flushed after the debounce window.
  // Keyed by scene id, so a group move schedules independent writes that never
  // clobber each other.
  const timersRef = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map());
  useEffect(() => {
    const timers = timersRef.current;
    return () => {
      timers.forEach((timer) => clearTimeout(timer));
      timers.clear();
    };
  }, []);

  // Debounce-persist every scene/chapter node in `changed`, each through its own
  // lane: sceneNode → updateSceneMeta, chapterNode → updateChapterPosition. The
  // timer map is keyed by the full node id (`sc-`/`ch-` prefixed), so the two
  // lanes never collide even when a scene and chapter share a numeric id.
  const persistPositions = useCallback((changed: Node[]) => {
    const timers = timersRef.current;
    for (const node of changed) {
      if (node.type !== 'sceneNode' && node.type !== 'chapterNode') continue;
      const key = String(node.id);
      const entityId = key.slice(ID_PREFIX_LEN);
      const position_x = Math.round(node.position.x);
      const position_y = Math.round(node.position.y);
      const write =
        node.type === 'chapterNode'
          ? () => updateChapterPosition(entityId, { position_x, position_y })
          : () => updateSceneMeta(entityId, { position_x, position_y });
      const existing = timers.get(key);
      if (existing) clearTimeout(existing);
      timers.set(
        key,
        setTimeout(() => {
          timers.delete(key);
          write().catch((err) =>
            console.error('[NodesView] failed to persist node position', err),
          );
        }, DRAG_PERSIST_MS),
      );
    }
  }, []);

  // While a single node drags, match its edges against every other node and draw
  // the guide lines. The actual position snap is applied once on drop (below),
  // so the node never fights the cursor mid-drag.
  const onNodeDrag = useCallback((_evt: React.MouseEvent, node: Node) => {
    const others = nodesRef.current.filter((n) => String(n.id) !== String(node.id));
    setGuides(computeAlignmentGuides(nodeRect(node), others.map(nodeRect)));
  }, []);

  const onNodeDragStop = useCallback(
    (_evt: React.MouseEvent, node: Node) => {
      setGuides(NO_GUIDES);
      // If the dragged node belongs to a multi-selection, React Flow moved the
      // whole group with it — persist every selected node, not just this one.
      const selected = nodesRef.current.filter((n) => n.selected);
      const inSelection =
        selected.length > 1 && selected.some((n) => String(n.id) === String(node.id));
      if (inSelection) {
        persistPositions(selected);
        return;
      }
      // Solo drop: apply a one-time alignment snap (wins over the 8px grid).
      const others = nodesRef.current.filter((n) => String(n.id) !== String(node.id));
      const g = computeAlignmentGuides(nodeRect(node), others.map(nodeRect));
      const snapped =
        g.snappedX != null || g.snappedY != null
          ? {
              ...node,
              position: {
                x: g.snappedX ?? node.position.x,
                y: g.snappedY ?? node.position.y,
              },
            }
          : node;
      if (snapped !== node) {
        setNodes((nds) =>
          nds.map((n) =>
            String(n.id) === String(node.id) ? { ...n, position: snapped.position } : n,
          ),
        );
      }
      persistPositions([snapped]);
    },
    [persistPositions],
  );

  const onSelectionDragStop = useCallback(
    (_evt: React.MouseEvent, dragged: Node[]) => {
      setGuides(NO_GUIDES);
      persistPositions(dragged);
    },
    [persistPositions],
  );

  // Keyboard actions. Nudge moves + persists the selected scenes; select-all /
  // clear flip the `selected` flag across the controlled node set.
  const nudgeSelected = useCallback(
    (dx: number, dy: number) => {
      const selected = nodesRef.current.filter(
        (n) => n.selected && n.type === 'sceneNode',
      );
      if (selected.length === 0) return;
      const moved = selected.map((n) => ({
        ...n,
        position: { x: n.position.x + dx, y: n.position.y + dy },
      }));
      const movedById = new Map(moved.map((n) => [String(n.id), n.position]));
      setNodes((nds) =>
        nds.map((n) =>
          movedById.has(String(n.id))
            ? { ...n, position: movedById.get(String(n.id))! }
            : n,
        ),
      );
      persistPositions(moved);
    },
    [persistPositions],
  );

  const selectAll = useCallback(
    () => setNodes((nds) => nds.map((n) => (n.selected ? n : { ...n, selected: true }))),
    [],
  );
  const clearSelection = useCallback(
    () => setNodes((nds) => nds.map((n) => (n.selected ? { ...n, selected: false } : n))),
    [],
  );

  useCanvasShortcuts(containerRef, {
    onNudge: nudgeSelected,
    onZoomIn: () => instanceRef.current?.zoomIn(),
    onZoomOut: () => instanceRef.current?.zoomOut(),
    onFitView: () => instanceRef.current?.fitView(),
    onSelectAll: selectAll,
    onClearSelection: clearSelection,
  });

  const onNodeDoubleClick = useCallback(
    (_evt: React.MouseEvent, node: Node) => {
      if (node.type !== 'sceneNode') return;
      onOpenScene(node.id.slice(ID_PREFIX_LEN));
    },
    [onOpenScene],
  );

  const actionContext = useMemo(
    () => ({ scriptId, chapters, startPoll, onReload }),
    [scriptId, chapters, startPoll, onReload],
  );

  return (
    <div
      ref={containerRef}
      tabIndex={0}
      className="mh-nodes-view"
      data-testid="nodes-view"
      aria-label={t('editor.nodesViewLabel')}
    >
      <ChapterActionContext.Provider value={actionContext}>
        <ReactFlow
          nodes={nodes}
          edges={graph.edges as Edge[]}
          nodeTypes={NODE_TYPES}
          onInit={(instance) => {
            instanceRef.current = instance;
          }}
          onNodesChange={onNodesChange}
          onNodeDrag={onNodeDrag}
          onNodeDragStop={onNodeDragStop}
          onSelectionDragStop={onSelectionDragStop}
          onNodeDoubleClick={onNodeDoubleClick}
          selectionMode={SelectionMode.Partial}
          selectionKeyCode="Shift"
          multiSelectionKeyCode={MULTI_SELECT_KEY}
          snapGrid={SNAP_GRID}
          snapToGrid
          onlyRenderVisibleElements
          fitView
          proOptions={{ hideAttribution: true }}
        >
          <Background variant={BackgroundVariant.Dots} gap={20} size={1} />
          <Controls showInteractive={false} />
          <MiniMap
            pannable
            zoomable
            aria-label={t('editor.nodesMinimapLabel')}
            maskColor="var(--minimap-mask)"
            nodeColor={(node) =>
              node.type === 'chapterNode' ? 'var(--indigo)' : 'var(--ink-faint)'
            }
          />
          <GuideOverlay guides={guides} />
        </ReactFlow>
      </ChapterActionContext.Provider>
    </div>
  );
}
