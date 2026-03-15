import { useCallback, useEffect } from 'react';
import { useStoryboardStore } from '../../stores/storyboardStore';
import { StoryboardNode, StoryboardEdge } from '../../types';

// ─── Types for React Flow compatibility ───────────────────────────────────────

export interface NodeChange {
  id: string;
  type: 'position' | 'dimensions' | 'remove' | 'select' | 'reset';
  position?: { x: number; y: number };
  dimensions?: { width: number; height: number };
  selected?: boolean;
}

export interface EdgeChange {
  id: string;
  type: 'remove' | 'select';
  selected?: boolean;
}

export interface Connection {
  source: string;
  target: string;
  sourceHandle?: string | null;
  targetHandle?: string | null;
}

// ─── Hook ─────────────────────────────────────────────────────────────────────

export function useStoryboardCanvas() {
  const {
    nodes,
    edges,
    selectedNodeId,
    setNodes,
    setEdges,
    updateNodeData,
    deleteNode,
    setSelectedNodeId,
    pushHistory,
    undo,
    redo,
  } = useStoryboardStore();

  // Handle node changes from React Flow
  const onNodesChange = useCallback(
    (changes: NodeChange[]) => {
      for (const change of changes) {
        switch (change.type) {
          case 'position':
            if (change.position) {
              updateNodeData(change.id, {
                position_x: change.position.x,
                position_y: change.position.y,
              });
            }
            break;
          case 'dimensions':
            if (change.dimensions) {
              updateNodeData(change.id, {
                width: change.dimensions.width,
                height: change.dimensions.height,
              });
            }
            break;
          case 'remove':
            deleteNode(change.id);
            break;
          case 'select':
            if (change.selected) {
              setSelectedNodeId(change.id);
            } else if (selectedNodeId === change.id) {
              setSelectedNodeId(null);
            }
            break;
        }
      }
    },
    [updateNodeData, deleteNode, setSelectedNodeId, selectedNodeId]
  );

  // Handle edge changes from React Flow
  const onEdgesChange = useCallback(
    (changes: EdgeChange[]) => {
      const removedIds = changes
        .filter((c) => c.type === 'remove')
        .map((c) => c.id);

      if (removedIds.length > 0) {
        setEdges(edges.filter((e) => !removedIds.includes(e.id)));
      }
    },
    [edges, setEdges]
  );

  // Handle new connections from React Flow
  const onConnect = useCallback(
    (connection: Connection) => {
      const newEdge: StoryboardEdge = {
        id: `edge-${connection.source}-${connection.target}-${Date.now()}`,
        project_id: nodes[0]?.project_id ?? '',
        source_node_id: connection.source,
        target_node_id: connection.target,
        source_handle: connection.sourceHandle ?? undefined,
        target_handle: connection.targetHandle ?? undefined,
        edge_type: 'default',
        created_at: new Date().toISOString(),
      };
      pushHistory();
      setEdges([...edges, newEdge]);
    },
    [nodes, edges, setEdges, pushHistory]
  );

  // Keyboard shortcut handler
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement;
      const isInputFocused =
        target.tagName === 'INPUT' ||
        target.tagName === 'TEXTAREA' ||
        target.isContentEditable;

      if (isInputFocused) return;

      // Cmd/Ctrl+Z → undo
      if ((e.metaKey || e.ctrlKey) && e.key === 'z' && !e.shiftKey) {
        e.preventDefault();
        undo();
        return;
      }

      // Cmd/Ctrl+Shift+Z or Cmd/Ctrl+Y → redo
      if (
        ((e.metaKey || e.ctrlKey) && e.shiftKey && e.key === 'z') ||
        ((e.metaKey || e.ctrlKey) && e.key === 'y')
      ) {
        e.preventDefault();
        redo();
        return;
      }

      // Delete / Backspace → delete selected node
      if ((e.key === 'Delete' || e.key === 'Backspace') && selectedNodeId) {
        pushHistory();
        deleteNode(selectedNodeId);
        return;
      }

      // Escape → deselect
      if (e.key === 'Escape') {
        setSelectedNodeId(null);
        return;
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [selectedNodeId, undo, redo, deleteNode, setSelectedNodeId, pushHistory]);

  return {
    nodes,
    edges,
    selectedNodeId,
    onNodesChange,
    onEdgesChange,
    onConnect,
  };
}
