import { useCallback } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { Clapperboard, ArrowRight } from 'lucide-react';
import { useTranslation } from 'react-i18next';

/**
 * ProjectStoryboardTab — retired in the Phase B P4 cutover.
 *
 * The old ReactFlow storyboard workbench (its own `storyboard_*` tables + the
 * `sb_*` routers, now 410-tombstoned) is gone; storyboarding lives inside each
 * script's editor as the per-scene shot board. The tab is kept (navigation must
 * not disappear) but its body is now a migration notice that routes the user to
 * the project's Scripts tab, where they open a script and board its scenes.
 */
interface Props {
  projectId: string;
}

export function ProjectStoryboardTab({ projectId }: Props) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { teamId } = useParams<{ teamId: string }>();

  const goToScripts = useCallback(() => {
    const base = teamId
      ? `/team/${teamId}/projects/${projectId}`
      : `/projects/${projectId}`;
    navigate(`${base}?tab=scripts`);
  }, [navigate, teamId, projectId]);

  return (
    <div
      className="flex flex-col items-center justify-center h-64 gap-4 text-center"
      data-testid="storyboard-moved-notice"
    >
      <div className="w-16 h-16 rounded-2xl bg-ink-800/50 flex items-center justify-center">
        <Clapperboard size={28} className="text-ink-600" />
      </div>
      <div className="max-w-md">
        <p className="text-sm font-medium text-ink-300">
          {t('projects.storyboardMoved.title')}
        </p>
        <p className="text-xs text-ink-500 mt-1.5 leading-relaxed">
          {t('projects.storyboardMoved.body')}
        </p>
      </div>
      <button
        onClick={goToScripts}
        data-testid="storyboard-go-to-scripts"
        className="flex items-center gap-2 px-5 py-2.5 text-sm font-medium rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white transition-colors mt-2"
      >
        {t('projects.storyboardMoved.goToScripts')}
        <ArrowRight size={15} />
      </button>
    </div>
  );
}
