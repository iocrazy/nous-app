/**
 * EpisodeStoryboardPage — the storyboard module's standalone page (IA
 * redesign Task 2, spec `2026-08-10-workspace-ia-redesign`). Storyboard was
 * promoted to the episode node's PRIMARY face by PR-A (三视图主工作面,
 * 2026-08-09 拍板) but still lived embedded inside Overview's "surface
 * panel" — this component IS that panel, lifted out into its own
 * sidebar-routed module (`?module=storyboard`) so it reads as a real page
 * instead of a conditional block bolted onto the workspace landing.
 *
 * Fixed three-view set — Storyboard | Canvas | Shot List — regardless of the
 * current workflow node's `surface`: unlike the old Overview panel (whose
 * view set was DERIVED from `viewsForNode(currentNode)` and could be empty
 * for a deliverable-only node), this page is only ever reached via an
 * explicit storyboard entry point (sidebar 分镜 row / a storyboard-surface
 * workflow-strip node), so it always shows the full storyboard surface.
 * `SURFACE_VIEWS.storyboard`'s keys are reused verbatim (not re-declared) so
 * no new i18n keys are needed for the tab labels.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Loading } from '../common/Loading';
import { requestShotFocus } from '../agentActivity/shotFocusBus';
import { EpisodeViewTabs } from './EpisodeViewTabs';
import { EpisodeSceneBoard } from './EpisodeSceneBoard';
import { EpisodeShotListTable, type EpisodeShotListTableHandle } from './EpisodeShotListTable';
import { WorkspaceCanvas } from './WorkspaceCanvas';
import { SURFACE_VIEWS } from './nodeSurface';
import type { EpisodeProgress } from '../../types';

export interface EpisodeStoryboardPageProps {
  projectId: string;
  teamId: string;
  episode: EpisodeProgress | null;
  /** 'storyboard' | 'canvas' | 'shotlist', from URL ?view=; defaults to 'storyboard'. */
  initialView: string | null;
  /** URL ?shot=: on entry, switch to canvas and requestShotFocus. */
  focusShotId: string | null;
  onViewChange: (view: string) => void;
  findExistingScript: (ep: EpisodeProgress) => Promise<string | null>;
  provisionScript: (ep: EpisodeProgress) => Promise<string | null>;
  /** Scene card's Open deep-link → the real editor at the scene level. */
  onOpenScene: (sceneId: string) => void;
}

const STORYBOARD_VIEWS = SURFACE_VIEWS.storyboard;
const DEFAULT_VIEW = STORYBOARD_VIEWS[0].key;

/**
 * Script id resolution state for the storyboard/shot-list panes. Ported
 * as-is from ProjectWorkspace's `SurfaceScriptState` (Task 3 review fix):
 * the probe MUST be read-only (`findExistingScript`, never
 * `provisionScript`) — a passive page landing must never silently create an
 * empty script. `'missing'` renders the explicit "Start Storyboard" CTA;
 * provisioning only happens from that click.
 */
type ScriptState =
  | { status: 'loading' }
  | { status: 'ready'; scriptId: string }
  | { status: 'missing' }
  | { status: 'provisioning' };

