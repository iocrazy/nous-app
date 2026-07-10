// frontend/features/canvas-core/ui/CanvasListPage.tsx
//
// Phase 0 of the Infinite-Canvas parity epic (G11): a top-level landing
// page so canvases are reachable from the main sidebar instead of being
// buried under Resources → Project Assets. One listTeamCanvases() call
// returns every project with its canvases as summary rows (empty projects
// included, so New Canvas is offered there) — NOT the project-assets tree,
// whose orphan filter hides freshly created empty canvases, and no
// per-project N+1 fan-out.

import { useCallback, useEffect, useState } from 'react';
import { Navigate, useNavigate, useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Frame, Loader2, Plus, Trash2 } from 'lucide-react';
import { PageHeader } from '../../../components/AILibrary/PageHeader';
import { useToast } from '../../../components/Toast';
import { CANVAS_NAV_ENABLED } from '../flags';
import { CanvasTrashSection } from './CanvasTrashSection';
import {
  createCanvas,
  deleteCanvas,
  listTeamCanvases,
  type TeamCanvasProject,
} from '../services/canvasService';

const FOCUS_RING =
  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500/40';

export default function CanvasListPage() {
  const { teamId } = useParams<{ teamId: string }>();
  const navigate = useNavigate();
  const { t } = useTranslation();
  const { addToast } = useToast();

  const [groups, setGroups] = useState<TeamCanvasProject[] | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  const [creatingProjectId, setCreatingProjectId] = useState<string | null>(null);
  const [reloadTick, setReloadTick] = useState(0);
  const reload = useCallback(() => setReloadTick((n) => n + 1), []);

  useEffect(() => {
    if (!CANVAS_NAV_ENABLED || !teamId) return undefined;
    let cancelled = false;
    // Reset before each run so a stale error (or another team's groups)
    // never survives a teamId change. reloadTick > 0 keeps the current
    // list on screen while a trash restore refetches.
    if (reloadTick === 0) {
      setGroups(null);
    }
    setLoadFailed(false);
    (async () => {
      try {
        // One call: every project with its canvases as summary rows
        // (server-ordered newest-first) — no N+1 fan-out.
        const tree = await listTeamCanvases(teamId);
        if (!cancelled) setGroups(tree);
      } catch (err) {
        console.error('[CanvasListPage] load failed:', err);
        if (!cancelled) setLoadFailed(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [teamId, reloadTick]);

  const handleDeleteCanvas = async (canvasId: string) => {
    try {
      await deleteCanvas(canvasId);
      // Optimistic prune — the row is in the trash now.
      setGroups(
        (prev) =>
          prev?.map((g) => ({
            ...g,
            canvases: g.canvases.filter((c) => c.id !== canvasId),
          })) ?? prev,
      );
      addToast(t('canvasList.movedToTrash', 'Moved to trash'), 'info');
    } catch (err) {
      console.error('[CanvasListPage] delete failed:', err);
      addToast(t('canvasList.deleteFailed', 'Failed to delete canvas'), 'error');
    }
  };

  // Mirrors ProjectAssetsTree.handleCreateCanvas: create empty, then drop
  // the user straight into the editor.
  const handleCreateCanvas = async (projectId: string) => {
    if (creatingProjectId) return;
    setCreatingProjectId(projectId);
    try {
      const canvas = await createCanvas(projectId, {
        name: t('canvasList.untitled', 'Untitled Canvas'),
      });
      navigate(`/team/${teamId}/canvas/${canvas.id}`);
    } catch (err) {
      console.error('[CanvasListPage] create canvas failed:', err);
      addToast(t('canvasList.createFailed', 'Failed to create canvas'), 'error');
    } finally {
      setCreatingProjectId(null);
    }
  };

  if (!CANVAS_NAV_ENABLED) {
    return <Navigate to={teamId ? `/team/${teamId}` : '/'} replace />;
  }

  const totalCanvases = groups?.reduce((sum, g) => sum + g.canvases.length, 0);

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-5xl px-6 pt-6 pb-12">
        <PageHeader
          title={t('canvasList.title', 'Canvas')}
          count={totalCanvases}
          subtitle={t('canvasList.subtitle', 'All canvases across your projects')}
        />

        {loadFailed && (
          <div className="rounded-2xl border border-line bg-island p-6 text-sm text-red-400">
            {t('canvasList.loadFailed', 'Failed to load canvases')}
          </div>
        )}

        {!loadFailed && groups === null && (
          <div className="flex h-40 items-center justify-center">
            <Loader2 size={20} className="animate-spin text-content-3" />
          </div>
        )}

        {!loadFailed && groups !== null && groups.length === 0 && (
          <div className="rounded-2xl border border-dashed border-line bg-island p-10 text-center text-sm text-content-3">
            {t(
              'canvasList.emptyProjects',
              'No projects yet — create a project to start a canvas.',
            )}
          </div>
        )}

        {!loadFailed &&
          groups?.map(({ project_id, project_name, canvases }) => (
            <section key={project_id} className="mb-8">
              <h2 className="mb-3 flex items-baseline gap-2 text-sm font-semibold text-content">
                <span className="truncate">{project_name}</span>
                <span className="text-xs font-normal text-content-4">{canvases.length}</span>
              </h2>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
                  {canvases.map((canvas) => (
                    <div key={canvas.id} className="group relative">
                      <button
                        onClick={() => navigate(`/team/${teamId}/canvas/${canvas.id}`)}
                        className={`flex w-full flex-col items-start gap-2 rounded-2xl border border-line bg-island p-4 text-left transition-all duration-200 hover:border-indigo-500/40 hover:bg-island-2 ${FOCUS_RING}`}
                      >
                        <div className="flex w-full items-center gap-2">
                          <Frame
                            size={16}
                            className="shrink-0 text-content-3 group-hover:text-indigo-400"
                          />
                          <span className="truncate pr-6 text-sm font-medium text-content">
                            {canvas.name || t('canvasList.untitled', 'Untitled Canvas')}
                          </span>
                        </div>
                        <div className="flex items-center gap-2 text-xs text-content-3">
                          <span className="rounded-full border border-line px-2 py-0.5 uppercase tracking-wide">
                            {t(`canvasList.kind.${canvas.kind}`, canvas.kind)}
                          </span>
                          <span>{new Date(canvas.updated_at).toLocaleDateString()}</span>
                        </div>
                      </button>
                      <button
                        aria-label={t('canvasList.moveToTrash', 'Move to trash')}
                        title={t('canvasList.moveToTrash', 'Move to trash')}
                        onClick={() => void handleDeleteCanvas(canvas.id)}
                        className={`absolute right-2.5 top-2.5 rounded-lg p-1.5 text-content-4 opacity-0 transition-opacity hover:bg-rose-500/10 hover:text-rose-400 focus-visible:opacity-100 group-hover:opacity-100 ${FOCUS_RING}`}
                      >
                        <Trash2 size={14} />
                      </button>
                    </div>
                  ))}
                  <button
                    onClick={() => handleCreateCanvas(project_id)}
                    disabled={creatingProjectId !== null}
                    className={`flex min-h-[76px] items-center justify-center gap-2 rounded-2xl border border-dashed border-line p-4 text-sm text-content-3 transition-all duration-200 hover:border-indigo-500/40 hover:text-content disabled:opacity-50 ${FOCUS_RING}`}
                  >
                    {creatingProjectId === project_id ? (
                      <Loader2 size={16} className="animate-spin" />
                    ) : (
                      <Plus size={16} />
                    )}
                    <span>{t('canvasList.newCanvas', 'New Canvas')}</span>
                  </button>
              </div>
            </section>
          ))}

        {!loadFailed && groups !== null && teamId && (
          <CanvasTrashSection teamId={teamId} onRestored={reload} />
        )}
      </div>
    </div>
  );
}
