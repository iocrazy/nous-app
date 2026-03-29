import { useCallback } from 'react';
import {
  ReactFlow,
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { useScriptCanvasStore } from '../../stores/scriptCanvasStore';
import { scriptNodeTypes } from './nodes';

export function ScriptCanvas() {
  const nodes = useScriptCanvasStore((s) => s.nodes);
  const edges = useScriptCanvasStore((s) => s.edges);
  const onNodesChange = useScriptCanvasStore((s) => s.onNodesChange);
  const onEdgesChange = useScriptCanvasStore((s) => s.onEdgesChange);
  const onConnect = useScriptCanvasStore((s) => s.onConnect);
  const setSelectedNode = useScriptCanvasStore((s) => s.setSelectedNode);
  const setViewportState = useScriptCanvasStore((s) => s.setViewportState);

  const handleNodeClick = useCallback(
    (_: React.MouseEvent, node: { id: string }) => {
      setSelectedNode(node.id);
    },
    [setSelectedNode],
  );

  const handlePaneClick = useCallback(() => {
    setSelectedNode(null);
  }, [setSelectedNode]);

  return (
    <div className="w-full h-full">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={scriptNodeTypes}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        onNodeClick={handleNodeClick}
        onPaneClick={handlePaneClick}
        onMoveEnd={(_, viewport) => setViewportState(viewport)}
        fitView
        deleteKeyCode={['Backspace', 'Delete']}
        className="bg-zinc-950"
      >
        <Background variant={BackgroundVariant.Dots} gap={20} size={1} color="#27272a" />
        <Controls className="!bg-zinc-900 !border-zinc-700 [&>button]:!bg-zinc-800 [&>button]:!border-zinc-700 [&>button]:!text-zinc-400" />
        <MiniMap
          nodeColor="#4f46e5"
          maskColor="rgba(0, 0, 0, 0.7)"
          className="!bg-zinc-900 !border-zinc-700"
        />
      </ReactFlow>
    </div>
  );
}
