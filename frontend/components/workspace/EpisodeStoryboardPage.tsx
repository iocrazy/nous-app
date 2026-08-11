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
import { useToast } from '../Toast';
import { onShotFocus } from '../agentActivity/shotFocusBus';
import { onOpenShotInList } from '../../features/canvas-core/smart/openShotInListBus';
import { StoryboardCanvasEmbed } from '../../features/canvas-core/ui/StoryboardCanvasEmbed';
import { EpisodeViewTabs } from './EpisodeViewTabs';
import { EpisodeSceneBoard } from './EpisodeSceneBoard';
import { EpisodeShotListTable, type EpisodeShotListTableHandle } from './EpisodeShotListTable';
import { SURFACE_VIEWS } from './nodeSurface';
import { __clearSceneShotsCache } from './useSceneShots';
import type { EpisodeProgress } from '../../types';

/** How many 150ms polls `scrollToShotCard` retries before giving up
 *  silently (~3s) — the shot-list view's data (scenes/shots) can still be
 *  loading when the "Delete in shot list" menu action switches tabs. */
const SCROLL_TO_SHOT_MAX_ATTEMPTS = 20;
const SCROLL_TO_SHOT_POLL_MS = 150;

export interface EpisodeStoryboardPageProps {
  projectId: string;
  teamId: string;
  episode: EpisodeProgress | null;
  /** 'storyboard' | 'canvas' | 'shotlist', from URL ?view=; defaults to 'storyboard'. */
  initialView: string | null;
  onViewChange: (view: string) => void;
  findExistingScript: (ep: EpisodeProgress) => Promise<string | null>;
  provisionScript: (ep: EpisodeProgress) => Promise<string | null>;
  /**
   * Entry point ② (shot-nodes-on-canvas Task 5): the URL `?shot=<id>` deep
   * link, resolved by ProjectWorkspace's own shot-URL effect. Every shot
   * focus request converges on the canvas now (Task 6 retired the embedded
   * editor's storyboard rail this used to alternate with) — ProjectWorkspace
   * clears the URL's `shot` param itself the moment it reads it (existing
   * one-shot precedent), so this page only needs to merge the value into its
   * own focus state and report back that it did.
   */
  focusShotId?: string | null;
  /** Fired once `focusShotId` has been merged into local state — lets
   *  ProjectWorkspace reset its own copy so a later render doesn't re-fire
   *  (does NOT wait for the canvas to actually finish focusing the node;
   *  see `StoryboardCanvasEmbed`/`CanvasView`'s own `onFocusHandled` for
   *  that longer async leg). */
  onFocusShotIdConsumed?: () => void;
}

const STORYBOARD_VIEWS = SURFACE_VIEWS.storyboard;
const DEFAULT_VIEW = STORYBOARD_VIEWS[0].key;

/**
 * episodeId -> scriptId|null, for episodes whose script existence has
 * already been probed once this session (fix 2, storyboard-page-polish:
 * re-entering the same episode's storyboard module used to re-run the
 * probe → Loading gate every time). Stale-while-revalidate, mirroring
 * `settledCache` in `agentActivity/useRunToolActivity.ts`: a cached value
 * renders IMMEDIATELY (skips the loading gate entirely), while the probe
 * still runs in the background to silently correct it — e.g. the script was
 * created from elsewhere since the last visit — and only touches state when
 * the fresh result actually differs from what's already on screen.
 */
const scriptProbeCache = new Map<string, string | null>();

