import { useCallback, useEffect, useRef } from 'react';
import {
  ReactFlow,
  ReactFlowProvider,
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  useReactFlow,
  Panel,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { LayoutGrid } from 'lucide-react';
import { useScriptCanvasStore } from '../../stores/scriptCanvasStore';
import { scriptNodeTypes } from './nodes';
import { applyDagreLayout } from './layout/dagreLayout';
import { ViewControls } from './components/ViewControls';

function ScriptCanvasInner() {
  const nodes = useScriptCanvasStore((s) => s.nodes);
  const edges = useScriptCanvasStore((s) => s.edges);
  const onNodesChange = useScriptCanvasStore((s) => s.onNodesChange);
  const onEdgesChange = useScriptCanvasStore((s) => s.onEdgesChange);
  const onConnect = useScriptCanvasStore((s) => s.onConnect);
  const setSelectedNode = useScriptCanvasStore((s) => s.setSelectedNode);
  const setViewportState = useScriptCanvasStore((s) => s.setViewportState);
  const setCanvasData = useScriptCanvasStore((s) => s.setCanvasData);

  const { fitView } = useReactFlow();

  // Track previous node count to trigger layout only when chapters are added/removed
  const prevNodeCountRef = useRef<number>(-1);

  const runLayout = useCallback(() => {
    const current = useScriptCanvasStore.getState();
    if (current.nodes.length === 0) return;
    const layouted = applyDagreLayout(current.nodes, current.edges);
    setCanvasData(layouted, current.edges);
    // Fit view after layout settles
    requestAnimationFrame(() => fitView({ padding: 0.1, duration: 300 }));
  }, [fitView, setCanvasData]);

  // Auto-layout when the number of nodes changes (initial load or add/remove)
  useEffect(() => {
    const count = nodes.length;
    if (count !== prevNodeCountRef.current) {
      prevNodeCountRef.current = count;
      if (count > 0) {
        runLayout();
      }
    }
  }, [nodes.length, runLayout]);

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
    <div className="w-full h-full relative">
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
        deleteKeyCode={null}
        className="bg-zinc-950"
      >
        <Background variant={BackgroundVariant.Dots} gap={20} size={1} color="#27272a" />
        <Controls className="!bg-zinc-900 !border-zinc-700 [&>button]:!bg-zinc-800 [&>button]:!border-zinc-700 [&>button]:!text-zinc-400" />
        <MiniMap
          nodeColor="#4f46e5"
          maskColor="rgba(0, 0, 0, 0.7)"
          className="!bg-zinc-900 !border-zinc-700"
        />
        <Panel position="top-right">
          <button
            onClick={runLayout}
            title="Re-layout"
            className="flex items-center gap-1.5 px-3 py-1.5 rounded bg-zinc-800 border border-zinc-700 text-zinc-300 text-xs hover:bg-zinc-700 hover:text-white transition-colors"
          >
            <LayoutGrid size={14} />
            Re-layout
          </button>
        </Panel>
      </ReactFlow>
      <ViewControls showZoom />
    </div>
  );
}

export function ScriptCanvas() {
  return (
    <ReactFlowProvider>
      <ScriptCanvasInner />
    </ReactFlowProvider>
  );
}
