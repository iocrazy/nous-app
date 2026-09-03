/**
 * WorkspaceCanvas — the workspace's Canvas module (replaces the last
 * "coming soon" placeholder). Glue between the projects-workspace epic and
 * the Infinite-Canvas parity epic: the project's canvases as cards, one
 * click into the fullscreen canvas editor, plus New Canvas.
 *
 * Deliberately a LIST, not an embedded surface — the editor owns its own
 * route/chrome (`/team/:teamId/canvas/:canvasId`), and embedding React
 * Flow inside the workspace shell would fight its keyboard/pan gestures.
 *
 * Each card's top half is a live minimap of the canvas's real node layout
 * (CanvasCardPreview) — the list endpoint returns full documents, so the
 * geometry is already on hand.
 */

import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Frame, Loader2, Plus, Sparkles } from 'lucide-react';
import { Loading } from '../common/Loading';
import { useToast } from '../Toast';
import {
  createCanvas,
  listCanvases,
} from '../../features/canvas-core/services/canvasService';
import type { Canvas, CreatableCanvasKind } from '../../features/canvas-core/types';
import { NewCanvasDialog } from '../../features/canvas-core/ui/NewCanvasDialog';
import { CanvasCardMenu } from '../../features/canvas-core/ui/CanvasCardMenu';
import { deleteCanvas } from '../../features/canvas-core/services/canvasService';
import { formatRelativeTime } from '../../utils/relativeTime';
import { CanvasCardPreview } from './CanvasCardPreview';

const FOCUS_RING =
  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500/40';

interface WorkspaceCanvasProps {
  projectId: string;
  /** Present in team context; personal projects fall back to the bare
   *  /canvas route (which resolves the team server-side). */
  teamId?: string;
}

