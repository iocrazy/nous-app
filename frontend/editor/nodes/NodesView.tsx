/**
 * NodesView — the scene/chapter flow projection surface (Phase B Task 2).
 *
 * Renders the mapper's node/edge graph in a controlled @xyflow/react canvas.
 * Scene nodes are draggable; a drag-stop persists the new coordinates via
 * `updateSceneMeta` (debounced 500ms per scene so a flurry of small moves
 * collapses into one write). Double-clicking a scene node jumps back to the
 * script view through `onOpenScene`. Chapter nodes are read-only here — their
 * action bar (Expand / Branch / Convert) lands in Task 3.
 */
import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ReactFlow,
  Background,
  BackgroundVariant,
  Controls,
  Handle,
  Position,
  applyNodeChanges,
  type Edge,
  type Node,
  type NodeChange,
  type NodeProps,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { useTranslation } from 'react-i18next';
import { mapToFlow, type ChapterNodeData } from './sceneNodeMapper';
import { SceneFlowNode } from './SceneFlowNode';
import { updateSceneMeta } from '../sceneService';
import type { SceneDoc } from '../types';
import type { ScriptChapter } from '../../types';

/** Debounce window for persisting a scene's dragged coordinates. */
const DRAG_PERSIST_MS = 500;
/** Length of the `sc-` / `ch-` id prefixes the mapper emits. */
const ID_PREFIX_LEN = 3;

/** Minimal read-only chapter card (Task 3 swaps in the action-bar version). */
function ChapterReadonlyNodeImpl({ data }: NodeProps) {
  const { t } = useTranslation();
  const d = data as unknown as ChapterNodeData;
  return (
    <div className="mh-flow-node mh-flow-chapter">
      <Handle type="target" position={Position.Top} className="mh-flow-handle" />
      <div className="mh-flow-chapter-head">
        {d.chapterNumber ? <span className="mh-scene-num-badge">{d.chapterNumber}</span> : null}
        <span className="mh-flow-chapter-title">
          {d.title || t('editor.nodesUntitledChapter')}
        </span>
      </div>
      {d.summary ? <div className="mh-flow-chapter-summary">{d.summary}</div> : null}
      <Handle type="source" position={Position.Bottom} className="mh-flow-handle" />
    </div>
  );
}
const ChapterReadonlyNode = memo(ChapterReadonlyNodeImpl);

const NODE_TYPES = { sceneNode: SceneFlowNode, chapterNode: ChapterReadonlyNode };

export interface NodesViewProps {
  scenes: SceneDoc[];
  chapters: ScriptChapter[];
  onOpenScene: (sceneId: string) => void;
}

export function NodesView({ scenes, chapters, onOpenScene }: NodesViewProps) {
  const { t } = useTranslation();
  const graph = useMemo(() => mapToFlow(scenes, chapters), [scenes, chapters]);
  const [nodes, setNodes] = useState<Node[]>(() => graph.nodes as Node[]);

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

  return (
    <div className="mh-nodes-view" data-testid="nodes-view" aria-label={t('editor.nodesViewLabel')}>
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
    </div>
  );
}
