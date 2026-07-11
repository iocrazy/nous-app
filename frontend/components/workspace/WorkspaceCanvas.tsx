/**
 * WorkspaceCanvas — the workspace's Canvas module (replaces the last
 * "coming soon" placeholder). Glue between the projects-workspace epic and
 * the Infinite-Canvas parity epic: the project's canvases as cards, one
 * click into the fullscreen canvas editor, plus New Canvas.
 *
 * Deliberately a LIST, not an embedded surface — the editor owns its own
 * route/chrome (`/team/:teamId/canvas/:canvasId`), and embedding React
 * Flow inside the workspace shell would fight its keyboard/pan gestures.
 */

import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Frame, Loader2, Plus } from 'lucide-react';
import { useToast } from '../Toast';
import {
  createCanvas,
  listCanvases,
} from '../../features/canvas-core/services/canvasService';
import type { Canvas } from '../../features/canvas-core/types';

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

  const handleCreate = async () => {
    if (creating) return;
    setCreating(true);
    try {
      const canvas = await createCanvas(projectId, {
        name: t('canvasList.untitled', 'Untitled Canvas'),
      });
      navigate(editorPath(String(canvas.id)));
    } catch (err) {
      // A create hiccup must not blow away the already-loaded list.
      console.error('[WorkspaceCanvas] create failed:', err);
      addToast(t('canvasList.createFailed', 'Failed to create canvas'), 'error');
    } finally {
      setCreating(false);
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
      <div className="flex h-40 items-center justify-center">
        <Loader2 size={18} className="animate-spin text-ink-500" />
      </div>
    );
  }

  return (
    <div>
      {canvases.length === 0 && (
        <div
          data-testid="workspace-canvas-empty"
          className="mb-4 rounded-xl border border-dashed border-ink-700 p-8 text-center text-sm text-ink-500"
        >
          {t(
            'projects.workspace.canvasEmpty',
            'No canvases yet — create one to start sketching this project.',
          )}
        </div>
      )}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {canvases.map((canvas) => (
          <button
            key={String(canvas.id)}
            onClick={() => navigate(editorPath(String(canvas.id)))}
            className="group flex flex-col items-start gap-2 rounded-xl border border-ink-700 bg-ink-900 p-4 text-left transition-colors hover:border-ink-500"
          >
            <div className="flex w-full items-center gap-2">
              <Frame size={15} className="shrink-0 text-ink-500 group-hover:text-ink-300" />
              <span className="truncate text-sm font-medium text-ink-100">
                {canvas.name || t('canvasList.untitled', 'Untitled Canvas')}
              </span>
            </div>
            <div className="flex items-center gap-2 text-[11px] text-ink-500">
              <span className="rounded-full border border-ink-700 px-2 py-0.5 uppercase tracking-wide">
                {t(`canvasList.kind.${canvas.kind}`, canvas.kind)}
              </span>
              {canvas.updated_at && (
                <span>{new Date(canvas.updated_at).toLocaleDateString()}</span>
              )}
            </div>
          </button>
        ))}
        <button
          onClick={() => void handleCreate()}
          disabled={creating}
          className="flex min-h-[72px] items-center justify-center gap-2 rounded-xl border border-dashed border-ink-700 p-4 text-sm text-ink-500 transition-colors hover:border-ink-500 hover:text-ink-300 disabled:opacity-50"
        >
          {creating ? <Loader2 size={15} className="animate-spin" /> : <Plus size={15} />}
          <span>{t('canvasList.newCanvas', 'New Canvas')}</span>
        </button>
      </div>
    </div>
  );
}

export default WorkspaceCanvas;
