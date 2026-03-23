import { useState, useCallback, useEffect } from 'react';
import { ChevronLeft, Loader2, Pencil } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useNavigate, useParams } from 'react-router-dom';
import { ReactFlowProvider } from '@xyflow/react';
import { Canvas } from '../../features/storyboard/Canvas';
import { useCanvasStore } from '../../stores/canvasStore';
import { useStoryboardStore } from '../../stores/storyboardStore';
import { fetchProject, updateProject } from '../../services/storyboardService';
import type { CanvasNode, CanvasEdge } from '../../stores/canvasStore';

/**
 * Map backend node_type to frontend ReactFlow node type.
 * Backend: upload, image, image_edit, storyboard_split, storyboard_gen, text_annotation, group, export
 * Frontend: uploadNode, imageNode, exportImageNode, textAnnotationNode, groupNode, storyboardNode, storyboardGenNode
 */
const BACKEND_TO_FRONTEND_NODE_TYPE: Record<string, string> = {
  upload: 'uploadNode',
  image: 'imageNode',
  image_edit: 'imageNode',
  storyboard_split: 'storyboardNode',
  storyboard_gen: 'storyboardGenNode',
  text_annotation: 'textAnnotationNode',
  group: 'groupNode',
  export: 'exportImageNode',
  image_to_video: 'exportImageNode',
};

function mapBackendNodesToCanvas(
  backendNodes: Array<{
    id: string;
    node_type: string;
    position_x: number;
    position_y: number;
    width?: number | null;
    height?: number | null;
    data_json?: Record<string, unknown>;
    sort_order?: number;
    locked?: boolean;
  }>,
): CanvasNode[] {
  return backendNodes
    .map((bn) => {
      const frontendType = BACKEND_TO_FRONTEND_NODE_TYPE[bn.node_type];
      if (!frontendType) {
        console.warn(`[CanvasEditorPage] Unknown node type: ${bn.node_type}`);
        return null;
      }
      return {
        id: String(bn.id),
        type: frontendType as CanvasNode['type'],
        position: { x: bn.position_x, y: bn.position_y },
        width: bn.width ?? undefined,
        height: bn.height ?? undefined,
        data: (bn.data_json ?? {}) as CanvasNode['data'],
      };
    })
    .filter((n): n is NonNullable<typeof n> => n !== null) as CanvasNode[];
}

function mapBackendEdgesToCanvas(
  backendEdges: Array<{
    id: string;
    source_node_id: string;
    target_node_id: string;
    source_handle?: string | null;
    target_handle?: string | null;
    edge_type?: string | null;
  }>,
): CanvasEdge[] {
  return backendEdges.map((be) => ({
    id: String(be.id),
    source: String(be.source_node_id),
    target: String(be.target_node_id),
    sourceHandle: (be.source_handle === 'output' ? 'source' : be.source_handle) ?? 'source',
    targetHandle: (be.target_handle === 'input' ? 'target' : be.target_handle) ?? 'target',
    type: 'disconnectableEdge' as const,
  }));
}

export function CanvasEditorPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { teamId, projectId } = useParams<{ teamId: string; projectId?: string }>();

  const setCanvasData = useCanvasStore((state) => state.setCanvasData);
  const setCurrentProject = useStoryboardStore((state) => state.setCurrentProject);

  const [projectName, setProjectName] = useState('Untitled Project');
  const [editingName, setEditingName] = useState(false);
  const [nameInput, setNameInput] = useState('');
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  // ─── Load project on mount ──────────────────────────────────────────────

  useEffect(() => {
    if (!projectId) {
      setLoading(false);
      return;
    }

    setCurrentProject(projectId);
    let cancelled = false;

    void (async () => {
      try {
        const project = await fetchProject(projectId);
        if (cancelled) return;
        setProjectName(project.name);
        setCanvasData(
          mapBackendNodesToCanvas(project.nodes),
          mapBackendEdgesToCanvas(project.edges),
        );
      } catch (err) {
        if (cancelled) return;
        const message = err instanceof Error ? err.message : String(err);
        setLoadError(message);
        console.error('[CanvasEditorPage] Failed to load project:', message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [projectId, setCanvasData, setCurrentProject]);

  // ─── Clean up on unmount ────────────────────────────────────────────────

  useEffect(() => {
    return () => {
      setCurrentProject(null);
    };
  }, [setCurrentProject]);

  // ─── Handlers ──────────────────────────────────────────────────────────

  const handleBack = useCallback(() => {
    navigate(`/team/${teamId}/storyboard`);
  }, [navigate, teamId]);

  const handleNameSubmit = useCallback(async () => {
    const trimmed = nameInput.trim();
    if (!trimmed || trimmed === projectName) {
      setEditingName(false);
      setNameInput(projectName);
      return;
    }
    setProjectName(trimmed);
    setEditingName(false);

    if (projectId) {
      try {
        await updateProject(projectId, { name: trimmed });
      } catch (err) {
        console.error('[CanvasEditorPage] Failed to update name:', err);
      }
    }
  }, [nameInput, projectName, projectId]);

  const handleNameKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLInputElement>) => {
      if (e.key === 'Enter') void handleNameSubmit();
      if (e.key === 'Escape') {
        setEditingName(false);
        setNameInput(projectName);
      }
    },
    [handleNameSubmit, projectName]
  );

  // ─── Render ───────────────────────────────────────────────────────────

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center bg-zinc-950">
        <Loader2 size={24} className="animate-spin text-gray-500" />
      </div>
    );
  }

  if (loadError) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-4 bg-zinc-950">
        <p className="text-sm text-red-400">Failed to load project</p>
        <p className="text-xs text-gray-500">{loadError}</p>
        <button
          type="button"
          onClick={handleBack}
          className="text-sm text-blue-400 hover:text-blue-300"
        >
          Back to projects
        </button>
      </div>
    );
  }

  return (
    <div className="h-full flex flex-col bg-zinc-950 overflow-hidden">
      {/* Top bar */}
      <div className="flex items-center gap-3 px-4 py-2.5 border-b border-gray-800 flex-shrink-0 bg-gray-900">
        <button
          type="button"
          onClick={handleBack}
          className="flex items-center gap-1.5 text-sm text-gray-400 hover:text-gray-100 transition-colors"
        >
          <ChevronLeft size={16} />
          <span className="hidden sm:inline">{t('storyboard.back', 'Back')}</span>
        </button>

        <div className="w-px h-4 bg-gray-700" />

        {editingName ? (
          <input
            type="text"
            value={nameInput}
            autoFocus
            onChange={(e) => setNameInput(e.target.value)}
            onBlur={() => void handleNameSubmit()}
            onKeyDown={handleNameKeyDown}
            className="px-2 py-0.5 bg-gray-800 border border-blue-500 rounded text-sm font-medium text-gray-100 focus:outline-none min-w-0 max-w-xs"
          />
        ) : (
          <button
            type="button"
            onClick={() => { setEditingName(true); setNameInput(projectName); }}
            className="flex items-center gap-1.5 text-sm font-medium text-gray-200 hover:text-white transition-colors group"
          >
            <span className="truncate max-w-xs">{projectName}</span>
            <Pencil size={12} className="opacity-0 group-hover:opacity-60 transition-opacity flex-shrink-0" />
          </button>
        )}
      </div>

      {/* Canvas */}
      <div className="flex-1 relative overflow-hidden min-h-0">
        <ReactFlowProvider>
          <Canvas />
        </ReactFlowProvider>
      </div>
    </div>
  );
}
