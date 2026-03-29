import { useEffect, useRef } from 'react';
import { useCanvasStore } from '../../../stores/canvasStore';
import { useStoryboardStore } from '../../../stores/storyboardStore';
import { syncCanvas, updateViewport } from '../../../services/storyboardService';

const SYNC_DEBOUNCE_MS = 800;
const VIEWPORT_DEBOUNCE_MS = 1200;

/**
 * Auto-saves canvas state (nodes, edges, viewport) to backend.
 * Debounced to avoid excessive API calls during rapid edits.
 */
export function useCanvasPersist() {
  const projectId = useStoryboardStore((s) => s.currentProjectId);
  const nodes = useCanvasStore((s) => s.nodes);
  const edges = useCanvasStore((s) => s.edges);
  const viewport = useCanvasStore((s) => s.currentViewport);

  const syncTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const viewportTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const initialLoadRef = useRef(true);
  const prevNodesRef = useRef(nodes);
  const prevEdgesRef = useRef(edges);

  // --- Debounced node/edge sync ---
  useEffect(() => {
    if (!projectId) return;

    // Skip first render (data just loaded from API)
    if (initialLoadRef.current) {
      initialLoadRef.current = false;
      prevNodesRef.current = nodes;
      prevEdgesRef.current = edges;
      return;
    }

    // Skip if nothing changed (reference equality)
    if (nodes === prevNodesRef.current && edges === prevEdgesRef.current) return;
    prevNodesRef.current = nodes;
    prevEdgesRef.current = edges;

    if (syncTimerRef.current) clearTimeout(syncTimerRef.current);

    syncTimerRef.current = setTimeout(async () => {
      try {
        await syncCanvas(projectId, {
          nodes: nodes.map((n) => ({
            id: n.id,
            position_x: n.position.x,
            position_y: n.position.y,
            width: n.measured?.width ?? n.width ?? null,
            height: n.measured?.height ?? n.height ?? null,
            data_json: n.data as Record<string, unknown>,
            sort_order: null,
            locked: (n.data as Record<string, unknown>)?.locked === true,
          })),
          edges: edges.map((e) => ({
            id: e.id,
            source_node_id: e.source,
            target_node_id: e.target,
            source_handle: e.sourceHandle ?? 'source',
            target_handle: e.targetHandle ?? 'target',
            edge_type: 'default',
          })),
        });
      } catch (err) {
        console.error('[useCanvasPersist] Sync failed:', err);
      }
    }, SYNC_DEBOUNCE_MS);

    return () => {
      if (syncTimerRef.current) clearTimeout(syncTimerRef.current);
    };
  }, [projectId, nodes, edges]);

  // --- Debounced viewport sync ---
  useEffect(() => {
    if (!projectId || !viewport) return;

    if (viewportTimerRef.current) clearTimeout(viewportTimerRef.current);

    viewportTimerRef.current = setTimeout(async () => {
      try {
        await updateViewport(projectId, viewport);
      } catch (err) {
        console.error('[useCanvasPersist] Viewport sync failed:', err);
      }
    }, VIEWPORT_DEBOUNCE_MS);

    return () => {
      if (viewportTimerRef.current) clearTimeout(viewportTimerRef.current);
    };
  }, [projectId, viewport]);

  // Reset on project change
  useEffect(() => {
    initialLoadRef.current = true;
  }, [projectId]);
}