export function WorkspaceCanvas({ projectId, teamId }: WorkspaceCanvasProps) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { addToast } = useToast();
  const [canvases, setCanvases] = useState<Canvas[] | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  const [creating, setCreating] = useState(false);
  const [dialogOpen, setDialogOpen] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setCanvases(null);
    setLoadFailed(false);
    listCanvases(projectId)
      .then((rows) => {
        if (!cancelled) setCanvases(rows);
      })
      .catch((err) => {
        console.error('[WorkspaceCanvas] load failed:', err);
        if (!cancelled) setLoadFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  const editorPath = (canvasId: string) =>
    teamId ? `/team/${teamId}/canvas/${canvasId}` : `/canvas/${canvasId}`;

  // IC-style create dialog (name + Standard/Smart kind) — the actual POST
  // happens here once the dialog submits.
  const handleCreate = async ({ name, kind }: { name: string; kind: CreatableCanvasKind }) => {
    if (creating) return;
    setCreating(true);
    try {
      const canvas = await createCanvas(projectId, {
        name: name || t('canvasList.untitled', 'Untitled Canvas'),
        kind,
      });
      navigate(editorPath(String(canvas.id)));
    } catch (err) {
      // A create hiccup must not blow away the already-loaded list.
      console.error('[WorkspaceCanvas] create failed:', err);
      addToast(t('canvasList.createFailed', 'Failed to create canvas'), 'error');
      setCreating(false);
    }
  };

  const handleDelete = async (canvasId: string) => {
    try {
      await deleteCanvas(canvasId);
      setCanvases((prev) => prev?.filter((c) => String(c.id) !== canvasId) ?? prev);
      addToast(t('canvasList.movedToTrash', 'Moved to trash'), 'info');
    } catch (err) {
      console.error('[WorkspaceCanvas] delete failed:', err);
      addToast(t('canvasList.deleteFailed', 'Failed to delete canvas'), 'error');
    }
  };

  if (loadFailed) {
    return (
      <div className="flex h-40 items-center justify-center text-sm text-red-400">
        {t('canvasList.loadFailed', 'Failed to load canvases')}
      </div>
    );
  }

  if (canvases === null) {
    return (
      <div className="flex h-40 items-center justify-center text-ink-500">
        <Loading center />
      </div>
    );
  }

  return (
    // The workspace content wrapper has no top padding (px-6 pb-8) — give
    // the card grid its own breathing room so previews don't sit flush.
    <div className="pt-5">
      {canvases.length === 0 && (
        <div
          data-testid="workspace-canvas-empty"
          className="mb-4 flex flex-col items-center gap-3 rounded-xl border border-dashed border-ink-700 px-8 py-10 text-center"
        >
          <span className="flex h-11 w-11 items-center justify-center rounded-full bg-ink-800 text-ink-400">
            <Frame size={18} />
          </span>
          <p className="text-sm text-ink-500">
            {t(
              'projects.workspace.canvasEmpty',
              'No canvases yet — create one to start sketching this project.',
            )}
          </p>
        </div>
      )}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {canvases.map((canvas) => (
          <div key={String(canvas.id)} className="group relative">
            <div className="absolute right-2 top-2 z-10 opacity-0 transition-opacity focus-within:opacity-100 group-hover:opacity-100">
              <CanvasCardMenu
                canvasId={String(canvas.id)}
                canvasName={canvas.name || t('canvasList.untitled', 'Untitled Canvas')}
                onRenamed={(name) =>
                  setCanvases(
                    (prev) =>
                      prev?.map((c) =>
                        c.id === canvas.id ? { ...c, name } : c,
                      ) ?? prev,
                  )
                }
                onDelete={() => void handleDelete(String(canvas.id))}
              />
            </div>
          <button
            data-testid="workspace-canvas-card"
            onClick={() => navigate(editorPath(String(canvas.id)))}
            className={`flex w-full flex-col overflow-hidden rounded-xl border border-ink-800 bg-ink-900 text-left transition-all duration-200 hover:-translate-y-0.5 hover:border-[var(--accent-border)] hover:shadow-lg hover:shadow-indigo-500/10 ${FOCUS_RING}`}
          >
            <CanvasCardPreview
              nodes={canvas.nodes_json}
              connections={canvas.connections_json}
            />
            <div className="flex w-full flex-1 flex-col gap-1.5 p-3.5">
              <span className="truncate text-sm font-medium text-ink-100">
                {canvas.name || t('canvasList.untitled', 'Untitled Canvas')}
              </span>
              <div className="flex items-center gap-2 text-[11px] text-ink-500">
                {canvas.kind === 'lite' ? (
                  <span className="inline-flex items-center gap-1 rounded-full bg-[var(--accent-soft)] px-2 py-0.5 font-medium text-[var(--accent-text)]">
                    <Sparkles size={10} />
                    {t('canvasList.kind.lite', 'Smart')}
                  </span>
                ) : (
                  <span className="rounded-full bg-ink-800 px-2 py-0.5 text-ink-400">
                    {t(`canvasList.kind.${canvas.kind}`, canvas.kind)}
                  </span>
                )}
                <span>
                  {t('canvasList.nodeCount', '{{count}} nodes', {
                    count: canvas.nodes_json?.length ?? 0,
                  })}
                </span>
                {canvas.updated_at && (
                  <>
                    <span aria-hidden className="text-ink-700">
                      ·
                    </span>
                    <span>{formatRelativeTime(canvas.updated_at, t)}</span>
                  </>
                )}
              </div>
            </div>
          </button>
          </div>
        ))}
        <button
          onClick={() => setDialogOpen(true)}
          disabled={creating}
          className={`group flex min-h-[176px] flex-col items-center justify-center gap-2.5 rounded-xl border border-dashed border-ink-700 p-4 text-sm text-ink-500 transition-all duration-200 hover:border-[var(--accent-border)] hover:bg-[var(--accent-soft)] hover:text-ink-300 disabled:opacity-50 ${FOCUS_RING}`}
        >
          <span className="flex h-9 w-9 items-center justify-center rounded-full border border-ink-700 transition-colors group-hover:border-[var(--accent-border)] group-hover:text-[var(--accent-text)]">
            {creating ? (
              <Loader2 size={15} className="animate-spin" />
            ) : (
              <Plus size={15} />
            )}
          </span>
          <span>{t('canvasList.newCanvas', 'New Canvas')}</span>
        </button>
      </div>
      <NewCanvasDialog
        open={dialogOpen}
        creating={creating}
        onCancel={() => setDialogOpen(false)}
        onCreate={(payload) => void handleCreate(payload)}
      />
    </div>
  );
}

export default WorkspaceCanvas;