export function EpisodeStoryboardPage({
  projectId,
  teamId,
  episode,
  initialView,
  focusShotId,
  onViewChange,
  findExistingScript,
  provisionScript,
  onOpenScene,
}: EpisodeStoryboardPageProps) {
  const { t } = useTranslation();

  const [view, setViewState] = useState<string>(
    initialView && STORYBOARD_VIEWS.some((v) => v.key === initialView) ? initialView : DEFAULT_VIEW,
  );
  const setView = useCallback(
    (next: string) => {
      setViewState(next);
      onViewChange(next);
    },
    [onViewChange],
  );

  // `?shot=` deep-link: land straight on Canvas and ask whoever is listening
  // on the bus to reveal the shot once it has mounted (300ms — the canvas
  // module's own load/mount effect needs to settle first).
  useEffect(() => {
    if (!focusShotId) return;
    setView('canvas');
    const timer = window.setTimeout(() => requestShotFocus(focusShotId), 300);
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- fire only on a focusShotId change, not on every setView identity change
  }, [focusShotId]);

  // Read-only probe (ported from ProjectWorkspace's surface panel, Task 3
  // review fix): re-probes on every episode-id change so switching episodes
  // via the sidebar ⇄ card re-resolves this page's script instead of leaving
  // a stale one mounted.
  const [script, setScript] = useState<ScriptState>({ status: 'loading' });
  useEffect(() => {
    if (!episode) {
      setScript({ status: 'loading' });
      return;
    }
    let cancelled = false;
    setScript({ status: 'loading' });
    findExistingScript(episode)
      .then((id) => {
        if (cancelled) return;
        setScript(id ? { status: 'ready', scriptId: id } : { status: 'missing' });
      })
      .catch((err) => {
        console.error('[EpisodeStoryboardPage] failed to probe script:', err);
        if (!cancelled) setScript({ status: 'missing' });
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- re-probe only on an episode-id change; `episode`'s object identity changes on every episodes refetch
  }, [episode?.episode_id, findExistingScript]);

  // The ONLY write trigger for this page's script — fired exclusively by the
  // "Start Storyboard" CTA (an explicit click), never by the probe above.
  const handleStartStoryboard = useCallback(() => {
    if (!episode || script.status === 'provisioning') return;
    setScript({ status: 'provisioning' });
    provisionScript(episode)
      .then((id) => {
        setScript(id ? { status: 'ready', scriptId: id } : { status: 'missing' });
      })
      .catch((err) => {
        console.error('[EpisodeStoryboardPage] failed to start storyboard:', err);
        setScript({ status: 'missing' });
      });
  }, [episode, script.status, provisionScript]);

  function renderScriptGate() {
    if (script.status === 'missing') {
      return (
        <div
          data-testid="episode-surface-no-script"
          className="rounded-lg border border-dashed border-line bg-island-2/40 px-6 py-10 text-center"
        >
          <p className="text-sm font-medium text-content-2">
            {t('projects.episodeSurface.noScriptHint')}
          </p>
          <button
            type="button"
            data-testid="episode-surface-start-storyboard"
            onClick={handleStartStoryboard}
            className="mt-3 rounded-md bg-[var(--accent-soft)] px-4 py-2 text-sm font-medium text-[var(--accent-text)] hover:opacity-90"
          >
            {t('projects.episodeSurface.startStoryboard')}
          </button>
        </div>
      );
    }
    // 'loading' (initial probe) or 'provisioning' (CTA click in flight).
    return (
      <div className="flex justify-center py-10">
        <Loading center />
      </div>
    );
  }

  // Imperative handle into the mounted table (hideExport) so the Export
  // trigger can live in the page header instead of inside the table's own
  // content pane, without a second scenes/shots fetch.
  const shotListTableRef = useRef<EpisodeShotListTableHandle>(null);

  return (
    <div data-testid="episode-storyboard-page" className="flex flex-col h-full min-h-0">
      <div className="flex items-center gap-3 border-b border-line px-4 py-2.5">
        <span className="text-[13.5px] font-bold">
          {t('projects.storyboardPage.title', 'Storyboard')}
        </span>
        <span className="font-mono text-[11px] text-ink-500">{episode?.shots_total ?? 0}</span>
        <EpisodeViewTabs views={STORYBOARD_VIEWS} active={view} onChange={setView} />
        {view === 'shotlist' && (
          <button
            type="button"
            data-testid="ep-shotlist-export"
            disabled={script.status !== 'ready'}
            onClick={() => shotListTableRef.current?.exportCsv()}
            className="ml-auto rounded-md border border-line px-3 py-1.5 text-[13px] font-medium text-content hover:bg-island-2 disabled:opacity-50"
          >
            {t('projects.shotList.export')}
          </button>
        )}
      </div>
      <div className="flex-1 overflow-y-auto px-6 py-4">
        {view === 'canvas' ? (
          // Reuse the SAME canvas module component (not a fork); its sidebar
          // module registration (top-level Canvas) is untouched.
          <div className="min-h-[24rem]">
            <WorkspaceCanvas projectId={projectId} teamId={teamId} />
          </div>
        ) : view === 'storyboard' ? (
          <div data-testid="episode-view-storyboard" className="min-h-[24rem]">
            {script.status === 'ready' ? (
              <EpisodeSceneBoard scriptId={script.scriptId} onOpenScene={onOpenScene} />
            ) : (
              renderScriptGate()
            )}
          </div>
        ) : (
          <div data-testid="episode-view-shotlist" className="min-h-[24rem]">
            {script.status === 'ready' ? (
              <EpisodeShotListTable ref={shotListTableRef} scriptId={script.scriptId} hideExport />
            ) : (
              renderScriptGate()
            )}
          </div>
        )}
      </div>
    </div>
  );
}

export default EpisodeStoryboardPage;
