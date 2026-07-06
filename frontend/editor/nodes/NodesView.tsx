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
  applyNodeChanges,
  type Edge,
  type Node,
  type NodeChange,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { useTranslation } from 'react-i18next';
import { mapToFlow } from './sceneNodeMapper';
import { SceneFlowNode } from './SceneFlowNode';
import { ChapterActionsNode, ChapterActionContext } from './ChapterActionsNode';
import { useConvertPoll } from '../useConvertPoll';
import { updateSceneMeta } from '../sceneService';
import type { SceneDoc } from '../types';
import type { ScriptChapter } from '../../types';

/** Debounce window for persisting a scene's dragged coordinates. */
const DRAG_PERSIST_MS = 500;
/** Length of the `sc-` / `ch-` id prefixes the mapper emits. */
const ID_PREFIX_LEN = 3;

const NODE_TYPES = { sceneNode: SceneFlowNode, chapterNode: ChapterActionsNode };

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
  const graph = useMemo(() => mapToFlow(scenes, chapters), [scenes, chapters]);
  const [nodes, setNodes] = useState<Node[]>(() => graph.nodes as Node[]);
  const { startPoll } = useConvertPoll(scriptId);

  // Re-seed when the projection changes (scene added / removed / reparented).
  // Drag-in-progress positions are transient client state; a structural change
  // to the underlying scenes/chapters is the authoritative reset point.
  useEffect(() => {
    setNodes(graph.nodes as Node[]);
  }, [graph]);

  const onNodesChange = useCallback(
    (changes: NodeChange[]) => setNodes((nds) => applyNodeChanges(changes, nds)),
    [],
  );

  // One pending coordinate write per scene id; flushed after the debounce window.
  const timersRef = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map());
  useEffect(() => {
    const timers = timersRef.current;
    return () => {
      timers.forEach((timer) => clearTimeout(timer));
      timers.clear();
    };
  }, []);

  const onNodeDragStop = useCallback((_evt: React.MouseEvent, node: Node) => {
    if (node.type !== 'sceneNode') return;
    const sceneId = node.id.slice(ID_PREFIX_LEN);
    const position_x = Math.round(node.position.x);
    const position_y = Math.round(node.position.y);
    const timers = timersRef.current;
    const existing = timers.get(sceneId);
    if (existing) clearTimeout(existing);
    timers.set(
      sceneId,
      setTimeout(() => {
        timers.delete(sceneId);
        updateSceneMeta(sceneId, { position_x, position_y }).catch((err) =>
          console.error('[NodesView] failed to persist scene position', err),
        );
      }, DRAG_PERSIST_MS),
    );
  }, []);

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
    <div className="mh-nodes-view" data-testid="nodes-view" aria-label={t('editor.nodesViewLabel')}>
      <ChapterActionContext.Provider value={actionContext}>
        <ReactFlow
          nodes={nodes}
          edges={graph.edges as Edge[]}
          nodeTypes={NODE_TYPES}
          onNodesChange={onNodesChange}
          onNodeDragStop={onNodeDragStop}
          onNodeDoubleClick={onNodeDoubleClick}
          fitView
          proOptions={{ hideAttribution: true }}
        >
          <Background variant={BackgroundVariant.Dots} gap={20} size={1} />
          <Controls showInteractive={false} />
        </ReactFlow>
      </ChapterActionContext.Provider>
    </div>
  );
}
