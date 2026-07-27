/**
 * SendToCanvasModal — two-step Project → Canvas picker that fires a
 * resource's current prompt (positive/negative + cover) into a canvas as
 * a fresh Prompt + Media node pair (Phase 2 Task 4 of spec
 * 2026-07-26-asset-prompt-management).
 *
 * Deliberately dumb about the target: it doesn't know or care which
 * project/canvas the resource "belongs" to. The caller (PromptSection)
 * hands over the already-resolved positive/negative text for whichever
 * lang side is currently active; this component only handles picking a
 * destination and navigating there. CanvasComposer's `location.state`
 * consumer (Task 4 step 4) turns the payload into nodes once the target
 * canvas has finished loading.
 *
 * Styling follows ShareModal's dark card convention (bg-ink-900 +
 * backdrop), sized down to a picker list since there's no form here.
 */
import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { ArrowLeft, Layers, X } from 'lucide-react';

import { listCanvases } from '../../features/canvas-core/services/canvasService';
import type { Canvas } from '../../features/canvas-core/types';
import { getResourceCoverUrl } from '../../services/resourceService';
import { fetchProjects } from '../../services/projectsService';
import type { Project, Resource } from '../../types';

export interface SendToCanvasModalProps {
  resource: Resource;
  /** Prompt text for whichever lang side is currently active in PromptSection. */
  positive: string;
  /** May be empty/absent — canvas insert omits the negative field then. */
  negative: string | null;
  onClose: () => void;
}

export function SendToCanvasModal({ resource, positive, negative, onClose }: SendToCanvasModalProps) {
  const { t } = useTranslation();
  const navigate = useNavigate();

  const [projects, setProjects] = useState<Project[]>([]);
  const [loadingProjects, setLoadingProjects] = useState(true);
  const [selectedProject, setSelectedProject] = useState<Project | null>(null);
  const [canvases, setCanvases] = useState<Canvas[]>([]);
  const [loadingCanvases, setLoadingCanvases] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetchProjects()
      .then((rows) => {
        if (!cancelled) setProjects(rows);
      })
      .catch((err) => console.error('[SendToCanvasModal] fetchProjects failed:', err))
      .finally(() => {
        if (!cancelled) setLoadingProjects(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const handlePickProject = (project: Project) => {
    setSelectedProject(project);
    setLoadingCanvases(true);
    listCanvases(project.id)
      .then((rows) => setCanvases(rows))
      .catch((err) => console.error('[SendToCanvasModal] listCanvases failed:', err))
      .finally(() => setLoadingCanvases(false));
  };

  const handlePickCanvas = (canvas: Canvas) => {
    // Mirrors ResourceDetailPage's canvas-ref navigation: team prefix only
    // when the destination project actually belongs to a team.
    const target = `${selectedProject?.team_id ? `/team/${selectedProject.team_id}` : ''}/canvas/${canvas.id}`;
    navigate(target, {
      state: {
        promptInsert: {
          assetId: resource.id,
          filename: resource.filename,
          positive,
          negative: negative || undefined,
          coverUrl: getResourceCoverUrl(resource.id),
        },
      },
    });
    onClose();
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div
        className="absolute inset-0 bg-black/60 backdrop-blur-sm"
        onClick={onClose}
        data-testid="send-to-canvas-backdrop"
      />
      <div className="relative bg-ink-900 border border-ink-800 rounded-2xl shadow-2xl w-full max-w-md max-h-[70vh] flex flex-col animate-in fade-in zoom-in-95 duration-200">
        <div className="flex items-center justify-between p-4 border-b border-ink-800 shrink-0">
          <div className="flex items-center gap-2 min-w-0">
            {selectedProject && (
              <button
                type="button"
                onClick={() => setSelectedProject(null)}
                aria-label={t('common.back', 'Back')}
                className="p-1 text-ink-400 hover:text-ink-50 hover:bg-ink-800 rounded-lg transition-colors shrink-0"
              >
                <ArrowLeft size={16} />
              </button>
            )}
            <h2 className="text-sm font-semibold text-ink-50 truncate">
              {selectedProject ? selectedProject.name : t('resources.infoPanel.sendToCanvas', 'Send to Canvas')}
            </h2>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="p-1 text-ink-400 hover:text-ink-50 hover:bg-ink-800 rounded-lg transition-colors shrink-0"
          >
            <X size={18} />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-2">
          {!selectedProject ? (
            loadingProjects ? (
              <div className="py-8 text-center text-xs text-ink-500">{t('common.loading', 'Loading...')}</div>
            ) : projects.length === 0 ? (
              <div className="py-8 text-center text-xs text-ink-500">
                {t('resources.infoPanel.sendToCanvasNoProjects', 'No projects yet')}
              </div>
            ) : (
              projects.map((project) => (
                <button
                  key={project.id}
                  type="button"
                  onClick={() => handlePickProject(project)}
                  className="w-full flex items-center gap-2 px-3 py-2 rounded-lg text-left text-sm text-ink-200 hover:bg-ink-800/60 transition-colors"
                >
                  <span className="flex-1 min-w-0 truncate">{project.name}</span>
                </button>
              ))
            )
          ) : loadingCanvases ? (
            <div className="py-8 text-center text-xs text-ink-500">{t('common.loading', 'Loading...')}</div>
          ) : canvases.length === 0 ? (
            <div className="py-8 text-center text-xs text-ink-500">
              {t('resources.infoPanel.sendToCanvasNoCanvases', 'This project has no canvases yet')}
            </div>
          ) : (
            canvases.map((canvas) => (
              <button
                key={canvas.id}
                type="button"
                onClick={() => handlePickCanvas(canvas)}
                className="w-full flex items-center gap-2 px-3 py-2 rounded-lg text-left text-sm text-ink-200 hover:bg-ink-800/60 transition-colors"
              >
                <Layers size={14} className="text-ink-500 shrink-0" />
                <span className="flex-1 min-w-0 truncate">{canvas.name}</span>
              </button>
            ))
          )}
        </div>
      </div>
    </div>
  );
}
