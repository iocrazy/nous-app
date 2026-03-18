import { useCallback, useEffect, useRef } from 'react';
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

// ─── Clipboard types ──────────────────────────────────────────────────────────

interface ClipboardSnapshot {
  nodes: StoryboardNode[];
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
    addNode,
    duplicateNode,
    setSelectedNodeId,
    pushHistory,
    undo,
    redo,
    currentProjectId,
  } = useStoryboardStore();

  // ─── Clipboard ref ────────────────────────────────────────────────────
  const clipboardRef = useRef<ClipboardSnapshot | null>(null);
  const pasteCountRef = useRef(0);

  // ─── Copy selected nodes ─────────────────────────────────────────────
  const copySelectedNodes = useCallback(() => {
    if (!selectedNodeId) return;
    const selectedNode = nodes.find((n) => n.id === selectedNodeId);
    if (!selectedNode) return;

    clipboardRef.current = { nodes: [selectedNode] };
    pasteCountRef.current = 0;
  }, [selectedNodeId, nodes]);

  // ─── Paste from clipboard ────────────────────────────────────────────
  const pasteNodes = useCallback(() => {
    const snapshot = clipboardRef.current;
    if (!snapshot || snapshot.nodes.length === 0) return;

    pushHistory();
    pasteCountRef.current += 1;
    const offset = pasteCountRef.current * 50;

    for (const original of snapshot.nodes) {
      const newId = `node-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
      const duplicated: StoryboardNode = {
        ...original,
        id: newId,
        project_id: currentProjectId ?? original.project_id,
        position_x: original.position_x + offset,
        position_y: original.position_y + offset,
        data_json: { ...original.data_json },
        sort_order: nodes.length,
        locked: false,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      };
      addNode(duplicated);
      setSelectedNodeId(newId);
    }
  }, [nodes.length, currentProjectId, pushHistory, addNode, setSelectedNodeId]);

  // ─── Check if clipboard has content ───────────────────────────────────
  const canPaste = clipboardRef.current !== null && clipboardRef.current.nodes.length > 0;

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
        const currentEdges = useStoryboardStore.getState().edges;
        setEdges(currentEdges.filter((e) => !removedIds.includes(e.id)));
      }
    },
    [setEdges]
  );

  // Handle new connections from React Flow
  const onConnect = useCallback(
    (connection: Connection) => {
      const currentNodes = useStoryboardStore.getState().nodes;
      const currentEdges = useStoryboardStore.getState().edges;
      const newEdge: StoryboardEdge = {
        id: `edge-${connection.source}-${connection.target}-${Date.now()}`,
        project_id: currentNodes[0]?.project_id ?? '',
        source_node_id: connection.source,
        target_node_id: connection.target,
        source_handle: connection.sourceHandle ?? undefined,
        target_handle: connection.targetHandle ?? undefined,
        edge_type: 'default',
        created_at: new Date().toISOString(),
      };
      pushHistory();
      setEdges([...currentEdges, newEdge]);
    },
    [setEdges, pushHistory]
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

      const commandPressed = e.metaKey || e.ctrlKey;
      const key = e.key.toLowerCase();

      // Cmd/Ctrl+C → copy
      if (commandPressed && key === 'c' && !e.shiftKey) {
        e.preventDefault();
        copySelectedNodes();
        return;
      }

      // Cmd/Ctrl+V → paste
      if (commandPressed && key === 'v' && !e.shiftKey) {
        e.preventDefault();
        pasteNodes();
        return;
      }

      // Cmd/Ctrl+Z → undo
      if (commandPressed && key === 'z' && !e.shiftKey) {
        e.preventDefault();
        undo();
        return;
      }

      // Cmd/Ctrl+Shift+Z or Cmd/Ctrl+Y → redo
      if (
        (commandPressed && e.shiftKey && key === 'z') ||
        (commandPressed && key === 'y')
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
  }, [
    selectedNodeId,
    undo,
    redo,
    deleteNode,
    setSelectedNodeId,
    pushHistory,
    copySelectedNodes,
    pasteNodes,
  ]);

  return {
    nodes,
    edges,
    selectedNodeId,
    onNodesChange,
    onEdgesChange,
    onConnect,
    copySelectedNodes,
    pasteNodes,
    canPaste,
  };
}
