// frontend/features/canvas-core/ui/CanvasListPage.tsx
//
// Phase 0 of the Infinite-Canvas parity epic (G11): a top-level landing
// page so canvases are reachable from the main sidebar instead of being
// buried under Resources → Project Assets. Lists every canvas grouped by
// project via listCanvases() — NOT the project-assets tree, whose orphan
// filter hides freshly created empty canvases.

import { useEffect, useState } from 'react';
import { Navigate, useNavigate, useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Frame, Loader2, Plus } from 'lucide-react';
import { PageHeader } from '../../../components/AILibrary/PageHeader';
import { useToast } from '../../../components/Toast';
import { fetchProjects } from '../../../services/projectsService';
import type { Project } from '../../../types';
import { CANVAS_NAV_ENABLED } from '../flags';
import { createCanvas, listCanvases } from '../services/canvasService';
import type { Canvas } from '../types';

interface ProjectGroup {
  project: Project;
  /** null = the canvas list for this project failed to load. */
  canvases: Canvas[] | null;
}

// ISO-8601 timestamps sort correctly with plain comparison; localeCompare
// is locale-sensitive and the wrong tool for machine timestamps.
function sortNewestFirst(canvases: Canvas[]): Canvas[] {
  return [...canvases].sort((a, b) =>
    a.updated_at < b.updated_at ? 1 : a.updated_at > b.updated_at ? -1 : 0,
  );
}

const FOCUS_RING =
  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500/40';

export default function CanvasListPage() {
  const { teamId } = useParams<{ teamId: string }>();
  const navigate = useNavigate();
  const { t } = useTranslation();
  const { addToast } = useToast();

  const [groups, setGroups] = useState<ProjectGroup[] | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  const [creatingProjectId, setCreatingProjectId] = useState<string | null>(null);

  useEffect(() => {
    if (!CANVAS_NAV_ENABLED) return undefined;
    let cancelled = false;
    // Reset before each run so a stale error (or another team's groups)
    // never survives a teamId change.
    setGroups(null);
    setLoadFailed(false);
    (async () => {
      try {
        const projects = await fetchProjects({ teamId });
        // A failed project keeps the rest of the page usable — its group
        // renders an inline failure notice instead of a fake-empty grid.
        const canvasLists = await Promise.all(
          projects.map((project) =>
            listCanvases(project.id).catch((err: unknown): null => {
              console.error('[CanvasListPage] listCanvases failed:', project.id, err);
              return null;
            }),
          ),
        );
        if (cancelled) return;
        setGroups(
          projects.map((project, i) => {
            const list = canvasLists[i];
            return { project, canvases: list === null ? null : sortNewestFirst(list) };
          }),
        );
      } catch (err) {
        console.error('[CanvasListPage] load failed:', err);
        if (!cancelled) setLoadFailed(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [teamId]);

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

  const totalCanvases = groups?.reduce((sum, g) => sum + (g.canvases?.length ?? 0), 0);

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
          groups?.map(({ project, canvases }) => (
            <section key={project.id} className="mb-8">
              <h2 className="mb-3 flex items-baseline gap-2 text-sm font-semibold text-content">
                <span className="truncate">{project.name}</span>
                {canvases !== null && (
                  <span className="text-xs font-normal text-content-4">{canvases.length}</span>
                )}
              </h2>
              {canvases === null ? (
                <div className="rounded-2xl border border-line bg-island p-4 text-sm text-red-400">
                  {t(
                    'canvasList.groupLoadFailed',
                    'Failed to load canvases for this project',
                  )}
                </div>
              ) : (
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
                  {canvases.map((canvas) => (
                    <button
                      key={canvas.id}
                      onClick={() => navigate(`/team/${teamId}/canvas/${canvas.id}`)}
                      className={`group flex flex-col items-start gap-2 rounded-2xl border border-line bg-island p-4 text-left transition-all duration-200 hover:border-indigo-500/40 hover:bg-island-2 ${FOCUS_RING}`}
                    >
                      <div className="flex w-full items-center gap-2">
                        <Frame
                          size={16}
                          className="shrink-0 text-content-3 group-hover:text-indigo-400"
                        />
                        <span className="truncate text-sm font-medium text-content">
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
                  ))}
                  <button
                    onClick={() => handleCreateCanvas(project.id)}
                    disabled={creatingProjectId !== null}
                    className={`flex min-h-[76px] items-center justify-center gap-2 rounded-2xl border border-dashed border-line p-4 text-sm text-content-3 transition-all duration-200 hover:border-indigo-500/40 hover:text-content disabled:opacity-50 ${FOCUS_RING}`}
                  >
                    {creatingProjectId === project.id ? (
                      <Loader2 size={16} className="animate-spin" />
                    ) : (
                      <Plus size={16} />
                    )}
                    <span>{t('canvasList.newCanvas', 'New Canvas')}</span>
                  </button>
                </div>
              )}
            </section>
          ))}
      </div>
    </div>
  );
}
