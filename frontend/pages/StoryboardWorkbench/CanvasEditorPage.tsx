import { useState, useCallback, useEffect } from 'react';
import { Users, Film, FileText, Download } from 'lucide-react';
import { useNavigate, useParams } from 'react-router-dom';
import { EditorTopBar } from '../../components/EditorTopBar';
import { EditorLoadingScreen } from '../../components/EditorLoadingScreen';
import { ReactFlowProvider } from '@xyflow/react';
import { Canvas } from '../../features/storyboard/Canvas';
import { useCanvasStore } from '../../stores/canvasStore';
import { useStoryboardStore } from '../../stores/storyboardStore';
import { fetchProject } from '../../services/storyboardService';
import { CharacterPanel } from '../../features/storyboard/ui/CharacterPanel';
import { AIChatDrawer } from '../../components/AIChatDrawer';
import { FrameTimeline } from '../../features/storyboard/ui/FrameTimeline';
import { ScriptImportDialog } from '../../features/storyboard/ui/ScriptImportDialog';
import { ExportDialog } from '../../features/storyboard/ui/ExportDialog';
import type { CanvasNode, CanvasEdge } from '../../stores/canvasStore';

type SidePanel = 'characters' | null;

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
  const navigate = useNavigate();
  const { teamId, projectId: parentProjectId, storyboardId: projectId } = useParams<{ teamId?: string; projectId?: string; storyboardId?: string }>();

  const setCanvasData = useCanvasStore((state) => state.setCanvasData);
  const setCurrentProject = useStoryboardStore((state) => state.setCurrentProject);

  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  // Panel states
  const [sidePanel, setSidePanel] = useState<SidePanel>(null);
  const [showTimeline, setShowTimeline] = useState(false);
  const [showScriptImport, setShowScriptImport] = useState(false);
  const [showExport, setShowExport] = useState(false);

  const toggleSidePanel = useCallback((panel: SidePanel) => {
    setSidePanel((prev) => (prev === panel ? null : panel));
  }, []);

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

  // ─── Render ───────────────────────────────────────────────────────────

  const handleBack = () =>
    navigate(teamId ? `/team/${teamId}/projects/${parentProjectId}?tab=storyboard` : '/projects');

  return (
    <div className="h-screen w-screen overflow-hidden flex flex-col bg-zinc-950">
      {/* Fullscreen loading overlay */}
      <EditorLoadingScreen visible={loading} />

      {/* Error state */}
      {!loading && loadError && (
        <div className="flex flex-col items-center justify-center flex-1 gap-4">
          <p className="text-sm text-red-400">Failed to load project</p>
          <p className="text-xs text-zinc-500">{loadError}</p>
          <button
            type="button"
            onClick={handleBack}
            className="text-sm text-indigo-400 hover:underline"
          >
            Back to project
          </button>
        </div>
      )}

      {/* Editor UI */}
      {!loadError && (
        <>
          <EditorTopBar
            projectName="Storyboard"
            onBack={handleBack}
            onExport={() => setShowExport(true)}
            onImport={() => setShowScriptImport(true)}
          />

          {/* Canvas + side panels */}
          <div className="flex-1 flex min-h-0">
            <div className="flex-1 relative overflow-hidden min-h-0">
              <ReactFlowProvider>
                <Canvas />
              </ReactFlowProvider>

              {/* Floating panel toolbar — left side vertical */}
              <div className="absolute top-4 left-4 flex flex-col gap-0.5 bg-zinc-900/90 backdrop-blur-sm rounded-xl p-1 border border-zinc-800/40 shadow-lg z-10">
                <FloatingIconButton
                  icon={<FileText size={16} />}
                  tooltip="Script Import"
                  onClick={() => setShowScriptImport(true)}
                />
                <FloatingIconButton
                  icon={<Users size={16} />}
                  tooltip="Characters"
                  active={sidePanel === 'characters'}
                  onClick={() => toggleSidePanel('characters')}
                />
                <FloatingIconButton
                  icon={<Film size={16} />}
                  tooltip="Timeline"
                  active={showTimeline}
                  onClick={() => setShowTimeline((v) => !v)}
                />
                {/* AI Chat moved to a bottom-right floating button (see
                    AIChatDrawer below). The legacy left-toolbar entry
                    was removed when chat migrated from
                    storyboardService.chatWithAI to the AI Library /
                    AgentRunner pipeline. */}
                <div className="my-0.5 mx-1.5 border-t border-zinc-700/50" />
                <FloatingIconButton
                  icon={<Download size={16} />}
                  tooltip="Export"
                  onClick={() => setShowExport(true)}
                />
              </div>
            </div>

            {/* Right side panel */}
            {sidePanel === 'characters' && projectId && (
              <div className="w-80 flex-shrink-0 border-l border-zinc-800/50 overflow-y-auto">
                <CharacterPanel projectId={projectId} onClose={() => setSidePanel(null)} />
              </div>
            )}
          </div>

          {/* Bottom timeline */}
          {showTimeline && (
            <FrameTimeline onClose={() => setShowTimeline(false)} />
          )}

          {/* Dialogs */}
          {projectId && (
            <>
              <ScriptImportDialog
                projectId={projectId}
                isOpen={showScriptImport}
                onClose={() => setShowScriptImport(false)}
              />
              <ExportDialog
                projectId={projectId}
                isOpen={showExport}
                onClose={() => setShowExport(false)}
              />
              {/* AI Chat Drawer — bottom-right FAB + slide-in panel.
                  Now goes through the AI Library / AgentRunner pipeline,
                  so users see Skill / Delegate sub-task cards inline
                  and every turn lands in agent_runs telemetry. */}
              <AIChatDrawer
                projectId={projectId}
                contextType="storyboard"
                contextId={projectId}
              />
            </>
          )}
        </>
      )}
    </div>
  );
}

// ─── Floating icon button ────────────────────────────────────────────────────

function FloatingIconButton({
  icon,
  tooltip,
  active,
  onClick,
}: {
  icon: React.ReactNode;
  tooltip: string;
  active?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={tooltip}
      className={`flex items-center justify-center w-8 h-8 rounded-lg transition-colors ${
        active
          ? 'bg-zinc-700 text-zinc-100'
          : 'text-zinc-500 hover:text-zinc-200 hover:bg-zinc-800/80'
      }`}
    >
      {icon}
    </button>
  );
}
