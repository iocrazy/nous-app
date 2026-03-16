import { useEffect, useRef } from 'react';
import { useStoryboardStore } from '../../stores/storyboardStore';
import { syncCanvas, updateViewport } from '../../services/storyboardService';

const NODES_EDGES_DEBOUNCE_MS = 260;
const VIEWPORT_DEBOUNCE_MS = 280;

// ─── Hook ─────────────────────────────────────────────────────────────────────

export function useStoryboardPersist() {
  const { currentProjectId, nodes, edges, viewport } = useStoryboardStore();

  const nodesEdgesTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const viewportTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const initialLoadRef = useRef(true);

  // Debounced sync for nodes & edges
  useEffect(() => {
    if (!currentProjectId) return;

    if (initialLoadRef.current) {
      initialLoadRef.current = false;
      return;
    }

    if (nodesEdgesTimer.current) {
      clearTimeout(nodesEdgesTimer.current);
    }

    nodesEdgesTimer.current = setTimeout(async () => {
      try {
        await syncCanvas(currentProjectId, {
          nodes: nodes.map((n) => ({
            id: n.id,
            position_x: n.position_x,
            position_y: n.position_y,
            width: n.width,
            height: n.height,
            data_json: n.data_json,
            sort_order: n.sort_order,
            locked: n.locked,
          })),
          edges: edges.map((e) => ({
            id: e.id,
            source_node_id: e.source_node_id,
            target_node_id: e.target_node_id,
            source_handle: e.source_handle,
            target_handle: e.target_handle,
            edge_type: e.edge_type,
          })),
        });
      } catch (err) {
        console.error('[useStoryboardPersist] Canvas sync failed:', err);
      }
    }, NODES_EDGES_DEBOUNCE_MS);

    return () => {
      if (nodesEdgesTimer.current) {
        clearTimeout(nodesEdgesTimer.current);
      }
    };
  }, [currentProjectId, nodes, edges]);

  // Debounced sync for viewport
  useEffect(() => {
    if (!currentProjectId) return;

    if (viewportTimer.current) {
      clearTimeout(viewportTimer.current);
    }

    viewportTimer.current = setTimeout(async () => {
      try {
        await updateViewport(currentProjectId, viewport);
      } catch (err) {
        console.error('[useStoryboardPersist] Viewport sync failed:', err);
      }
    }, VIEWPORT_DEBOUNCE_MS);

    return () => {
      if (viewportTimer.current) {
        clearTimeout(viewportTimer.current);
      }
    };
  }, [currentProjectId, viewport]);
}