/** Exposed for tests — module-level cache otherwise leaks between cases. */
export function __clearScriptProbeCache(): void {
  scriptProbeCache.clear();
}

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
  onViewChange,
  findExistingScript,
  provisionScript,
  focusShotId,
  onFocusShotIdConsumed,
}: EpisodeStoryboardPageProps) {
  const { t } = useTranslation();
  const { addToast } = useToast();

  const [view, setViewState] = useState<string>(
    initialView && STORYBOARD_VIEWS.some((v) => v.key === initialView) ? initialView : DEFAULT_VIEW,
  );

  // Manual tab click (EpisodeViewTabs' onChange) — the ONLY path that writes
  // to the URL. Review fix round 1 (sticky `?shot=`): ProjectWorkspace's
  // `onViewChange` deletes `shot` from the URL in the same setSearchParams
  // call — `shot` is a one-shot deep-link trigger, invalidated the moment the
  // writer manually picks a view. Also reused (Task 5) by the two
  // NON-url-driven focus entry points below (shot card click / shotFocusBus)
  // since both are genuine "switch tab" actions the URL should reflect —
  // unlike the pure `focusShotId` prop effect further down, which sets
  // `view` directly without a second `onViewChange` call (the URL already
  // says `view=canvas`, that's WHY the prop is populated).
  const handleTabChange = useCallback(
    (next: string) => {
      setViewState(next);
      onViewChange(next);
    },
    [onViewChange],
  );

  // Shot viewport focus (Task 5) — merges all three entry points into one
  // piece of state, threaded down into the embedded canvas.
  const [activeFocusShotId, setActiveFocusShotId] = useState<string | null>(null);
  const handleFocusHandled = useCallback(() => setActiveFocusShotId(null), []);

  // Entry ①: a view-one shot card click. Replaces the old "深链编辑器"
  // behavior (retired in Task 6 along with the editor's storyboard rail) —
  // the Canvas tab now IS the episode's real storyboard canvas (shot nodes
  // bound to `script_shots`), so focusing there is meaningful.
  const handleShotCardFocus = useCallback(
    (shotId: string, _sceneId: string) => {
      setActiveFocusShotId(shotId);
      handleTabChange('canvas');
    },
    [handleTabChange],
  );

  // Entry ②: URL `?shot=<id>` (see `focusShotId`'s doc comment on the props
  // interface — ProjectWorkspace already cleared the URL by the time this
  // fires). `view` is set directly (not via `handleTabChange` — this is a
  // prop-driven jump, not a manual tab click the URL needs a second write for).
  useEffect(() => {
    if (!focusShotId) return;
    setViewState('canvas');
    setActiveFocusShotId(focusShotId);
    onFocusShotIdConsumed?.();
  }, [focusShotId, onFocusShotIdConsumed]);

  // Entry ③: `shotFocusBus` (an agent panel's shot summary chip). Was one of
  // two subscribers alongside EditorShell's own (shot-nodes-on-canvas Task 5
  // binding decision, 2026-08-11) until Task 6 retired the editor's
  // storyboard rail — this page is now the bus's sole subscriber.
  useEffect(() => onShotFocus((shotId) => {
    setActiveFocusShotId(shotId);
    handleTabChange('canvas');
  }), [handleTabChange]);

  // Scene card's Open (view one, `EpisodeSceneBoard`'s `ep-scene-open-*`
  // button): used to deep-link into the embedded editor's storyboard rail at
  // that scene (Task 8) — retired in Task 6 along with the rest of that
  // rail. The click always happens while this page is already on view one
  // (`EpisodeSceneBoard` only renders there), so there is no tab to switch
  // and nowhere to bubble this to; it purely re-centers a column that may
  // have scrolled out of the horizontally-scrolling row. Handled entirely
  // locally — unlike the shot-focus entries above, publisher and target
  // share this exact component tree.
  const handleOpenScene = useCallback((sceneId: string) => {
    document
      .querySelector<HTMLElement>(`[data-testid="scene-column-${sceneId}"]`)
      ?.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'center' });
  }, []);

  // Task 4's node-menu "Delete in shot list" (`openShotInListBus`) — switch
  // to view one and scroll/highlight the matching shot card. The card's DOM
  // node may not exist yet the instant the tab switches (EpisodeSceneBoard's
  // own scenes/shots fetch is async) — `pendingScrollShotId` survives the
  // tab switch and the effect below polls briefly for the element.
  const [pendingScrollShotId, setPendingScrollShotId] = useState<string | null>(null);
  useEffect(() => onOpenShotInList((shotId) => {
    setPendingScrollShotId(shotId);
    handleTabChange('storyboard');
  }), [handleTabChange]);

  useEffect(() => {
    if (!pendingScrollShotId || view !== 'storyboard') return;
    let cancelled = false;
    let attempts = 0;
    const tryScroll = () => {
      if (cancelled) return;
      const el = document.querySelector(`[data-testid="shot-card-${pendingScrollShotId}"]`);
      if (el) {
        el.scrollIntoView({ behavior: 'smooth', block: 'center' });
        setPendingScrollShotId(null);
        return;
      }
      attempts += 1;
      if (attempts >= SCROLL_TO_SHOT_MAX_ATTEMPTS) {
        // Give up silently — same fire-and-forget philosophy as the buses
        // this converges (a no-op beats yanking the writer around).
        setPendingScrollShotId(null);
        return;
      }
      window.setTimeout(tryScroll, SCROLL_TO_SHOT_POLL_MS);
    };
    tryScroll();
    return () => {
      cancelled = true;
    };
  }, [pendingScrollShotId, view]);

  // Read-only probe (ported from ProjectWorkspace's surface panel, Task 3
  // review fix): re-probes on every episode-id change so switching episodes
  // via the sidebar ⇄ card re-resolves this page's script instead of leaving
  // a stale one mounted. Fix 2 (stale-while-revalidate): a cached result for
  // this episode renders immediately (no Loading gate on re-entry); the
  // probe still fires to refresh the cache in the background.
  const [script, setScript] = useState<ScriptState>(() => {
    if (!episode || !scriptProbeCache.has(episode.episode_id)) return { status: 'loading' };
    const cached = scriptProbeCache.get(episode.episode_id) ?? null;
    return cached ? { status: 'ready', scriptId: cached } : { status: 'missing' };
  });
  useEffect(() => {
    if (!episode) {
      setScript({ status: 'loading' });
      return;
    }
    const epId = episode.episode_id;
    const hadCache = scriptProbeCache.has(epId);
    const cached = hadCache ? (scriptProbeCache.get(epId) ?? null) : undefined;
    let cancelled = false;
    setScript(
      hadCache
        ? cached
          ? { status: 'ready', scriptId: cached }
          : { status: 'missing' }
        : { status: 'loading' },
    );
    findExistingScript(episode)
      .then((id) => {
        if (cancelled) return;
        scriptProbeCache.set(epId, id);
        // Background refresh (hadCache): only touch state when the fresh
        // result actually differs, so an already-rendered board doesn't
        // flicker/remount for a no-op refresh.
        if (hadCache && cached === id) return;
        setScript(id ? { status: 'ready', scriptId: id } : { status: 'missing' });
      })
      .catch((err) => {
        console.error('[EpisodeStoryboardPage] failed to probe script:', err);
        // Only clobber the view with the empty state on a first-visit probe
        // failure — a background refresh failure should leave the already-
        // rendered (cached) content alone.
        if (!cancelled && !hadCache) setScript({ status: 'missing' });
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
        // Cache invalidation (fix 2): a fresh/newly-provisioned script must
        // never be shadowed by a stale probe-cache entry (e.g. 'missing'
        // from before this click) or a stale scene/shots cache entry keyed
        // by a reused scriptId — clear both so the next read is live.
        scriptProbeCache.set(episode.episode_id, id);
        if (id) __clearSceneShotsCache(id);
        setScript(id ? { status: 'ready', scriptId: id } : { status: 'missing' });
      })
      .catch((err) => {
        console.error('[EpisodeStoryboardPage] failed to start storyboard:', err);
        // Typed failure echo (CLAUDE.md「触发路径必须类型化失败回显」) — was
        // silently swallowed by console.error alone when this block was
        // ported from ProjectWorkspace, which had this toast.
        addToast(t('common.error'), 'error');
        setScript({ status: 'missing' });
      });
  }, [episode, script.status, provisionScript, addToast, t]);

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
      {/* Header matches the editor's doc-tabs row (EditorShell's
          .mh-center-topbar/.mh-doc-tabs, mounted inline for the 'script'
          module): ONLY the pill segmented control, no page title, no count,
          no border-b divider — the tabs share the content pane's px-6
          horizontal inset (Fix 1, storyboard-page-polish). */}
      <div className="flex items-center gap-3 px-6 pt-4 pb-3">
        <EpisodeViewTabs views={STORYBOARD_VIEWS} active={view} onChange={handleTabChange} />
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
          // The episode's own storyboard canvas (shot-nodes-on-canvas Task 5)
          // — NOT the sidebar "Canvas" module's materials-canvas library
          // (`WorkspaceCanvas`, unchanged, still lives at that separate
          // entry point). `episode` is only null for the brief window before
          // the episodes fetch resolves — the tabs render regardless, so
          // this guards against mounting the embed with no episode id yet.
          <div data-testid="episode-view-canvas" className="min-h-[24rem]">
            {episode ? (
              <StoryboardCanvasEmbed
                episodeId={episode.episode_id}
                teamId={teamId}
                focusShotId={activeFocusShotId}
                onFocusHandled={handleFocusHandled}
              />
            ) : (
              <div className="flex justify-center py-10">
                <Loading center />
              </div>
            )}
          </div>
        ) : view === 'storyboard' ? (
          <div data-testid="episode-view-storyboard" className="min-h-[24rem]">
            {script.status === 'ready' ? (
              <EpisodeSceneBoard
                scriptId={script.scriptId}
                onOpenScene={handleOpenScene}
                onOpenShot={handleShotCardFocus}
              />
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
