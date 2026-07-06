import { useEffect } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { ToastProvider, useToast } from '../../components/Toast';

/**
 * Fullscreen route — mounted OUTSIDE AppLayout (see router.tsx).
 *
 * Retired in the Phase B P4 cutover: the ReactFlow storyboard canvas is gone
 * (storyboarding now lives inside the script editor as the per-scene shot
 * board). The route is KEPT so old bookmarks / links don't 404 — it now
 * redirects to the project's Scripts tab and toasts that the surface moved.
 */
function StoryboardMovedRedirect() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { addToast } = useToast();
  const { teamId, projectId } = useParams<{ teamId: string; projectId: string }>();

  useEffect(() => {
    addToast(t('projects.storyboardMoved.toast'), 'info');
    const target = projectId
      ? teamId
        ? `/team/${teamId}/projects/${projectId}?tab=scripts`
        : `/projects/${projectId}?tab=scripts`
      : '/projects';
    navigate(target, { replace: true });
  }, [navigate, addToast, t, teamId, projectId]);

  return (
    <div
      className="flex h-screen items-center justify-center text-sm text-ink-400"
      data-testid="storyboard-moved-redirect"
    >
      {t('projects.storyboardMoved.toast')}
    </div>
  );
}

export function StoryboardWorkbench() {
  return (
    <ToastProvider>
      <StoryboardMovedRedirect />
    </ToastProvider>
  );
}
